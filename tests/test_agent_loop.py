"""The loop and its tools — Phase 0's exit criterion.

"The agent answers from a fact store": the model is scripted, so what these
prove is that the loop reaches memory, gets an answer back, writes what it was
told, and audits all of it whatever happens.
"""

from __future__ import annotations

import pytest

from steward import store
from steward.agent import llm, loop
from steward.agent.privacy import Redactor, pseudonym
from steward.agent.tools import ToolBox
from steward.models import FactKind, Role

from .agent_stub import OpenAIStub, completion


@pytest.fixture
def spender(db: str) -> int:
    sponsor = store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, db_path=db)
    return store.insert_person(
        name="Ana Whitfield",
        role=Role.SPENDER,
        sponsor_id=sponsor,
        phone="+447700900002",
        db_path=db,
    )


@pytest.fixture
def box(db: str, spender: int) -> ToolBox:
    return ToolBox(person_id=spender, redactor=Redactor.build(db_path=db), db_path=db)


# --- the ToolBox -------------------------------------------------------------


def test_every_tool_is_described(box: ToolBox) -> None:
    """The descriptions are the model's only documentation."""
    specs = box.specs()

    names = {spec["function"]["name"] for spec in specs}
    assert names == {"recall_facts", "remember_fact", "forget_fact", "recent_conversation"}
    assert all(spec["function"]["description"] for spec in specs)


def test_no_tool_takes_a_person_id(box: ToolBox) -> None:
    """The scope property, asserted rather than trusted: the model cannot name
    another person, so it cannot reach one. A system prompt is not a permission
    system, and this is what keeps it from having to be."""
    for spec in box.specs():
        assert "person_id" not in spec["function"]["parameters"]["properties"]


def test_recall_returns_what_was_stored(box: ToolBox, spender: int, db: str) -> None:
    store.upsert_fact(person_id=spender, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)

    result = box.dispatch("recall_facts", {})

    assert result["count"] == 1
    assert result["facts"][0]["key"] == "soap"
    assert result["facts"][0]["source"] == "stated"


def test_remember_then_recall(box: ToolBox) -> None:
    box.dispatch(
        "remember_fact", {"kind": FactKind.SCHEDULE, "key": "hours", "value": "9-5 weekdays"}
    )

    recalled = box.dispatch("recall_facts", {"kind": FactKind.SCHEDULE})

    assert recalled["facts"][0]["value"] == "9-5 weekdays"
    assert box.writes_log == [{"action": "remember", "kind": FactKind.SCHEDULE, "key": "hours"}]


def test_an_unknown_fact_kind_is_refused(box: ToolBox) -> None:
    result = box.dispatch("remember_fact", {"kind": "vibes", "key": "k", "value": "v"})

    assert "unknown kind" in result["error"]


def test_forget_removes_it_from_recall(box: ToolBox) -> None:
    stored = box.dispatch(
        "remember_fact", {"kind": FactKind.MOOD, "key": "today", "value": "stressed"}
    )

    box.dispatch("forget_fact", {"fact_id": stored["fact_id"]})

    assert box.dispatch("recall_facts", {})["count"] == 0


def test_forgetting_another_persons_fact_is_refused(box: ToolBox, db: str) -> None:
    """A guessed id must not become a cross-person read or delete."""
    stranger = store.insert_person(name="Someone Else", role=Role.SPENDER, db_path=db)
    theirs = store.upsert_fact(
        person_id=stranger, kind=FactKind.MOOD, key="today", value="private", db_path=db
    )

    result = box.dispatch("forget_fact", {"fact_id": theirs})

    assert "belonging to you" in result["error"]
    assert store.list_facts(stranger, db_path=db)[0]["value"] == "private"


def test_a_bad_argument_is_reported_not_raised(box: ToolBox) -> None:
    """The model corrects course on the next turn; the run does not die."""
    result = box.dispatch("recall_facts", {"nonsense": 1})

    assert "bad arguments" in result["error"]


def test_an_unknown_tool_is_reported(box: ToolBox) -> None:
    assert "unknown tool" in box.dispatch("buy_a_boat", {})["error"]


def test_tool_results_pass_through_the_redactor(box: ToolBox) -> None:
    """A tool that reads a raw stored value cannot hand it to the model
    unredacted just by forgetting to — dispatch does it for every tool."""
    box.dispatch(
        "remember_fact",
        {"kind": FactKind.IDENTITY, "key": "mum", "value": "reach Rae on +447700900001"},
    )

    result = box.dispatch("recall_facts", {})

    assert "+447700900001" not in str(result)


# --- the loop ----------------------------------------------------------------


def test_the_agent_answers_from_the_fact_store(db: str, spender: int) -> None:
    """Phase 0's exit criterion, end to end."""
    store.upsert_fact(person_id=spender, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)
    stub = OpenAIStub(
        [
            completion(tool_calls=[("recall_facts", {})]),
            completion(content="You said you're out of soap."),
        ]
    )

    result = loop.run(
        "what do you know about me?", person_id=spender, db_path=db, http=stub.client()
    )

    assert result["answer"] == "You said you're out of soap."
    assert [e["tool"] for e in result["evidence"]] == ["recall_facts"]
    # The tool result the model saw actually carried the stored fact.
    assert result["evidence"][0]["result"]["facts"][0]["key"] == "soap"


def test_what_the_person_said_is_remembered_across_runs(db: str, spender: int) -> None:
    first = OpenAIStub(
        [
            completion(
                tool_calls=[
                    ("remember_fact", {"kind": FactKind.SUPPLY, "key": "soap", "value": "out"})
                ]
            ),
            completion(content="Noted."),
        ]
    )
    loop.run("I'm out of soap", person_id=spender, db_path=db, http=first.client())

    second = OpenAIStub(
        [completion(tool_calls=[("recall_facts", {})]), completion(content="Soap.")]
    )
    result = loop.run("what am I out of?", person_id=spender, db_path=db, http=second.client())

    assert result["evidence"][0]["result"]["facts"][0]["value"] == "out"


def test_the_question_is_redacted_before_it_is_sent_or_stored(db: str, spender: int) -> None:
    stub = OpenAIStub([completion(content="ok")])

    result = loop.run(
        "I'm Ana Whitfield, call me on +447700900002",
        person_id=spender,
        db_path=db,
        http=stub.client(),
    )

    sent = stub.bodies()
    assert "Ana Whitfield" not in sent
    assert "+447700900002" not in sent
    assert pseudonym(spender) in sent
    # And the same for what landed in the audit trail.
    row = store.get_agent_run(result["run_id"], db_path=db)
    assert row is not None
    assert "Ana Whitfield" not in row["question"]


def test_the_reader_sees_their_own_name_in_the_displayed_answer(db: str, spender: int) -> None:
    stub = OpenAIStub([completion(content=f"Hello {pseudonym(spender)}.")])

    result = loop.run("hi", person_id=spender, db_path=db, http=stub.client())

    assert result["answer"] == f"Hello {pseudonym(spender)}."  # stored pseudonymous
    assert result["display_answer"] == "Hello Ana Whitfield."  # shown to them, personal


def test_the_run_is_audited_with_tools_and_usage(db: str, spender: int) -> None:
    stub = OpenAIStub(
        [completion(tool_calls=[("recall_facts", {})]), completion(content="nothing yet")]
    )

    result = loop.run("what do you know?", person_id=spender, db_path=db, http=stub.client())

    row = store.get_agent_run(result["run_id"], db_path=db)
    assert row is not None
    assert row["answer"] == "nothing yet"
    assert row["tools_used"] == "recall_facts"
    assert row["tokens_in"] == 20  # both turns counted
    assert row["model"] == "gpt-5"


def test_a_crashed_run_still_lands_an_audit_row_saying_why(db: str, spender: int) -> None:
    """Two runs on payoptimize's first live sweep landed blank. "The agent was
    invoked and something went wrong" is not an audit trail."""
    stub = OpenAIStub(status=500)

    with pytest.raises(llm.AgentError):
        loop.run("anything", person_id=spender, db_path=db, http=stub.client())

    row = store.recent_agent_runs(limit=1, db_path=db)[0]
    assert row["answer"].startswith(loop.FAILED_PREFIX)
    assert "500" in row["answer"]


def test_the_loop_stops_at_its_tool_budget(db: str, spender: int) -> None:
    """A model that never stops calling tools must not run forever."""
    stub = OpenAIStub([completion(tool_calls=[("recall_facts", {})])] * (loop.MAX_TURNS + 5))

    result = loop.run("loop forever", person_id=spender, db_path=db, http=stub.client())

    assert result["answer"] == loop.CAP_ANSWER
    assert len(result["evidence"]) == loop.MAX_TURNS


def test_an_unconfigured_agent_says_so(db: str, spender: int, monkeypatch) -> None:
    """Unset is a normal install: everything that is not the model still works."""
    monkeypatch.setenv("OPENAI_API_KEY", "")

    with pytest.raises(llm.AgentError, match="cannot think"):
        loop.run("hi", person_id=spender, db_path=db)
