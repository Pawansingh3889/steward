"""The privacy boundary, from both sides.

These tests are the reason the boundary is one file with one assert: they can
name every value that must not cross and check the actual outgoing bytes.
"""

from __future__ import annotations

import pytest

from steward import config, store
from steward.agent import llm
from steward.agent.privacy import REDACTED, Redactor, pseudonym
from steward.models import Role

from .agent_stub import OpenAIStub, completion


@pytest.fixture
def people(db: str) -> tuple[int, int]:
    sponsor = store.insert_person(
        name="Rae Whitfield",
        role=Role.SPONSOR,
        phone="+447700900001",
        email="rae@example.com",
        db_path=db,
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


def test_names_become_pseudonyms(people: tuple[int, int], db: str) -> None:
    _, spender = people
    redactor = Redactor.build(db_path=db)

    assert redactor.redact("Ana Whitfield needs soap") == f"{pseudonym(spender)} needs soap"


def test_a_first_name_is_not_half_replaced(people: tuple[int, int], db: str) -> None:
    """Aliases are applied longest-first. Replacing "Ana" before "Ana Whitfield"
    would leave "person_2 Whitfield" — the surname still on the wire."""
    _, spender = people
    redactor = Redactor.build(db_path=db)

    out = redactor.redact("Ana Whitfield")

    assert out == pseudonym(spender)
    assert "Whitfield" not in out


def test_phones_and_emails_do_not_survive(people: tuple[int, int], db: str) -> None:
    redactor = Redactor.build(db_path=db)

    out = redactor.redact("reach me on +447700900002 or ana@example.com")

    assert "+447700900002" not in out
    assert "ana@example.com" not in out


def test_an_unknown_phone_is_still_caught(db: str) -> None:
    """The alias list only knows people in the database. A number pasted into a
    message belongs to someone who never enrolled — and is still a person."""
    redactor = Redactor.build(db_path=db)

    assert "+447442382622" not in redactor.redact("call the shop on +447442382622")


def test_coordinates_never_cross(db: str) -> None:
    """The plan's promise: the model is told "arrives in two days", never where
    the person lives. Location is processed on the device; this is the backstop."""
    redactor = Redactor.build(db_path=db)

    assert redactor.redact("device at 51.5074, -0.1278") == f"device at {REDACTED}"


def test_a_card_number_never_crosses(db: str) -> None:
    redactor = Redactor.build(db_path=db)

    assert "4242424242424242" not in redactor.redact("card 4242424242424242")


def test_secrets_are_removed_not_pseudonymised(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """There is nothing to reason about in a key, only something to leak."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-abc123")
    redactor = Redactor.build(db_path=db)

    assert redactor.redact("key is sk-live-abc123") == f"key is {REDACTED}"


def test_tool_results_are_redacted_at_every_depth(people: tuple[int, int], db: str) -> None:
    """An enrolled person's own identifiers pseudonymise (the model can reason
    about "person_2 again"); a stranger's are removed outright, because there is
    no continuity to preserve and nothing to reason about."""
    _, spender = people
    redactor = Redactor.build(db_path=db)

    out = redactor.redact_value(
        {"facts": [{"value": "Ana Whitfield", "note": ["+447700900002", "+447442382622"]}]}
    )

    assert out == {"facts": [{"value": pseudonym(spender), "note": [pseudonym(spender), REDACTED]}]}


def test_the_reader_gets_their_own_name_back_and_nobody_elses(
    people: tuple[int, int], db: str
) -> None:
    sponsor, spender = people
    redactor = Redactor.build(db_path=db)
    safe = redactor.redact("Ana Whitfield asked Rae Whitfield for £40")

    shown = redactor.restore_for(safe, spender)

    assert "Ana Whitfield" in shown
    assert "Rae Whitfield" not in shown  # the sponsor stays a pseudonym
    assert pseudonym(sponsor) in shown


def test_the_denylist_carries_every_literal_that_must_not_appear(
    people: tuple[int, int], db: str
) -> None:
    denylist = Redactor.build(db_path=db).denylist()

    assert "Ana Whitfield" in denylist
    assert "+447700900002" in denylist
    assert "ana@example.com" in denylist
    assert config.openai_api_key() in denylist


# --- the send-time assert ----------------------------------------------------


def test_the_send_refuses_when_a_denylisted_value_reached_the_body(db: str) -> None:
    """The guarantee. Every layer above can have a bug; this is where it stops
    being a leak and starts being a loud error."""
    stub = OpenAIStub([completion(content="hi")])

    with pytest.raises(llm.AgentError, match="redaction failure"):
        llm.complete(
            [{"role": "user", "content": "Ana Whitfield"}],
            denylist=("Ana Whitfield",),
            http=stub.client(),
        )

    assert stub.requests == []  # it refused before sending, not after


def test_the_assert_sees_through_nesting(db: str) -> None:
    """It runs on the serialized payload, so no depth of structure hides a value."""
    stub = OpenAIStub([completion(content="hi")])

    with pytest.raises(llm.AgentError, match="redaction failure"):
        llm.complete(
            [
                {"role": "user", "content": "fine"},
                {"role": "tool", "content": '{"deep": {"deeper": ["+447700900002"]}}'},
            ],
            denylist=("+447700900002",),
            http=stub.client(),
        )

    assert stub.requests == []


def test_a_clean_body_goes_through(db: str) -> None:
    stub = OpenAIStub([completion(content="hello")])

    reply = llm.complete(
        [{"role": "user", "content": "person_2 needs soap"}],
        denylist=("Ana Whitfield",),
        http=stub.client(),
    )

    assert reply.content == "hello"
    assert len(stub.requests) == 1
