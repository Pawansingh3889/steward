"""Spending: everything that can move money, and nothing that decides whether to.

Permission belongs to pay-warden, in another process, evaluating a policy the
sponsor wrote. This package describes purchases, relays verdicts, and carries
escalations to the person entitled to decide them.
"""

from __future__ import annotations

from . import purchase, warden

__all__ = ["purchase", "warden"]
