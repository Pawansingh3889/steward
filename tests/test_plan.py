"""Phase 6: a trip plan with a savings schedule the user shaped.

Three things carry this phase, and all three are about who decides:

  the arithmetic is honest  — when it does not add up, it says so, and offers
                              three options without choosing between them.
  only a person commits     — the model proposes drafts; nothing it can call
                              activates one.
  a plan advises, never blocks — pay-warden is still the only thing that refuses.
"""

from __future__ import annotations

from datetime import date

import pytest

from steward import cli, store
from steward.agent.privacy import Redactor
from steward.agent.tools import ToolBox
from steward.models import Role
from steward.plan import goals, schedule
from steward.plan.schedule import MONTHLY, PlanError

from .warden_stub import WardenStub, allowed

TODAY = date(2026, 8, 2)


@pytest.fixture
def spender(db: str) -> int:
    sponsor = store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, db_path=db)
    return store.insert_person(
        name="Ana Whitfield", role=Role.SPENDER, sponsor_id=sponsor, db_path=db
    )


@pytest.fixture
def box(db: str, spender: int) -> ToolBox:
    return ToolBox(person_id=spender, redactor=Redactor.build(db_path=db), db_path=db)


def draft(db: str, person: int, **kwargs) -> dict:
    defaults = {
        "name": "Laptop",
        "target_cents": 60000,
        "finish": date(2027, 3, 2),
        "start": TODAY,
    }
    return goals.propose(person_id=person, db_path=db, **{**defaults, **kwargs})


# --- the arithmetic ----------------------------------------------------------


def test_monthly_stepping_does_not_drift_through_short_months() -> None:
    """Someone paid on the 31st is saving on the 31st, except where there is no
    31st — and is back on it the following month."""
    steps = [schedule.add_periods(date(2026, 1, 31), MONTHLY, n).isoformat() for n in range(5)]

    assert steps == ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31"]


def test_the_two_month_functions_agree_at_a_month_end() -> None:
    """They disagreed. `periods_between` compared raw day numbers, so 31 Jan to
    28 Feb came out as zero months and anyone starting late in a long month was
    told their plan was impossible."""
    start, finish = date(2026, 1, 31), date(2026, 2, 28)

    assert schedule.add_periods(start, MONTHLY, 1) == finish
    assert schedule.periods_between(start, finish, MONTHLY) == 1
    assert schedule.solve(target_cents=10000, start=start, finish=finish).periods == 1


@pytest.mark.parametrize("day", [29, 30, 31])
def test_no_start_day_makes_a_plan_impossible(day: int) -> None:
    start = date(2026, 1, day)
    finish = schedule.add_periods(start, MONTHLY, 3)

    assert schedule.solve(target_cents=30000, start=start, finish=finish).periods == 3


def test_periods_round_down() -> None:
    """Counting a half period would tell someone they will have saved money
    they have not been paid yet."""
    assert schedule.periods_between(date(2026, 1, 1), date(2026, 1, 25), "weekly") == 3


def test_contributions_round_up() -> None:
    """A schedule landing a penny short of its own target is worse than one
    arriving a penny early."""
    plan = schedule.solve(
        target_cents=1000, start=TODAY, finish=schedule.add_periods(TODAY, MONTHLY, 3)
    )

    assert plan.per_period_cents == 334
    assert plan.saved_cents == 1002
    assert plan.reaches_target


def test_the_missing_quantity_is_the_one_that_moves() -> None:
    by_date = schedule.solve(target_cents=60000, start=TODAY, finish=date(2027, 3, 2))
    by_amount = schedule.solve(target_cents=60000, start=TODAY, per_period_cents=8572)

    assert by_date.per_period_cents == 8572  # solved for the amount
    assert by_amount.periods == 7  # solved for the date
    assert by_amount.finish == date(2027, 3, 2)


def test_a_plan_that_misses_says_so_rather_than_moving_the_date() -> None:
    plan = schedule.solve(
        target_cents=60000, start=TODAY, finish=date(2026, 11, 2), per_period_cents=5000
    )

    assert not plan.reaches_target
    assert plan.shortfall_cents == 45000
    assert plan.finish == date(2026, 11, 2)  # untouched
    assert plan.target_cents == 60000  # untouched


def test_three_ways_out_with_the_same_shape_and_no_recommendation() -> None:
    """A renderer has to be able to read them down a column."""
    plan = schedule.solve(
        target_cents=60000, start=TODAY, finish=date(2026, 11, 2), per_period_cents=5000
    )

    options = schedule.ways_to_close(plan)

    assert [o.change for o in options] == ["take_longer", "smaller_goal", "more_each_time"]
    keys = {tuple(sorted(o.as_dict())) for o in options}
    assert len(keys) == 1  # identical field sets
    assert not any("recommend" in o.note or "best" in o.note for o in options)


def test_the_singular_is_not_one_more_months() -> None:
    plan = schedule.solve(
        target_cents=10000,
        start=TODAY,
        finish=schedule.add_periods(TODAY, MONTHLY, 2),
        per_period_cents=4000,
    )

    assert "1 more month at" in schedule.ways_to_close(plan)[0].note


def test_the_period_noun_is_a_map_not_a_character_strip() -> None:
    """`"daily".rstrip("ly")` is `"dai"`. A new cadence should fail loudly, not
    produce a sentence with a typo in it."""
    assert schedule.noun("monthly", 1) == "month"
    assert schedule.noun("fortnightly", 2) == "fortnights"
    with pytest.raises(PlanError):
        schedule.noun("daily")


def test_unaffordable_is_not_the_same_as_unreachable() -> None:
    plan = schedule.solve(
        target_cents=60000, start=TODAY, finish=date(2027, 3, 2), affordable_cents=4000
    )

    assert plan.reaches_target  # the schedule works
    assert plan.affordable is False  # they just cannot manage it
    assert schedule.ways_to_close(plan)  # so there are still options


def test_not_saying_what_you_can_afford_is_unknown_not_fine() -> None:
    plan = schedule.solve(target_cents=1000, start=TODAY, per_period_cents=500)

    assert plan.affordable is None


def test_money_already_put_aside_shortens_the_schedule() -> None:
    plan = schedule.solve(
        target_cents=60000, start=TODAY, per_period_cents=10000, already_saved_cents=20000
    )

    assert plan.periods == 4  # 40000 left, not 60000
    assert plan.saved_cents == 60000


def test_both_saved_and_short_are_reported() -> None:
    """ "£20 short" and "you'll have £80 of £100" are the same fact, and only one
    of them is an answer."""
    plan = schedule.solve(
        target_cents=10000,
        start=TODAY,
        finish=schedule.add_periods(TODAY, MONTHLY, 2),
        per_period_cents=4000,
    )

    assert plan.as_dict()["saved_cents"] == 8000
    assert plan.as_dict()["shortfall_cents"] == 2000


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"target_cents": 0, "per_period_cents": 100}, "worth something"),
        ({"target_cents": 1000}, "either a date"),
        ({"target_cents": 1000, "per_period_cents": 0}, "never reaches"),
        ({"target_cents": 1000, "cadence": "hourly", "per_period_cents": 10}, "unknown cadence"),
        ({"target_cents": 1000, "already_saved_cents": -5, "per_period_cents": 10}, "negative"),
    ],
)
def test_bad_input_raises_a_sentence(kwargs: dict, message: str) -> None:
    with pytest.raises(PlanError, match=message):
        schedule.solve(start=TODAY, **kwargs)


def test_plan_errors_are_runtime_errors() -> None:
    """Not ValueError: that is what int("abc") raises, and "the model sent junk"
    needs a different reply from "this goal is not reachable"."""
    assert issubclass(PlanError, RuntimeError)
    assert not issubclass(PlanError, ValueError)


# --- only a person commits ---------------------------------------------------


def test_a_proposal_is_a_draft(db: str, spender: int) -> None:
    plan = draft(db, spender)

    assert plan["status"] == store.PLAN_DRAFT
    assert store.get_plan(plan["plan_id"], db_path=db)["activated_ts"] == ""


def test_no_tool_starts_or_stops_a_plan(box: ToolBox) -> None:
    """An agent that could activate its own proposals would make the draft
    distinction a formality — the same reason there is no confirm_fact tool and
    no approve_purchase tool."""
    names = {spec["function"]["name"] for spec in box.specs()}

    assert "propose_plan" in names
    for forbidden in ("activate", "start", "abandon", "commit", "contribute"):
        assert not any(forbidden in name for name in names), forbidden
    assert "error" in box.dispatch("activate_plan", {"plan_id": 1})


def test_no_tool_chooses_the_start_date(box: ToolBox) -> None:
    """A backdated plan has more periods in it, so it reports progress on money
    nobody put aside — the same class of problem as a model naming its own price."""
    spec = next(s for s in box.specs() if s["function"]["name"] == "propose_plan")

    assert "start" not in spec["function"]["parameters"]["properties"]


def test_activating_twice_is_refused(db: str, spender: int) -> None:
    plan = draft(db, spender)
    goals.activate(plan["plan_id"], person_id=spender, db_path=db)

    with pytest.raises(store.NotFoundError):
        goals.activate(plan["plan_id"], person_id=spender, db_path=db)


def test_activation_is_recorded(db: str, spender: int) -> None:
    """A deterministic router branch writes no turn and no episode, so without
    this column there would be no record anywhere that a person started it."""
    plan = draft(db, spender)

    goals.activate(plan["plan_id"], person_id=spender, db_path=db)

    assert store.get_plan(plan["plan_id"], db_path=db)["activated_ts"]


def test_an_active_plan_cannot_be_reshaped(db: str, spender: int) -> None:
    """Changing the numbers under a plan somebody agreed to makes the thing they
    agreed to and the thing steward tracks two different plans."""
    plan = draft(db, spender)
    goals.activate(plan["plan_id"], person_id=spender, db_path=db)

    with pytest.raises(PlanError, match="already active"):
        goals.adjust(plan["plan_id"], person_id=spender, target_cents=100, db_path=db)


def test_pinning_all_three_is_refused(db: str, spender: int) -> None:
    plan = draft(db, spender)

    with pytest.raises(PlanError, match="at most two"):
        goals.adjust(
            plan["plan_id"],
            person_id=spender,
            target_cents=50000,
            finish=date(2027, 1, 1),
            per_period_cents=5000,
            db_path=db,
        )


def test_another_persons_plan_is_out_of_reach(db: str, spender: int) -> None:
    stranger = store.insert_person(name="Someone Else", role=Role.SPENDER, db_path=db)
    theirs = draft(db, stranger)

    for call in (
        lambda: goals.adjust(theirs["plan_id"], person_id=spender, target_cents=1, db_path=db),
        lambda: goals.activate(theirs["plan_id"], person_id=spender, db_path=db),
        lambda: goals.contribute(theirs["plan_id"], 100, person_id=spender, db_path=db),
    ):
        with pytest.raises(PlanError, match="belonging to you"):
            call()
    assert store.get_plan(theirs["plan_id"], db_path=db)["status"] == store.PLAN_DRAFT


def test_the_store_scopes_by_person_even_if_a_caller_forgets(db: str, spender: int) -> None:
    """Defence in depth: the WHERE clause carries the scope, not just goals.py."""
    stranger = store.insert_person(name="Someone Else", role=Role.SPENDER, db_path=db)
    theirs = draft(db, stranger)

    with pytest.raises(store.NotFoundError):
        store.set_plan_status(
            theirs["plan_id"],
            person_id=spender,
            status=store.PLAN_ACTIVE,
            expect=store.PLAN_DRAFT,
            db_path=db,
        )


# --- flights and hotels ------------------------------------------------------


@pytest.mark.parametrize("kind", ["flight", "accommodation"])
def test_flights_and_hotels_always_need_a_person(db: str, spender: int, kind: str) -> None:
    plan = draft(db, spender, kind="trip", name="Lisbon")

    item = goals.add_item(
        plan["plan_id"],
        person_id=spender,
        description="x",
        amount_cents=1000,
        kind=kind,
        db_path=db,
    )

    assert item["needs_human"] is True


def test_the_flag_cannot_be_passed_in_at_all(db: str, spender: int) -> None:
    """Computed in the store from the kind, so no layer above can clear it."""
    import inspect

    assert "needs_human" not in inspect.signature(store.insert_plan_item).parameters
    assert "needs_human" not in inspect.signature(goals.add_item).parameters

    plan = draft(db, spender, kind="trip")
    store.insert_plan_item(
        plan_id=plan["plan_id"],
        description="BA1234",
        amount_cents=9000,
        kind="flight",
        db_path=db,
    )
    assert store.list_plan_items(plan["plan_id"], db_path=db)[0]["needs_human"] == 1


def test_nothing_in_plan_imports_spend() -> None:
    """The invariant that makes needs_human a promise rather than a flag being
    ignored: there is no code path from a plan item to a payment."""
    from pathlib import Path

    package = Path(goals.__file__).parent
    for source in package.glob("*.py"):
        for line in source.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "spend" not in stripped, f"{source.name}: {stripped}"


def test_an_item_on_a_missing_plan_is_an_error_not_a_crash(db: str, spender: int) -> None:
    """foreign_keys=ON would raise IntegrityError, which is not a StoreError and
    would escape dispatch, the loop and the router uncaught."""
    with pytest.raises(store.NotFoundError):
        store.insert_plan_item(
            plan_id=999, description="x", amount_cents=1, kind="other", db_path=db
        )


def test_items_and_target_drift_is_shown_not_hidden(db: str, spender: int) -> None:
    plan = draft(db, spender, kind="trip", target_cents=50000)
    goals.add_item(
        plan["plan_id"],
        person_id=spender,
        description="Flights",
        amount_cents=40000,
        kind="flight",
        db_path=db,
    )
    goals.add_item(
        plan["plan_id"],
        person_id=spender,
        description="Hotel",
        amount_cents=30000,
        kind="accommodation",
        db_path=db,
    )

    row = store.get_plan(plan["plan_id"], db_path=db)
    shown = goals.view(row, db_path=db)

    assert shown["items_total_cents"] == 70000
    assert shown["items_covered"] is False


# --- advisory, never blocking ------------------------------------------------


def test_a_purchase_says_what_it_costs_an_active_goal(db: str, spender: int, box: ToolBox) -> None:
    plan = draft(db, spender, per_period_cents=5000, finish=None)
    goals.activate(plan["plan_id"], person_id=spender, db_path=db)
    box.warden = WardenStub([allowed()])

    result = box.dispatch(
        "request_purchase",
        {
            "description": "headphones",
            "amount_cents": 10000,
            "currency": "GBP",
            "merchant_name": "Shop",
            "merchant_url": "https://shop.example",
        },
    )

    assert result["verdict"] == "allowed"  # not blocked
    assert result["goal_impact"][0]["costs"] == "2 months"
    assert "does not stop the purchase" in result["goal_impact"][0]["note"]


def test_a_draft_is_never_counted_against_spending(db: str, spender: int) -> None:
    """Nobody agreed to a draft, so nothing can set it back — and counting them
    would let the model shrink someone's apparent room to spend just by
    proposing plans."""
    draft(db, spender, per_period_cents=5000, finish=None)

    assert goals.impact(spender, 10000, db_path=db) == []


def test_impact_of_nothing_is_nothing(db: str, spender: int) -> None:
    plan = draft(db, spender)
    goals.activate(plan["plan_id"], person_id=spender, db_path=db)

    assert goals.impact(spender, 0, db_path=db) == []


def test_the_prompt_forbids_deciding_for_them() -> None:
    from steward.agent import loop

    assert "Do not refuse, do not lecture" in loop.SYSTEM_PROMPT
    assert "you have no way to start one" in loop.SYSTEM_PROMPT


# --- contributions -----------------------------------------------------------


def test_contributions_are_stated_and_shorten_what_is_left(db: str, spender: int) -> None:
    plan = draft(db, spender)

    updated = goals.contribute(plan["plan_id"], 20000, person_id=spender, db_path=db)

    assert updated["contributed_cents"] == 20000
    assert updated["remaining_cents"] == 40000


def test_a_contribution_has_to_be_something(db: str, spender: int) -> None:
    plan = draft(db, spender)

    with pytest.raises(PlanError, match="has to be something"):
        goals.contribute(plan["plan_id"], 0, person_id=spender, db_path=db)


# --- the tools ---------------------------------------------------------------


def test_proposing_through_the_tool(box: ToolBox) -> None:
    result = box.dispatch(
        "propose_plan",
        {"name": "Laptop", "target_cents": 60000, "per_period_cents": 10000},
    )

    assert result["status"] == store.PLAN_DRAFT
    assert result["periods"] == 6


def test_a_tool_error_is_reported_not_raised(box: ToolBox) -> None:
    result = box.dispatch("propose_plan", {"name": "Laptop", "target_cents": 0})

    assert "worth something" in result["error"]


def test_a_malformed_date_becomes_a_question_not_a_crash(box: ToolBox) -> None:
    """`solve` then reports what it actually needs."""
    result = box.dispatch(
        "propose_plan", {"name": "Laptop", "target_cents": 60000, "finish": "next March"}
    )

    assert "either a date" in result["error"]


def test_show_plans_lists_drafts_and_active(box: ToolBox, db: str, spender: int) -> None:
    draft(db, spender, name="Laptop")
    second = draft(db, spender, name="Lisbon", kind="trip")
    goals.activate(second["plan_id"], person_id=spender, db_path=db)

    result = box.dispatch("show_plans", {})

    assert result["count"] == 2
    assert {p["status"] for p in result["plans"]} == {"draft", "active"}


# --- starting one by text ----------------------------------------------------


def _router(db: str, channel):
    from steward.surface.router import Router

    return Router(db_path=db, channel=channel)


def _line(db: str, spender: int) -> str:
    store.set_share_mode(spender, store.SHARE_PRIVATE, db_path=db)
    with store.transaction(db) as conn:
        conn.execute("UPDATE people SET phone = ? WHERE id = ?", ("+447700900002", spender))
    return "+447700900002"


def test_start_that_plan_activates_the_only_draft(db: str, spender: int) -> None:
    from steward.surface.base import Inbound, RecordingChannel

    plan = draft(db, spender)
    line = _line(db, spender)
    channel = RecordingChannel()

    handled = _router(db, channel).receive(Inbound(sender=line, body="start that plan"))

    assert handled.kind == "plan_started"
    assert store.get_plan(plan["plan_id"], db_path=db)["status"] == store.PLAN_ACTIVE
    assert "won't stop you spending" in handled.bodies()[0]


def test_two_drafts_ask_which_rather_than_guessing(db: str, spender: int) -> None:
    from steward.surface.base import Inbound, RecordingChannel

    draft(db, spender, name="Laptop")
    draft(db, spender, name="Lisbon")
    line = _line(db, spender)

    handled = _router(db, RecordingChannel()).receive(Inbound(sender=line, body="start that plan"))

    assert handled.kind == "ambiguous_plan"
    assert "more than one draft" in handled.bodies()[0]
    assert all(row["status"] == store.PLAN_DRAFT for row in store.list_plans(spender, db_path=db))


def test_naming_one_of_several_starts_that_one(db: str, spender: int) -> None:
    from steward.surface.base import Inbound, RecordingChannel

    first = draft(db, spender, name="Laptop")
    draft(db, spender, name="Lisbon")
    line = _line(db, spender)

    handled = _router(db, RecordingChannel()).receive(
        Inbound(sender=line, body=f"start that plan #{first['plan_id']}")
    )

    assert handled.kind == "plan_started"
    assert store.get_plan(first["plan_id"], db_path=db)["status"] == store.PLAN_ACTIVE


def test_no_draft_says_so_rather_than_reaching_the_model(db: str, spender: int) -> None:
    """If this fell through, the model — which has no activation tool — would
    cheerfully say "started it" and nothing would have happened."""
    from steward.surface.base import Inbound, RecordingChannel

    line = _line(db, spender)

    handled = _router(db, RecordingChannel()).receive(Inbound(sender=line, body="start that plan"))

    assert handled.kind == "no_draft"
    assert "no draft plan waiting" in handled.bodies()[0]


def test_an_already_running_plan_is_named(db: str, spender: int) -> None:
    from steward.surface.base import Inbound, RecordingChannel

    plan = draft(db, spender, name="Laptop")
    goals.activate(plan["plan_id"], person_id=spender, db_path=db)
    line = _line(db, spender)

    handled = _router(db, RecordingChannel()).receive(Inbound(sender=line, body="start that plan"))

    assert "Laptop is already running" in handled.bodies()[0]


# --- the person's surface ----------------------------------------------------


def test_proposing_from_the_cli_shows_the_gap_and_three_ways_out(
    db: str, spender: int, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(
        [
            "--db",
            db,
            "plan",
            "propose",
            "--person",
            str(spender),
            "--name",
            "Lisbon",
            "--target-cents",
            "60000",
            "--finish",
            "2026-11-02",
            "--per-period-cents",
            "5000",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "short £450.00" in out
    assert "three ways to close the gap" in out
    assert "your call, not mine" in out
    assert "take_longer" in out and "smaller_goal" in out and "more_each_time" in out
    assert "it does nothing until you start it" in out


def test_the_cli_activates_and_says_what_that_means(
    db: str, spender: int, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = draft(db, spender)

    cli.main(
        ["--db", db, "plan", "activate", "--person", str(spender), "--id", str(plan["plan_id"])]
    )

    out = capsys.readouterr().out
    assert "started: Laptop" in out
    assert "It will not stop you" in out


def test_a_trip_item_says_who_books_it(
    db: str, spender: int, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = draft(db, spender, kind="trip", name="Lisbon")

    cli.main(
        [
            "--db",
            db,
            "plan",
            "item",
            "--person",
            str(spender),
            "--id",
            str(plan["plan_id"]),
            "--description",
            "Flights",
            "--amount-cents",
            "18000",
            "--kind",
            "flight",
        ]
    )

    assert "you book this one yourself" in capsys.readouterr().out


def test_listing_shows_status_and_progress(
    db: str, spender: int, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = draft(db, spender)
    goals.contribute(plan["plan_id"], 20000, person_id=spender, db_path=db)

    cli.main(["--db", db, "plan", "list", "--person", str(spender)])

    out = capsys.readouterr().out
    assert "[draft]" in out
    assert "put aside so far: £200.00" in out


def test_the_cli_refuses_a_plan_that_makes_no_sense(db: str, spender: int) -> None:
    with pytest.raises(SystemExit, match="worth something"):
        cli.main(
            [
                "--db",
                db,
                "plan",
                "propose",
                "--person",
                str(spender),
                "--name",
                "x",
                "--target-cents",
                "0",
                "--per-period-cents",
                "100",
            ]
        )
