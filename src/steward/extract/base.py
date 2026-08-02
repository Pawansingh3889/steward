"""What an extractor produces, and the rule every extractor obeys.

This package is where raw material stops. A calendar file, a bank alert, a
device's coordinates — they come in here and what leaves is `Candidate` objects:
short, keyed, structured statements. Nothing upstream of this boundary is
allowed downstream of it, and the send-time assert in `agent/llm.py` is what
turns that from an intention into a guarantee.

`source` is not decoration. It records how much the system is entitled to
believe a thing, and it survives all the way to the screen a person reads when
they ask what is held about them:

  stated   — the person said it. The strongest claim available.
  parsed   — a deterministic parser read it out of a document. Wrong only if
             the document was wrong or the parser has a bug.
  inferred — a model concluded it from free-form text. Might simply be untrue.

Later phases decide what each tier may authorise. This phase makes sure the
tier is never lost, because a system that forgets which of its beliefs it
guessed will eventually spend money on one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

STATED = "stated"
PARSED = "parsed"
INFERRED = "inferred"

SOURCES = (STATED, PARSED, INFERRED)


@dataclass(frozen=True)
class Candidate:
    """One extracted statement, not yet committed to memory."""

    kind: str  # a models.FactKind
    key: str  # stable slug, so re-extracting the same document updates in place
    value: str
    source: str = PARSED

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "key": self.key, "value": self.value, "source": self.source}


class Extractor(Protocol):
    """Anything that turns one piece of raw material into candidates."""

    name: str

    def extract(self, raw: str) -> list[Candidate]: ...


def slug(text: str, *, limit: int = 32) -> str:
    """A stable key from free text.

    Stable is the requirement: re-importing the same calendar must update the
    same facts rather than accumulate near-duplicates, and `upsert_fact` keys
    on exactly this string.
    """
    out: list[str] = []
    for char in text.lower().strip():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:limit] or "untitled"
