"""A scripted pay-warden, so the suite never spawns a subprocess.

Mirrors what the real server returns, including the shapes that matter: an
allowed request carries a payment_url, a parked one carries an attempt_id and
nothing else, and `approve_purchase` answers with `released`.
"""

from __future__ import annotations

from typing import Any

from steward.spend.warden import ALLOWED, DENIED, NEEDS_APPROVAL


class WardenStub:
    def __init__(self, replies: list[Any] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        if not self.replies:
            raise AssertionError(f"unscripted call to {tool}")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def last(self, tool: str) -> dict[str, Any]:
        for name, arguments in reversed(self.calls):
            if name == tool:
                return arguments
        raise AssertionError(f"{tool} was never called")

    def tools_called(self) -> list[str]:
        return [name for name, _ in self.calls]


def allowed(attempt_id: str = "att_1", url: str = "https://pay.example/session/1") -> dict:
    return {
        "attempt_id": attempt_id,
        "verdict": ALLOWED,
        "rule_id": "within_budget",
        "reason": "within the daily budget",
        "session_id": "ses_1",
        "payment_url": url,
    }


def parked(attempt_id: str = "att_1", reason: str = "over the single-purchase limit") -> dict:
    return {
        "attempt_id": attempt_id,
        "verdict": NEEDS_APPROVAL,
        "rule_id": "max_single_purchase",
        "reason": reason,
    }


def denied(reason: str = "merchant is not on the allowlist") -> dict:
    return {
        "attempt_id": "att_1",
        "verdict": DENIED,
        "rule_id": "merchant_allowlist",
        "reason": reason,
    }


def refused(attempt_id: str = "att_1", reason: str = "over the single-purchase limit") -> dict:
    """What `reject_purchase` answers. No session, and none can follow: the
    attempt is no longer pending, which is the only state approve acts on."""
    return {"attempt_id": attempt_id, "rejected": True, "note": "", "reason": reason}


def released(attempt_id: str = "att_1", url: str = "https://pay.example/session/2") -> dict:
    return {
        "attempt_id": attempt_id,
        "released": True,
        "session_id": "ses_2",
        "payment_url": url,
    }
