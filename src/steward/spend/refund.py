"""Asking for money back.

Deliberately the smallest thing that is honest. steward records a refund
request, shows it to the sponsor alongside everything else about the money, and
tracks how it ended. It does **not** contact a merchant, open a dispute, or
speak to a bank — all of which are outward acts on somebody's behalf with legal
weight, and none of which this project has earned the right to do unattended.

Two properties worth stating.

**The reason is the person's own words, stored verbatim.** A model must not
summarise a complaint: the paraphrase is a different complaint, and this is the
text a merchant might eventually read.

**A refund is anchored to a pay-warden attempt id**, so it refers to something
that actually happened rather than to a description somebody typed. You cannot
ask for a refund on a purchase that was never made.
"""

from __future__ import annotations

from typing import Any

from .. import store
from ..models import money


class RefundError(RuntimeError):
    """The refund could not be recorded or resolved."""


def request(
    *,
    person_id: int,
    attempt_id: str,
    description: str,
    amount_cents: int,
    currency: str = "GBP",
    reason: str,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Record that somebody wants their money back."""
    if not attempt_id:
        raise RefundError("a refund has to point at a purchase that happened")
    if not reason.strip():
        raise RefundError("say what went wrong — a refund with no reason is not a request")
    if amount_cents <= 0:
        raise RefundError("a refund has to be for something")

    refund_id = store.insert_refund(
        person_id=person_id,
        attempt_id=attempt_id,
        description=description,
        amount_cents=amount_cents,
        currency=currency,
        reason=reason.strip(),
        db_path=db_path,
    )
    return {
        "refund_id": refund_id,
        "status": store.REFUND_REQUESTED,
        "description": description,
        "amount": money(amount_cents, currency),
        # Said here so no surface has to remember to. Somebody who thinks
        # steward has filed a claim on their behalf will not chase it themselves.
        "note": "recorded — steward has not contacted the merchant, and will not",
    }


def outstanding(person_id: int, *, db_path: str | None = None) -> list[dict[str, Any]]:
    return store.list_refunds(person_id, status=store.REFUND_REQUESTED, db_path=db_path)


def everything(person_id: int, *, db_path: str | None = None) -> list[dict[str, Any]]:
    return store.list_refunds(person_id, db_path=db_path)


def resolve(
    refund_id: int,
    *,
    person_id: int,
    refunded: bool,
    resolution: str = "",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Record how it ended, once the person knows.

    They tell steward; steward does not find out. Same rule as the rest of the
    money in this system — it is user-stated, because there is no bank feed and
    inventing one would be the dishonest part.
    """
    row = store.get_refund(refund_id, db_path=db_path)
    if row is None or int(row["person_id"]) != person_id:
        raise RefundError(f"no refund {refund_id} belonging to you")
    status = store.REFUND_REFUNDED if refunded else store.REFUND_REFUSED
    store.resolve_refund(
        refund_id,
        person_id=person_id,
        status=status,
        resolution=resolution.strip(),
        db_path=db_path,
    )
    return {"refund_id": refund_id, "status": status, "description": str(row["description"])}
