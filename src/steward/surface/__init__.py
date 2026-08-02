"""Surfaces: how people reach steward, and how it reaches them back.

Two lines — the spender's and the sponsor's — over whatever channel is
configured. The in-process `RecordingChannel` is a first-class implementation,
not a test double: with no messaging provider the whole product still works on
one machine, which is what keeps an expiring Linq sandbox from being able to
stop anyone managing their money.
"""

from __future__ import annotations

from . import base, linq, router

__all__ = ["base", "linq", "router"]
