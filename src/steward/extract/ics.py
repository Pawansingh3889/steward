"""Reading a calendar without a calendar library, and without leaking one.

An .ics file is one of the most personal documents a person owns: attendee
email addresses, meeting descriptions, physical locations, medical
appointments by name. The plan's promise is that none of it leaves the device,
and the way to keep that promise is for this module to be the only thing that
ever sees the file, and for what it returns to be short and structural — "away
2026-08-12 to 2026-08-15" rather than the event that says why.

Hand-rolled rather than `icalendar`, for the reason the project ports instead of
rewriting elsewhere: a dependency here would be a third party in the room with
the rawest data in the system, and RFC 5545's line folding and escaping are
eighty lines of well-specified string handling.

What is deliberately *not* extracted: descriptions, attendees, organisers,
URLs, and any location more precise than the fact that one exists. They are the
richest fields and the ones a person would be most upset to find in a log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..models import FactKind
from .base import PARSED, Candidate, slug

# An all-day event, or a timed one. Both may carry a trailing Z or a TZID.
_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})")

# Summaries that mean "this person is not at home", which is the only thing
# this module tries to conclude. Everything else becomes a plain busy marker.
_AWAY_WORDS = ("holiday", "annual leave", "vacation", "trip", "away", "travel", "flight")


@dataclass(frozen=True)
class Event:
    summary: str
    start: date
    end: date | None
    all_day: bool
    has_location: bool

    @property
    def days(self) -> int:
        if self.end is None:
            return 1
        return max(1, (self.end - self.start).days)

    @property
    def is_away(self) -> bool:
        lowered = self.summary.lower()
        return any(word in lowered for word in _AWAY_WORDS)


def unfold(text: str) -> list[str]:
    """RFC 5545 line folding: a line beginning with space or tab continues the
    previous one. Miss this and a long summary silently becomes two junk
    properties."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def unescape(value: str) -> str:
    """RFC 5545 escaping, in the order that matters: backslash last, or every
    other unescape would eat its own escape character."""
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _parse_date(value: str) -> date | None:
    match = _DATE.match(value.strip())
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def _split_property(line: str) -> tuple[str, str, str]:
    """`DTSTART;VALUE=DATE:20260812` → ("DTSTART", "VALUE=DATE", "20260812")."""
    name, _, value = line.partition(":")
    name, _, params = name.partition(";")
    return name.strip().upper(), params, value


def parse_events(text: str) -> list[Event]:
    """Every VEVENT in the file, reduced to the five fields we will admit."""
    events: list[Event] = []
    current: dict[str, object] | None = None
    for line in unfold(text):
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            current = {}
            continue
        if stripped == "END:VEVENT":
            if current is not None and current.get("summary") and current.get("start"):
                start = current["start"]
                end = current.get("end")
                assert isinstance(start, date)
                assert end is None or isinstance(end, date)
                events.append(
                    Event(
                        summary=str(current["summary"]),
                        start=start,
                        end=end,
                        all_day=bool(current.get("all_day")),
                        has_location=bool(current.get("has_location")),
                    )
                )
            current = None
            continue
        if current is None:
            continue

        name, params, value = _split_property(stripped)
        if name == "SUMMARY":
            current["summary"] = unescape(value).strip()
        elif name == "DTSTART":
            current["start"] = _parse_date(value)
            current["all_day"] = "VALUE=DATE" in params.upper()
        elif name == "DTEND":
            current["end"] = _parse_date(value)
        elif name == "LOCATION":
            # Recorded as a boolean and nothing else. The value itself is a
            # street address, and a street address is the thing we are here to
            # keep out of every downstream log.
            current["has_location"] = bool(value.strip())
    return events


def extract(raw: str, *, horizon_days: int = 120, today: date | None = None) -> list[Candidate]:
    """Calendar file in, schedule facts out.

    Past events are dropped and distant ones ignored: memory is for deciding
    what to do next, and an agent reasoning about last March's dentist
    appointment is carrying weight for no one.
    """
    # UTC, like every other timestamp here. It can be a few hours out from the
    # person's own "today" at the edges of a day, which for a 120-day horizon
    # changes nothing — and `today=` is injectable so tests never depend on it.
    now = today or datetime.now(UTC).date()
    candidates: list[Candidate] = []
    for event in parse_events(raw):
        if event.start < now:
            continue
        if (event.start - now).days > horizon_days:
            continue
        if event.is_away:
            span = event.start.isoformat()
            if event.end and event.days > 1:
                span = f"{event.start.isoformat()} to {event.end.isoformat()}"
            candidates.append(
                Candidate(
                    kind=FactKind.SCHEDULE,
                    key=f"away_{slug(event.summary, limit=24)}",
                    # The summary is kept because the person wrote it and will
                    # want to recognise it. The location, description and
                    # attendees are not.
                    value=f"away: {event.summary} ({span})",
                    source=PARSED,
                )
            )
        elif event.all_day:
            candidates.append(
                Candidate(
                    kind=FactKind.SCHEDULE,
                    key=f"day_{event.start.isoformat()}_{slug(event.summary, limit=16)}",
                    value=f"{event.summary} (all day {event.start.isoformat()})",
                    source=PARSED,
                )
            )
    return candidates
