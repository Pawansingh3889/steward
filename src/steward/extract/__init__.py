"""Extraction: where raw material stops and facts begin.

Nothing in this package is imported by `agent/`. The dependency runs one way —
the pipeline writes facts to the store, and the agent reads facts from the
store — so there is no code path by which a raw calendar file or a bank alert
can reach a model prompt.
"""

from __future__ import annotations

from . import bank, base, eta, ics, local, pipeline

__all__ = ["bank", "base", "eta", "ics", "local", "pipeline"]
