"""A read-only dashboard for one sponsor, and the privacy contract it keeps.

The sponsor sees decisions, the ledger, escalations, plans and refunds. They do
not see the conversation. In the CLI that boundary is invisible, because a
command nobody can run leaves no trace on screen; here the absence is drawn, and
that is the whole reason this surface exists.

Three properties hold it up, and each is enforced by something structural rather
than by remembering:

  * `store.shared_turns` is the only turn reader this package may call. Not a
    filter applied to a wider read — a different function, so there is no
    argument to get wrong. `tests/test_web.py` scans these files for the others.
  * The household is bound at process start (see `scope.py`) and never read from
    a request. This surface has no authentication, so an authorisation check
    would be a line of code someone could delete; instead there is no check to
    delete, because no route takes a person id.
  * Nothing here writes. Approving stays a CLI and text action: a sponsor who
    could release a purchase from an unauthenticated page would make the policy
    engine's escalation a formality.
"""

from __future__ import annotations

from .app import build_app
from .scope import Household, ScopeError, resolve

__all__ = ["Household", "ScopeError", "build_app", "resolve"]
