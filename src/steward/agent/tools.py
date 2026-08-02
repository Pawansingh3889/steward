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
from ..catalogue import search
from ..extract.eta import Point
from ..memory import recall
from ..models import FactKind
from ..spend import purchase
from ..spend.warden import Warden, WardenError
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
        "find_options",
        "Find what is available for something the person needs, with the price"
        " and how long delivery takes. ALWAYS show them every option returned,"
        " with both numbers, and ask which they want — do not pick for them and"
        " do not quietly drop the expensive or the slow one. Cheapest is not"
        " best when someone has run out today. Every result is from a modelled"
        " FIXTURE catalogue; say so.",
        {
            "query": {
                "type": "string",
                "description": "what they need, in their words, e.g. 'soap'",
            },
            "limit": {"type": "integer", "description": "how many, default 6"},
        },
    ),
    _spec(
        "buy_offer",
        "Buy one option the person chose, by its offer_id from find_options."
        " Pass price_cents exactly as it was shown to them: if it no longer"
        " matches the catalogue the purchase is refused, because their"
        " agreement was to a price, not to whatever it now costs. Only call"
        " this after they have chosen — never to save them a step.",
        {
            "offer_id": {"type": "string", "description": "offer_id from find_options"},
            "price_cents": {
                "type": "integer",
                "description": "the price_cents you showed them for this offer",
            },
        },
    ),
    _spec(
        "request_purchase",
        "Buy something for the person. Policy decides, not you: the answer is"
        " 'allowed' (a payment link comes back and they tap their passkey),"
        " 'needs_approval' (their sponsor has been asked — tell them it is"
        " waiting, NOT that it failed), or 'denied' (relay the reason as given"
        " and do not soften it). Amounts are integer minor units: 1250 is"
        " £12.50. Only request what the person actually asked for.",
        {
            "description": {
                "type": "string",
                "description": "what is being bought, in plain words",
            },
            "amount_cents": {
                "type": "integer",
                "description": "integer minor units, e.g. 1250 for £12.50",
            },
            "currency": {"type": "string", "description": "ISO code, e.g. GBP"},
            "merchant_name": {"type": "string"},
            "merchant_url": {"type": "string"},
            "merchant_country": {"type": "string", "description": "two-letter code, e.g. GB"},
        },
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
    # Injected in tests so the suite never spawns pay-warden; None means the
    # real subprocess over MCP stdio.
    warden: Warden | None = None
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

    def _destination(self) -> Point | None:
        """Roughly where this person is, for the delivery estimate.

        Read here and passed straight into the distance model, which returns
        days. The coordinate itself never enters a tool result, so it has no
        route to the model — see extract/eta.py for why even a distance would
        be too much.
        """
        person = store.get_person(self.person_id, db_path=self.db_path)
        if person is None or person["home_lat"] is None or person["home_lon"] is None:
            return None
        return Point(float(person["home_lat"]), float(person["home_lon"]))

    def _tool_find_options(self, query: str = "", limit: int = 6) -> dict[str, Any]:
        if not query:
            return {"error": "query is required"}
        offers = search.find(
            query, destination=self._destination(), limit=_clamp(limit, 6, ceiling=12)
        )
        if not offers:
            return {
                "options": [],
                "count": 0,
                "note": f"nothing in the catalogue matches {query!r}",
            }
        return {
            "options": [offer.as_dict() for offer in offers],
            "count": len(offers),
            "note": "show ALL of these with price and delivery, and ask which they want",
        }

    def _tool_buy_offer(self, offer_id: str = "", price_cents: int = 0) -> dict[str, Any]:
        if not offer_id or price_cents <= 0:
            return {"error": "offer_id and the price_cents you showed them are required"}
        try:
            # The authoritative price comes from the catalogue, not from the
            # model. This is the line that stops a misremembered number from
            # becoming the number the policy engine evaluates and the person pays.
            offer = search.quote(offer_id, int(price_cents), destination=self._destination())
        except search.PriceMoved as exc:
            return {"error": str(exc), "verdict": "price_moved", "recheck": True}
        return self._tool_request_purchase(
            description=offer.name,
            amount_cents=offer.price_cents,
            currency=offer.currency,
            merchant_name=offer.supplier_name,
            merchant_url=offer.supplier_url,
            merchant_country=offer.supplier_country,
        )

    def _tool_request_purchase(
        self,
        description: str = "",
        amount_cents: int = 0,
        currency: str = "GBP",
        merchant_name: str = "",
        merchant_url: str = "",
        merchant_country: str = "GB",
    ) -> dict[str, Any]:
        """Ask pay-warden for money. Note what is absent: any tool to approve.

        The agent can request and can report, but only the sponsor can release
        a parked attempt — and they do it through the CLI, not through anything
        the model can reach. An agent able to approve its own escalations would
        make the sponsor's policy a suggestion.
        """
        if not description or amount_cents <= 0:
            return {"error": "description and a positive amount_cents are required"}
        if not merchant_name or not merchant_url:
            return {"error": "merchant_name and merchant_url are required"}
        try:
            outcome = purchase.buy(
                person_id=self.person_id,
                description=description,
                amount_cents=int(amount_cents),
                currency=currency,
                merchant_name=merchant_name,
                merchant_url=merchant_url,
                merchant_country=merchant_country,
                db_path=self.db_path,
                client=self.warden,
            )
        except (purchase.PurchaseError, WardenError) as exc:
            # A policy engine that cannot be reached is not permission to spend.
            return {"error": str(exc), "verdict": "unavailable"}
        self.writes_log.append(
            {
                "action": "purchase",
                "verdict": outcome["verdict"],
                "description": description,
                "amount_cents": amount_cents,
            }
        )
        return outcome

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
