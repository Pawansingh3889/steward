"""Simulated households: the parameters, stated where they can be argued with.

Every number below is an assumption, and the result of this evaluation is only
as good as they are. So they are named, defaulted in one place, and swept in
`report.py` rather than buried in the loop — because the failure mode of a
simulation written by the same person who wrote the thing being evaluated is
that it quietly encodes the conclusion.

**The one that matters is `forgetfulness`.** It is the rate at which a person
fails to notice they have run out of something. If it is set high the agent
wins by construction, and a single headline number from a single setting would
be worthless. So the finding is reported as a *curve over* it: the honest claim
is not "the agent helps" but "the agent helps above this rate, and below it does
not", which is a statement somebody can disagree with using their own estimate
of how forgetful they are.

The essentials, prices and delivery bands are taken from the fixture catalogue
this project already ships, so the simulation and the product are describing the
same world.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Essential:
    """Something that runs out and has to be replaced.

    `days_per_pack` is how long one purchase lasts. `notice_days` is how much
    warning there is — the level at which a person *could* tell it is running
    low if they were paying attention. That gap is where the agent operates.
    """

    name: str
    days_per_pack: int
    # Cheapest supplier, slowest. The trade-off the catalogue models.
    cheap_cents: int
    cheap_days: int
    # Dearest, fastest — what you buy when you have already run out.
    urgent_cents: int
    urgent_days: int
    notice_days: int = 3


# Prices and delivery bands mirror catalogue/fixtures.py, so the simulation and
# the product are not quietly describing two different worlds.
ESSENTIALS: tuple[Essential, ...] = (
    Essential(
        "soap", days_per_pack=30, cheap_cents=320, cheap_days=3, urgent_cents=420, urgent_days=1
    ),
    Essential(
        "shampoo", days_per_pack=45, cheap_cents=340, cheap_days=3, urgent_cents=399, urgent_days=2
    ),
    Essential(
        "laundry", days_per_pack=40, cheap_cents=690, cheap_days=3, urgent_cents=725, urgent_days=2
    ),
    Essential(
        "kitchen roll",
        days_per_pack=20,
        cheap_cents=380,
        cheap_days=2,
        urgent_cents=420,
        urgent_days=1,
    ),
    Essential(
        "toothpaste",
        days_per_pack=35,
        cheap_cents=250,
        cheap_days=2,
        urgent_cents=310,
        urgent_days=1,
    ),
    Essential(
        "coffee", days_per_pack=25, cheap_cents=790, cheap_days=3, urgent_cents=850, urgent_days=2
    ),
)


@dataclass(frozen=True)
class Household:
    """One simulated spender, and the money they have to work with."""

    name: str
    # Everything in integer minor units, like the rest of the codebase.
    monthly_income_cents: int
    monthly_commitments_cents: int
    goal_target_cents: int
    goal_months: int

    # How often the person fails to notice something has run out, per day.
    # The parameter the whole result is reported against.
    forgetfulness: float = 0.5

    # What fraction of whatever is left at the end of a month actually reaches
    # a goal when nothing was set aside deliberately. Well below 1.0 on purpose:
    # money that is merely "left over" mostly gets spent, which is the effect
    # phasing is supposed to counter. It is an assumption, and it is swept.
    leftover_saved_fraction: float = 0.25

    # Chance per month of an unexpected expense, and how big.
    shock_chance: float = 0.25
    shock_cents: int = 6000

    essentials: tuple[Essential, ...] = ESSENTIALS

    def disposable_cents(self) -> int:
        return self.monthly_income_cents - self.monthly_commitments_cents

    def with_forgetfulness(self, value: float) -> Household:
        return replace(self, forgetfulness=value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "monthly_income_cents": self.monthly_income_cents,
            "monthly_commitments_cents": self.monthly_commitments_cents,
            "disposable_cents": self.disposable_cents(),
            "goal_target_cents": self.goal_target_cents,
            "goal_months": self.goal_months,
            "forgetfulness": self.forgetfulness,
            "leftover_saved_fraction": self.leftover_saved_fraction,
            "shock_chance": self.shock_chance,
        }


# Four households, chosen to span the cases where the answer might differ rather
# than to flatter: one with room to spare, one with almost none, one whose goal
# is out of reach on any schedule, and one that is barely solvent.
HOUSEHOLDS: tuple[Household, ...] = (
    Household(
        "comfortable",
        monthly_income_cents=140000,
        monthly_commitments_cents=60000,
        goal_target_cents=60000,
        goal_months=6,
    ),
    Household(
        "tight",
        monthly_income_cents=90000,
        monthly_commitments_cents=68000,
        goal_target_cents=40000,
        goal_months=8,
    ),
    Household(
        # The goal cannot be reached in the time given, whatever anyone does.
        # Included so the evaluation has a case the agent cannot win.
        "overreaching",
        monthly_income_cents=100000,
        monthly_commitments_cents=70000,
        goal_target_cents=90000,
        goal_months=3,
    ),
    Household(
        "precarious",
        monthly_income_cents=75000,
        monthly_commitments_cents=62000,
        goal_target_cents=20000,
        goal_months=10,
        shock_chance=0.4,
    ),
)


@dataclass
class Stock:
    """How much of each essential is left, in days."""

    levels: dict[str, int] = field(default_factory=dict)

    @classmethod
    def full(cls, essentials: tuple[Essential, ...]) -> Stock:
        return cls({item.name: item.days_per_pack for item in essentials})

    def consume(self, name: str) -> None:
        self.levels[name] = self.levels.get(name, 0) - 1

    def days_left(self, name: str) -> int:
        return self.levels.get(name, 0)

    def out_of(self, name: str) -> bool:
        return self.days_left(name) <= 0

    def restock(self, item: Essential, arriving_in_days: int) -> int:
        """Order now, arrive later. Returns the day count until it lands."""
        return arriving_in_days
