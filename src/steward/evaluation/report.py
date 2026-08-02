"""Running the whole evaluation, and reporting it as a curve rather than a number.

A single headline — "the agent saves you £X" — would be the least defensible
thing this project could produce, because it would be a restatement of whatever
`forgetfulness` was set to. So the sweep is the report: the same households, the
same seeds, both arms, at forgetfulness from 0 to 0.9.

What that buys is a falsifiable shape, and a check that can fail.

The check is `converges_with_no_advantage`: strip the agent of everything it has
— anticipation, reliability and phasing — and the two arms must produce
identical numbers. If they do not, the difference everywhere else is an artefact
of how the arms were written rather than a mechanism, and the result should be
discarded.

It failed three times before it passed, and each failure was a real defect in
this harness rather than a finding. See `world.py` for what they were.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from . import metrics, world
from .household import HOUSEHOLDS, Household
from .metrics import PRIMARY, Comparison, Summary

__all__ = ["PRIMARY", "by_household", "converges_with_no_advantage", "crossover", "sweep"]

# Enough seeds to average out the dice without pretending to statistical power.
# The claim is about a mechanism, not an effect size in a population.
SEEDS = (11, 22, 33, 44, 55)
SWEEP = (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9)
MONTHS = 6


@dataclass(frozen=True)
class Point:
    """One rung of the sweep."""

    forgetfulness: float
    primary: Comparison
    secondary: list[Comparison]

    def as_dict(self) -> dict[str, Any]:
        return {
            "forgetfulness": self.forgetfulness,
            "primary": self.primary.as_dict(),
            "secondary": [c.as_dict() for c in self.secondary],
        }


SECONDARY = (
    "essentials_spend_cents",
    "urgent_orders",
    "saved_for_goal_cents",
    "could_not_afford",
)


def run_all(
    households: tuple[Household, ...] = HOUSEHOLDS,
    *,
    forgetfulness: float,
    seeds: tuple[int, ...] = SEEDS,
    months: int = MONTHS,
) -> list[Summary]:
    """Every household × every seed × both arms, at one forgetfulness."""
    summaries: list[Summary] = []
    for household in households:
        subject = household.with_forgetfulness(forgetfulness)
        for seed in seeds:
            for run in world.both_arms(subject, seed=seed, months=months).values():
                summaries.append(metrics.summarise(run))
    return summaries


def sweep(
    households: tuple[Household, ...] = HOUSEHOLDS,
    *,
    values: tuple[float, ...] = SWEEP,
    seeds: tuple[int, ...] = SEEDS,
    months: int = MONTHS,
) -> list[Point]:
    points: list[Point] = []
    for value in values:
        summaries = run_all(households, forgetfulness=value, seeds=seeds, months=months)
        points.append(
            Point(
                forgetfulness=value,
                primary=metrics.compare(summaries, PRIMARY),
                secondary=[metrics.compare(summaries, name) for name in SECONDARY],
            )
        )
    return points


def by_household(
    *, forgetfulness: float, seeds: tuple[int, ...] = SEEDS, months: int = MONTHS
) -> list[dict[str, Any]]:
    """The same comparison, per household, because the mean hides the story.

    Averaging across households turned "three total wins and one household that
    cannot afford its own goal" into "the agent is slightly worse", which is
    true of the mean and false of every household in it. A mean over four
    households where one is an outlier is not a summary, it is a way of not
    looking.
    """
    rows: list[dict[str, Any]] = []
    for household in HOUSEHOLDS:
        summaries = run_all((household,), forgetfulness=forgetfulness, seeds=seeds, months=months)
        primary = metrics.compare(summaries, PRIMARY)
        rows.append(
            {
                "household": household.name,
                **primary.as_dict(),
                "could_not_afford": metrics.compare(summaries, "could_not_afford").as_dict(),
            }
        )
    return rows


def crossover(points: list[Point]) -> float | None:
    """The lowest forgetfulness at which the agent arm is actually better.

    None means it never was, which is a publishable answer. The whole point of
    reporting this rather than a headline is that it can come back None.
    """
    for point in points:
        if point.primary.better == "with_agent":
            return point.forgetfulness
    return None


def converges_with_no_advantage(
    households: tuple[Household, ...] = HOUSEHOLDS,
    *,
    seeds: tuple[int, ...] = SEEDS,
    months: int = MONTHS,
) -> bool:
    """The check that decides whether any of this means anything.

    Zero all three components — no anticipation (`notice_days=0`), no forgetting,
    no phasing — and the arms must produce identical primary numbers. A
    difference here would mean they differ for some reason nobody intended, and
    every other number in this report would be measuring that instead.
    """
    summaries: list[Summary] = []
    for household in households:
        subject = household.with_forgetfulness(0.0)
        for seed in seeds:
            runs = world.both_arms(
                subject, seed=seed, months=months, notice_days=0, phase_savings=False
            )
            summaries.extend(metrics.summarise(run) for run in runs.values())
    return metrics.compare(summaries, PRIMARY).difference == 0


def as_json(points: list[Point]) -> str:
    return json.dumps(
        {
            "primary_metric": PRIMARY,
            "months": MONTHS,
            "seeds": list(SEEDS),
            "households": [h.as_dict() for h in HOUSEHOLDS],
            "converges_with_no_advantage": converges_with_no_advantage(),
            "crossover_forgetfulness": crossover(points),
            "sweep": [point.as_dict() for point in points],
            "by_household_at_0.5": by_household(forgetfulness=0.5),
        },
        indent=2,
    )
