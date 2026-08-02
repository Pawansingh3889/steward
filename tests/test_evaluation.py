"""The evaluation harness — tested harder than the thing it evaluates.

A simulation written by whoever wrote the product is the easiest place in a
project like this to fool yourself, so most of what follows checks that the
harness can produce an *unfavourable* answer, that its convergence check can
fail, and that the primary metric was fixed before anyone looked.
"""

from __future__ import annotations

import json

import pytest

from steward.evaluation import metrics, report, world
from steward.evaluation.household import HOUSEHOLDS, Household
from steward.evaluation.metrics import PRIMARY

FAST = (11, 22)  # two seeds; the full sweep is slow and the shape is the same


@pytest.fixture
def household() -> Household:
    return HOUSEHOLDS[0]


# --- the check that makes the result mean anything ---------------------------


def test_stripping_every_advantage_makes_the_arms_identical() -> None:
    """The load-bearing check. If this fails, every other number here is
    measuring a decision made in world.py rather than a mechanism.

    It failed three times before it passed, each time on a real defect: zeroing
    only forgetfulness (anticipation survives), supplier choice baked into the
    arm rather than derived from time remaining, and the two arms sharing one
    RNG stream so the no-agent arm's extra draws walked its shocks out of step.
    """
    assert report.converges_with_no_advantage(seeds=FAST, months=3)


def test_the_check_can_actually_fail(household: Household) -> None:
    """A check that cannot fail is decoration. Leave the agent one advantage and
    the arms must diverge."""
    summaries = []
    for seed in FAST:
        runs = world.both_arms(
            household.with_forgetfulness(0.0),
            seed=seed,
            months=3,
            notice_days=3,  # anticipation left in
            phase_savings=False,
        )
        summaries.extend(metrics.summarise(run) for run in runs.values())

    assert metrics.compare(summaries, PRIMARY).difference != 0


def test_the_arms_see_the_same_world(household: Household) -> None:
    """Shocks come from a stream the person's behaviour never touches, so the
    two arms cannot be handed different worlds."""
    runs = world.both_arms(household.with_forgetfulness(0.9), seed=11, months=6)

    shocks = {arm: len(run.of_kind("shock")) for arm, run in runs.items()}
    assert shocks["without_agent"] == shocks["with_agent"]


def test_a_run_is_deterministic(household: Household) -> None:
    first = world.simulate(household, arm=world.WITH_AGENT, seed=7, months=3)
    again = world.simulate(household, arm=world.WITH_AGENT, seed=7, months=3)

    assert first.as_dicts() == again.as_dicts()


def test_an_unknown_arm_is_refused(household: Household) -> None:
    with pytest.raises(ValueError, match="unknown arm"):
        world.simulate(household, arm="wishful", seed=1)


# --- the honesty properties --------------------------------------------------


def test_the_primary_metric_is_fixed_in_code() -> None:
    """Choosing it after seeing results is how a project proves whatever it
    happened to produce."""
    assert PRIMARY == "stockout_days_per_month"


def test_the_harness_can_report_that_the_agent_lost(household: Household) -> None:
    """The result has to be capable of coming out unfavourable, or it is not a
    measurement. `overreaching` is in the set precisely so one case can lose."""
    rows = report.by_household(forgetfulness=0.5, seeds=FAST, months=3)

    outcomes = {row["household"]: row["better"] for row in rows}
    assert outcomes["overreaching"] == "without_agent"
    assert "with_agent" in outcomes.values()


def test_crossover_can_come_back_none() -> None:
    """None means the agent never helped, which must be a reachable answer."""
    hopeless = (HOUSEHOLDS[2],)  # overreaching, alone
    points = report.sweep(hopeless, values=(0.0, 0.5), seeds=FAST, months=3)

    assert report.crossover(points) is None


def test_a_tie_is_reported_as_neither() -> None:
    comparison = metrics.Comparison(metric=PRIMARY, without_agent=4.0, with_agent=4.0)

    assert comparison.better == "neither"


@pytest.mark.parametrize(
    "metric, without, with_, expected",
    [
        ("stockout_days_per_month", 10.0, 4.0, "with_agent"),  # lower is better
        ("stockout_days_per_month", 4.0, 10.0, "without_agent"),
        ("saved_for_goal_cents", 100.0, 400.0, "with_agent"),  # higher is better
        ("saved_for_goal_cents", 400.0, 100.0, "without_agent"),
    ],
)
def test_direction_is_per_metric(metric: str, without: float, with_: float, expected: str) -> None:
    """Fewer stockouts is good; less saved is not. A single "smaller wins" rule
    would have reported the agent as better for saving nothing."""
    assert metrics.Comparison(metric=metric, without_agent=without, with_agent=with_).better == (
        expected
    )


# --- the mechanism -----------------------------------------------------------


def test_the_agent_arm_never_starves_the_essentials(household: Household) -> None:
    """An earlier version deducted the goal contribution first, and the
    household whose goal needed its whole income spent six months with no soap.
    That was this file modelling the opposite of the product's own decision:
    plans are advisory, so a goal can never outrank a necessity."""
    broke = HOUSEHOLDS[2]  # goal needs its entire disposable income
    run = world.simulate(broke, arm=world.WITH_AGENT, seed=11, months=6)

    reduced = run.of_kind("contribution_reduced")
    assert reduced, "the contribution should have been cut back to leave money for essentials"
    assert all(int(e.detail["put_cents"]) < int(e.detail["wanted_cents"]) for e in reduced)


def test_supplier_choice_is_situational_not_arm_based(household: Household) -> None:
    """Building it into the arm would smuggle a second advantage alongside the
    first. Both arms take the cheap slow one when it will arrive in time."""
    run = world.simulate(household.with_forgetfulness(0.9), arm=world.WITHOUT_AGENT, seed=11)

    orders = run.of_kind("ordered")
    assert orders
    # A person who has run out buys urgently — the situation, not the arm.
    assert all(order.detail["urgent"] for order in orders)


def test_an_item_that_ran_out_is_still_reordered(household: Household) -> None:
    """A strict `0 < days_left` in the agent branch once made stockouts
    permanent: nothing below zero could ever trigger an order."""
    run = world.simulate(household, arm=world.WITH_AGENT, seed=11, months=6, notice_days=0)

    assert run.of_kind("ordered")


def test_the_log_is_raw_events_not_totals(household: Household) -> None:
    """So a metric nobody has thought of yet can be computed later without
    re-running anything."""
    run = world.simulate(household, arm=world.WITH_AGENT, seed=11, months=2)

    kinds = {event.kind for event in run.events}
    assert {"income", "ordered", "delivered", "final"} <= kinds
    assert all("day" in event.as_dict() for event in run.events)


# --- the report --------------------------------------------------------------


def test_the_report_is_json_and_carries_its_own_caveats() -> None:
    points = report.sweep(HOUSEHOLDS[:1], values=(0.0, 0.9), seeds=FAST, months=3)

    parsed = json.loads(report.as_json(points))

    assert parsed["primary_metric"] == PRIMARY
    # The check travels with the result, so nobody can quote the numbers
    # without the thing that says whether they mean anything.
    assert "converges_with_no_advantage" in parsed
    assert parsed["households"]  # the assumptions, printed alongside
    assert parsed["sweep"][0]["forgetfulness"] == 0.0


def test_every_assumption_is_reported(household: Household) -> None:
    """The result is only as good as these, so they travel with it."""
    printed = household.as_dict()

    for assumption in ("forgetfulness", "leftover_saved_fraction", "shock_chance"):
        assert assumption in printed
