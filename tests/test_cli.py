"""The CLI — specifically, that correcting your memory never needs the model.

If deletion only worked through the agent, deletion would be a request rather
than a guarantee. Several of these tests unset OPENAI_API_KEY on purpose.
"""

from __future__ import annotations

import json

import pytest

from steward import cli, store
from steward.memory import episodic
from steward.models import FactKind, Role


@pytest.fixture
def person(db: str) -> int:
    return store.insert_person(
        name="Ana Whitfield", role=Role.SPENDER, phone="+447700900002", db_path=db
    )


def run(db: str, *argv: str) -> int:
    return cli.main(["--db", db, *argv])


# --- people ------------------------------------------------------------------


def test_enrolling_and_listing(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(db, "people", "add", "--name", "Rae", "--role", "sponsor") == 0
    run(db, "people", "list")

    assert "Rae" in capsys.readouterr().out


def test_an_invalid_role_is_refused(db: str) -> None:
    with pytest.raises(SystemExit, match="role must be one of"):
        run(db, "people", "add", "--name", "Rae", "--role", "landlord")


# --- resolving who a command is about ----------------------------------------


def test_a_single_person_needs_no_flag(db: str, person: int, capsys) -> None:
    assert run(db, "memory", "list") == 0
    assert "Ana Whitfield" in capsys.readouterr().out


def test_two_people_force_an_explicit_choice(db: str, person: int) -> None:
    """This default is right until the day a household has two people in it,
    and then it silently shows one of them the other's memory."""
    store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, db_path=db)

    with pytest.raises(SystemExit, match="which person"):
        run(db, "memory", "list")


def test_a_person_can_be_found_by_phone(db: str, person: int, capsys) -> None:
    store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, db_path=db)

    assert run(db, "memory", "list", "--phone", "+447700900002") == 0
    assert "Ana Whitfield" in capsys.readouterr().out


def test_an_unknown_person_id_fails_loudly(db: str, person: int) -> None:
    with pytest.raises(SystemExit, match="no person with id 999"):
        run(db, "memory", "list", "--person", "999")


def test_an_empty_database_says_what_to_do(db: str) -> None:
    with pytest.raises(SystemExit, match="nobody is enrolled"):
        run(db, "memory", "list")


# --- memory, with no model configured ----------------------------------------


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of these tests: memory works with the agent unavailable."""
    monkeypatch.setenv("OPENAI_API_KEY", "")


def test_listing_memory_without_a_model(
    db: str, person: int, no_model: None, capsys: pytest.CaptureFixture[str]
) -> None:
    store.upsert_fact(person_id=person, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)
    episodic.remember(person_id=person, text="I'm out of soap again", db_path=db)

    assert run(db, "memory", "list") == 0

    out = capsys.readouterr().out
    assert "soap" in out
    assert "I'm out of soap again" in out


def test_forgetting_a_fact_without_a_model(db: str, person: int, no_model: None) -> None:
    fact_id = store.upsert_fact(
        person_id=person, kind=FactKind.MOOD, key="today", value="stressed", db_path=db
    )

    assert run(db, "memory", "forget", "--fact", str(fact_id)) == 0

    assert store.list_facts(person, db_path=db) == []


def test_forgetting_an_episode_without_a_model(db: str, person: int, no_model: None) -> None:
    episode_id = episodic.remember(person_id=person, text="I'm out of soap", db_path=db)
    assert episode_id is not None

    assert run(db, "memory", "forget", "--episode", str(episode_id)) == 0

    assert store.list_episodes(person, db_path=db) == []


def test_forget_needs_exactly_one_target(db: str, person: int) -> None:
    with pytest.raises(SystemExit, match="exactly one"):
        run(db, "memory", "forget")
    with pytest.raises(SystemExit, match="exactly one"):
        run(db, "memory", "forget", "--fact", "1", "--episode", "2")


def test_forgetting_something_already_gone_fails_loudly(db: str, person: int) -> None:
    """Reporting success without doing anything is the failure mode this
    command cannot have."""
    fact_id = store.upsert_fact(
        person_id=person, kind=FactKind.MOOD, key="today", value="fine", db_path=db
    )
    run(db, "memory", "forget", "--fact", str(fact_id))

    with pytest.raises(SystemExit):
        run(db, "memory", "forget", "--fact", str(fact_id))


def test_stating_a_fact_directly(db: str, person: int, no_model: None) -> None:
    """Correcting the agent without having to talk it round."""
    assert (
        run(db, "memory", "add", "--kind", FactKind.SCHEDULE, "--key", "hours", "--value", "9-5")
        == 0
    )

    assert store.list_facts(person, db_path=db)[0]["value"] == "9-5"


def test_an_invalid_fact_kind_is_refused(db: str, person: int) -> None:
    with pytest.raises(SystemExit, match="kind must be one of"):
        run(db, "memory", "add", "--kind", "vibes", "--key", "k", "--value", "v")


def test_searching_memory(db: str, person: int, capsys: pytest.CaptureFixture[str]) -> None:
    episodic.remember(person_id=person, text="I'm out of soap again", db_path=db)

    assert run(db, "memory", "search", "soap") == 0
    assert "soap" in capsys.readouterr().out


def test_a_search_that_finds_nothing_says_so(
    db: str, person: int, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(db, "memory", "search", "helicopters") == 0
    assert "never said" in capsys.readouterr().out


def test_reindexing_reports_which_embedder_actually_ran(
    db: str, person: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """The interesting failure is a silent fallback to the lexical matcher when
    Ollama was meant to be answering, so the command says which one it used and
    why — naming the refusal, not a generic "unreachable"."""
    episodic.remember(person_id=person, text="I'm out of soap again", db_path=db)

    assert run(db, "memory", "reindex") == 0
    out = capsys.readouterr().out
    assert "reindexed 1 episode(s)" in out
    assert "HashingEmbedder" in out
    # Unset, so no fallback notice: this is a working install, not a degraded one.
    assert "fell back" not in out


def test_reindexing_says_why_it_fell_back_to_the_lexical_matcher(
    db: str, person: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STEWARD_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("OLLAMA_BASE", "http://192.168.1.50:11434")
    episodic.remember(person_id=person, text="I'm out of soap again", db_path=db)

    assert run(db, "memory", "reindex") == 0

    out = capsys.readouterr().out
    assert "fell back" in out
    assert "not on this machine" in out


def test_a_reindex_that_gives_out_part_way_exits_rather_than_tracing_back(
    db: str, person: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other failing command in this file exits with a message. This one
    also has to say how far it got, because it stops with the store half
    re-encoded and the person needs to know to run it again."""
    from steward.memory import embed

    episodic.remember(person_id=person, text="I'm out of soap again", db_path=db)

    def die(*args: object, **kwargs: object) -> None:
        raise embed.EmbeddingError("timed out")

    monkeypatch.setattr("steward.memory.embed.HashingEmbedder.encode", die)

    with pytest.raises(SystemExit, match="stopped after reindexing 0 of 1"):
        run(db, "memory", "reindex")


# --- colour ------------------------------------------------------------------


def _escapes(text: str) -> bool:
    return "\033" in text


def test_a_redirected_run_carries_no_escape_sequences(
    db: str, person: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """Colour used to be unconditional, so `steward memory list > notes.txt`
    wrote a file full of \\033[1m. capsys is not a terminal, which is exactly
    the case that was broken."""
    run(db, "memory", "list")

    assert not _escapes(capsys.readouterr().out)


def test_no_color_is_obeyed(
    db: str, person: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A standing answer the user gave once, for every program on the machine.
    It outranks a terminal being present, and outranks FORCE_COLOR."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")

    run(db, "memory", "list")

    assert not _escapes(capsys.readouterr().out)


def test_the_flag_wins_over_a_terminal(
    db: str, person: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    run(db, "--no-color", "memory", "list")

    assert not _escapes(capsys.readouterr().out)


def test_a_terminal_does_get_colour(
    db: str, person: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half: gating it off everywhere would be its own bug."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    run(db, "memory", "list")

    assert _escapes(capsys.readouterr().out)


def test_json_is_never_coloured_even_on_a_terminal(
    db: str, person: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Something downstream parses this. An escape sequence in front of a brace
    is a parse error waiting to happen."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    run(db, "--json", "memory", "list")

    out = capsys.readouterr().out
    assert not _escapes(out)
    json.loads(out)


def test_the_terminal_and_the_dashboard_agree_about_a_verdict() -> None:
    """One vocabulary, in models.py, because a verdict that reads amber on the
    dashboard and green in the terminal would be two."""
    from steward.models import tone_of
    from steward.web import render

    assert render.tone_of is tone_of
    assert tone_of("allowed") == "good"
    assert tone_of("needs_approval") == "wait"
    assert tone_of("denied") == "bad"
    # A legitimate answer somebody gave, so not red — and an unknown verdict is
    # never dressed up as a good one.
    assert tone_of("declined") == "flat"
    assert tone_of("something nobody has heard of") == render.UNRECOGNISED

    cli._set_palette(True)
    try:
        assert cli.tone("allowed") and cli.tone("denied")
        assert cli.tone("allowed") != cli.tone("denied")
        assert cli.tone("declined") == ""
        assert cli.tone("something nobody has heard of") == ""
    finally:
        cli._set_palette(False)


def test_json_output_is_machine_readable(
    db: str, person: int, capsys: pytest.CaptureFixture[str]
) -> None:
    store.upsert_fact(person_id=person, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)

    run(db, "--json", "memory", "list")

    held = json.loads(capsys.readouterr().out)
    assert held["counts"]["facts"] == 1


# --- ask ---------------------------------------------------------------------


def test_ask_without_a_key_explains_and_points_at_memory(
    db: str, person: int, no_model: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unconfigured agent is a normal install, not a broken one."""
    assert run(db, "ask", "what do you know about me?") == 2

    out = capsys.readouterr().out
    assert "OPENAI_API_KEY is unset" in out
    assert "steward memory list" in out
