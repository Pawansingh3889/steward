"""The store: people, facts, turns, audit.

The tombstone behaviour gets the most attention here, because "forget that"
is a promise this product makes to someone who cannot easily verify it.
"""

from __future__ import annotations

import pytest

from steward import store
from steward.models import FactKind, Role


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
        db_path=db,
    )
    return sponsor, spender


def test_schema_initializes_on_a_fresh_path(db: str) -> None:
    store.init_db(db)
    assert store.list_people(db_path=db) == []


def test_a_spender_points_at_their_sponsor(household: tuple[int, int], db: str) -> None:
    sponsor, spender = household
    row = store.get_person(spender, db_path=db)

    assert row is not None
    assert row["sponsor_id"] == sponsor
    assert row["role"] == Role.SPENDER


def test_a_sponsor_is_funded_by_nobody(household: tuple[int, int], db: str) -> None:
    sponsor, _ = household
    row = store.get_person(sponsor, db_path=db)

    assert row is not None
    assert row["sponsor_id"] is None


def test_an_inbound_text_finds_its_sender(household: tuple[int, int], db: str) -> None:
    _, spender = household
    found = store.person_by_phone("+447700900002", db_path=db)

    assert found is not None
    assert found["id"] == spender
    assert store.person_by_phone("+447700900999", db_path=db) is None


# --- facts -------------------------------------------------------------------


def test_a_stated_fact_comes_back(household: tuple[int, int], db: str) -> None:
    _, spender = household
    store.upsert_fact(person_id=spender, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)
    facts = store.list_facts(spender, db_path=db)

    assert len(facts) == 1
    assert facts[0]["value"] == "out"
    assert facts[0]["source"] == "stated"


def test_restating_replaces_rather_than_accumulates(household: tuple[int, int], db: str) -> None:
    """Saying the same thing twice is one truth. A memory that grew every time
    someone repeated themselves would be unreadable the moment it mattered."""
    _, spender = household
    store.upsert_fact(
        person_id=spender, kind=FactKind.SCHEDULE, key="hours", value="9-5", db_path=db
    )
    store.upsert_fact(
        person_id=spender, kind=FactKind.SCHEDULE, key="hours", value="10-6", db_path=db
    )

    live = store.list_facts(spender, db_path=db)
    assert len(live) == 1
    assert live[0]["value"] == "10-6"
    # The superseded row survives as history, not as memory.
    assert len(store.list_facts(spender, include_deleted=True, db_path=db)) == 2


def test_deleting_a_fact_removes_it_from_memory(household: tuple[int, int], db: str) -> None:
    _, spender = household
    fact_id = store.upsert_fact(
        person_id=spender, kind=FactKind.MOOD, key="today", value="stressed", db_path=db
    )

    store.delete_fact(fact_id, db_path=db)

    assert store.list_facts(spender, db_path=db) == []
    row = store.get_fact(fact_id, db_path=db)
    assert row is not None
    assert row["deleted_ts"]  # tombstoned, not gone


def test_deleting_a_fact_twice_is_an_error_not_a_shrug(household: tuple[int, int], db: str) -> None:
    """Reporting success without doing anything is the one failure mode
    "forget that" cannot have."""
    _, spender = household
    fact_id = store.upsert_fact(
        person_id=spender, kind=FactKind.MOOD, key="today", value="fine", db_path=db
    )
    store.delete_fact(fact_id, db_path=db)

    with pytest.raises(store.NotFoundError):
        store.delete_fact(fact_id, db_path=db)
    with pytest.raises(store.NotFoundError):
        store.delete_fact(9999, db_path=db)


def test_a_deleted_fact_can_be_stated_again(household: tuple[int, int], db: str) -> None:
    """The partial unique index exists for exactly this: a plain UNIQUE would
    make deletion permanent in the worst way — you could never say it again."""
    _, spender = household
    first = store.upsert_fact(
        person_id=spender, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db
    )
    store.delete_fact(first, db_path=db)

    second = store.upsert_fact(
        person_id=spender, kind=FactKind.SUPPLY, key="soap", value="out again", db_path=db
    )

    assert second != first
    live = store.list_facts(spender, db_path=db)
    assert [f["value"] for f in live] == ["out again"]


def test_facts_filter_by_kind(household: tuple[int, int], db: str) -> None:
    _, spender = household
    store.upsert_fact(person_id=spender, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)
    store.upsert_fact(
        person_id=spender, kind=FactKind.GOAL, key="laptop", value="£600 by March", db_path=db
    )

    assert len(store.list_facts(spender, kind=FactKind.GOAL, db_path=db)) == 1


def test_facts_are_scoped_to_one_person(household: tuple[int, int], db: str) -> None:
    sponsor, spender = household
    store.upsert_fact(person_id=spender, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)

    assert store.list_facts(sponsor, db_path=db) == []


# --- turns -------------------------------------------------------------------


def test_turns_read_forwards_even_though_the_limit_takes_from_the_end(
    household: tuple[int, int], db: str
) -> None:
    _, spender = household
    for n in range(5):
        store.insert_turn(person_id=spender, speaker="spender", text=f"m{n}", db_path=db)

    recent = store.recent_turns(spender, limit=3, db_path=db)

    assert [t["text"] for t in recent] == ["m2", "m3", "m4"]


def test_the_sponsor_sees_only_what_was_shared(household: tuple[int, int], db: str) -> None:
    """The default is private. A sponsor sees decisions, ledger and
    escalations — never the chat, unless the spender opted a turn in."""
    _, spender = household
    store.insert_turn(person_id=spender, speaker="spender", text="private thing", db_path=db)
    store.insert_turn(
        person_id=spender,
        speaker="spender",
        text="can I have £40 for a coat",
        shared_with_sponsor=True,
        db_path=db,
    )

    shared = store.shared_turns(spender, db_path=db)

    assert [t["text"] for t in shared] == ["can I have £40 for a coat"]


# --- audit -------------------------------------------------------------------


def test_a_run_opens_then_finishes(household: tuple[int, int], db: str) -> None:
    _, spender = household
    run_id = store.insert_agent_run(
        person_id=spender, trigger_kind="ask", question="q", model="gpt-5", db_path=db
    )

    opened = store.get_agent_run(run_id, db_path=db)
    assert opened is not None
    assert opened["answer"] == ""  # the row exists before the model is asked

    store.finish_agent_run(
        run_id,
        answer="a",
        tools_used="recall_facts",
        tokens_in=1,
        tokens_out=2,
        latency_ms=3,
        db_path=db,
    )
    finished = store.get_agent_run(run_id, db_path=db)
    assert finished is not None
    assert finished["answer"] == "a"
    assert finished["tools_used"] == "recall_facts"


def test_finishing_a_run_that_does_not_exist_raises(db: str) -> None:
    with pytest.raises(store.NotFoundError):
        store.finish_agent_run(
            404, answer="", tools_used="", tokens_in=0, tokens_out=0, latency_ms=0, db_path=db
        )
