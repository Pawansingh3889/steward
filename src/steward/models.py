"""Small shared values: time, ids, and the vocabularies the store persists.

Everything here is a plain string or dataclass. Nothing imports sqlite3, httpx
or the model — this module is safe to import from any layer, which is the point
of it existing rather than each layer minting its own timestamp format.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta


def utc_now_iso() -> str:
    """One timestamp format, everywhere: UTC, second precision, sortable as text.

    SQLite has no date type, so every ordering and window query is a string
    comparison. That only works if every writer produces the same shape.
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def iso_since(seconds: int) -> str:
    """The cutoff for a rolling window, in the same format as utc_now_iso."""
    return (datetime.now(UTC) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    """Prefixed ids so a bare string in a log says what it identifies."""
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Role:
    """Who a person is in a spending relationship.

    The asymmetry this whole project is built on: the SPENDER decides what is
    needed and the SPONSOR's money pays for it. One human can hold both roles
    in different relationships (a parent sponsors a child, and is the spender
    of their own parent's care budget), so the role belongs to the pairing, not
    to the person — see `people.role` being per-row rather than per-human.
    """

    SPENDER = "spender"
    SPONSOR = "sponsor"

    ALL = (SPENDER, SPONSOR)


class FactKind:
    """What a remembered fact is for.

    The kind decides who may see it and whether it can drive a spend, so it is
    a closed vocabulary rather than a free string. `MOOD` is here because the
    owner chose that mood is asked and stored, never inferred — which makes it
    a fact like any other, and therefore deletable like any other.
    """

    IDENTITY = "identity"  # name, household, who they live with
    PREFERENCE = "preference"  # brands, dietary needs, "never buy X"
    CONSTRAINT = "constraint"  # allergies, budgets the person states
    SCHEDULE = "schedule"  # working hours, holidays, recurring commitments
    MOOD = "mood"  # asked, never inferred
    GOAL = "goal"  # what they are saving toward
    SUPPLY = "supply"  # "out of soap", "coffee lasts ~3 weeks"

    ALL = (IDENTITY, PREFERENCE, CONSTRAINT, SCHEDULE, MOOD, GOAL, SUPPLY)


class Trigger:
    """Why the agent ran. Audit rows are grouped by this."""

    ASK = "ask"  # a human typed something
    SCHEDULED = "scheduled"  # a timer
    INBOUND = "inbound"  # a text arrived
