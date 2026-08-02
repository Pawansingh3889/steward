"""Who sent this, and what happens next.

The whole of "two humans transact by text" is here. A message arrives on a
line; the line identifies a person; the person's role decides what the message
means. A spender texting is a conversation with the agent. A sponsor texting is
a decision about an escalation — their line carries approvals and policy and
nothing else, which is exactly what lets them stay out of the day-to-day.

Three rules that are security properties rather than conveniences:

**An unknown number gets nothing.** Not an error, not a help message, not "who
is this?" — silence. Anything else confirms to a stranger that this number
belongs to a system that moves money, and a reply is the first half of a
conversation with somebody who should not be having one.

**A sponsor can only decide their own household's escalations.** The lookup is
scoped by sponsor id, so a number that belongs to one sponsor cannot approve
another's spending by guessing a number.

**Nothing is shared by default.** The spender's conversation reaches the
sponsor only while the spender has turned sharing on, and only they can turn it
on. The agent has no tool for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .. import store
from ..agent import llm, loop
from ..models import Role, Trigger
from ..spend import purchase
from ..spend.warden import Warden, WardenError
from .base import OTHER, YES, Channel, Delivery, Inbound, Outbound, RecordingChannel, intent

# What a spender texts to change who can see this conversation. Matched as
# whole phrases rather than keywords: "keep this private" must not be triggered
# by the word "private" appearing in a sentence about something else.
SHARE_ON = ("share this", "share this chat", "let them see this", "share with my sponsor")
SHARE_OFF = ("keep this private", "make this private", "stop sharing", "private again")


@dataclass
class Handled:
    """What one inbound message caused."""

    person_id: int = 0
    kind: str = "ignored"
    replies: list[Delivery] = field(default_factory=list)
    detail: str = ""

    def bodies(self) -> list[str]:
        return [reply.outbound.body for reply in self.replies]

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "kind": self.kind,
            "detail": self.detail,
            "replies": [reply.as_dict() for reply in self.replies],
        }


@dataclass
class Router:
    """Routes inbound messages, and addresses outbound ones to the right line."""

    db_path: str | None = None
    channel: Channel = field(default_factory=RecordingChannel)
    http: httpx.Client | None = None
    warden: Warden | None = None

    # --- sending -------------------------------------------------------------

    def send(self, person_id: int, body: str, *, about: str = "") -> Delivery:
        """Address a message to a person, and let the channel find their line."""
        outbound = Outbound(person_id=person_id, body=body, about=about)
        person = store.get_person(person_id, db_path=self.db_path)
        return self.channel.send(outbound, to=str(person["phone"]) if person else "")

    # --- receiving -----------------------------------------------------------

    def receive(self, inbound: Inbound) -> Handled:
        person = store.person_by_phone(inbound.sender, db_path=self.db_path)
        if person is None:
            # Silence. See the module docstring: a reply would confirm to a
            # stranger what this number is.
            return Handled(kind="unknown_sender", detail=f"no person on {inbound.sender}")
        if person["role"] == Role.SPONSOR:
            return self._sponsor_said(person, inbound.body)
        return self._spender_said(person, inbound.body)

    # --- the spender's line --------------------------------------------------

    def _spender_said(self, person: dict[str, Any], body: str) -> Handled:
        person_id = int(person["id"])
        lowered = body.strip().lower()

        if any(phrase in lowered for phrase in SHARE_ON):
            return self._set_sharing(person, store.SHARE_SHARED)
        if any(phrase in lowered for phrase in SHARE_OFF):
            return self._set_sharing(person, store.SHARE_PRIVATE)

        try:
            result = loop.run(
                body,
                person_id=person_id,
                db_path=self.db_path,
                trigger=Trigger.INBOUND,
                http=self.http,
                warden=self.warden,
            )
        except llm.AgentError as exc:
            # The person texted a real question and is owed a real answer about
            # why there is not one. Silence here reads as being ignored.
            reply = self.send(
                person_id,
                "I can't think straight just now — something's wrong with my setup."
                " Your message is saved and nothing has been spent.",
                about="agent_error",
            )
            return Handled(person_id, "agent_error", [reply], str(exc))

        replies = [self.send(person_id, result["display_answer"], about="answer")]
        replies.extend(self._notify_sponsor(person, result))
        return Handled(person_id, "answered", replies, detail=f"run {result['run_id']}")

    def _set_sharing(self, person: dict[str, Any], mode: str) -> Handled:
        person_id = int(person["id"])
        store.set_share_mode(person_id, mode, db_path=self.db_path)
        if mode == store.SHARE_SHARED:
            body = (
                "Sharing this conversation with your sponsor from now on."
                " Say 'keep this private' to stop."
            )
        else:
            body = (
                "This conversation is private again. They still see decisions and"
                " anything they're asked to approve — that part isn't mine to hide."
            )
        return Handled(person_id, "sharing", [self.send(person_id, body, about="sharing")])

    def _notify_sponsor(self, person: dict[str, Any], result: dict[str, Any]) -> list[Delivery]:
        """Text the sponsor about anything parked during this run."""
        sent: list[Delivery] = []
        for item in result.get("evidence", []):
            outcome = item.get("result") or {}
            escalation_id = outcome.get("escalation_id") or 0
            if not escalation_id:
                continue
            escalation = store.get_escalation(int(escalation_id), db_path=self.db_path)
            if escalation is None:
                continue
            sent.append(
                self.send(
                    int(escalation["sponsor_id"]),
                    self._escalation_text(person, escalation),
                    about=f"escalation:{escalation_id}",
                )
            )
        return sent

    def _escalation_text(self, person: dict[str, Any], escalation: dict[str, Any]) -> str:
        amount = int(escalation["amount_cents"]) / 100
        symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(str(escalation["currency"]), "")
        return (
            f"{person['name']} wants {escalation['description']}"
            f" — {symbol}{amount:,.2f} {escalation['currency']}"
            f" from {escalation['merchant_name']}.\n"
            # The rule that fired, worded as the policy engine worded it. The
            # sponsor is being asked because their own policy said to ask them.
            f"Policy: {escalation['reason']}\n"
            f"Reply YES or NO (#{escalation['id']})."
        )

    # --- the sponsor's line --------------------------------------------------

    def _sponsor_said(self, person: dict[str, Any], body: str) -> Handled:
        sponsor_id = int(person["id"])
        waiting = purchase.pending_for_sponsor(sponsor_id, db_path=self.db_path)
        decision = intent(body)
        chosen = _referenced(body, waiting)

        if not waiting:
            return Handled(
                sponsor_id,
                "nothing_waiting",
                [self.send(sponsor_id, "Nothing is waiting on you right now.", about="idle")],
            )
        if decision == OTHER:
            return Handled(
                sponsor_id,
                "unclear",
                [self.send(sponsor_id, self._what_is_waiting(waiting), about="unclear")],
            )
        if chosen is None:
            if len(waiting) > 1:
                # A bare "yes" with two things pending is ambiguous, and
                # guessing which would be guessing with somebody's money.
                return Handled(
                    sponsor_id,
                    "ambiguous",
                    [self.send(sponsor_id, self._which_one(waiting), about="ambiguous")],
                )
            chosen = waiting[0]

        return (
            self._approve(sponsor_id, chosen)
            if decision == YES
            else self._decline(sponsor_id, chosen)
        )

    def _approve(self, sponsor_id: int, escalation: dict[str, Any]) -> Handled:
        try:
            result = purchase.approve(
                int(escalation["id"]),
                sponsor_id=sponsor_id,
                db_path=self.db_path,
                client=self.warden,
            )
        except (purchase.PurchaseError, WardenError) as exc:
            return Handled(
                sponsor_id,
                "approve_failed",
                [self.send(sponsor_id, f"I couldn't release that: {exc}", about="approve_failed")],
                str(exc),
            )
        replies = [
            self.send(sponsor_id, f"Approved — {result['description']}.", about="approved"),
            # The spender gets the link, not the sponsor. The sponsor said yes;
            # it is still the spender's errand and their passkey.
            self.send(
                int(result["spender_id"]),
                f"{result['description']} is approved. Finish it here:\n{result['payment_url']}",
                about="approved",
            ),
        ]
        return Handled(sponsor_id, "approved", replies, detail=str(escalation["id"]))

    def _decline(self, sponsor_id: int, escalation: dict[str, Any]) -> Handled:
        result = purchase.decline(
            int(escalation["id"]), sponsor_id=sponsor_id, db_path=self.db_path
        )
        replies = [
            self.send(sponsor_id, f"Declined — {result['description']}.", about="declined"),
            # Told plainly, without inventing a reason the sponsor did not give.
            self.send(
                int(result["spender_id"]),
                f"Your sponsor said no to {result['description']} this time.",
                about="declined",
            ),
        ]
        return Handled(sponsor_id, "declined", replies, detail=str(escalation["id"]))

    def _what_is_waiting(self, waiting: list[dict[str, Any]]) -> str:
        lines = [self._one_line(row) for row in waiting]
        return "Waiting on you:\n" + "\n".join(lines) + "\nReply YES or NO."

    def _which_one(self, waiting: list[dict[str, Any]]) -> str:
        lines = [self._one_line(row) for row in waiting]
        return (
            "You have more than one waiting — which?\n"
            + "\n".join(lines)
            + "\nReply e.g. 'yes #"
            + str(waiting[0]["id"])
            + "'."
        )

    def _one_line(self, row: dict[str, Any]) -> str:
        amount = int(row["amount_cents"]) / 100
        symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(str(row["currency"]), "")
        return f"  #{row['id']}  {row['description']} — {symbol}{amount:,.2f}"


def _referenced(body: str, waiting: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find an escalation the sponsor named, by "#3" or a bare "3".

    Only matches ids that are actually pending for them: a stray number in a
    message ("yes, 2 of them") must not select something at random.
    """
    tokens = {token.strip("#.,!?") for token in body.replace("#", " #").split()}
    for row in waiting:
        if str(row["id"]) in tokens:
            return row
    return None
