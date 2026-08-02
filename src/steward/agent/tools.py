"""What the agent can do, and the scope it can do it in.

The critical property is in the constructor, not in any tool: **`person_id` is
bound when the ToolBox is built and no tool takes it as an argument.** The model
cannot address another person's memory because it has no way to name one. A
`person_id` parameter on `recall_facts` would make cross-person reads a prompt
away, and no system prompt is a permission system.

Every result goes back through the redactor in `dispatch` on the way out, so a
tool that reads a raw stored value cannot hand it to the model unredacted just
by forgetting to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import store
from ..memory import recall
from ..models import FactKind
from .privacy import Redactor

MAX_FACT_VALUE = 500  # a fact is a fact, not an essay


def _spec(name: str, description: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": params,
                "additionalProperties": False,
            },
        },
    }


_KINDS = ", ".join(FactKind.ALL)

SPECS: list[dict[str, Any]] = [
    _spec(
        "recall_facts",
        "Everything currently remembered about the person you are talking to."
        " Call this before answering anything about them — an answer from memory"
        f" beats a guess. Optionally filter by kind ({_KINDS}).",
        {"kind": {"type": "string", "description": f"one of: {_KINDS}. Empty for all."}},
    ),
    _spec(
        "remember_fact",
        "Store something the person told you, so it survives this conversation."
        " Only store what they actually stated; never store an inference as a"
        " fact. Restating an existing (kind, key) replaces it.",
        {
            "kind": {"type": "string", "description": f"one of: {_KINDS}"},
            "key": {
                "type": "string",
                "description": "short stable slug, e.g. 'working_hours' or 'soap'",
            },
            "value": {"type": "string", "description": "the fact, in the person's own terms"},
        },
    ),
    _spec(
        "forget_fact",
        "Delete a remembered fact, by the id shown in recall_facts. Use this"
        " whenever the person asks you to forget something. It stops influencing"
        " every future decision.",
        {"fact_id": {"type": "integer", "description": "id from recall_facts"}},
    ),
    _spec(
        "recent_conversation",
        "The last few turns of this conversation, oldest first. Use it when the"
        " person refers back to something without repeating it.",
        {"limit": {"type": "integer", "description": "how many turns, default 20"}},
    ),
    _spec(
        "search_memory",
        "Search things the person has said in the past, by resemblance. Use it"
        " for context and colour — 'you mentioned this before'. Do NOT use it as"
        " grounds for a decision or a purchase: what they said once is not the"
        " same as a fact they have confirmed. Returns nothing when nothing"
        " genuinely resembles the query, which means they never said it.",
        {
            "query": {"type": "string", "description": "what to look for"},
            "limit": {"type": "integer", "description": "how many, default 5"},
        },
    ),
]


def _clamp(limit: Any, default: int, ceiling: int = 100) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(ceiling, value))


@dataclass
class ToolBox:
    """One run's tools, scoped to one person. See the module docstring."""

    person_id: int
    redactor: Redactor
    db_path: str | None = None
    run_id: int = 0
    # What the run actually did, for the caller to display and audit.
    writes_log: list[dict[str, Any]] = field(default_factory=list)

    def specs(self) -> list[dict[str, Any]]:
        return list(SPECS)

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            result = handler(**arguments)
        except TypeError as exc:
            # The model sent arguments the tool does not take. Tell it, so the
            # next iteration can correct course instead of the run dying.
            result = {"error": f"bad arguments for {name}: {exc}"}
        except store.NotFoundError as exc:
            # A real, expected outcome — the model referenced a fact that is
            # already gone. It should say so, not crash the conversation.
            result = {"error": str(exc)}
        return self.redactor.redact_value(result)

    # --- reads ---------------------------------------------------------------

    def _fact_view(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "fact_id": int(row["id"]),
            "kind": str(row["kind"]),
            "key": str(row["key"]),
            "value": str(row["value"]),
            "source": str(row["source"]),
            "since": str(row["created_ts"]),
        }

    def _tool_recall_facts(self, kind: str = "") -> dict[str, Any]:
        rows = store.list_facts(self.person_id, kind=kind, db_path=self.db_path)
        return {"facts": [self._fact_view(row) for row in rows], "count": len(rows)}

    def _tool_search_memory(self, query: str = "", limit: int = 5) -> dict[str, Any]:
        if not query:
            return {"error": "query is required"}
        found = recall.search(
            self.person_id, query, limit=_clamp(limit, 5, ceiling=20), db_path=self.db_path
        )
        return {"episodes": found, "count": len(found)}

    def _tool_recent_conversation(self, limit: int = 20) -> dict[str, Any]:
        rows = store.recent_turns(self.person_id, limit=_clamp(limit, 20), db_path=self.db_path)
        return {
            "turns": [
                {"speaker": str(row["speaker"]), "text": str(row["text"]), "at": str(row["ts"])}
                for row in rows
            ]
        }

    # --- writes --------------------------------------------------------------

    def _tool_remember_fact(self, kind: str = "", key: str = "", value: str = "") -> dict[str, Any]:
        if kind not in FactKind.ALL:
            return {"error": f"unknown kind {kind!r}; use one of: {_KINDS}"}
        if not key or not value:
            return {"error": "both key and value are required"}
        if len(value) > MAX_FACT_VALUE:
            return {"error": f"value too long ({len(value)} chars, max {MAX_FACT_VALUE})"}
        fact_id = store.upsert_fact(
            person_id=self.person_id,
            kind=kind,
            key=key,
            value=value,
            source="stated",
            db_path=self.db_path,
        )
        self.writes_log.append({"action": "remember", "kind": kind, "key": key})
        return {"fact_id": fact_id, "stored": True, "kind": kind, "key": key}

    def _tool_forget_fact(self, fact_id: int = 0) -> dict[str, Any]:
        row = store.get_fact(int(fact_id), db_path=self.db_path)
        # Scope check, not a lookup convenience: without it, a guessed id reads
        # and deletes another person's memory.
        if row is None or int(row["person_id"]) != self.person_id:
            return {"error": f"no fact {fact_id} belonging to you"}
        store.delete_fact(int(fact_id), db_path=self.db_path)
        self.writes_log.append(
            {"action": "forget", "kind": str(row["kind"]), "key": str(row["key"])}
        )
        return {"fact_id": int(fact_id), "forgotten": True, "key": str(row["key"])}
