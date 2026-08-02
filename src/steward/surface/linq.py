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

Measured on the sandbox at the same time, which is where the timeout below comes
from: end-to-end delivery p50 2.65s and p99 4.50s, API p99 700ms. Twenty seconds
is generous, and generous is right — a message that is slow is still wanted,
whereas a retry is a second text to somebody's phone.

**Opening a conversation works too**, verified 2026-08-02. It took a while to
find because the error messages point away from the answer: every wrong shape
returns `1005`, and `1005` is documented as "a parameter value fails
validation", which reads like a bad phone number rather than a wrong field name.
The tell was that one variant complained about something *else* —

    {"from": …, "recipients": […]}  →  1005 at least 1 recipient is required
    {"from": …, "to":         […]}  →  1005 at least one message part is required

— which is how `to` was identified. The second error is the real surprise:
**opening a chat sends the first message in the same call.** That is not an
API quirk to work around, it is how the medium works — you cannot open an
iMessage thread with somebody without saying something to them.

    POST /chats  {"from": "+1…", "to": ["+44…"],
                  "message": {"parts": [{"type": "text", "value": …}]}}

It is idempotent: called for somebody you already have a conversation with, it
returns that conversation rather than starting a second one. So `send` looks for
a chat, and opens one carrying the message when there is not one — which means
the caller never has to know which case it was in, and a first message is never
sent twice.

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


def open_chat(to: str, body: str, *, token: str, base: str, client: httpx.Client) -> dict[str, Any]:
    """Open a conversation, carrying the first message.

    The message is not optional — `POST /chats` refuses without one, and that is
    the medium being honest rather than the API being awkward: an iMessage
    thread does not exist until something has been said in it.

    Idempotent on Linq's side. Calling this for somebody who already has a
    conversation returns theirs, so a race between two sends cannot leave a
    person with two threads.
    """
    sender = config.linq_from_number()
    if not sender:
        raise LinqError(
            "LINQ_FROM_NUMBER is unset — a new conversation needs a line to come from"
            " (GET /phone_numbers lists the ones this account owns)"
        )
    response = client.post(
        f"{base}/chats",
        headers=_headers(token),
        json={"from": sender, "to": [to], **text_message(body)},
    )
    if response.status_code >= 400:
        raise LinqError(
            f"could not open a chat with {to}: {response.status_code} {response.text[:300]}"
        )
    chat = response.json().get("chat")
    if not isinstance(chat, dict):
        raise LinqError(f"Linq opened a chat but returned no chat object: {response.text[:200]}")
    return chat


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
                # Opening the conversation *is* sending the message — see the
                # module docstring. Returning here rather than falling through
                # is what stops a first message going twice.
                opened = open_chat(to, outbound.body, token=token, base=base, client=client)
                service = str(opened.get("service", "")) or "unknown protocol"
                return Delivery(outbound, to, True, f"opened a chat and sent over {service}")
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
