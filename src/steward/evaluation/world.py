"""Running a household for N months, with and without the agent.

**What the agent's advantage actually is, stated so it can be attacked.**

It is *lead time*, and nothing else. Both arms buy the same things from the same
catalogue at the same prices. The difference is when they notice:

  without — a person notices they have run out only after they have, and then
            only on a day they happen to think about it (`forgetfulness`).
  with    — steward holds a supply fact, so it raises the item while there are
            still `notice_days` left.

**Which supplier gets used is not part of the arm.** Both arms follow the same
rule — take the cheap slow one if it will arrive before you run out, otherwise
pay for speed — because that is what anybody does, and because building the
supplier choice into the arm would have been a second advantage smuggled in
alongside the first. An earlier version did exactly that, and the convergence
check caught it: with anticipation stripped out the arms still differed, which
meant the numbers were measuring a decision made in this file rather than a
mechanism.

So the whole difference is *when the order is triggered*. It is not "the agent
is cleverer"; it is that a thing which does not forget converts urgency into
choice, and the cheap supplier is only reachable when there is time to wait.

The advantage has **three components**, and they are separable:

  anticipation — `notice_days` of warning before running out.
  reliability  — not forgetting, once there is something to act on.
  phasing      — money set aside on payday rather than at month end.

Zero all three and the arms must produce identical numbers. Getting to that
statement took three corrections, each found by the check rather than by
reading: zeroing forgetfulness alone does not converge them (anticipation
survives); the supplier choice was baked into the arm rather than derived from
how much time was left; and phasing changes the balance, which changes what is
affordable, which changes stockouts. A convergence check that had passed on the
first attempt would have been evidence of nothing.

The second mechanism is the same shape for money: the agent arm sets the goal
contribution aside on payday, the no-agent arm saves whatever is left at the end
of the month, of which only `leftover_saved_fraction` survives. That is a
behavioural claim and it is a parameter, not a constant.

**The contribution never starves the essentials.** An earlier version deducted
it first, and the household whose goal needed its whole disposable income then
spent six months with no soap — the agent arm losing by a mile on the primary
metric. That was not a finding, it was this file modelling something the product
deliberately does not do: plans here are *advisory*, and the reason that decision
was taken is precisely so a goal can never outrank a necessity.

**Limitations, up front.**

  * No language model runs here. This measures whether the *mechanism* helps,
    assuming the agent executes it correctly. Whether an LLM executes it
    correctly is not tested by this and is not claimed.
  * A month is 30 days and income is regular. Real precarity is lumpier, and
    lumpier income would probably make phasing look better, not worse — so this
    is the conservative direction.
  * Nobody in this simulation is ill, or changes their mind, or shares a
    household with someone who also uses the soap.

Everything is seeded, and **the world and the behaviour draw from separate
streams**. That is not tidiness. With one stream the no-agent arm consumed an
extra random number every time it checked whether the person noticed, which
walked its shock sequence out of step with the other arm — so the two arms were
being run against different worlds while the docstring claimed otherwise. The
convergence check found it; nothing else would have.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Any

from .household import Essential, Household, Stock

DAYS_PER_MONTH = 30

WITHOUT_AGENT = "without_agent"
WITH_AGENT = "with_agent"
ARMS = (WITHOUT_AGENT, WITH_AGENT)


@dataclass
class Event:
    """One thing that happened. The log is the primary artefact.

    Raw events rather than running totals, so a metric nobody thought of yet can
    still be computed later without re-running anything.
    """

    day: int
    kind: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"day": self.day, "kind": self.kind, **self.detail}


@dataclass
class Run:
    household: str
    arm: str
    seed: int
    months: int
    forgetfulness: float
    events: list[Event] = field(default_factory=list)

    def record(self, day: int, kind: str, **detail: Any) -> None:
        self.events.append(Event(day=day, kind=kind, detail=detail))

    def of_kind(self, kind: str) -> list[Event]:
        return [event for event in self.events if event.kind == kind]

    def as_dicts(self) -> list[dict[str, Any]]:
        return [
            {"household": self.household, "arm": self.arm, "seed": self.seed, **event.as_dict()}
            for event in self.events
        ]


@dataclass
class _Order:
    item: Essential
    arrives_on: int
    cost_cents: int


def simulate(
    household: Household,
    *,
    arm: str,
    seed: int,
    months: int = 6,
    notice_days: int | None = None,
    phase_savings: bool = True,
) -> Run:
    """One household, one arm, N months. Deterministic given the seed.

    `notice_days` and `phase_savings` exist for the convergence check, which
    strips the agent of every advantage in turn so that the two arms can be
    shown to produce identical numbers when it has none.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; use one of: {', '.join(ARMS)}")

    # Two streams. `world_rng` drives what happens to the household and must be
    # identical across arms; `behaviour_rng` drives what the person does and is
    # drawn from only by the arm that has a person deciding. Sharing one stream
    # silently gives the arms different worlds — see the module docstring.
    world_rng = random.Random(seed)
    behaviour_rng = random.Random(seed + 1_000_003)
    run = Run(
        household=household.name,
        arm=arm,
        seed=seed,
        months=months,
        forgetfulness=household.forgetfulness,
    )
    essentials = household.essentials
    if notice_days is not None:
        essentials = tuple(replace(item, notice_days=notice_days) for item in essentials)
    household = replace(household, essentials=essentials)
    stock = Stock.full(household.essentials)
    orders: list[_Order] = []
    balance = 0
    saved_for_goal = 0
    # What a phased plan would put aside each month. The agent arm follows it;
    # the no-agent arm has no such thing and saves leftovers instead.
    per_month = -(-household.goal_target_cents // max(1, household.goal_months))

    for day in range(1, months * DAYS_PER_MONTH + 1):
        month_day = (day - 1) % DAYS_PER_MONTH + 1

        # --- money ---------------------------------------------------------
        if month_day == 1:
            balance += household.monthly_income_cents - household.monthly_commitments_cents
            run.record(day, "income", amount_cents=household.disposable_cents())
            if arm == WITH_AGENT and phase_savings:
                # Set aside, but never below what the month's essentials cost.
                # Plans are advisory: they inform, they do not outrank soap.
                reserve = sum(item.urgent_cents for item in household.essentials)
                put = min(per_month, max(0, balance - reserve))
                if put < per_month:
                    run.record(
                        day, "contribution_reduced", wanted_cents=per_month, put_cents=max(0, put)
                    )
                if put:
                    balance -= put
                    saved_for_goal += put
                    run.record(day, "goal_contribution", amount_cents=put, phased=True)
            if world_rng.random() < household.shock_chance:
                balance -= household.shock_cents
                run.record(day, "shock", amount_cents=household.shock_cents)

        # --- deliveries ----------------------------------------------------
        for order in [o for o in orders if o.arrives_on == day]:
            stock.levels[order.item.name] = order.item.days_per_pack
            run.record(day, "delivered", item=order.item.name)
        orders = [o for o in orders if o.arrives_on > day]

        # --- consumption ---------------------------------------------------
        for item in household.essentials:
            stock.consume(item.name)
            if stock.out_of(item.name):
                run.record(day, "stockout_day", item=item.name)

        # --- noticing, which is the whole difference ------------------------
        for item in household.essentials:
            if any(order.item.name == item.name for order in orders):
                continue  # already on its way

            days_left = stock.days_left(item.name)

            if arm == WITH_AGENT:
                # steward holds a supply fact, so it raises the item while there
                # is still time. Never forgets — that is the claim the sweep
                # tests. `<=` on both sides, not `0 <`: an item that somehow ran
                # out must still be reordered, or a stockout would be permanent.
                if days_left > item.notice_days:
                    continue
                run.record(day, "raised_early", item=item.name, days_left=days_left)
            else:
                # A person notices only once it is gone, and then only on a day
                # they think about it.
                if not stock.out_of(item.name):
                    continue
                if behaviour_rng.random() < household.forgetfulness:
                    continue

            # The same rule for both arms — see the module docstring.
            if days_left >= item.cheap_days:
                cost, wait = item.cheap_cents, item.cheap_days
            else:
                cost, wait = item.urgent_cents, item.urgent_days

            if balance < cost:
                run.record(day, "could_not_afford", item=item.name, cost_cents=cost)
                continue
            balance -= cost
            orders.append(_Order(item=item, arrives_on=day + wait, cost_cents=cost))
            run.record(
                day,
                "ordered",
                item=item.name,
                cost_cents=cost,
                arrives_in_days=wait,
                urgent=(cost == item.urgent_cents),
            )

        # --- month end -----------------------------------------------------
        if month_day == DAYS_PER_MONTH and (arm == WITHOUT_AGENT or not phase_savings):
            # Whatever is left, some of which survives to the goal.
            leftover = max(0, balance)
            put = int(leftover * household.leftover_saved_fraction)
            if put:
                balance -= put
                saved_for_goal += put
                run.record(day, "goal_contribution", amount_cents=put, phased=False)

    run.record(
        months * DAYS_PER_MONTH,
        "final",
        saved_for_goal_cents=saved_for_goal,
        goal_target_cents=household.goal_target_cents,
        goal_reached=saved_for_goal >= household.goal_target_cents,
        balance_cents=balance,
    )
    return run


def both_arms(
    household: Household,
    *,
    seed: int,
    months: int = 6,
    notice_days: int | None = None,
    phase_savings: bool = True,
) -> dict[str, Run]:
    """The same world, twice. Same seed both times, so any difference is the arm."""
    return {
        arm: simulate(
            household,
            arm=arm,
            seed=seed,
            months=months,
            notice_days=notice_days,
            phase_savings=phase_savings,
        )
        for arm in ARMS
    }
