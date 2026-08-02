"""The event stream a pilot is analysed from.

Two things carry these tests: that a message can be joined to the decision it
caused, and that the export cannot leak an identity. Both were gaps the pilot
plan flagged as cheap now and unrecoverable later.
"""

from __future__ import annotations

import json

import pytest

from steward import pilot, store
from steward.agent import loop
from steward.memory import recall
from steward.models import FactKind, Role

from .agent_stub import OpenAIStub, completion
from .warden_stub import WardenStub, parked

SOAP = {
    "description": "hand soap",
    "amount_cents": 450,
    "currency": "GBP",
    "merchant_name": "Fixture Store",
    "merchant_url": "https://fixture.example",
    "merchant_country": "GB",
}


@pytest.fixture
def household(db: str) -> tuple[int, int]:
    sponsor = store.insert_person(
        name="Rae Whitfield", role=Role.SPONSOR, phone="+447700900001", db_path=db
    )
    spender = store.insert_person(
        name="Ana Whitfield",
        role=Role.SPENDER,
        sponsor_id=sponsor,
        phone="+447700900002",
        email="ana@example.com",
        db_path=db,
    )
    return sponsor, spender


# --- the join that was missing ------------------------------------------------


def test_a_message_joins_to_the_decision_it_caused(db: str, household) -> None:
    """The gap the pilot plan called the one that would actually hurt: without
    this, matching a request to its outcome is a guess about timestamps."""
    _, spender = household
    model = OpenAIStub(
        [completion(tool_calls=[("request_purchase", SOAP)]), completion(content="Asked Rae.")]
    )

    result = loop.run(
        "can I have soap?",
        person_id=spender,
        db_path=db,
        http=model.client(),
        warden=WardenStub([parked()]),
    )

    stream = pilot.events(spender, db_path=db)
    run_id = result["run_id"]
    message = next(r for r in stream if r["kind"] == "message" and r["speaker"] == "person")
    escalation = next(r for r in stream if r["kind"] == "escalation_raised")

    assert message["run_id"] == run_id
    assert escalation["run_id"] == run_id  # same run: the join works


def test_a_turn_with_no_run_behind_it_is_zero_not_wrong(db: str, household) -> None:
    """A deterministic router reply has no agent run. Zero says so; inventing an
    id would make an unrelated decision look like its consequence."""
    _, spender = household
    store.insert_turn(person_id=spender, speaker="steward", text="ok", db_path=db)

    stream = pilot.events(spender, db_path=db)

    assert [r["run_id"] for r in stream if r["kind"] == "message"] == [0]


# --- corrections, first-class -------------------------------------------------


def test_forgetting_a_belief_is_recorded_as_a_correction(db: str, household) -> None:
    _, spender = household
    fact_id = store.upsert_fact(
        person_id=spender, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db
    )

    recall.forget(recall.FACT, fact_id, person_id=spender, db_path=db)

    correction = store.list_corrections(spender, db_path=db)[0]
    assert correction["kind"] == store.DELETED_BELIEF
    assert correction["subject"] == "fact"


def test_rejecting_a_proposal_is_a_different_correction(db: str, household) -> None:
    """ "They corrected us" and "a machine guessed and they said no" are
    different facts about whether this is working, and a tombstone alone cannot
    tell them apart."""
    _, spender = household
    fact_id = store.upsert_fact(
        person_id=spender,
        kind=FactKind.SCHEDULE,
        key="boiler",
        value="Thursday",
        source="inferred",
        pending=True,
        db_path=db,
    )

    recall.forget(recall.FACT, fact_id, person_id=spender, db_path=db)

    assert store.list_corrections(spender, db_path=db)[0]["kind"] == store.REJECTED_PROPOSAL


def test_a_superseded_fact_is_not_a_correction(db: str, household) -> None:
    """Restating something tombstones the old row. That is not somebody telling
    us we were wrong, and counting it as one would inflate the pilot's headline
    number with ordinary use."""
    _, spender = household
    for value in ("9-5", "10-6"):
        store.upsert_fact(
            person_id=spender, kind=FactKind.SCHEDULE, key="hours", value=value, db_path=db
        )

    assert store.list_corrections(spender, db_path=db) == []


def test_a_correction_carries_the_run_it_happened_in(db: str, household) -> None:
    _, spender = household
    fact_id = store.upsert_fact(
        person_id=spender, kind=FactKind.MOOD, key="today", value="fine", db_path=db
    )

    recall.forget(recall.FACT, fact_id, person_id=spender, run_id=77, db_path=db)

    assert store.list_corrections(spender, db_path=db)[0]["run_id"] == 77


# --- it cannot leak an identity -----------------------------------------------


def test_no_name_number_or_email_appears_anywhere(db: str, household) -> None:
    """A pilot export ends up in a spreadsheet, and sometimes in an email. It
    should be harmless when it does."""
    _, spender = household
    model = OpenAIStub([completion(content="ok")])
    loop.run(
        "I'm Ana Whitfield on +447700900002",
        person_id=spender,
        db_path=db,
        http=model.client(),
    )

    rendered = json.dumps(pilot.events(spender, db_path=db))

    for identifier in ("Ana Whitfield", "Rae Whitfield", "+447700900002", "ana@example.com"):
        assert identifier not in rendered


def test_message_text_is_excluded_by_default(db: str, household) -> None:
    """Counts and kinds answer the pilot's questions. The words do not, and an
    analysis file carrying somebody's private messages is a different object."""
    _, spender = household
    store.insert_turn(person_id=spender, speaker="person", text="I'm skint again", db_path=db)

    stream = pilot.events(spender, db_path=db)

    assert "I'm skint" not in json.dumps(stream)
    assert stream[0]["chars"] == len("I'm skint again")


def test_text_can_be_included_deliberately(db: str, household) -> None:
    """A person asking what is held about them is owed the actual words."""
    _, spender = household
    store.insert_turn(person_id=spender, speaker="person", text="I'm skint again", db_path=db)

    stream = pilot.events(spender, include_text=True, db_path=db)

    assert stream[0]["text"] == "I'm skint again"


def test_the_pair_id_is_stable_and_pseudonymous(db: str, household) -> None:
    sponsor, spender = household

    assert pilot.pair_id(store.get_person(spender, db_path=db)) == f"pair_{sponsor}_{spender}"


# --- the weekly read ----------------------------------------------------------


def test_the_summary_counts_what_the_pilot_asks_about(db: str, household) -> None:
    _, spender = household
    model = OpenAIStub(
        [completion(tool_calls=[("request_purchase", SOAP)]), completion(content="Asked.")]
    )
    loop.run(
        "soap?", person_id=spender, db_path=db, http=model.client(), warden=WardenStub([parked()])
    )
    fact_id = store.upsert_fact(
        person_id=spender, kind=FactKind.MOOD, key="today", value="fine", db_path=db
    )
    recall.forget(recall.FACT, fact_id, person_id=spender, db_path=db)

    summary = pilot.summary(spender, db_path=db)

    assert summary["corrections"] == 1
    assert summary["escalations_raised"] == 1
    assert summary["escalations_undecided"] == 1  # nobody has answered yet
    assert summary["agent_failures"] == 0


def test_events_are_in_time_order(db: str, household) -> None:
    _, spender = household
    for text in ("first", "second", "third"):
        store.insert_turn(person_id=spender, speaker="person", text=text, db_path=db)

    stream = pilot.events(spender, db_path=db)

    assert [r["at"] for r in stream] == sorted(r["at"] for r in stream)


def test_an_unknown_person_is_an_error_not_an_empty_report(db: str) -> None:
    """An empty export for a typo'd id reads as "this pair did nothing", which
    is a conclusion rather than a missing row."""
    with pytest.raises(store.NotFoundError):
        pilot.events(999, db_path=db)


def test_a_plan_does_not_break_the_export(db: str, household) -> None:
    """`plan_proposed` carries the plan's own kind, and that field is named
    `plan_kind` because `kind` is the event's name.

    It was `kind` at first, which collided with the parameter of the same name
    and raised a TypeError — so `pilot.events` failed for anybody who had ever
    made a plan, which is most of the people a pilot would be watching. Nothing
    covered plans here, so it went unseen until a surface read the stream.
    """
    _, spender = household
    store.insert_plan(
        person_id=spender,
        name="Lisbon",
        kind="trip",
        target_cents=60000,
        currency="GBP",
        cadence="monthly",
        per_period_cents=15000,
        start_date="2026-08-02",
        finish_date="2026-12-02",
        db_path=db,
    )

    stream = pilot.events(spender, db_path=db)

    proposed = next(row for row in stream if row["kind"] == "plan_proposed")
    assert proposed["plan_kind"] == "trip"
    assert proposed["target_cents"] == 60000
