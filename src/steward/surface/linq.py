"""Linq: real messages to real phones, over iMessage, RCS or SMS.

**Verified live on 2026-08-02** against the sandbox — one message delivered to a
real handset. What that verification mostly bought was finding out that the
adapter written from a documented guess was **wrong in every particular**, which
is the argument for doing it rather than shipping the guess:

    guessed   POST /messages   {"to": …, "from": …, "body": …}
    actual    POST /chats/{chat_id}/messages
                               {"message": {"parts": [{"type": "text", "value": …}]}}

Messages are addressed to a **chat**, not to a number. A chat is a conversation
between the account's handle and one or more others, and it carries the protocol
— the sandbox chat negotiated iMessage, so a message sent through here arrives
as a blue bubble rather than an SMS. Linq picks that; steward does not ask.

**Chat creation is still unverified.** `POST /chats` rejects every body shape
probed against it, and the public guides document sending into an existing chat
without showing how one is opened. So `send` finds a chat and refuses clearly if
there is not one, rather than pretending. Opening a conversation with somebody
new is the one thing this adapter cannot yet do, and saying so is more use than
a plausible-looking call that 400s the first time a real person needs it.

**Dry-run by default**, still. A text reaches a real person, cannot be recalled,
and may cost money. `STEWARD_LINQ_LIVE=1` is the opt-in; without it this
describes what it would have sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .. import config
from .base import Delivery, Outbound

DEFAULT_TIMEOUT = 20.0


class LinqError(RuntimeError):
    """Linq refused, or is not configured."""


def _headers(token: str) -> dict[str, str]:
    # Bearer, verified: a bare token returns 2004 "Invalid authorization format".
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _handles(chat: dict[str, Any]) -> set[str]:
    return {str(h.get("handle", "")) for h in (chat.get("handles") or [])}


def find_chat(to: str, *, token: str, base: str, client: httpx.Client) -> dict[str, Any] | None:
    """The existing conversation with this number, if there is one.

    Matched on the handle rather than `display_name`, which is a label and can
    be anything. Group chats are skipped: a message meant for one person must
    not land in a room.
    """
    response = client.get(f"{base}/chats", headers=_headers(token))
    if response.status_code != 200:
        raise LinqError(f"could not list chats: {response.status_code} {response.text[:200]}")
    for chat in response.json().get("chats", []):
        if chat.get("is_group"):
            continue
        if to in _handles(chat):
            return dict(chat)
    return None


def text_message(body: str) -> dict[str, Any]:
    """The documented message shape: parts, each typed."""
    return {"message": {"parts": [{"type": "text", "value": body}]}}


@dataclass
class LinqChannel:
    """Sends over Linq when told to, and describes itself honestly when not."""

    name: str = "linq"
    live: bool | None = None  # None means read the environment
    http: httpx.Client | None = None

    def _is_live(self) -> bool:
        return config.linq_live() if self.live is None else self.live

    def send(self, outbound: Outbound, *, to: str) -> Delivery:
        if not to:
            return Delivery(outbound, to, False, "no number on file for this person")

        token = config.linq_token()
        if not token:
            return Delivery(outbound, to, False, "LINQ_API_TOKEN is unset")
        base = config.linq_api_base()

        if not self._is_live():
            # The honest default. Not an error, not a success — a description.
            return Delivery(
                outbound, to, False, "dry run: set STEWARD_LINQ_LIVE=1 to actually send"
            )

        client = self.http or httpx.Client(timeout=DEFAULT_TIMEOUT)
        try:
            chat = find_chat(to, token=token, base=base, client=client)
            if chat is None:
                # See the module docstring: opening a conversation is the one
                # thing this cannot do yet, and it says so rather than guessing.
                return Delivery(
                    outbound,
                    to,
                    False,
                    f"no existing Linq chat with {to}, and opening one is not implemented —"
                    " start the conversation from the Linq dashboard first",
                )
            response = client.post(
                f"{base}/chats/{chat['id']}/messages",
                headers=_headers(token),
                json=text_message(outbound.body),
            )
        except httpx.HTTPError as exc:
            return Delivery(outbound, to, False, f"could not reach Linq: {exc}")
        except LinqError as exc:
            return Delivery(outbound, to, False, str(exc))
        finally:
            if self.http is None:
                client.close()

        if response.status_code >= 400:
            # Verbatim, truncated only for length.
            return Delivery(
                outbound, to, False, f"Linq returned {response.status_code}: {response.text[:300]}"
            )
        service = str(chat.get("service", "")) or "unknown protocol"
        return Delivery(outbound, to, True, f"sent over {service} ({response.status_code})")
