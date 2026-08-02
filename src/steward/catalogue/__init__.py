"""The catalogue: what can be bought, at what price, arriving when.

A modelled storefront, labelled FIXTURE everywhere it surfaces. See
`fixtures.py` for the measured reason the open web is not used instead.
"""

from __future__ import annotations

from . import fixtures, search

__all__ = ["fixtures", "search"]
