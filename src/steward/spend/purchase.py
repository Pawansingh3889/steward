"""Buying something: the one path from "I need soap" to a payment link.

Three outcomes, and the middle one is the reason this product exists.

  allowed         — pay-warden minted a session. The spender gets a link and
                    taps their passkey. The sponsor was not involved and did
                    not need to be.
  needs_approval  — parked. An escalation row is written, the sponsor decides,
                    and only then is the attempt released. The spender is not
                    refused; they are waiting.
  denied          — the sponsor's policy says no. Relayed verbatim, with the
                    rule that fired, and not softened. An agent that editorialises
                    a denial into "I couldn't find that" teaches people the
                    system is broken rather than that a limit exists.

The asymmetry this whole project is built on is the middle case: the sponsor
cannot be in the loop for every purchase, so policy handles the ordinary ones
and a human is asked only where the policy says a human should be.

Nothing here decides anything. Every branch below is driven by a verdict that
came from pay-warden, and the one place a payment session can be minted after a
park — `approve` — replays pay-warden's own recorded request rather than
anything reconstructed on this side.
"""

from __future__ import annotations

from typing import Any

from .. import store
from ..models import Role
from . import warden
from .warden import Decision, Warden, WardenError

PENDING = "pending"
APPROVED = "approved"
DECLINED = "declined"


class PurchaseError(RuntimeError):
    """The purchase could not be attempted at all."""


def _sponsor_for(person: dict[str, Any], db_path: str | None) -> dict[str, Any]:
    """Who approves for this spender.

    A spender with no sponsor cannot escalate, and a system that quietly
    auto-approved in that case would be worst exactly where it matters. Fail
    loudly instead — the household is misconfigured, and that is fixable.
    """
    sponsor_id = person["sponsor_id"]
    if not sponsor_id:
        raise PurchaseError(
            f"{person['name']} has no sponsor, so nobody can approve a purchase for them"
        )
    sponsor = store.get_person(int(sponsor_id), db_path=db_path)
    if sponsor is None:
        raise PurchaseError(f"sponsor {sponsor_id} is missing from the household")
    return sponsor


def buy(
    *,
    person_id: int,
    description: str,
    amount_cents: int,
    currency: str = "GBP",
    merchant_name: str,
    merchant_url: str,
    merchant_country: str = "GB",
    run_id: int = 0,
    db_path: str | None = None,
    client: Warden | None = None,
) -> dict[str, Any]:
    """Attempt a purchase. Returns the outcome and, if parked, the escalation."""
    if amount_cents <= 0:
        raise PurchaseError("a purchase has to cost something")
    person = store.get_person(person_id, db_path=db_path)
    if person is None:
        raise PurchaseError(f"no person {person_id}")
    if person["role"] != Role.SPENDER:
        # A sponsor spending their own money is a different product. Blocking it
        # here keeps "whose budget is this?" from ever being ambiguous.
        raise PurchaseError("only a spender can request a purchase against a sponsor's policy")

    decision = warden.request(
        person_id=person_id,
        description=description,
        amount_cents=amount_cents,
        currency=currency,
        merchant_name=merchant_name,
        merchant_url=merchant_url,
        merchant_country=merchant_country,
        warden=client,
    )

    outcome: dict[str, Any] = {
        "verdict": decision.verdict,
        "reason": decision.reason,
        "rule_id": decision.rule_id,
        "description": description,
        "amount_cents": amount_cents,
        "currency": currency,
        "payment_url": decision.payment_url,
        "escalation_id": 0,
    }

    if decision.needs_approval:
        sponsor = _sponsor_for(person, db_path)
        if not decision.attempt_id:
            # Without pay-warden's handle there is nothing to release later, and
            # an escalation the sponsor can approve but nobody can act on is
            # worse than a clean failure.
            raise PurchaseError("pay-warden parked the purchase without an attempt id")
        outcome["escalation_id"] = store.insert_escalation(
            spender_id=person_id,
            sponsor_id=int(sponsor["id"]),
            attempt_id=decision.attempt_id,
            description=description,
            merchant_name=merchant_name,
            amount_cents=amount_cents,
            currency=currency,
            rule_id=decision.rule_id,
            reason=decision.reason,
            run_id=run_id,
            db_path=db_path,
        )
        outcome["sponsor_id"] = int(sponsor["id"])
    return outcome


def pending_for_sponsor(sponsor_id: int, *, db_path: str | None = None) -> list[dict[str, Any]]:
    return store.list_escalations(sponsor_id=sponsor_id, status=PENDING, db_path=db_path)


def approve(
    escalation_id: int,
    *,
    sponsor_id: int | None = None,
    db_path: str | None = None,
    client: Warden | None = None,
) -> dict[str, Any]:
    """The sponsor says yes. Releases the parked attempt and returns the link.

    The status transition is claimed *before* pay-warden is called, so a double
    tap cannot mint two sessions: the second caller loses the race in SQL and
    never reaches the network. If the release then fails, the escalation is put
    back to pending — better a sponsor who has to tap twice than one whose
    approval silently bought nothing.
    """
    escalation = _owned_escalation(escalation_id, sponsor_id, db_path)
    store.decide_escalation(escalation_id, status=APPROVED, db_path=db_path)
    try:
        decision: Decision = warden.release(str(escalation["attempt_id"]), warden=client)
    except WardenError:
        store.reopen_escalation(escalation_id, db_path=db_path)
        raise
    store.set_escalation_payment_url(escalation_id, decision.payment_url, db_path=db_path)
    return {
        "escalation_id": escalation_id,
        "status": APPROVED,
        "payment_url": decision.payment_url,
        "description": str(escalation["description"]),
        "amount_cents": int(escalation["amount_cents"]),
        "currency": str(escalation["currency"]),
        "spender_id": int(escalation["spender_id"]),
    }


def decline(
    escalation_id: int,
    *,
    sponsor_id: int | None = None,
    db_path: str | None = None,
    note: str = "",
    client: Warden | None = None,
) -> dict[str, Any]:
    """The sponsor says no, and pay-warden is told.

    It used to be enough to record this locally: an attempt nobody released
    stayed parked, which was the correct end state while parked was the only
    end state pay-warden had. It now records a refusal, so staying quiet would
    leave the two sides disagreeing — steward saying declined while the policy
    engine still says it is waiting on somebody — and the attempt would remain
    releasable by anyone who called approve on it later.

    Same shape as `approve`: claim the transition in SQL first so a double tap
    cannot send two refusals, and put it back if the call fails. Failing leaves
    the escalation pending, which is the safe direction — the spender keeps
    waiting, but nothing was bought and nothing was recorded that did not happen.
    """
    escalation = _owned_escalation(escalation_id, sponsor_id, db_path)
    store.decide_escalation(escalation_id, status=DECLINED, db_path=db_path)
    try:
        warden.reject(str(escalation["attempt_id"]), note, warden=client)
    except WardenError:
        store.reopen_escalation(escalation_id, db_path=db_path)
        raise
    return {
        "escalation_id": escalation_id,
        "status": DECLINED,
        "description": str(escalation["description"]),
        "spender_id": int(escalation["spender_id"]),
        "note": note,
    }


def _owned_escalation(
    escalation_id: int, sponsor_id: int | None, db_path: str | None
) -> dict[str, Any]:
    escalation = store.get_escalation(escalation_id, db_path=db_path)
    if escalation is None or (
        sponsor_id is not None and int(escalation["sponsor_id"]) != sponsor_id
    ):
        raise PurchaseError(f"no escalation {escalation_id} awaiting you")
    if escalation["status"] != PENDING:
        raise PurchaseError(f"escalation {escalation_id} was already {escalation['status']}")
    return escalation
