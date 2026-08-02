"""Episodic memory and the unified recall layer.

Phase 1's exit criterion is that "what do you know about me?" is answerable
**and correctable** — so the deletion tests here matter more than the search
ones. Search being merely lexical is a known limit; deletion not working would
be a broken promise.
"""

from __future__ import annotations

import pytest

from steward import store
from steward.memory import embed, episodic, recall
from steward.models import FactKind, Role


@pytest.fixture
def person(db: str) -> int:
    return store.insert_person(name="Ana Whitfield", role=Role.SPENDER, db_path=db)


# --- the embedder ------------------------------------------------------------


def test_a_vector_is_stable_across_processes() -> None:
    """blake2b, not Python's hash(), which is salted per process. If this ever
    regresses, every stored vector silently stops matching after a restart."""
    embedder = embed.HashingEmbedder()

    assert embedder.encode("out of soap") == embed.HashingEmbedder().encode("out of soap")
    # The literal is the point: recomputing it with the same code proves nothing
    # about process-independence, but a fixed expectation catches a hash swap.
    assert embedder._bucket("soap") == embed.HashingEmbedder()._bucket("soap")


def test_vectors_are_unit_length() -> None:
    vector = embed.HashingEmbedder().encode("I am completely out of hand soap again")

    assert sum(value * value for value in vector) == pytest.approx(1.0)


def test_length_does_not_beat_relevance() -> None:
    """Without normalization a rambling episode outranks an exact one purely for
    having more tokens."""
    embedder = embed.HashingEmbedder()
    query = embedder.encode("soap")
    exact = embedder.encode("soap")
    rambling = embedder.encode("soap " + " ".join(f"word{n}" for n in range(200)))

    assert embed.similarity(query, exact) > embed.similarity(query, rambling)


def test_empty_text_yields_a_zero_vector_that_matches_nothing() -> None:
    embedder = embed.HashingEmbedder()

    assert embed.similarity(embedder.encode("the and of"), embedder.encode("soap")) == 0.0


def test_packing_round_trips() -> None:
    vector = embed.HashingEmbedder().encode("out of soap")

    restored = embed.unpack(embed.pack(vector))

    assert restored == pytest.approx(vector, abs=1e-6)


def test_comparing_different_dimensions_raises_rather_than_lying(db: str) -> None:
    with pytest.raises(ValueError, match="dimension"):
        embed.similarity([1.0, 0.0], [1.0, 0.0, 0.0])


# --- episodic ----------------------------------------------------------------


def test_an_episode_is_found_by_what_it_was_about(person: int, db: str) -> None:
    episodic.remember(person_id=person, text="I'm completely out of soap", db_path=db)
    episodic.remember(person_id=person, text="the boiler is making a noise", db_path=db)

    found = episodic.search(person_id=person, query="soap", db_path=db)

    assert len(found) == 1
    assert "soap" in found[0].text


def test_nothing_is_returned_when_nothing_resembles_the_query(person: int, db: str) -> None:
    """An agent handed a weak match treats it as evidence. "You mentioned you
    were out of soap" is worse than silence when they never did."""
    episodic.remember(person_id=person, text="the boiler is making a noise", db_path=db)

    assert episodic.search(person_id=person, query="soap", db_path=db) == []


def test_unindexable_text_is_not_stored(person: int, db: str) -> None:
    """It could never be retrieved, so storing it only pads the list a person
    sees when they ask what is held about them."""
    assert episodic.remember(person_id=person, text="   ", db_path=db) is None
    assert episodic.remember(person_id=person, text="the and of", db_path=db) is None

    assert store.list_episodes(person, db_path=db) == []


def test_an_acknowledgement_is_still_stored(person: int, db: str) -> None:
    """Documenting the limit rather than faking a fix: "ok" is a real token, and
    deciding it is not worth remembering is a judgement about meaning that
    belongs in phase 2's extraction layer, not in a widened stopword list."""
    assert episodic.remember(person_id=person, text="ok thanks", db_path=db) is not None


def test_episodes_are_scoped_to_one_person(person: int, db: str) -> None:
    stranger = store.insert_person(name="Someone Else", role=Role.SPENDER, db_path=db)
    episodic.remember(person_id=stranger, text="I'm out of soap", db_path=db)

    assert episodic.search(person_id=person, query="soap", db_path=db) == []


def test_a_forgotten_episode_stops_being_found(person: int, db: str) -> None:
    episode_id = episodic.remember(person_id=person, text="I'm out of soap", db_path=db)
    assert episode_id is not None

    episodic.forget(episode_id, db_path=db)

    assert episodic.search(person_id=person, query="soap", db_path=db) == []
    assert store.get_episode(episode_id, db_path=db)["deleted_ts"]  # tombstoned


def test_ties_break_toward_the_more_recent(person: int, db: str) -> None:
    """When two things were said with equal relevance, the later one is the
    current state of affairs."""
    first = episodic.remember(person_id=person, text="out of soap", db_path=db)
    second = episodic.remember(person_id=person, text="out of soap", db_path=db)

    found = episodic.search(person_id=person, query="out of soap", db_path=db)

    assert [e.episode_id for e in found] == [second, first]


def test_a_vector_from_another_embedder_is_skipped_not_fatal(person: int, db: str) -> None:
    """A model swap leaves rows behind. One stale row must not break search."""
    episodic.remember(person_id=person, text="I'm out of soap", db_path=db)
    store.insert_episode(
        person_id=person,
        text="written by a 4-dimension embedder",
        embedding=embed.pack([0.5, 0.5, 0.5, 0.5]),
        db_path=db,
    )

    found = episodic.search(person_id=person, query="soap", db_path=db)

    assert len(found) == 1
    assert "soap" in found[0].text


def test_search_respects_its_limit(person: int, db: str) -> None:
    for n in range(8):
        episodic.remember(person_id=person, text=f"I need soap batch {n}", db_path=db)

    assert len(episodic.search(person_id=person, query="soap", limit=3, db_path=db)) == 3


# --- recall: the unified view ------------------------------------------------


def test_everything_returns_both_kinds_of_memory(person: int, db: str) -> None:
    store.upsert_fact(person_id=person, kind=FactKind.SUPPLY, key="soap", value="out", db_path=db)
    episodic.remember(person_id=person, text="I'm out of soap again", db_path=db)

    held = recall.everything(person, db_path=db)

    assert held["counts"] == {"facts": 1, "pending": 0, "episodes": 1}
    assert held["facts"][0]["key"] == "soap"
    assert held["episodes"][0]["text"] == "I'm out of soap again"


def test_a_fact_says_where_it_came_from(person: int, db: str) -> None:
    """ "You told me" and "I worked it out" are different claims and must never
    be displayed as the same one."""
    store.upsert_fact(
        person_id=person,
        kind=FactKind.SCHEDULE,
        key="hours",
        value="9-5",
        source="parsed",
        db_path=db,
    )

    assert recall.everything(person, db_path=db)["facts"][0]["source"] == "parsed"


def test_forget_removes_a_fact_from_the_answer(person: int, db: str) -> None:
    fact_id = store.upsert_fact(
        person_id=person, kind=FactKind.MOOD, key="today", value="stressed", db_path=db
    )

    result = recall.forget(recall.FACT, fact_id, person_id=person, db_path=db)

    assert result["forgotten"] is True
    assert result["was"] == "today"
    assert recall.everything(person, db_path=db)["counts"]["facts"] == 0


def test_forget_removes_an_episode_from_the_answer(person: int, db: str) -> None:
    episode_id = episodic.remember(person_id=person, text="I'm out of soap", db_path=db)
    assert episode_id is not None

    recall.forget(recall.EPISODE, episode_id, person_id=person, db_path=db)

    assert recall.everything(person, db_path=db)["counts"]["episodes"] == 0


def test_forget_refuses_another_persons_memory(person: int, db: str) -> None:
    stranger = store.insert_person(name="Someone Else", role=Role.SPENDER, db_path=db)
    theirs = store.upsert_fact(
        person_id=stranger, kind=FactKind.MOOD, key="today", value="private", db_path=db
    )

    with pytest.raises(store.NotFoundError, match="belonging to you"):
        recall.forget(recall.FACT, theirs, person_id=person, db_path=db)

    assert store.list_facts(stranger, db_path=db)[0]["value"] == "private"


def test_forget_rejects_an_unknown_kind(person: int, db: str) -> None:
    with pytest.raises(ValueError, match="unknown memory kind"):
        recall.forget("secrets", 1, person_id=person, db_path=db)


def test_the_full_answer_is_never_truncated(person: int, db: str) -> None:
    """Someone asking what is held about them is owed all of it. A paginated
    answer to this particular question is a wrong one."""
    for n in range(40):
        episodic.remember(person_id=person, text=f"thing number {n} about soap", db_path=db)

    assert recall.everything(person, db_path=db)["counts"]["episodes"] == 40


# --- the local embedder ------------------------------------------------------


class FakeOllama:
    """A local model that knows soap and hand wash are the same errand.

    Deliberately not a real embedding model: what these tests are about is the
    wiring, the fallback and the width, none of which need a model to be right
    about language.
    """

    def __init__(self, dimensions: int = 8) -> None:
        self.dimensions = dimensions
        self.calls: list[str] = []

    def encode(self, text: str) -> list[float]:
        self.calls.append(text)
        soapy = any(word in text.lower() for word in ("soap", "hand wash", "washing"))
        vector = [1.0, 0.0] if soapy else [0.0, 1.0]
        return embed.normalize(vector + [0.0] * (self.dimensions - 2))


def test_a_local_model_matches_what_the_lexical_one_cannot(db: str, person: int) -> None:
    """The whole reason for this: "out of soap" and "need hand wash" share no
    tokens, so the hashing embedder scores them zero however good it is."""
    lexical = embed.HashingEmbedder()

    assert (
        embed.similarity(
            lexical.encode("I'm out of soap"), lexical.encode("need to restock hand wash")
        )
        == 0.0
    )

    model = FakeOllama()
    episodic.remember(person_id=person, text="I'm out of soap", embedder=model, db_path=db)

    found = episodic.search(
        person_id=person, query="need to restock hand wash", embedder=model, db_path=db
    )

    assert [e.text for e in found] == ["I'm out of soap"]


def test_vectors_of_different_widths_are_never_compared(db: str, person: int) -> None:
    """Written by the old embedder, searched with the new one. Skipping is the
    only honest answer — comparing them produces a confident, meaningless
    number, which is worse than finding nothing."""
    episodic.remember(
        person_id=person, text="I'm out of soap", embedder=embed.HashingEmbedder(), db_path=db
    )

    found = episodic.search(person_id=person, query="soap", embedder=FakeOllama(), db_path=db)

    assert found == []


def test_reindexing_makes_the_old_episodes_findable_again(db: str, person: int) -> None:
    """Which is why switching embedders is not complete without it. Silently
    losing somebody's history on a config change would be worse than asking."""
    episodic.remember(
        person_id=person, text="I'm out of soap", embedder=embed.HashingEmbedder(), db_path=db
    )
    model = FakeOllama()

    result = episodic.reindex(person, embedder=model, db_path=db)

    assert result["reindexed"] == 1
    assert result["dimensions"] == 8
    again = episodic.search(person_id=person, query="hand wash", embedder=model, db_path=db)
    assert [e.text for e in again] == ["I'm out of soap"]


def test_reindexing_re_encodes_and_never_rewrites_what_was_said(db: str, person: int) -> None:
    """It is a change of representation. The text is the person's."""
    episodic.remember(person_id=person, text="I'm out of soap", db_path=db)
    before = store.list_episodes(person, db_path=db)[0]

    episodic.reindex(person, embedder=FakeOllama(), db_path=db)

    after = store.list_episodes(person, db_path=db)[0]
    assert after["text"] == before["text"]
    assert after["embedding"] != before["embedding"]


def test_an_unset_model_means_the_lexical_matcher(monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_EMBEDDING_MODEL", "")

    assert isinstance(embed.build(), embed.HashingEmbedder)


def test_an_unreachable_local_model_falls_back_rather_than_failing(monkeypatch) -> None:
    """Recall is conversational colour. Taking a whole conversation down because
    Ollama is not running would be the wrong trade — and `memory reindex` is
    where somebody is actually asking, so that is where it is reported."""
    monkeypatch.setenv("STEWARD_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr("steward.extract.local.available", lambda **kwargs: False)

    assert isinstance(embed.build(), embed.HashingEmbedder)


def test_the_embedder_refuses_a_host_that_is_not_this_machine(monkeypatch) -> None:
    """It sends the same raw sentences the extractor does, so it answers to the
    same rule — reused rather than reimplemented, because a second copy is a
    second thing to get wrong."""
    from steward.extract import local

    monkeypatch.setenv("OLLAMA_BASE", "http://192.168.1.50:11434")
    monkeypatch.setenv("STEWARD_LOCAL_LLM_ALLOW_REMOTE", "")

    with pytest.raises(local.LocalModelError, match="STEWARD_LOCAL_LLM_ALLOW_REMOTE"):
        embed.OllamaEmbedder("nomic-embed-text")


def test_a_model_that_fails_mid_conversation_does_not_take_the_turn_down(
    db: str, person: int
) -> None:
    """Reachability is checked once, at construction. This is the other half: a
    resident model can still fail a single call, and the first real run of this
    hit exactly that — an 8B model cold-starting outran the timeout and the
    error came out through `remember`.

    A lexically-encoded episode is a narrower memory, not a lost one.
    """

    class ColdStart:
        dimensions = 8

        def encode(self, text: str) -> list[float]:
            raise embed.EmbeddingError("timed out")

    episodic.remember(person_id=person, text="I'm out of soap", embedder=ColdStart(), db_path=db)

    stored = store.list_episodes(person, db_path=db)
    assert [row["text"] for row in stored] == ["I'm out of soap"]
    # Encoded by the fallback, so it is findable the lexical way meanwhile.
    found = episodic.search(person_id=person, query="soap", db_path=db)
    assert [e.text for e in found] == ["I'm out of soap"]


def test_a_model_that_gives_out_stops_the_reindex_rather_than_mixing_widths(
    db: str, person: int
) -> None:
    """`remember` falls back to lexical mid-conversation; reindex must not. Two
    widths in one store means whichever set does not match the current embedder
    goes silently unsearchable — the exact state this command exists to repair.
    Stopping half-done and saying how far it got is the recoverable failure."""

    class DiesOnTheSecond:
        dimensions = 8

        def __init__(self) -> None:
            self.calls = 0

        def encode(self, text: str) -> list[float]:
            self.calls += 1
            if self.calls > 1:
                raise embed.EmbeddingError("timed out")
            return embed.normalize([1.0] + [0.0] * 7)

    for text in ("I'm out of soap", "the boiler man comes Thursday"):
        episodic.remember(person_id=person, text=text, db_path=db)

    with pytest.raises(embed.EmbeddingError, match="stopped after reindexing 1 of 2"):
        episodic.reindex(person, embedder=DiesOnTheSecond(), db_path=db)


def test_a_non_numeric_embedding_is_an_embedding_error_not_a_value_error() -> None:
    """Everything out of `encode` has to be EmbeddingError, because that is the
    single type callers fall back on. A bare ValueError would sail past
    `_encode` and take somebody's whole turn down."""
    import httpx

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"embedding": ["not", "a", "vector"]})
    )

    with pytest.raises(embed.EmbeddingError, match="non-numeric"):
        embed.OllamaEmbedder("nomic-embed-text", http=httpx.Client(transport=transport)).encode(
            "I'm out of soap"
        )


def test_a_refused_host_is_not_reported_as_an_unreachable_one(monkeypatch) -> None:
    """The two want different things done about them, and only one of them is
    "start Ollama". Sending somebody to debug a service while their OLLAMA_BASE
    points off the device is the one wrong answer that really costs."""
    monkeypatch.setenv("STEWARD_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("OLLAMA_BASE", "http://192.168.1.50:11434")
    monkeypatch.setenv("STEWARD_LOCAL_LLM_ALLOW_REMOTE", "")

    assert "refused" in embed.why_lexical()
    assert isinstance(embed.build(), embed.HashingEmbedder)

    monkeypatch.setenv("OLLAMA_BASE", "http://127.0.0.1:11434")
    monkeypatch.setattr("steward.extract.local.available", lambda **kwargs: False)

    assert "nothing answered" in embed.why_lexical()


def test_nothing_explains_away_a_working_model(monkeypatch) -> None:
    """ "" means build() really is handing back the local one — the notice in
    `memory reindex` hangs off this being empty."""
    monkeypatch.setenv("STEWARD_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr("steward.extract.local.available", lambda **kwargs: True)

    assert embed.why_lexical() == ""
    assert isinstance(embed.build(), embed.OllamaEmbedder)
