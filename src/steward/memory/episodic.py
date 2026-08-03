"""Episodic memory: what was said, searchable by resemblance.

The division of labour with facts is the decision the plan records, and it
matters more than it looks. **Facts drive decisions; episodes are colour.** A
fact is structured, keyed, and singular — "working hours: 9-5" — and the agent
is allowed to act on one. An episode is a sentence someone said, retrieved by
similarity, and it exists so the agent sounds like it was listening rather than
so it can justify a purchase. Nothing that spends money reads from here.

That is why ranking in Python is not a shortcut. A household's episodes number
in the thousands over a year, and a thousand 256-float dot products is well
under a millisecond — while sqlite-vec or an index would be a dependency, a
build step and a migration path bought for a feature that never blocks a
decision. If episodes ever do grow past that, the fix is the same fix, later,
with real numbers behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import store
from . import embed
from .embed import Embedder

DEFAULT_LIMIT = 5
# Only for an embedder that declares no floor of its own — a test double, or
# something duck-typed from outside this package. The real floors live on the
# embedders, in embed.py, because the number is a property of the model that
# produced the score and not of this module.
#
# This constant used to be *the* threshold, at 0.12, described as tuned so that
# "unrelated household sentences land near zero". Measured against a labelled
# corpus that turned out to be false twice over: 5% of unrelated lexical pairs
# already cleared it, and under nomic-embed-text nothing scores below 0.24, so
# it matched every episode for every query.
FALLBACK_MIN_SIMILARITY = 0.22


@dataclass
class Episode:
    episode_id: int
    text: str
    created_ts: str
    similarity: float


def _encode(embedder: Embedder, text: str) -> tuple[list[float], Embedder]:
    """Encode, and fall back to the lexical matcher if the model cannot.

    Reaching a model that is running is checked once, at construction. This is
    the other half: a local model can be resident and still fail a single call —
    a cold start that outruns the timeout is the ordinary case, and the first
    real run of this hit exactly that.

    Episodic memory is conversational colour. Taking somebody's whole turn down
    because an embedding was slow would trade the thing they asked for against
    the thing they did not. The lexically-encoded episode is a narrower memory,
    not a lost one, and `memory reindex` upgrades it later.

    Returns the embedder that *actually* produced the vector, which the caller
    needs and cannot infer. Scores are only comparable to the floor belonging to
    the thing that computed them: judging a fallen-back lexical query (true pairs
    average 0.07) against nomic's floor of 0.55 would return nothing at all, and
    it would look like the person had never said anything.
    """
    try:
        return embedder.encode(text), embedder
    except embed.EmbeddingError:
        lexical = embed.HashingEmbedder()
        return lexical.encode(text), lexical


def remember(
    *,
    person_id: int,
    text: str,
    turn_id: int | None = None,
    embedder: Embedder | None = None,
    db_path: str | None = None,
) -> int | None:
    """Store one episode. The text is kept as the person said it.

    Episodes live on the device unredacted, like the turns they come from —
    redaction happens on the way to the model, not on the way to disk. Redacting
    at rest would mean the person's own memory of their own life comes back to
    them full of [redacted], which helps nobody and protects nothing: anyone who
    can read this database can already read `turns`.

    Returns None for text with nothing indexable in it — "the and of", or
    whitespace. Such an episode could never be retrieved (its vector is all
    zeros and scores zero against everything), so storing it would only pad the
    list a person sees when they ask what is held about them.

    Note what this does *not* filter: "ok" and "thanks" are real tokens and get
    stored. Recognising an acknowledgement as not worth remembering is a
    judgement about meaning, not a token count, and the honest place for it is
    phase 2's extraction layer — where a local model already decides what in a
    piece of text is worth keeping. Widening the stopword list to fake it would
    cost recall everywhere else for a cosmetic win here.
    """
    embedder = embedder or embed.build()
    if not embed.tokenize(text):
        return None
    vector, _ = _encode(embedder, text)
    return store.insert_episode(
        person_id=person_id,
        text=text,
        embedding=embed.pack(vector),
        turn_id=turn_id,
        db_path=db_path,
    )


def search(
    *,
    person_id: int,
    query: str,
    limit: int = DEFAULT_LIMIT,
    min_similarity: float | None = None,
    embedder: Embedder | None = None,
    db_path: str | None = None,
) -> list[Episode]:
    """The most similar live episodes, best first.

    Returns nothing rather than the least-bad match when nothing resembles the
    query. An agent handed a weak match treats it as evidence, and "you
    mentioned you were out of soap" is worse than silence when they never did.
    """
    embedder = embedder or embed.build()
    target, used = _encode(embedder, query)
    if min_similarity is None:
        # From the embedder that produced `target`, not the one configured —
        # see `_encode`. `getattr` so a duck-typed embedder without a floor
        # still works rather than raising.
        min_similarity = getattr(used, "min_similarity", FALLBACK_MIN_SIMILARITY)
    scored: list[Episode] = []
    for row in store.list_episodes(person_id, db_path=db_path):
        vector = embed.unpack(bytes(row["embedding"]))
        if len(vector) != len(target):
            # Written by a different embedder — a model swap, most likely.
            # Skipping is right: comparing them yields a confident, meaningless
            # number, and one stale row must not break the whole search.
            continue
        score = embed.similarity(target, vector)
        if score >= min_similarity:
            scored.append(
                Episode(
                    episode_id=int(row["id"]),
                    text=str(row["text"]),
                    created_ts=str(row["created_ts"]),
                    similarity=round(score, 4),
                )
            )
    # Ties break toward the more recent episode: when two things were said with
    # equal relevance, the later one is the current state of affairs.
    scored.sort(key=lambda episode: (-episode.similarity, -episode.episode_id))
    return scored[:limit]


def forget(episode_id: int, *, db_path: str | None = None) -> None:
    store.delete_episode(episode_id, db_path=db_path)


def as_dict(episode: Episode) -> dict[str, Any]:
    return {
        "episode_id": episode.episode_id,
        "text": episode.text,
        "at": episode.created_ts,
        "similarity": episode.similarity,
    }


def reindex(
    person_id: int,
    *,
    embedder: Embedder | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Re-embed everything already remembered, with the current embedder.

    Needed because vectors of different widths are never compared — `search`
    skips them rather than producing a confident meaningless number. That is the
    right behaviour and it means switching embedders makes existing episodes
    invisible until this has run. Silently losing somebody's history on a config
    change would be worse than making them ask for it back.

    Re-embedding is possible at all because the text is stored beside the
    vector. An episode store that kept only vectors could never change its mind
    about how to encode them.

    Raises `embed.EmbeddingError` if the model gives out part-way through,
    naming how far it got. Reindexing is idempotent, so running it again after
    the model is behaving costs nothing but time.
    """
    embedder = embedder or embed.build()
    rows = store.list_episodes(person_id, db_path=db_path)
    done = skipped = 0
    for row in rows:
        text = str(row["text"])
        if not embed.tokenize(text):
            skipped += 1
            continue
        try:
            vector = embedder.encode(text)
        except embed.EmbeddingError as exc:
            # Pointedly *not* `_encode`'s fallback. Writing a lexical vector
            # here would leave two widths in one person's store, and whichever
            # set does not match the embedder in force goes silently
            # unsearchable — precisely the state this command exists to repair.
            # Stopping half-done and saying so is the recoverable failure.
            raise embed.EmbeddingError(
                f"stopped after reindexing {done} of {len(rows)} episode(s): {exc}"
            ) from exc
        store.set_episode_embedding(int(row["id"]), embed.pack(vector), db_path=db_path)
        done += 1
    return {
        "reindexed": done,
        "skipped": skipped,
        "embedder": type(embedder).__name__,
        "dimensions": getattr(embedder, "dimensions", 0),
        "min_similarity": getattr(embedder, "min_similarity", FALLBACK_MIN_SIMILARITY),
        # False means nobody has measured a floor for this model and it is
        # running on the strict default. Worth saying out loud: it is the
        # difference between "recall is quiet" and "recall is broken".
        "calibrated": getattr(embedder, "calibrated", True),
    }
