"""Nothing a model guessed becomes a belief until a person says so.

A street address written in prose has no syntax any redaction pattern can
catch, so the defence against one reaching the frontier model is that nobody
guessed it into memory unattended. Inferred facts land *pending*: invisible to
`recall_facts`, and therefore to the model, until confirmed.

The tests that matter most are the ones asserting invisibility, and the one
asserting that no tool can confirm — an agent confirming its own guesses would
turn the whole mechanism into a formality.
"""

from __future__ import annotations

import json

import pytest

from steward import cli, store
from steward.agent import loop
from steward.agent.privacy import Redactor
from steward.agent.tools import ToolBox
from steward.extract import pipeline
from steward.extract.base import INFERRED, PARSED, Candidate
from steward.memory import recall
from steward.models import FactKind, Role

from .agent_stub import OpenAIStub, completion

ADDRESS = "deliver to 42 Wharf Lane, Salford M5 3EX"


@pytest.fixture
def person(db: str) -> int:
    return store.insert_person(name="Ana Whitfield", role=Role.SPENDER, db_path=db)


def guess(kind: str = FactKind.SCHEDULE, key: str = "boiler", value: str = "Thursday"):
    return pipeline.Extraction(
        candidates=[Candidate(kind=kind, key=key, value=value, source=INFERRED)],
        extractor="local_model",
    )


# --- landing pending ---------------------------------------------------------


def test_an_inferred_fact_lands_pending(person: int, db: str) -> None:
    written = pipeline.commit(person, guess(), db_path=db)

    assert written[0]["pending"] is True
    assert store.list_facts(person, db_path=db) == []
    assert len(store.list_facts(person, pending=True, db_path=db)) == 1


def test_a_parsed_fact_does_not(person: int, db: str) -> None:
    """Every field a deterministic parser emits was chosen by hand, in code,
    and reviewed. There is nothing to confirm."""
    extraction = pipeline.Extraction(
        candidates=[Candidate(FactKind.SUPPLY, "soap", "out", PARSED)], extractor="ics"
    )

    written = pipeline.commit(person, extraction, db_path=db)

    assert written[0]["pending"] is False
    assert len(store.list_facts(person, db_path=db)) == 1


# --- invisibility ------------------------------------------------------------


def test_the_agent_cannot_see_a_pending_fact(person: int, db: str) -> None:
    pipeline.commit(person, guess(value=ADDRESS), db_path=db)
    box = ToolBox(person_id=person, redactor=Redactor.build(db_path=db), db_path=db)

    result = box.dispatch("recall_facts", {})

    assert result["count"] == 0
    assert "Wharf Lane" not in json.dumps(result)


def test_a_pending_fact_reaches_no_request_body(person: int, db: str) -> None:
    """The whole point, end to end."""
    pipeline.commit(person, guess(value=ADDRESS), db_path=db)
    stub = OpenAIStub(
        [completion(tool_calls=[("recall_facts", {})]), completion(content="nothing yet")]
    )

    loop.run("what do you know?", person_id=person, db_path=db, http=stub.client())

    assert "Wharf Lane" not in stub.bodies()


def test_no_tool_can_confirm_a_fact(person: int, db: str) -> None:
    """An agent confirming its own guesses would make this a formality."""
    box = ToolBox(person_id=person, redactor=Redactor.build(db_path=db), db_path=db)

    names = {spec["function"]["name"] for spec in box.specs()}

    assert not any("confirm" in name for name in names)
    assert "error" in box.dispatch("confirm_fact", {"fact_id": 1})


# --- a proposal must never displace a belief ---------------------------------


def test_a_guess_does_not_evict_what_the_person_actually_said(person: int, db: str) -> None:
    """Otherwise a model that guessed wrong would silently replace something
    they told us with a proposal they cannot even see until they go looking."""
    store.upsert_fact(
        person_id=person, kind=FactKind.SCHEDULE, key="boiler", value="Friday", db_path=db
    )

    pipeline.commit(person, guess(value="Thursday"), db_path=db)

    believed = store.list_facts(person, db_path=db)
    assert [fact["value"] for fact in believed] == ["Friday"]
    assert [f["value"] for f in store.list_facts(person, pending=True, db_path=db)] == ["Thursday"]


def test_restating_something_directly_clears_the_proposal_about_it(person: int, db: str) -> None:
    """The person has now said what is true; there is nothing left to ask."""
    pipeline.commit(person, guess(value="Thursday"), db_path=db)

    store.upsert_fact(
        person_id=person, kind=FactKind.SCHEDULE, key="boiler", value="Friday", db_path=db
    )

    assert store.list_facts(person, pending=True, db_path=db) == []
    assert store.list_facts(person, db_path=db)[0]["value"] == "Friday"


def test_re_extracting_replaces_the_proposal_rather_than_stacking_them(
    person: int, db: str
) -> None:
    for _ in range(3):
        pipeline.commit(person, guess(), db_path=db)

    assert len(store.list_facts(person, pending=True, db_path=db)) == 1


# --- confirming --------------------------------------------------------------


def test_confirming_promotes_it_to_a_belief(person: int, db: str) -> None:
    fact_id = pipeline.commit(person, guess(), db_path=db)[0]["fact_id"]

    store.confirm_fact(fact_id, db_path=db)

    believed = store.list_facts(person, db_path=db)
    assert len(believed) == 1
    assert believed[0]["source"] == "stated"  # it now carries the person's authority
    assert store.list_facts(person, pending=True, db_path=db) == []


def test_confirmation_keeps_the_provenance_promotion_would_erase(person: int, db: str) -> None:
    """A `stated` fact with a confirmation time was read by a machine and agreed
    to, which is not quite the same as one the person typed themselves."""
    fact_id = pipeline.commit(person, guess(), db_path=db)[0]["fact_id"]
    store.confirm_fact(fact_id, db_path=db)

    view = recall.everything(person, db_path=db)["facts"][0]

    assert view["source"] == "stated"
    assert view["confirmed"] is True
    # A directly-stated fact is distinguishable from a confirmed one.
    store.upsert_fact(person_id=person, kind=FactKind.MOOD, key="today", value="ok", db_path=db)
    typed = next(f for f in recall.everything(person, db_path=db)["facts"] if f["key"] == "today")
    assert typed["confirmed"] is False


def test_confirming_supersedes_the_belief_it_replaces(person: int, db: str) -> None:
    store.upsert_fact(
        person_id=person, kind=FactKind.SCHEDULE, key="boiler", value="Friday", db_path=db
    )
    fact_id = pipeline.commit(person, guess(value="Thursday"), db_path=db)[0]["fact_id"]

    store.confirm_fact(fact_id, db_path=db)

    believed = store.list_facts(person, db_path=db)
    assert [fact["value"] for fact in believed] == ["Thursday"]


def test_confirming_twice_is_an_error(person: int, db: str) -> None:
    fact_id = pipeline.commit(person, guess(), db_path=db)[0]["fact_id"]
    store.confirm_fact(fact_id, db_path=db)

    with pytest.raises(store.NotFoundError, match="no pending fact"):
        store.confirm_fact(fact_id, db_path=db)


def test_confirming_a_belief_is_an_error(person: int, db: str) -> None:
    fact_id = store.upsert_fact(
        person_id=person, kind=FactKind.MOOD, key="today", value="ok", db_path=db
    )

    with pytest.raises(store.NotFoundError, match="no pending fact"):
        store.confirm_fact(fact_id, db_path=db)


def test_confirming_a_deleted_proposal_is_an_error(person: int, db: str) -> None:
    fact_id = pipeline.commit(person, guess(), db_path=db)[0]["fact_id"]
    store.delete_fact(fact_id, db_path=db)

    with pytest.raises(store.NotFoundError):
        store.confirm_fact(fact_id, db_path=db)


def test_confirming_another_persons_proposal_is_refused(person: int, db: str) -> None:
    stranger = store.insert_person(name="Someone Else", role=Role.SPENDER, db_path=db)
    theirs = pipeline.commit(stranger, guess(), db_path=db)[0]["fact_id"]

    with pytest.raises(store.NotFoundError, match="belonging to you"):
        recall.confirm(theirs, person_id=person, db_path=db)

    assert len(store.list_facts(stranger, pending=True, db_path=db)) == 1


# --- the person's surface ----------------------------------------------------


def test_the_cli_shows_proposals_separately_from_beliefs(
    db: str, person: int, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")  # confirmation never needs the model
    store.upsert_fact(person_id=person, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)
    pipeline.commit(person, guess(value=ADDRESS), db_path=db)

    cli.main(["--db", db, "memory", "list"])

    out = capsys.readouterr().out
    assert "waiting for you" in out
    assert "nothing sees them until you say so" in out
    assert ADDRESS in out  # the person can read it; the model cannot


def test_the_cli_confirms(db: str, person: int, capsys: pytest.CaptureFixture[str]) -> None:
    fact_id = pipeline.commit(person, guess(), db_path=db)[0]["fact_id"]

    assert cli.main(["--db", db, "memory", "confirm", "--fact", str(fact_id)]) == 0

    assert "confirmed" in capsys.readouterr().out
    assert len(store.list_facts(person, db_path=db)) == 1


def test_rejecting_is_just_forgetting(db: str, person: int) -> None:
    fact_id = pipeline.commit(person, guess(), db_path=db)[0]["fact_id"]

    assert cli.main(["--db", db, "memory", "forget", "--fact", str(fact_id)]) == 0

    assert store.list_facts(person, pending=True, db_path=db) == []
    assert store.list_facts(person, db_path=db) == []


def test_confirming_something_that_does_not_exist_fails_loudly(db: str, person: int) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--db", db, "memory", "confirm", "--fact", "999"])
