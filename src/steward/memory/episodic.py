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
# Below this, a "match" is hash noise rather than a resemblance. Tuned against
# the lexical embedder: unrelated household sentences land near zero, and a
# genuine restatement clears it comfortably.
MIN_SIMILARITY = 0.12


@dataclass
class Episode:
    episode_id: int
    text: str
    created_ts: str
    similarity: float


def _encode(embedder: Embedder, text: str) -> list[float]:
    """Encode, and fall back to the lexical matcher if the model cannot.

    Reaching a model that is running is checked once, at construction. This is
    the other half: a local model can be resident and still fail a single call —
    a cold start that outruns the timeout is the ordinary case, and the first
    real run of this hit exactly that.

    Episodic memory is conversational colour. Taking somebody's whole turn down
    because an embedding was slow would trade the thing they asked for against
    the thing they did not. The lexically-encoded episode is a narrower memory,
    not a lost one, and `memory reindex` upgrades it later.
    """
    try:
        return embedder.encode(text)
    except embed.EmbeddingError:
        return embed.HashingEmbedder().encode(text)


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
    vector = _encode(embedder, text)
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
    min_similarity: float = MIN_SIMILARITY,
    embedder: Embedder | None = None,
    db_path: str | None = None,
) -> list[Episode]:
    """The most similar live episodes, best first.

    Returns nothing rather than the least-bad match when nothing resembles the
    query. An agent handed a weak match treats it as evidence, and "you
    mentioned you were out of soap" is worse than silence when they never did.
    """
    embedder = embedder or embed.build()
    target = _encode(embedder, query)
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
    }
