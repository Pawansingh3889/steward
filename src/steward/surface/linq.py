"""Linq: real messages to real phones.

**This adapter is unverified against the live sandbox.** The request shape below
is written from the documented v3 partner API, but no message has been sent
through it, and saying so is more useful than a confident wrapper that turns out
to address the wrong endpoint. `Delivery.detail` carries Linq's verbatim
response so the first live attempt diagnoses itself.

It is also **dry-run by default**. Sending a text is an outward-facing act on
someone else's behalf: it reaches a real person's phone, cannot be recalled, and
may cost money. `STEWARD_LINQ_LIVE=1` is the opt-in, and until it is set this
channel prints exactly what it would have sent and reports `delivered=False`
with the reason. A messaging integration that goes live merely because a token
happens to be present is how a test run becomes a text to somebody's parent.

The sandbox expires on 9 August 2026. Nothing above this module should notice
when it does — see `base.Channel` and `RecordingChannel`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .. import config
from .base import Delivery, Outbound

DEFAULT_TIMEOUT = 20.0


class LinqError(RuntimeError):
    """Linq refused, or is not configured."""


@dataclass
class LinqChannel:
    """Sends over Linq when told to, and describes itself honestly when not."""

    name: str = "linq"
    live: bool | None = None  # None means read the environment
    http: httpx.Client | None = None

    def _is_live(self) -> bool:
        return config.linq_live() if self.live is None else self.live

    def send(self, outbound: Outbound, *, to: str) -> Delivery:
        if not to:
            return Delivery(outbound, to, False, "no number on file for this person")

        token = config.linq_token()
        if not token:
            return Delivery(outbound, to, False, "LINQ_API_TOKEN is unset")
        sender = config.linq_from_number()
        if not sender:
            return Delivery(outbound, to, False, "LINQ_FROM_NUMBER is unset")

        if not self._is_live():
            # The honest default. Not an error, not a success — a description.
            return Delivery(
                outbound, to, False, "dry run: set STEWARD_LINQ_LIVE=1 to actually send"
            )

        client = self.http or httpx.Client(timeout=DEFAULT_TIMEOUT)
        try:
            response = client.post(
                f"{config.linq_api_base()}/messages",
                json={"to": to, "from": sender, "body": outbound.body},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            return Delivery(outbound, to, False, f"could not reach Linq: {exc}")
        finally:
            if self.http is None:
                client.close()

        if response.status_code >= 400:
            # Verbatim, and truncated only for length. The first live attempt
            # against an unverified endpoint is exactly when a summarised error
            # costs an afternoon.
            return Delivery(
                outbound, to, False, f"Linq returned {response.status_code}: {response.text[:300]}"
            )
        return Delivery(outbound, to, True, f"Linq accepted it ({response.status_code})")
