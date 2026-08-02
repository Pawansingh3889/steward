"""Messages in and out, over whatever carries them.

The interface exists because the messaging provider is the least durable part
of this system. Linq's sandbox expires on 9 August 2026; a phone number can be
recycled; a carrier can start filtering. None of that should be able to stop a
person managing their own money, so every surface is a `Channel` and the
in-process one is a first-class implementation rather than a test double.

Two lines, not one. The spender texts about what they need; the sponsor's line
carries approvals and policy and nothing else. That separation is the product:
it is what lets a sponsor stay out of the day-to-day without losing the say
they actually care about. `Channel.send` therefore takes a person, never a bare
number — routing to a *line* is a decision about who is entitled to read
something, and it should not be possible to make it by typing a phone number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# What a sponsor can text back to decide an escalation. Deliberately short and
# forgiving: the reply is written with one thumb, probably while walking.
YES_WORDS = frozenset({"y", "yes", "ok", "okay", "approve", "approved", "go", "sure", "fine"})
NO_WORDS = frozenset({"n", "no", "nope", "decline", "declined", "deny", "reject", "stop"})


@dataclass(frozen=True)
class Inbound:
    """A message that arrived. `sender` is the raw line it came from."""

    sender: str
    body: str


@dataclass
class Outbound:
    """A message to send, addressed to a person rather than to a number."""

    person_id: int
    body: str
    # Set when the message is a consequence of something, so a transcript can
    # be read back and a test can assert what a particular event produced.
    about: str = ""


@dataclass
class Delivery:
    """What happened when we tried to send it."""

    outbound: Outbound
    to: str
    delivered: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "to": self.to,
            "body": self.outbound.body,
            "about": self.outbound.about,
            "delivered": self.delivered,
            "detail": self.detail,
        }


class Channel(Protocol):
    """Somewhere messages can be sent. Receiving is the caller's problem —
    a webhook, a poll, or a person typing into the CLI all arrive as `Inbound`."""

    name: str

    def send(self, outbound: Outbound, *, to: str) -> Delivery: ...


@dataclass
class RecordingChannel:
    """Keeps messages instead of sending them.

    The default channel, and not only for tests. With no messaging provider
    configured this is what runs, so the whole product works end to end on one
    machine — which is also how it stays true that an expired Linq sandbox
    cannot block the brain.
    """

    name: str = "recording"
    sent: list[Delivery] = field(default_factory=list)

    def send(self, outbound: Outbound, *, to: str) -> Delivery:
        delivery = Delivery(outbound=outbound, to=to, delivered=True, detail="recorded, not sent")
        self.sent.append(delivery)
        return delivery

    def to_person(self, person_id: int) -> list[str]:
        return [d.outbound.body for d in self.sent if d.outbound.person_id == person_id]

    def last(self) -> Delivery | None:
        return self.sent[-1] if self.sent else None


def intent(body: str) -> str:
    """Read a sponsor's reply as yes, no, or neither.

    Only the *first* word counts. "no, go ahead" and "yes but not that one" both
    exist, and a system that scans for any yes-word anywhere would approve a
    purchase on the strength of the word "fine" in the middle of a sentence.
    Anything unrecognised falls through to `OTHER`, which asks rather than
    guessing — the cost of a needless question is a second of someone's day; the
    cost of a wrong guess is their money.
    """
    words = body.strip().lower().replace(",", " ").split()
    if not words:
        return OTHER
    first = words[0].strip(".!?")
    if first in YES_WORDS:
        return YES
    if first in NO_WORDS:
        return NO
    return OTHER


YES = "yes"
NO = "no"
OTHER = "other"
