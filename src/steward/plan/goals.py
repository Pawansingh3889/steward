"""Plans a person shaped, and what they cost when something else comes up.

The division of labour matches the rest of the codebase. `schedule.py` does
arithmetic and knows nothing about people; this module holds the plans and is
the single implementation both the CLI and the agent's tools sit on — the same
shape as `memory/recall.py`.

**Three rules, all about who decides.**

A plan is a *draft* until a person activates it. Drafts are free: the agent can
propose and reshape them all day, because a draft is arithmetic and arithmetic
commits nobody to anything. An active plan is different — steward talks about
it and warns when a purchase dents it — so activation is a person's act, like
confirming a fact or approving a purchase, and there is deliberately no tool
for it. Nor for abandoning one: releasing a commitment is the same authority as
making it.

**The start date is not a parameter the model may choose.** It comes from
`models.utc_today()`. A backdated plan has more periods in it, so it reports
progress on money nobody ever put aside — the same class of problem as a model
naming its own price, which `buy_offer` solves the same way.

**An active plan is advisory.** `impact` says what a purchase costs a goal — a
date slipping, in plain terms — and that is all it does. pay-warden remains the
only thing that can refuse, and nothing here edits a sponsor's policy. steward
has no bank access; a plan is a promise about money it does not hold, and
blocking spending to protect one would be claiming an authority it does not
have.

Nothing in this package imports `spend/`. There is no path from a plan item to
a payment, which is what makes `needs_human` a promise about the future rather
than a flag that is already being ignored.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from .. import store
from ..models import utc_today
from . import schedule
from .schedule import CADENCES, MONTHLY, PlanError, Schedule

GOAL = "goal"
TRIP = "trip"
KINDS = (GOAL, TRIP)

FLIGHT = "flight"
ACCOMMODATION = "accommodation"
OTHER = "other"
ITEM_KINDS = (FLIGHT, ACCOMMODATION, OTHER)


def _as_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PlanError(f"{label} should look like 2026-03-01, not {value!r}") from exc


def _recompute(row: dict[str, Any], *, affordable_cents: int = 0) -> Schedule:
    return schedule.solve(
        target_cents=int(row["target_cents"]),
        cadence=str(row["cadence"]),
        start=_as_date(str(row["start_date"]), "start"),
        finish=_as_date(str(row["finish_date"]), "finish"),
        per_period_cents=int(row["per_period_cents"]) or None,
        currency=str(row["currency"]),
        already_saved_cents=int(row["contributed_cents"]),
        affordable_cents=affordable_cents,
    )


def view(row: dict[str, Any], *, db_path: str | None = None) -> dict[str, Any]:
    """One plan as every surface shows it, recomputed from its own numbers."""
    computed = _recompute(row)
    items = store.list_plan_items(int(row["id"]), db_path=db_path)
    items_total = sum(int(item["amount_cents"]) for item in items)
    return {
        "plan_id": int(row["id"]),
        "name": str(row["name"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "currency": str(row["currency"]),
        "start": str(row["start_date"]),
        "finish": str(row["finish_date"]),
        **computed.as_dict(),
        "contributed_cents": int(row["contributed_cents"]),
        "remaining_cents": computed.to_save_cents,
        # A trip's target and the sum of its items are kept independent, so
        # adding an item never silently rewrites a schedule somebody agreed to.
        # The drift is surfaced instead of hidden, and closing it is the
        # person's call like every other parameter here.
        "items_total_cents": items_total,
        "items_covered": items_total <= int(row["target_cents"]),
        "items": [
            {
                "item_id": int(item["id"]),
                "description": str(item["description"]),
                "amount_cents": int(item["amount_cents"]),
                "kind": str(item["kind"]),
                # On every item rather than explained once, so no surface can
                # render a flight as though it were something steward buys.
                "needs_human": bool(item["needs_human"]),
                "status": str(item["status"]),
            }
            for item in items
        ],
    }


def _shaped(row: dict[str, Any], computed: Schedule, db_path: str | None) -> dict[str, Any]:
    return {
        **view(row, db_path=db_path),
        "ways_to_close": [option.as_dict() for option in schedule.ways_to_close(computed)],
    }


def propose(
    *,
    person_id: int,
    name: str,
    target_cents: int,
    finish: date | None = None,
    per_period_cents: int | None = None,
    cadence: str = MONTHLY,
    currency: str = "GBP",
    kind: str = GOAL,
    already_saved_cents: int = 0,
    affordable_cents: int = 0,
    start: date | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Work out a schedule and keep it as a draft.

    `start` exists only so tests can pin the clock. **No tool passes it** — see
    the module docstring; a model that chose its own start date could backdate a
    plan into reporting savings that never happened.

    Returns the plan *and* the ways to close any gap, because a proposal that
    misses is still worth seeing: the useful answer to "can I have £600 by
    March?" is often "not at £50 a month, and here is what would do it".
    """
    if kind not in KINDS:
        raise PlanError(f"a plan is a {' or a '.join(KINDS)}, not {kind!r}")
    if not name.strip():
        raise PlanError("a plan needs a name, so it can be talked about later")

    computed = schedule.solve(
        target_cents=target_cents,
        cadence=cadence,
        start=start or utc_today(),
        finish=finish,
        per_period_cents=per_period_cents,
        currency=currency,
        already_saved_cents=already_saved_cents,
        affordable_cents=affordable_cents,
    )
    plan_id = store.insert_plan(
        person_id=person_id,
        name=name.strip(),
        kind=kind,
        target_cents=computed.target_cents,
        currency=currency,
        cadence=computed.cadence,
        per_period_cents=computed.per_period_cents,
        start_date=computed.start.isoformat(),
        finish_date=computed.finish.isoformat(),
        db_path=db_path,
    )
    if already_saved_cents:
        store.add_contribution(plan_id, already_saved_cents, person_id=person_id, db_path=db_path)
    row = store.get_plan(plan_id, db_path=db_path)
    assert row is not None
    return _shaped(row, computed, db_path)


def adjust(
    plan_id: int,
    *,
    person_id: int,
    target_cents: int | None = None,
    finish: date | None = None,
    per_period_cents: int | None = None,
    cadence: str | None = None,
    affordable_cents: int = 0,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Change one thing and recompute the rest.

    Whichever of {target, deadline, per-period} is *not* given is the one that
    moves. Giving all three is refused rather than silently ignoring one: the
    point of this interaction is that a person sees what each choice costs the
    others, and that is lost if everything can be pinned at once.
    """
    row = _owned(plan_id, person_id, db_path)
    if row["status"] != store.PLAN_DRAFT:
        raise PlanError(
            f"{row['name']} is already {row['status']} — start a new plan rather than"
            " changing the numbers under one you agreed to"
        )
    if target_cents is not None and finish is not None and per_period_cents is not None:
        raise PlanError(
            "set at most two of target, date and amount — the third is what this works out"
        )
    if cadence is not None and cadence not in CADENCES:
        raise PlanError(f"unknown cadence {cadence!r}; use one of: {', '.join(CADENCES)}")

    # The quantity being solved for is whichever the caller left out; everything
    # else keeps its stored value.
    solving_for_finish = finish is None and per_period_cents is not None
    computed = schedule.solve(
        target_cents=target_cents if target_cents is not None else int(row["target_cents"]),
        cadence=cadence or str(row["cadence"]),
        start=_as_date(str(row["start_date"]), "start"),
        finish=None
        if solving_for_finish
        else (finish or _as_date(str(row["finish_date"]), "finish")),
        per_period_cents=per_period_cents,
        currency=str(row["currency"]),
        already_saved_cents=int(row["contributed_cents"]),
        affordable_cents=affordable_cents,
    )
    store.update_plan_schedule(
        plan_id,
        person_id=person_id,
        target_cents=computed.target_cents,
        per_period_cents=computed.per_period_cents,
        cadence=computed.cadence,
        finish_date=computed.finish.isoformat(),
        db_path=db_path,
    )
    updated = store.get_plan(plan_id, db_path=db_path)
    assert updated is not None
    return _shaped(updated, computed, db_path)


def add_item(
    plan_id: int,
    *,
    person_id: int,
    description: str,
    amount_cents: int,
    kind: str = OTHER,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Add something the plan has to pay for.

    `needs_human` is not passed: `store.insert_plan_item` computes it from the
    kind, so a flight is confirmed by a person no matter which layer asked.
    """
    _owned(plan_id, person_id, db_path)
    if kind not in ITEM_KINDS:
        raise PlanError(f"unknown item kind {kind!r}; use one of: {', '.join(ITEM_KINDS)}")
    if not description.strip():
        raise PlanError("an item needs a description")
    item_id = store.insert_plan_item(
        plan_id=plan_id,
        description=description.strip(),
        amount_cents=max(0, int(amount_cents)),
        kind=kind,
        db_path=db_path,
    )
    item = next(
        row for row in store.list_plan_items(plan_id, db_path=db_path) if int(row["id"]) == item_id
    )
    return {"item_id": item_id, "kind": kind, "needs_human": bool(item["needs_human"])}


def activate(plan_id: int, *, person_id: int, db_path: str | None = None) -> dict[str, Any]:
    """A person starts a plan. There is deliberately no tool for this."""
    row = _owned(plan_id, person_id, db_path)
    store.set_plan_status(
        plan_id,
        person_id=person_id,
        status=store.PLAN_ACTIVE,
        expect=store.PLAN_DRAFT,
        db_path=db_path,
    )
    return {"plan_id": plan_id, "name": str(row["name"]), "status": store.PLAN_ACTIVE}


def abandon(plan_id: int, *, person_id: int, db_path: str | None = None) -> dict[str, Any]:
    """A person stops one. Also not a tool: releasing a commitment is the same
    authority as making it."""
    row = _owned(plan_id, person_id, db_path)
    store.set_plan_status(
        plan_id,
        person_id=person_id,
        status=store.PLAN_ABANDONED,
        expect=str(row["status"]),
        db_path=db_path,
    )
    return {"plan_id": plan_id, "name": str(row["name"]), "status": store.PLAN_ABANDONED}


def contribute(
    plan_id: int, amount_cents: int, *, person_id: int, db_path: str | None = None
) -> dict[str, Any]:
    """Record money the person says they have put aside. Stated, never inferred."""
    _owned(plan_id, person_id, db_path)
    if amount_cents <= 0:
        raise PlanError("a contribution has to be something")
    store.add_contribution(plan_id, int(amount_cents), person_id=person_id, db_path=db_path)
    row = store.get_plan(plan_id, db_path=db_path)
    assert row is not None
    return view(row, db_path=db_path)


def active(person_id: int, *, db_path: str | None = None) -> list[dict[str, Any]]:
    rows = store.list_plans(person_id, status=store.PLAN_ACTIVE, db_path=db_path)
    return [view(row, db_path=db_path) for row in rows]


def drafts(person_id: int, *, db_path: str | None = None) -> list[dict[str, Any]]:
    return store.list_plans(person_id, status=store.PLAN_DRAFT, db_path=db_path)


def everything(person_id: int, *, db_path: str | None = None) -> list[dict[str, Any]]:
    return [view(row, db_path=db_path) for row in store.list_plans(person_id, db_path=db_path)]


def impact(
    person_id: int, amount_cents: int, *, db_path: str | None = None
) -> list[dict[str, Any]]:
    """What spending this would cost each **active** goal.

    Drafts are excluded: nobody has agreed to one, so nothing can set it back —
    and counting them would let the model shrink someone's apparent room to
    spend just by proposing plans.

    Advisory, and phrased as a consequence rather than a judgement: so many
    periods later, so a date moves.
    """
    if amount_cents <= 0:
        return []
    out: list[dict[str, Any]] = []
    for row in store.list_plans(person_id, status=store.PLAN_ACTIVE, db_path=db_path):
        per_period = int(row["per_period_cents"])
        if per_period <= 0:
            continue
        periods = math.ceil(amount_cents / per_period)
        finish = _as_date(str(row["finish_date"]), "finish")
        cadence = str(row["cadence"])
        out.append(
            {
                "plan_id": int(row["id"]),
                "name": str(row["name"]),
                "costs_periods": periods,
                "costs": f"{periods} {schedule.noun(cadence, periods)}",
                "finish_was": finish.isoformat(),
                "finish_would_be": schedule.add_periods(finish, cadence, periods).isoformat(),
                # Said here so no surface has to remember to add it.
                "note": "advisory only — this does not stop the purchase",
            }
        )
    return out


def _owned(plan_id: int, person_id: int, db_path: str | None) -> dict[str, Any]:
    """Scope check, not a lookup convenience: without it a guessed id reaches
    another person's plan."""
    row = store.get_plan(plan_id, db_path=db_path)
    if row is None or int(row["person_id"]) != person_id:
        raise PlanError(f"no plan {plan_id} belonging to you")
    return row
