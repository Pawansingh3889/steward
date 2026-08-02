"""Parsers first, the local model only for what is left.

The order is the whole design. Bank alerts and calendar files are written by
machines to a specification, so a parser reads them exactly, for free, offline,
and identically every time. Handing those to a language model instead would be
slower, non-deterministic, and would occasionally invent a balance.

So the model is the fallback, not the front door: it runs when no parser
recognised the material, and what it produces is marked `inferred` and stays
marked all the way to the screen. A person looking at their memory can see
which facts a machine read and which one guessed.

Committing is separate from extracting on purpose. `extract_all` is pure — it
opens no database and can be run against a mailbox to see what *would* be
learned. `commit` is the step that changes what the agent believes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .. import store
from . import bank, ics, local
from .base import Candidate

# Cheap recognisers, in the order they are tried. A parser that claims material
# it cannot actually read would silently starve the fallback, so each one is
# conservative and the pipeline falls through when they all decline.
CALENDAR_MARKERS = ("BEGIN:VCALENDAR", "BEGIN:VEVENT")
BANK_MARKERS = ("balance", "bal ", "spent", "debit", "card ending", "payment of")


@dataclass
class Extraction:
    """What one piece of material yielded, and how."""

    candidates: list[Candidate] = field(default_factory=list)
    extractor: str = "none"
    # Set when the local model was wanted but could not run. Surfaced rather
    # than swallowed: "nothing was learned" and "nothing could be attempted"
    # look identical in a fact list and mean very different things.
    degraded: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "extractor": self.extractor,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "degraded": self.degraded,
        }


def looks_like_calendar(raw: str) -> bool:
    upper = raw.upper()
    return any(marker in upper for marker in CALENDAR_MARKERS)


def looks_like_bank_alert(raw: str) -> bool:
    lowered = raw.lower()
    if len(raw) > 600:  # a bank alert is a notification, not a document
        return False
    if not any(marker in lowered for marker in BANK_MARKERS):
        return False
    return bool(bank.find_amounts(raw))


def extract_all(
    raw: str,
    *,
    use_local_model: bool = True,
    http: httpx.Client | None = None,
) -> Extraction:
    """One piece of raw material in, candidates out. Touches no database."""
    if not raw.strip():
        return Extraction()

    if looks_like_calendar(raw):
        return Extraction(candidates=ics.extract(raw), extractor="ics")
    if looks_like_bank_alert(raw):
        return Extraction(candidates=bank.extract(raw), extractor="bank")
    if not use_local_model:
        return Extraction(extractor="none", degraded="local model not requested")

    try:
        return Extraction(candidates=local.extract(raw, http=http), extractor="local_model")
    except local.LocalModelError as exc:
        # Not fatal, and not silent. The deterministic parsers are the floor;
        # this is the part that degrades when Ollama is not installed.
        return Extraction(extractor="local_model", degraded=str(exc))


def commit(
    person_id: int,
    extraction: Extraction,
    *,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Write candidates into memory, preserving how each was learned.

    `upsert_fact` keys on (person, kind, key), so re-importing the same calendar
    updates the same facts instead of accumulating near-duplicates — which is
    what the stable slugs in `base.slug` are for.
    """
    written: list[dict[str, Any]] = []
    for candidate in extraction.candidates:
        fact_id = store.upsert_fact(
            person_id=person_id,
            kind=candidate.kind,
            key=candidate.key,
            value=candidate.value,
            source=candidate.source,
            db_path=db_path,
        )
        written.append({"fact_id": fact_id, **candidate.as_dict()})
    return written


def ingest(
    person_id: int,
    raw: str,
    *,
    use_local_model: bool = True,
    db_path: str | None = None,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    """Extract and commit in one step, for callers that want both."""
    extraction = extract_all(raw, use_local_model=use_local_model, http=http)
    written = commit(person_id, extraction, db_path=db_path)
    return {"extractor": extraction.extractor, "degraded": extraction.degraded, "facts": written}
