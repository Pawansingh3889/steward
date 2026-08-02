""" "What do you know about me?" — answered once, for every surface.

Phase 1's exit criterion has two halves, and the second is the hard one:
answerable *and correctable*. Correctable means a person can delete something
without the model's cooperation. If the only route to deletion runs through a
tool call the agent decides whether to make, then deletion is a request, not a
guarantee — and this product is asking someone to hand over their errands, their
schedule and their moods on the strength of that guarantee.

So the functions here are the single implementation, and both surfaces are thin
over them: `agent/tools.py` exposes them to the model, and `cli.py` exposes them
to the person directly. The CLI path never loads the model at all. A person can
delete a memory while OPENAI_API_KEY is unset, while the API is down, and while
the agent is mid-conversation about the very thing they are deleting.
"""

from __future__ import annotations

from typing import Any

from .. import store
from . import episodic

FACT = "fact"
EPISODE = "episode"
KINDS = (FACT, EPISODE)


def _fact_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "kind": str(row["kind"]),
        "key": str(row["key"]),
        "value": str(row["value"]),
        # "you told me" and "I worked it out" are different claims and must
        # never be displayed as if they were the same one.
        "source": str(row["source"]),
        "since": str(row["created_ts"]),
        # Present and true when this began as a machine's reading and the
        # person agreed to it, which promotion to `stated` would otherwise hide.
        "confirmed": bool(row["confirmed_ts"]),
    }


def _episode_view(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": int(row["id"]), "text": str(row["text"]), "at": str(row["created_ts"])}


def everything(person_id: int, *, db_path: str | None = None) -> dict[str, Any]:
    """Everything remembered about one person, in the order they would read it.

    No limit and no pagination. Someone asking what is held about them is owed
    all of it — a truncated answer to this particular question is a wrong one.

    `pending` is listed separately and is not part of `facts`, because the two
    are different claims: one is what steward believes and acts on, the other is
    what a model thought it read and is waiting to be told about.
    """
    facts = store.list_facts(person_id, db_path=db_path)
    pending = store.list_facts(person_id, pending=True, db_path=db_path)
    episodes = store.list_episodes(person_id, db_path=db_path)
    return {
        "facts": [_fact_view(row) for row in facts],
        "pending": [_fact_view(row) for row in pending],
        "episodes": [_episode_view(row) for row in episodes],
        "counts": {"facts": len(facts), "pending": len(pending), "episodes": len(episodes)},
    }


def confirm(
    fact_id: int, *, person_id: int | None = None, db_path: str | None = None
) -> dict[str, Any]:
    """Accept a proposal. From here it is a belief and may drive decisions."""
    row = store.get_fact(fact_id, db_path=db_path)
    if row is None or (person_id is not None and int(row["person_id"]) != person_id):
        raise store.NotFoundError(f"no fact {fact_id} belonging to you")
    store.confirm_fact(fact_id, db_path=db_path)
    return {"id": fact_id, "confirmed": True, "key": str(row["key"]), "value": str(row["value"])}


def search(
    person_id: int, query: str, *, limit: int = episodic.DEFAULT_LIMIT, db_path: str | None = None
) -> list[dict[str, Any]]:
    """Episodes resembling a query. Facts are not searched: they are few, keyed,
    and meant to be read whole."""
    found = episodic.search(person_id=person_id, query=query, limit=limit, db_path=db_path)
    return [episodic.as_dict(episode) for episode in found]


def forget(
    kind: str,
    item_id: int,
    *,
    person_id: int | None = None,
    run_id: int = 0,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Delete one remembered thing, whoever asked, and record that they did.

    `person_id` is the scope check. It is optional only because the CLI runs as
    the person themselves and has already established who that is; every caller
    that takes the id from somewhere less trustworthy — the model, an HTTP
    request — must pass it, and a guessed id then fails instead of deleting a
    stranger's memory.

    The correction row is the point. "This system held something wrong about me
    and I fixed it" is the phenomenon a pilot is trying to observe, and a
    tombstone alone cannot distinguish it from a fact that was simply
    superseded by a newer one.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown memory kind {kind!r}; use one of: {', '.join(KINDS)}")

    if kind == FACT:
        row = store.get_fact(item_id, db_path=db_path)
        remove = store.delete_fact
        label = str(row["key"]) if row else ""
    else:
        row = store.get_episode(item_id, db_path=db_path)
        remove = store.delete_episode
        label = str(row["text"])[:60] if row else ""

    if row is None or (person_id is not None and int(row["person_id"]) != person_id):
        raise store.NotFoundError(f"no {kind} {item_id} belonging to you")

    # Read before the delete: a pending fact is a machine's guess being turned
    # down, a live one is a belief being corrected, and afterwards they look the
    # same.
    was_proposal = kind == FACT and bool(row.get("pending"))
    remove(item_id, db_path=db_path)
    store.insert_correction(
        person_id=int(row["person_id"]),
        kind=store.REJECTED_PROPOSAL if was_proposal else store.DELETED_BELIEF,
        subject=kind,
        subject_id=item_id,
        detail=label,
        run_id=run_id,
        db_path=db_path,
    )
    return {"kind": kind, "id": item_id, "forgotten": True, "was": label}
