"""What to measure, decided before looking.

The roadmap said to record raw events "so the primary metric can be chosen late
without rework". Half of that is right and half of it is a trap. Recording raw
events *is* right — a metric nobody thought of yet can be computed later without
re-running anything, and `world.Run` keeps the log for exactly that reason.

But choosing the *primary* metric after seeing the results is how a project
proves whatever it happened to produce. With six metrics and two arms, something
will look good. So `PRIMARY` is fixed here, in code, and it was fixed before the
first run: **stockout-days per household-month.**

It is also the metric the agent is least flattered by, which is deliberate. It
does not measure money saved (where the phasing assumption does the work), and
it does not measure sponsor interruptions (which the agent wins trivially by
having a policy engine). It measures the thing the product claims to be for:
whether the person actually had soap.

Everything else is reported as secondary and labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .world import Run

# Fixed before the first run. See the module docstring.
PRIMARY = "stockout_days_per_month"
LOWER_IS_BETTER = {"stockout_days_per_month", "essentials_spend_cents", "could_not_afford"}


@dataclass(frozen=True)
class Summary:
    """One run, reduced. Every field is derived from the event log."""

    household: str
    arm: str
    seed: int
    months: int
    forgetfulness: float

    stockout_days: int
    stockout_days_per_month: float
    essentials_spend_cents: int
    orders: int
    urgent_orders: int
    could_not_afford: int
    saved_for_goal_cents: int
    goal_reached: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "household": self.household,
            "arm": self.arm,
            "seed": self.seed,
            "months": self.months,
            "forgetfulness": self.forgetfulness,
            PRIMARY: self.stockout_days_per_month,
            "stockout_days": self.stockout_days,
            "essentials_spend_cents": self.essentials_spend_cents,
            "orders": self.orders,
            "urgent_orders": self.urgent_orders,
            "could_not_afford": self.could_not_afford,
            "saved_for_goal_cents": self.saved_for_goal_cents,
            "goal_reached": self.goal_reached,
        }


def summarise(run: Run) -> Summary:
    stockouts = len(run.of_kind("stockout_day"))
    ordered = run.of_kind("ordered")
    final = run.of_kind("final")[-1].detail if run.of_kind("final") else {}
    return Summary(
        household=run.household,
        arm=run.arm,
        seed=run.seed,
        months=run.months,
        forgetfulness=run.forgetfulness,
        stockout_days=stockouts,
        stockout_days_per_month=round(stockouts / max(1, run.months), 2),
        essentials_spend_cents=sum(int(e.detail.get("cost_cents", 0)) for e in ordered),
        orders=len(ordered),
        urgent_orders=sum(1 for e in ordered if e.detail.get("urgent")),
        could_not_afford=len(run.of_kind("could_not_afford")),
        saved_for_goal_cents=int(final.get("saved_for_goal_cents", 0)),
        goal_reached=bool(final.get("goal_reached", False)),
    )


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


@dataclass(frozen=True)
class Comparison:
    """Two arms over the same worlds."""

    metric: str
    without_agent: float
    with_agent: float

    @property
    def difference(self) -> float:
        return round(self.with_agent - self.without_agent, 2)

    @property
    def better(self) -> str:
        """Which arm won, or neither.

        A difference of exactly zero is reported as a tie rather than rounded
        into a win. At low forgetfulness the two arms genuinely converge, and
        that convergence is the evidence this simulation is measuring a
        mechanism rather than restating an assumption.
        """
        if self.difference == 0:
            return "neither"
        wins_when_lower = self.metric in LOWER_IS_BETTER
        agent_lower = self.difference < 0
        return "with_agent" if agent_lower == wins_when_lower else "without_agent"

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "without_agent": self.without_agent,
            "with_agent": self.with_agent,
            "difference": self.difference,
            "better": self.better,
        }


def compare(summaries: list[Summary], metric: str) -> Comparison:
    def pick(arm: str) -> list[float]:
        return [float(s.as_dict()[metric]) for s in summaries if s.arm == arm]

    return Comparison(
        metric=metric,
        without_agent=mean(pick("without_agent")),
        with_agent=mean(pick("with_agent")),
    )
