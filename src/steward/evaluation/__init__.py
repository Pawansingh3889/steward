"""Does this actually make anyone better off?

A simulation, run against a no-agent baseline over the same seeded worlds. It
measures whether the *mechanism* helps, assuming the agent executes it — not
whether a language model executes it, which nothing here tests and nothing here
claims.

The result is reported as a curve over how forgetful the person is, because a
single number would be a restatement of that one parameter. See `world.py` for
what the agent's advantage actually is, stated so it can be argued with.
"""

from __future__ import annotations

from . import household, metrics, report, world

__all__ = ["household", "metrics", "report", "world"]
