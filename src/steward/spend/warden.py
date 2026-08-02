"""Talking to pay-warden, which is the only thing that may authorise a spend.

pay-warden is a separate process reached over MCP stdio, and that separation is
deliberate rather than incidental. It is pinned to `mcp` 1.x while this is on
2.x, so the protocol is the contract between them instead of a shared import
and neither can drag the other's dependencies along. It holds the Prava
credentials, so a bug in steward's reasoning cannot reach them. And it cannot be
talked round: the policy is a YAML file the sponsor wrote, evaluated by rules,
in a process the agent has no other handle on.

**Steward never decides whether a purchase is allowed.** It describes what is
being bought and relays the answer. Every path to money goes through
`request_purchase`, and there is no branch anywhere in this codebase that mints
a payment session on its own.

One connection per call. Spawning a subprocess costs a beat, and a long-lived
session would avoid it — but it would also mean a background event loop, a
reconnect policy, and state that can go stale between a preview and the request
it was previewing. A person asks for soap a few times a week, not a few times a
second; correctness and crash-isolation are worth more here than the beat.

Money crosses as a decimal string, because that is pay-warden's wire format.
Everywhere on this side of the boundary it is integer minor units — see
`amount_to_decimal`, which is the only place the two representations meet.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from typing import IO, Any, Protocol

from .. import config

# pay-warden's vocabulary, mirrored rather than imported — see the module
# docstring. If it ever gains a verdict, this list failing to know about it is
# exactly the loud failure we want.
ALLOWED = "allowed"
DENIED = "denied"
NEEDS_APPROVAL = "needs_approval"
VERDICTS = (ALLOWED, DENIED, NEEDS_APPROVAL)

CALL_TIMEOUT = 30.0


class WardenError(RuntimeError):
    """pay-warden could not be reached, or answered something unusable."""


def amount_to_decimal(minor_units: int) -> str:
    """Integer minor units to pay-warden's decimal string. The only place the
    two representations meet, so rounding can only ever be wrong here."""
    return str(Decimal(minor_units) / Decimal(100))


def agent_name(person_id: int) -> str:
    """How a spender identifies themselves to pay-warden.

    Budgets and velocity limits are scoped by this string, so it has to be
    stable and per-person — one shared name would let one spender exhaust
    another's allowance. It is the pseudonym rather than the name on purpose:
    pay-warden keeps an audit trail on disk and has no need of anyone's name to
    enforce a budget.
    """
    return f"steward:person_{person_id}"


@dataclass(frozen=True)
class Decision:
    """What pay-warden said. `payment_url` is present only when it allowed."""

    verdict: str
    rule_id: str
    reason: str
    attempt_id: str = ""
    payment_url: str = ""
    session_id: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOWED

    @property
    def needs_approval(self) -> bool:
        return self.verdict == NEEDS_APPROVAL

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "attempt_id": self.attempt_id,
            "payment_url": self.payment_url,
        }


def _decision(payload: dict[str, Any]) -> Decision:
    verdict = str(payload.get("verdict", ""))
    if verdict not in VERDICTS:
        # Never default to allowed. An unrecognised verdict means the two sides
        # disagree about the vocabulary, and the safe reading of a disagreement
        # about permission is that permission was not given.
        raise WardenError(f"pay-warden returned an unknown verdict {verdict!r}")
    return Decision(
        verdict=verdict,
        rule_id=str(payload.get("rule_id", "")),
        reason=str(payload.get("reason", "")),
        attempt_id=str(payload.get("attempt_id", "")),
        payment_url=str(payload.get("payment_url", "")),
        session_id=str(payload.get("session_id", "")),
    )


class Warden(Protocol):
    """The seam every caller takes, so tests never spawn a subprocess."""

    def call(self, tool: str, arguments: dict[str, Any]) -> Any: ...


class StdioWarden:
    """The real thing: pay-warden as an MCP subprocess over stdio."""

    def __init__(self, command: list[str] | None = None, cwd: str | None = None) -> None:
        self.command = command or config.pay_warden_command()
        self.cwd = cwd if cwd is not None else config.pay_warden_cwd()

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        # pay-warden's stderr is captured rather than inherited. Inheriting it
        # prints its request log over whatever the person is reading; silencing
        # it throws away the only account of why a spawn failed. So it is held
        # and attached to the error, which is the one moment it is worth having.
        #
        # A real file, not StringIO: the stream is handed to the child process
        # as its stderr, so it has to have a file descriptor. An in-memory
        # buffer fails with a bare `fileno` on every call.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
            try:
                return asyncio.run(self._call(tool, arguments, errlog))
            except WardenError:
                raise
            except Exception as exc:
                # Spawn failure, transport error, protocol mismatch: to a caller
                # deciding whether it may spend, these are all the same answer.
                raise WardenError(
                    f"could not reach pay-warden via {' '.join(self.command)!r}: {exc}"
                    f"{_stderr_tail(errlog)}"
                ) from exc

    async def _call(self, tool: str, arguments: dict[str, Any], errlog: IO[str]) -> Any:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self.command[0],
            args=list(self.command[1:]),
            cwd=self.cwd,
            env=_forwarded_env(),
        )
        async with (
            stdio_client(params, errlog=errlog) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool, arguments, read_timeout_seconds=CALL_TIMEOUT)
            return _unwrap(result)


def _stderr_tail(errlog: IO[str], lines: int = 8) -> str:
    try:
        errlog.flush()
        errlog.seek(0)
        captured = [line for line in errlog.read().splitlines() if line.strip()]
    except (OSError, ValueError):
        # Reading the diagnostic must never replace the failure it describes.
        return ""
    if not captured:
        return ""
    return "\n  pay-warden said:\n    " + "\n    ".join(captured[-lines:])


# Environment prefixes forwarded into the subprocess. MCP's stdio client hands
# a child a deliberately minimal environment, which is the right default and
# also strips exactly the variables pay-warden configures itself with — its
# policy path, its audit database, its Prava credentials. Forwarding by
# namespace rather than by name means pay-warden can grow a setting without
# steward needing to hear about it, while nothing outside those namespaces
# (our OpenAI key, for one) is ever handed to another process.
FORWARDED_PREFIXES = ("PAY_WARDEN_", "PRAVA_")

# Steward's own settings, which merely happen to share the prefix.
_NOT_FORWARDED = frozenset({"PAY_WARDEN_COMMAND", "PAY_WARDEN_ARGS", "PAY_WARDEN_CWD"})


def _forwarded_env() -> dict[str, str]:
    from mcp.client.stdio import get_default_environment

    env = dict(get_default_environment())
    env.update(
        {
            key: value
            for key, value in os.environ.items()
            if key.startswith(FORWARDED_PREFIXES) and key not in _NOT_FORWARDED
        }
    )
    return env


def _unwrap(result: Any) -> Any:
    """Pull the payload out of an MCP CallToolResult.

    Tools return dicts, which the SDK ships as JSON in a text content block.
    `structuredContent` is preferred when present — a newer server sends the
    object directly and re-parsing the text copy would be a second chance to
    get it wrong.
    """
    if getattr(result, "isError", False):
        raise WardenError(f"pay-warden reported an error: {_text(result)[:300]}")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps a non-object return in {"result": ...}.
        return structured.get("result", structured)
    text = _text(result)
    if not text:
        raise WardenError("pay-warden returned an empty response")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise WardenError(f"pay-warden returned unparseable content: {text[:200]}") from exc


def _text(result: Any) -> str:
    return "".join(getattr(block, "text", "") for block in (getattr(result, "content", None) or []))


# --- the operations steward actually performs --------------------------------


def _products(description: str, amount_cents: int) -> list[dict[str, Any]]:
    return [
        {"description": description, "unit_price": amount_to_decimal(amount_cents), "quantity": 1}
    ]


def _arguments(
    *,
    person_id: int,
    description: str,
    amount_cents: int,
    currency: str,
    merchant_name: str,
    merchant_url: str,
    merchant_country: str,
) -> dict[str, Any]:
    return {
        "agent": agent_name(person_id),
        "merchant_name": merchant_name,
        "merchant_url": merchant_url,
        "merchant_country": merchant_country,
        "products": _products(description, amount_cents),
        "total_amount": amount_to_decimal(amount_cents),
        "currency": currency,
    }


def preview(
    *,
    person_id: int,
    description: str,
    amount_cents: int,
    currency: str,
    merchant_name: str,
    merchant_url: str,
    merchant_country: str,
    warden: Warden | None = None,
) -> Decision:
    """Ask what would happen. Nothing is minted and nothing is recorded."""
    client = warden or StdioWarden()
    return _decision(
        client.call(
            "preview_purchase",
            _arguments(
                person_id=person_id,
                description=description,
                amount_cents=amount_cents,
                currency=currency,
                merchant_name=merchant_name,
                merchant_url=merchant_url,
                merchant_country=merchant_country,
            ),
        )
    )


def request(
    *,
    person_id: int,
    description: str,
    amount_cents: int,
    currency: str,
    merchant_name: str,
    merchant_url: str,
    merchant_country: str,
    warden: Warden | None = None,
) -> Decision:
    """Ask for the money. The only path to a payment session in this codebase."""
    client = warden or StdioWarden()
    return _decision(
        client.call(
            "request_purchase",
            _arguments(
                person_id=person_id,
                description=description,
                amount_cents=amount_cents,
                currency=currency,
                merchant_name=merchant_name,
                merchant_url=merchant_url,
                merchant_country=merchant_country,
            ),
        )
    )


def release(attempt_id: str, *, warden: Warden | None = None) -> Decision:
    """Replay a parked attempt after the sponsor approved it.

    pay-warden mints from the request it recorded, not from anything passed in
    here — so what gets paid for is what was actually escalated, even if
    something on this side has since been edited.
    """
    client = warden or StdioWarden()
    payload = client.call("approve_purchase", {"attempt_id": attempt_id})
    if isinstance(payload, dict) and payload.get("error"):
        raise WardenError(str(payload["error"]))
    if not isinstance(payload, dict) or not payload.get("released"):
        raise WardenError(f"pay-warden did not release {attempt_id}: {payload!r:.200}")
    return Decision(
        verdict=ALLOWED,
        rule_id="sponsor_approved",
        reason="released by the sponsor",
        attempt_id=str(payload.get("attempt_id", attempt_id)),
        payment_url=str(payload.get("payment_url", "")),
        session_id=str(payload.get("session_id", "")),
    )


def audit_log(*, person_id: int | None = None, limit: int = 20, warden: Warden | None = None):
    """pay-warden's own record. Read from the source rather than mirrored here,
    so the ledger a sponsor reads is the one the policy engine actually wrote."""
    client = warden or StdioWarden()
    arguments: dict[str, Any] = {"limit": limit}
    if person_id is not None:
        arguments["agent"] = agent_name(person_id)
    result = client.call("get_audit_log", arguments)
    return result if isinstance(result, list) else []
