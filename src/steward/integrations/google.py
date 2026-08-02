"""Reading a Google account, and handing on almost none of it.

**Unverified against a live Google account.** The shapes below are written from
the documented v3 Calendar and v1 Gmail APIs, and no request has been made with
real credentials. Saying so is more useful than a confident wrapper that turns
out to address the wrong field, and every error carries Google's verbatim
response so the first real attempt diagnoses itself. Obtaining a token is the
operator's job — steward runs no consent flow and stores no client secret it
was not given.

**Fetch only.** Nothing here interprets. Calendar events become
`extract.ics.Event` — the same type a `.ics` file produces — and go through
`extract.ics.candidates_from`, which is the single place deciding what a
calendar may contribute. Locations, descriptions and attendees are dropped
there, so a second calendar source cannot arrive with a second, laxer policy.
Mail bodies go to `extract.pipeline` and are never stored.

That is the whole privacy argument for this package: **it hands raw material to
`extract/` and keeps none of it**. `agent/` does not import it, so there is no
path from an inbox to a model prompt.

Read-only scopes, and read-only code. There is no send, no delete, no calendar
write. An agent that could email on someone's behalf from their own account is
a different and much larger promise than this project has made.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from .. import config
from ..extract.ics import Event

CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"

DEFAULT_TIMEOUT = 30.0

# Read-only, and asserted in `scopes_are_read_only` so a widened scope in a
# config file is a test failure rather than a surprise.
SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
)


class GoogleError(RuntimeError):
    """Google refused, is unreachable, or is not configured."""


def scopes_are_read_only(scopes: tuple[str, ...] = SCOPES) -> bool:
    return all(scope.endswith(".readonly") for scope in scopes)


@dataclass
class Session:
    """An access token, and how to get a fresh one.

    Refresh is attempted once on a 401 and no further: a token that will not
    refresh is a configuration problem, and retrying it in a loop turns that
    into a rate limit as well.
    """

    access_token: str = ""
    http: httpx.Client | None = None

    def _client(self) -> tuple[httpx.Client, bool]:
        if self.http is not None:
            return self.http, False
        return httpx.Client(timeout=DEFAULT_TIMEOUT), True

    def token(self) -> str:
        return self.access_token or config.google_access_token()

    def refresh(self) -> str:
        """Exchange the refresh token for a new access token."""
        refresh_token = config.google_refresh_token()
        client_id = config.google_client_id()
        client_secret = config.google_client_secret()
        if not (refresh_token and client_id and client_secret):
            raise GoogleError(
                "cannot refresh: GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID and"
                " GOOGLE_CLIENT_SECRET must all be set (see .env.example)"
            )
        client, owned = self._client()
        try:
            response = client.post(
                TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.HTTPError as exc:
            raise GoogleError(f"could not reach Google to refresh the token: {exc}") from exc
        finally:
            if owned:
                client.close()
        if response.status_code != 200:
            raise GoogleError(
                f"token refresh returned {response.status_code}: {response.text[:300]}"
            )
        token = str(response.json().get("access_token", ""))
        if not token:
            raise GoogleError("token refresh returned no access_token")
        self.access_token = token
        return token

    def get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.token()
        if not token:
            raise GoogleError(
                "GOOGLE_ACCESS_TOKEN is unset — steward reads no calendar or mail without it"
            )
        client, owned = self._client()
        try:
            response = self._send(client, url, params, token)
            if response.status_code == 401:
                response = self._send(client, url, params, self.refresh())
        except httpx.HTTPError as exc:
            raise GoogleError(f"could not reach Google: {exc}") from exc
        finally:
            if owned:
                client.close()
        if response.status_code != 200:
            # Verbatim. Against an unverified endpoint this is the difference
            # between a five-minute fix and an afternoon.
            raise GoogleError(f"Google returned {response.status_code}: {response.text[:300]}")
        return dict(response.json())

    def _send(
        self, client: httpx.Client, url: str, params: dict[str, Any] | None, token: str
    ) -> httpx.Response:
        return client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})


# --- calendar ----------------------------------------------------------------


def _google_date(value: dict[str, Any]) -> tuple[date | None, bool]:
    """A Google start/end block. `date` means all-day; `dateTime` means timed."""
    if "date" in value:
        try:
            return date.fromisoformat(str(value["date"])), True
        except ValueError:
            return None, True
    raw = str(value.get("dateTime", ""))
    if not raw:
        return None, False
    try:
        return datetime.fromisoformat(raw).date(), False
    except ValueError:
        return None, False


def to_events(payload: dict[str, Any]) -> list[Event]:
    """Google's JSON into the same `Event` a `.ics` file produces.

    Only five fields survive, and they are the five `extract.ics` already
    admits. `location` becomes a boolean here rather than being carried and
    dropped later: a street address that is never assigned cannot be leaked by
    a future edit.
    """
    events: list[Event] = []
    for item in payload.get("items") or []:
        summary = str(item.get("summary", "")).strip()
        start, all_day = _google_date(item.get("start") or {})
        if not summary or start is None:
            continue
        end, _ = _google_date(item.get("end") or {})
        events.append(
            Event(
                summary=summary,
                start=start,
                end=end,
                all_day=all_day,
                has_location=bool(str(item.get("location", "")).strip()),
            )
        )
    return events


def fetch_events(
    *,
    calendar_id: str = "primary",
    days_ahead: int = 120,
    session: Session | None = None,
    today: date | None = None,
) -> list[Event]:
    """Upcoming events, already reduced to what a calendar may contribute."""
    session = session or Session()
    now = today or datetime.now(UTC).date()
    payload = session.get(
        f"{CALENDAR_BASE}/calendars/{calendar_id}/events",
        params={
            "timeMin": f"{now.isoformat()}T00:00:00Z",
            "timeMax": f"{(now + timedelta(days=days_ahead)).isoformat()}T00:00:00Z",
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
        },
    )
    return to_events(payload)


# --- mail --------------------------------------------------------------------

# Mail worth reading is mail a machine wrote: bank alerts and booking
# confirmations, which `extract/` has deterministic parsers for. A broad query
# would pull in a person's correspondence to be handed to a local model for no
# gain — the narrow one is the privacy measure, not a performance one.
DEFAULT_QUERY = (
    "(from:alerts OR from:noreply OR subject:(receipt OR booking OR balance)) newer_than:30d"
)


def fetch_message_ids(
    *, query: str = DEFAULT_QUERY, limit: int = 20, session: Session | None = None
) -> list[str]:
    session = session or Session()
    payload = session.get(
        f"{GMAIL_BASE}/users/me/messages",
        params={"q": query, "maxResults": max(1, min(100, limit))},
    )
    return [str(item["id"]) for item in (payload.get("messages") or []) if item.get("id")]


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _walk_parts(part: dict[str, Any]) -> str:
    """Plain text only. HTML mail is markup a parser would have to strip, and
    every alert worth reading carries a text/plain alternative."""
    if str(part.get("mimeType", "")) == "text/plain":
        data = str((part.get("body") or {}).get("data", ""))
        if data:
            return _decode(data)
    return "\n".join(_walk_parts(child) for child in (part.get("parts") or []))


def message_text(payload: dict[str, Any]) -> str:
    """The readable body of one message, subject included.

    The subject matters: "Your balance is £340.10" is often the whole alert, and
    a body-only reader misses it.
    """
    payload_part = payload.get("payload") or {}
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in (payload_part.get("headers") or [])
    }
    body = _walk_parts(payload_part) or _decode(
        str((payload_part.get("body") or {}).get("data", ""))
    )
    subject = headers.get("subject", "")
    return f"{subject}\n{body}".strip()


def fetch_message(message_id: str, *, session: Session | None = None) -> str:
    session = session or Session()
    return message_text(session.get(f"{GMAIL_BASE}/users/me/messages/{message_id}"))
