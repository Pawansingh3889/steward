"""The loop and its tools — Phase 0's exit criterion.

"The agent answers from a fact store": the model is scripted, so what these
prove is that the loop reaches memory, gets an answer back, writes what it was
told, and audits all of it whatever happens.
"""

from __future__ import annotations

import json

import pytest

from steward import store
from steward.agent import llm, loop
from steward.agent.privacy import Redactor, pseudonym
from steward.agent.tools import ToolBox
from steward.models import FactKind, Role, Speaker

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
    assert names == {
        "recall_facts",
        "remember_fact",
        "forget_fact",
        "recent_conversation",
        "search_memory",
        "request_purchase",
        "find_options",
        "buy_offer",
        "propose_plan",
        "adjust_plan",
        "add_plan_item",
        "show_plans",
    }
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


# --- what a conversation leaves behind ---------------------------------------


def test_both_sides_are_logged_but_only_the_person_becomes_an_episode(
    db: str, spender: int
) -> None:
    """An agent that embeds its own output starts recalling its own guesses as
    though the person had said them."""
    stub = OpenAIStub([completion(content="I'll add soap to the list.")])

    loop.run("I'm out of soap", person_id=spender, db_path=db, http=stub.client())

    turns = store.recent_turns(spender, db_path=db)
    assert [(t["speaker"], t["text"]) for t in turns] == [
        (Speaker.PERSON, "I'm out of soap"),
        (Speaker.STEWARD, "I'll add soap to the list."),
    ]
    episodes = store.list_episodes(spender, db_path=db)
    assert [e["text"] for e in episodes] == ["I'm out of soap"]


def test_the_episode_keeps_the_raw_words_while_the_model_saw_a_pseudonym(
    db: str, spender: int
) -> None:
    """Redaction happens on the way to the model, not on the way to disk. Their
    own memory of their own life must not read back [redacted]."""
    stub = OpenAIStub([completion(content="noted")])

    loop.run("Ana Whitfield needs soap", person_id=spender, db_path=db, http=stub.client())

    assert store.list_episodes(spender, db_path=db)[0]["text"] == "Ana Whitfield needs soap"
    assert "Ana Whitfield" not in stub.bodies()


def test_the_episode_points_back_at_its_turn(db: str, spender: int) -> None:
    stub = OpenAIStub([completion(content="ok")])

    loop.run("I'm out of soap", person_id=spender, db_path=db, http=stub.client())

    turn = store.recent_turns(spender, db_path=db)[0]
    assert store.list_episodes(spender, db_path=db)[0]["turn_id"] == turn["id"]


def test_record_false_leaves_no_trace_in_memory(db: str, spender: int) -> None:
    """For callers asking on the person's behalf rather than relaying something
    they said."""
    stub = OpenAIStub([completion(content="ok")])

    result = loop.run(
        "is she out of anything?",
        person_id=spender,
        db_path=db,
        record=False,
        http=stub.client(),
    )

    assert store.recent_turns(spender, db_path=db) == []
    assert store.list_episodes(spender, db_path=db) == []
    # The audit row still exists — that is never optional.
    assert store.get_agent_run(result["run_id"], db_path=db) is not None


def test_a_failed_run_does_not_log_an_answer_that_never_happened(db: str, spender: int) -> None:
    stub = OpenAIStub(status=500)

    with pytest.raises(llm.AgentError):
        loop.run("I'm out of soap", person_id=spender, db_path=db, http=stub.client())

    # The question was said and is remembered; no reply was ever given.
    assert [t["speaker"] for t in store.recent_turns(spender, db_path=db)] == [Speaker.PERSON]


def test_the_agent_can_search_what_was_said_in_an_earlier_conversation(
    db: str, spender: int
) -> None:
    first = OpenAIStub([completion(content="noted")])
    loop.run("I'm completely out of soap", person_id=spender, db_path=db, http=first.client())

    second = OpenAIStub(
        [
            completion(tool_calls=[("search_memory", {"query": "soap"})]),
            completion(content="You mentioned soap."),
        ]
    )
    result = loop.run("what did I need?", person_id=spender, db_path=db, http=second.client())

    found = result["evidence"][0]["result"]["episodes"]
    assert found[0]["text"] == "I'm completely out of soap"


def test_search_memory_returns_nothing_rather_than_the_least_bad_match(
    db: str, spender: int
) -> None:
    box = ToolBox(person_id=spender, redactor=Redactor.build(db_path=db), db_path=db)

    assert box.dispatch("search_memory", {"query": "helicopters"}) == {
        "episodes": [],
        "count": 0,
    }


# --- what the model was sent -------------------------------------------------


def test_a_run_records_what_the_model_was_sent(db: str, spender: int) -> None:
    """The table and its writer both existed and nothing ever called one, so a
    design that was there on paper stored nothing at all."""
    model = OpenAIStub([completion(content="Noted.")])

    result = loop.run("I'm out of soap", person_id=spender, db_path=db, http=model.client())

    saved = store.get_agent_transcript(result["run_id"], db_path=db)
    assert saved is not None
    messages = json.loads(saved["messages"])
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_the_transcript_holds_the_redacted_view_not_the_persons_words(
    db: str, spender: int
) -> None:
    """`turns` keeps what they said, on their own machine, unredacted. This keeps
    what the model was given — which is where other people are pseudonyms. Two
    records, deliberately, so that answering "why did it decide that" never
    needs a second copy of anybody's raw conversation."""
    model = OpenAIStub([completion(content="Noted.")])

    result = loop.run(
        "Rae Whitfield asked me to get soap", person_id=spender, db_path=db, http=model.client()
    )

    sent = json.loads(store.get_agent_transcript(result["run_id"], db_path=db)["messages"])
    to_model = " ".join(str(m.get("content") or "") for m in sent)
    assert "Rae Whitfield" not in to_model
    assert pseudonym(1) in to_model
    # And the person's own words survive intact where they belong.
    assert any("Rae Whitfield" in row["text"] for row in store.recent_turns(spender, db_path=db))


def test_a_run_that_was_asked_not_to_be_remembered_stores_no_transcript(
    db: str, spender: int
) -> None:
    """`record=False` means it. A caller that asked not to be remembered did not
    mean "except the transcript"."""
    model = OpenAIStub([completion(content="Noted.")])

    result = loop.run(
        "I'm out of soap", person_id=spender, db_path=db, http=model.client(), record=False
    )

    assert store.get_agent_transcript(result["run_id"], db_path=db) is None


def test_forgetting_what_was_said_reaches_the_transcript_too(db: str, spender: int) -> None:
    """The sentence is in both places. Tombstoning the episode and leaving the
    transcript would make "forget that" true of memory and false of disk — the
    half-deletion this whole project exists not to do."""
    from steward.memory import recall

    model = OpenAIStub([completion(content="Noted.")])
    result = loop.run("I'm out of soap", person_id=spender, db_path=db, http=model.client())
    episode = store.list_episodes(spender, db_path=db)[0]
    assert store.get_agent_transcript(result["run_id"], db_path=db) is not None

    recall.forget(recall.EPISODE, int(episode["id"]), person_id=spender, db_path=db)

    assert store.get_agent_transcript(result["run_id"], db_path=db) is None
    assert store.list_agent_transcripts(spender, db_path=db) == []


def test_forgetting_one_run_leaves_the_others(db: str, spender: int) -> None:
    """A tombstone is for the thing asked about, not the conversation."""
    from steward.memory import recall

    model = OpenAIStub([completion(content="ok"), completion(content="ok")])
    first = loop.run("out of soap", person_id=spender, db_path=db, http=model.client())
    second = loop.run("out of rice", person_id=spender, db_path=db, http=model.client())
    soap = next(e for e in store.list_episodes(spender, db_path=db) if "soap" in e["text"])

    recall.forget(recall.EPISODE, int(soap["id"]), person_id=spender, db_path=db)

    assert store.get_agent_transcript(first["run_id"], db_path=db) is None
    assert store.get_agent_transcript(second["run_id"], db_path=db) is not None
