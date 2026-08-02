"""The privacy boundary: what the model may never see.

Built fresh per run from the live environment and the people table, so it can
never go stale against either. Two kinds of protection, doing different jobs:

  pseudonyms — people's names, phones and emails become `person_<id>`.
               Deterministic on purpose: the same person reads the same across
               runs (the model can reason about "person_3 again"), and putting
               a name back for that person's own display needs no stored map.
  denylist   — literal secret values (the OpenAI key, the Linq token, the Prava
               identity) plus pattern classes. These are removed, never
               pseudonymised: there is nothing to reason about, only something
               to leak.

The pattern classes are wider here than in payoptimize, because the raw material
is a person's life rather than a merchant's traffic. Phone numbers and email
addresses are the identifiers this product is *reached on*, and coordinates are
the one input the plan promises never crosses: the model is told "arrives in two
days", never where the person lives.

`llm.complete` asserts the denylist against every outgoing body, so redaction
here is enforced there — a bug in this file fails loudly at the send, not
silently at OpenAI's door. The patterns below are not on that denylist (they
match classes, not values), so they are defence in depth rather than the
guarantee; the guarantee is the literal list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import config, store

REDACTED = "[redacted]"

# Anything that looks like a secret key from any provider, a bare 13-19 digit
# run (a PAN until proven otherwise), an E.164 phone number, an email address,
# or a decimal-degrees coordinate pair. A false positive costs a little
# legibility; a false negative is the whole project.
_PATTERNS = (
    re.compile(r"\bsk[-_][\w-]+"),
    re.compile(r"\b\d{13,19}\b"),
    re.compile(r"\+\d{10,15}\b"),
    re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    # e.g. "51.5074, -0.1278" — a home address in two numbers.
    re.compile(r"-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}"),
)


def pseudonym(person_id: int) -> str:
    return f"person_{person_id}"


@dataclass
class Redactor:
    """One run's view of what must not cross, and how to talk around it."""

    secrets: tuple[str, ...]
    # person name/phone/email → pseudonym, longest first so "Anna Whitfield"
    # cannot be half-replaced via "Anna".
    aliases: tuple[tuple[str, str], ...]
    by_person: dict[int, str] = field(default_factory=dict)

    @classmethod
    def build(cls, *, db_path: str | None = None) -> Redactor:
        aliases: list[tuple[str, str]] = []
        by_person: dict[int, str] = {}
        for person in store.list_people(db_path=db_path):
            person_id = int(person["id"])
            alias = pseudonym(person_id)
            by_person[person_id] = str(person["name"])
            for value in (
                str(person["name"]),
                str(person["phone"]),
                str(person["email"]),
            ):
                if value:
                    aliases.append((value, alias))
        aliases.sort(key=lambda pair: -len(pair[0]))
        return cls(secrets=config.secret_values(), aliases=tuple(aliases), by_person=by_person)

    def redact(self, text: str) -> str:
        for value, alias in self.aliases:
            text = text.replace(value, alias)
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        for pattern in _PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    def redact_value(self, value: Any) -> Any:
        """Recursively redact every string inside a tool result."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {key: self.redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        return value

    def restore_for(self, text: str, person_id: int) -> str:
        """Put the reader's OWN name back into an answer. Only theirs: everyone
        else stays a pseudonym even in the displayed text, so a sponsor reading
        a decision never learns a name the spender did not share."""
        name = self.by_person.get(person_id)
        if name:
            text = text.replace(pseudonym(person_id), name)
        return text

    def denylist(self) -> tuple[str, ...]:
        """The literal strings that must never appear in an outgoing request."""
        return self.secrets + tuple(value for value, _ in self.aliases)
