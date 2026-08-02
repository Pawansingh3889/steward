"""Integrations: bringing real accounts in, and keeping almost none of it.

Every module here **fetches and hands straight to `extract/`**. None of them
interpret, none of them store raw material, and `agent/` imports none of them —
so there is no path from somebody's inbox or calendar to a model prompt.

Read-only throughout. There is no send, no delete and no calendar write: an
agent that could mail from a person's own account is a far larger promise than
this project has made.
"""

from __future__ import annotations

from . import google, prices, sync

__all__ = ["google", "prices", "sync"]
