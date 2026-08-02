"""Saving up for something, and being honest about whether it works.

A savings schedule is three numbers and a rhythm: **how much**, **by when**, and
**how much each time**. Fix any two and the third follows. That is the whole
interaction model, and it is why the roadmap calls for parameters at every
stage — a person does not arrive knowing all three, they arrive knowing one and
a feeling about the others, and the useful thing is to show what each choice
costs the other two.

The rule this module exists to enforce: **when it does not add up, say so.** A
planner that quietly stretches the deadline to make the arithmetic work, or
rounds the target down to something reachable, has substituted its own judgement
for the person's on the one question they actually asked.
`Schedule.shortfall_cents` is the number that must never be hidden, and
`ways_to_close` states three comparable options rather than choosing between
them.

This module knows nothing about people, databases or money movement. It is
arithmetic over integer minor units, with contributions rounded **up** so a
schedule can never quietly land a penny short of its own target.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

WEEKLY = "weekly"
FORTNIGHTLY = "fortnightly"
MONTHLY = "monthly"
CADENCES = (WEEKLY, FORTNIGHTLY, MONTHLY)

_DAYS = {WEEKLY: 7, FORTNIGHTLY: 14}

# The noun for one period. A map rather than `cadence.rstrip("ly")`, which is a
# *character* strip and only happens to work for the three cadences here:
# "daily".rstrip("ly") is "dai". Adding a cadence should be a KeyError, not a
# sentence with a typo in it.
_NOUN = {WEEKLY: "week", FORTNIGHTLY: "fortnight", MONTHLY: "month"}


class PlanError(RuntimeError):
    """The plan as stated cannot be interpreted.

    A RuntimeError like every other domain error here (`PurchaseError`,
    `WardenError`, `PriceMoved`, `LocalModelError`). Deliberately not a
    ValueError: that is what `int("abc")` raises, and a caller must be able to
    tell "the model sent junk" from "this goal is not reachable" — the two need
    different replies.
    """


def noun(cadence: str, count: int = 1) -> str:
    """ "month" or "months", so no caller has to write "1 more months"."""
    word = _NOUN.get(cadence)
    if word is None:
        raise PlanError(f"unknown cadence {cadence!r}; use one of: {', '.join(CADENCES)}")
    return word if count == 1 else f"{word}s"


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def add_periods(start: date, cadence: str, periods: int) -> date:
    """Move forward whole periods. Monthly means the same day next month, not
    30 days — someone paid on the 28th is saving on the 28th."""
    if cadence in _DAYS:
        return start + timedelta(days=_DAYS[cadence] * periods)
    if cadence != MONTHLY:
        raise PlanError(f"unknown cadence {cadence!r}; use one of: {', '.join(CADENCES)}")
    month_index = start.month - 1 + periods
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp for short months: the 31st becomes the 28th in February, and is the
    # 31st again in March. Anything else drifts the schedule earlier every year.
    return date(year, month, min(start.day, _days_in_month(year, month)))


def periods_between(start: date, end: date, cadence: str) -> int:
    """Whole periods that fit between two dates.

    Rounded **down**, and floored at zero: a span with three and a half weeks in
    it has three payments, not four, because counting the half would tell
    someone they will have saved money they have not been paid yet.

    The month-end clamp mirrors `add_periods` exactly, and it is not cosmetic.
    Comparing raw day numbers made 31 January → 28 February come out as *zero*
    months, so anyone starting on the 29th–31st with a deadline in a shorter
    month was told their plan was impossible.
    """
    if end <= start:
        return 0
    if cadence in _DAYS:
        return (end - start).days // _DAYS[cadence]
    if cadence != MONTHLY:
        raise PlanError(f"unknown cadence {cadence!r}; use one of: {', '.join(CADENCES)}")
    months = (end.year - start.year) * 12 + (end.month - start.month)
    # The anniversary of `start` within `end`'s month is clamped the same way
    # add_periods clamps it, so the two functions agree on where a period lands.
    anniversary = min(start.day, _days_in_month(end.year, end.month))
    if end.day < anniversary:
        months -= 1
    return max(0, months)


@dataclass(frozen=True)
class Option:
    """One way to close a gap. Every field is always present, so three options
    can be read down a column instead of compared field by field."""

    change: str  # take_longer | smaller_goal | more_each_time
    note: str
    target_cents: int
    per_period_cents: int
    finish: str
    currency: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "change": self.change,
            "note": self.note,
            "target_cents": self.target_cents,
            "per_period_cents": self.per_period_cents,
            "finish": self.finish,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class Schedule:
    """A schedule, and what it does and does not achieve.

    Named `Schedule` rather than `Plan` because a *plan* in this project is a
    stored, owned, activatable thing with an id and a status. This is the
    arithmetic underneath one.
    """

    target_cents: int
    per_period_cents: int
    cadence: str
    start: date
    finish: date
    periods: int
    currency: str = "GBP"
    # Money the person says is already put aside. The schedule covers only what
    # is left; steward has no bank access, so this is a stated figure and never
    # an inferred one.
    already_saved_cents: int = 0
    # What they can spare each period, when they have said. Zero means they have
    # not, and feasibility is then unknown rather than fine.
    affordable_cents: int = 0

    @property
    def to_save_cents(self) -> int:
        return max(0, self.target_cents - self.already_saved_cents)

    @property
    def saved_cents(self) -> int:
        """What this schedule reaches, including anything already put aside."""
        return self.already_saved_cents + self.per_period_cents * self.periods

    @property
    def shortfall_cents(self) -> int:
        """How far it lands short of the target. Never hidden."""
        return max(0, self.target_cents - self.saved_cents)

    @property
    def reaches_target(self) -> bool:
        return self.shortfall_cents == 0

    @property
    def affordable(self) -> bool | None:
        """None when they have not said what they can spare — which is not the
        same as yes, and must not be displayed as if it were."""
        if not self.affordable_cents:
            return None
        return self.per_period_cents <= self.affordable_cents

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_cents": self.target_cents,
            "per_period_cents": self.per_period_cents,
            "cadence": self.cadence,
            "periods": self.periods,
            "start": self.start.isoformat(),
            "finish": self.finish.isoformat(),
            "currency": self.currency,
            "already_saved_cents": self.already_saved_cents,
            # Both, always: "£20 short" and "you will have £80 of £100" are the
            # same fact, and only the second one is an answer.
            "saved_cents": self.saved_cents,
            "shortfall_cents": self.shortfall_cents,
            "reaches_target": self.reaches_target,
            "affordable": self.affordable,
        }


def solve(
    *,
    target_cents: int,
    start: date,
    cadence: str = MONTHLY,
    finish: date | None = None,
    per_period_cents: int | None = None,
    currency: str = "GBP",
    already_saved_cents: int = 0,
    affordable_cents: int = 0,
) -> Schedule:
    """Fix two of {target, deadline, per-period} and get the third.

    Both given is not an error — it is the interesting case, where a person has
    an amount they can manage and a date they want, and the answer is whether
    those two facts are compatible. That is what `shortfall_cents` is for, and
    it is why nothing here silently adjusts either one.
    """
    if target_cents <= 0:
        raise PlanError("a goal has to be worth something")
    if cadence not in CADENCES:
        raise PlanError(f"unknown cadence {cadence!r}; use one of: {', '.join(CADENCES)}")
    if already_saved_cents < 0:
        raise PlanError("money already put aside cannot be negative")
    if finish is None and per_period_cents is None:
        raise PlanError(
            "give either a date to reach it by or an amount to put aside each time —"
            " with neither, there is no schedule to work out"
        )

    outstanding = max(0, target_cents - already_saved_cents)

    if finish is None:
        assert per_period_cents is not None
        if per_period_cents <= 0:
            raise PlanError("putting aside nothing each time never reaches anything")
        periods = math.ceil(outstanding / per_period_cents) if outstanding else 0
        return Schedule(
            target_cents=target_cents,
            per_period_cents=per_period_cents,
            cadence=cadence,
            start=start,
            finish=add_periods(start, cadence, periods),
            periods=periods,
            currency=currency,
            already_saved_cents=already_saved_cents,
            affordable_cents=affordable_cents,
        )

    periods = periods_between(start, finish, cadence)
    if periods == 0 and outstanding:
        raise PlanError(
            f"there is not a whole {noun(cadence)} between now and then,"
            " so there is nothing to spread the saving over"
        )
    # Round up: a schedule landing a penny short of its own target is worse than
    # one arriving a penny early.
    if per_period_cents is not None:
        amount = per_period_cents
    else:
        amount = math.ceil(outstanding / periods) if periods else 0
    return Schedule(
        target_cents=target_cents,
        per_period_cents=amount,
        cadence=cadence,
        start=start,
        finish=finish,
        periods=periods,
        currency=currency,
        already_saved_cents=already_saved_cents,
        affordable_cents=affordable_cents,
    )


def ways_to_close(plan: Schedule) -> list[Option]:
    """The honest options when a schedule does not reach, or is not affordable.

    Three, always in the same order, always with the same fields, and never one
    recommended above the others. Which of "later", "less", or "more each time"
    is right depends on what the goal is for and how tight the money is —
    neither of which this knows, and both of which the person does.
    """
    if plan.reaches_target and plan.affordable is not False:
        return []

    if plan.affordable is False:
        # They cannot manage this amount. Work from what they can.
        outstanding = plan.to_save_cents
        periods = math.ceil(outstanding / plan.affordable_cents) if outstanding else 0
        return [
            Option(
                change="take_longer",
                note="keep the goal, put aside what you can, and get there later",
                target_cents=plan.target_cents,
                per_period_cents=plan.affordable_cents,
                finish=add_periods(plan.start, plan.cadence, periods).isoformat(),
                currency=plan.currency,
            ),
            Option(
                change="smaller_goal",
                note="keep the date and aim at what that adds up to",
                target_cents=plan.already_saved_cents + plan.affordable_cents * plan.periods,
                per_period_cents=plan.affordable_cents,
                finish=plan.finish.isoformat(),
                currency=plan.currency,
            ),
            Option(
                change="more_each_time",
                note="keep both, and find the difference somewhere else",
                target_cents=plan.target_cents,
                per_period_cents=plan.per_period_cents,
                finish=plan.finish.isoformat(),
                currency=plan.currency,
            ),
        ]

    # It is short of the target rather than unaffordable.
    needed = math.ceil(plan.to_save_cents / plan.periods) if plan.periods else plan.to_save_cents
    extra = math.ceil(plan.shortfall_cents / plan.per_period_cents) if plan.per_period_cents else 0
    return [
        Option(
            change="take_longer",
            note=f"{extra} more {noun(plan.cadence, extra)} at the same amount",
            target_cents=plan.target_cents,
            per_period_cents=plan.per_period_cents,
            finish=add_periods(plan.finish, plan.cadence, extra).isoformat(),
            currency=plan.currency,
        ),
        Option(
            change="smaller_goal",
            note="what this schedule actually reaches by then",
            target_cents=plan.saved_cents,
            per_period_cents=plan.per_period_cents,
            finish=plan.finish.isoformat(),
            currency=plan.currency,
        ),
        Option(
            change="more_each_time",
            note="keep the goal and the date, and put aside more each time",
            target_cents=plan.target_cents,
            per_period_cents=needed,
            finish=plan.finish.isoformat(),
            currency=plan.currency,
        ),
    ]
