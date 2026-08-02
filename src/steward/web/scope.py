"""Whose household this process serves, decided once, before it listens.

The dashboard has no authentication. That is defensible only because there is
nothing on it to reach: no writes, and no route that names a household. The
second half is this module's job.

A scheme where the sponsor arrives in the URL would put the authorisation check
in a request handler, where it is a line of code somebody can delete and a test
somebody can update. Binding it here means there is no check to delete — the
handlers never ask a request who it is about, because no route carries it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import store
from ..models import Role


class ScopeError(RuntimeError):
    """This process cannot serve that person."""


@dataclass(frozen=True)
class Household:
    """One sponsor, frozen at boot.

    The *id* is frozen; the membership is not. A spender enrolled while the
    server is running should appear on a refresh — what must never change is
    whose household this is.
    """

    sponsor_id: int
    db_path: str | None = None

    def sponsor(self) -> dict[str, Any]:
        row = store.get_person(self.sponsor_id, db_path=self.db_path)
        if row is None:
            # Resolvable at boot, gone now. Better a loud error than a page
            # titled "None's household" rendering someone else's escalations.
            raise ScopeError(f"person {self.sponsor_id} is no longer in the database")
        return row

    def spenders(self) -> list[dict[str, Any]]:
        """Who this sponsor funds. `list_people` has no sponsor filter, so the
        filter is here — one place, rather than in each panel that needs it."""
        return [
            row
            for row in store.list_people(db_path=self.db_path)
            if row["sponsor_id"] is not None and int(row["sponsor_id"]) == self.sponsor_id
        ]

    def owns(self, person_id: int) -> bool:
        return any(int(row["id"]) == person_id for row in self.spenders())


def resolve(person_id: int, *, db_path: str | None = None) -> Household:
    """Bind a household, refusing everything that is not one.

    Refusing a *spender* is the load-bearing half. A Household built on a
    spender's id looks healthy: `list_escalations(sponsor_id=…)` returns empty
    and `shared_turns` returns that person's own turns, so the page renders
    their conversation under a heading about what their sponsor may read. Every
    other way of getting the scope wrong is visible; this one is not.

    Deliberately not `cli._resolve_person`, which falls back to "the only person
    in the database". That default is right for a one-shot command and wrong for
    a process that stays up: it would bind a server to whoever happens to be row
    one.
    """
    row = store.get_person(person_id, db_path=db_path)
    if row is None:
        raise ScopeError(f"no person with id {person_id}")
    if row["role"] != Role.SPONSOR:
        raise ScopeError(
            f"{row['name']} is a {row['role']}, and this dashboard serves a sponsor."
            " A spender's own view is a different surface with the opposite privacy"
            " contract, so serving one here would show their conversation to whoever"
            " opened the page."
        )
    return Household(sponsor_id=person_id, db_path=db_path)
