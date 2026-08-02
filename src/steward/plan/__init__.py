"""Plans: saving up for something, and what it costs to spend elsewhere.

**Nothing in this package imports `spend/`.** There is no path from a plan item
to a payment — the only route to money remains a live person's conversation
through `request_purchase` or `buy_offer`. That is what makes `needs_human` on
a flight a promise about the future rather than a flag something is already
quietly ignoring, and it is checkable with one grep.

A plan is advisory. It informs; pay-warden decides.
"""

from __future__ import annotations

from . import goals, schedule

__all__ = ["goals", "schedule"]
