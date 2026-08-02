"""Bringing a real account in, and turning a real trip into a real plan.

Two jobs, and the second is phase 7's point.

`pull_calendar` and `pull_mail` fetch and hand straight to `extract/`. Nothing
raw is stored and nothing raw is returned — what comes back is a count and the
facts that were learned, which is all a surface should ever print about
somebody's inbox.

`trip_from_calendar` is the connective bit: an away event in the calendar
already knows the dates, so it can propose a trip plan whose deadline is the
departure and whose schedule is however many periods fit before it. **It
proposes a draft** — the person still says what it is worth and still starts it.
An agent that read a holiday off a calendar and began committing money against
it would be deciding, from a diary entry, that somebody is definitely going.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from ..extract import ics, pipeline
from ..extract.ics import Event
from ..models import utc_today
from ..plan import goals
from . import google
from .google import Session


def pull_calendar(
    person_id: int,
    *,
    calendar_id: str = "primary",
    days_ahead: int = 120,
    session: Session | None = None,
    today: date | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Read the calendar and keep only what a calendar may contribute."""
    events = google.fetch_events(
        calendar_id=calendar_id, days_ahead=days_ahead, session=session, today=today
    )
    candidates = ics.candidates_from(events, horizon_days=days_ahead, today=today)
    written = pipeline.commit(
        person_id,
        pipeline.Extraction(candidates=candidates, extractor="google_calendar"),
        db_path=db_path,
    )
    return {"events_read": len(events), "facts": written}


def pull_mail(
    person_id: int,
    *,
    query: str = google.DEFAULT_QUERY,
    limit: int = 20,
    session: Session | None = None,
    use_local_model: bool = True,
    http: httpx.Client | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Read matching mail through the extraction pipeline.

    Each message is fetched, parsed and discarded in turn. Nothing accumulates
    the bodies, so there is no list of a person's mail anywhere in this process
    for a later bug to hand to something.
    """
    session = session or Session()
    ids = google.fetch_message_ids(query=query, limit=limit, session=session)
    written: list[dict[str, Any]] = []
    read_by: list[str] = []
    for message_id in ids:
        body = google.fetch_message(message_id, session=session)
        if not body.strip():
            continue
        extraction = pipeline.extract_all(body, use_local_model=use_local_model, http=http)
        read_by.append(extraction.extractor)
        written.extend(pipeline.commit(person_id, extraction, db_path=db_path))
    return {"messages_read": len(ids), "read_by": read_by, "facts": written}


def away_events(events: list[Event], *, today: date | None = None) -> list[Event]:
    """Upcoming events that mean the person is not at home."""
    now = today or utc_today()
    return [event for event in events if event.is_away and event.start >= now]


def trip_from_calendar(
    person_id: int,
    *,
    target_cents: int,
    events: list[Event] | None = None,
    calendar_id: str = "primary",
    session: Session | None = None,
    cadence: str = "monthly",
    currency: str = "GBP",
    today: date | None = None,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    """Propose a trip plan sized to the next away event in the calendar.

    Returns None when there is nothing to plan for, rather than inventing a
    trip. The deadline is the departure date, so the schedule falls out of the
    calendar and the person is left with the one question only they can answer:
    what it needs to be worth.
    """
    now = today or utc_today()
    if events is None:
        events = google.fetch_events(calendar_id=calendar_id, session=session, today=now)
    upcoming = away_events(events, today=now)
    if not upcoming:
        return None
    trip = min(upcoming, key=lambda event: event.start)
    return {
        **goals.propose(
            person_id=person_id,
            name=trip.summary,
            target_cents=target_cents,
            finish=trip.start,
            cadence=cadence,
            currency=currency,
            kind=goals.TRIP,
            start=now,
            db_path=db_path,
        ),
        "from_calendar": {"summary": trip.summary, "departs": trip.start.isoformat()},
    }
