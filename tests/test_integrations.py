"""Phase 7: a real calendar drives a real plan.

The integrations fetch and hand straight to `extract/`. What is asserted most
here is what they *do not* do: keep raw material, widen a scope, write to
anybody's account, or let a calendar contribute anything a `.ics` file could not.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from steward import store
from steward.extract import ics
from steward.integrations import google, prices, sync
from steward.models import Role
from steward.plan import goals

TODAY = date(2026, 8, 2)

CALENDAR_PAYLOAD = {
    "items": [
        {
            "summary": "Family holiday in Lisbon",
            "start": {"date": "2026-09-12"},
            "end": {"date": "2026-09-19"},
            "location": "14 Rua do Carmo, Lisboa 1200-092",
            "description": "Flight BA1234. Call +447442382622 on arrival.",
            "attendees": [{"email": "rae@example.com"}],
        },
        {
            "summary": "Dentist",
            "start": {"dateTime": "2026-08-20T09:30:00+01:00"},
            "end": {"dateTime": "2026-08-20T10:00:00+01:00"},
        },
        {"summary": "", "start": {"date": "2026-08-25"}},  # nameless: skipped
        {"summary": "Broken", "start": {}},  # dateless: skipped
    ]
}


@pytest.fixture
def person(db: str) -> int:
    sponsor = store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, db_path=db)
    return store.insert_person(
        name="Ana Whitfield", role=Role.SPENDER, sponsor_id=sponsor, db_path=db
    )


def session(handler) -> google.Session:
    return google.Session(
        access_token="tok", http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def calendar_session(payload=None) -> google.Session:
    return session(lambda request: httpx.Response(200, json=payload or CALENDAR_PAYLOAD))


# --- scopes and posture ------------------------------------------------------


def test_every_scope_is_read_only() -> None:
    """A widened scope should be a test failure, not a surprise."""
    assert google.scopes_are_read_only()
    assert not google.scopes_are_read_only(("https://www.googleapis.com/auth/gmail.send",))


def test_there_is_no_way_to_send_delete_or_write() -> None:
    """An agent that could mail from a person's own account is a far larger
    promise than this project has made."""
    names = [name for name in dir(google) if not name.startswith("_")]

    for forbidden in ("send", "delete", "trash", "insert", "update", "modify"):
        assert not any(forbidden in name.lower() for name in names), forbidden


def test_agent_does_not_import_integrations() -> None:
    """No path from an inbox to a model prompt."""
    from pathlib import Path

    from steward import agent

    for source in Path(agent.__file__).parent.glob("*.py"):
        for line in source.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "integrations" not in stripped, f"{source.name}: {stripped}"


# --- the calendar ------------------------------------------------------------


def test_google_events_become_the_same_type_an_ics_file_produces() -> None:
    events = google.to_events(CALENDAR_PAYLOAD)

    assert [event.summary for event in events] == ["Family holiday in Lisbon", "Dentist"]
    assert events[0].all_day is True
    assert events[1].all_day is False  # dateTime, not date
    assert events[0].start == date(2026, 9, 12)


def test_a_location_becomes_a_boolean_and_never_a_string() -> None:
    """A street address that is never assigned cannot be leaked by a later edit."""
    events = google.to_events(CALENDAR_PAYLOAD)

    assert events[0].has_location is True
    assert "Rua do Carmo" not in str([vars(event) for event in events])


def test_the_calendar_policy_is_the_same_one_ics_files_get(db: str, person: int) -> None:
    """One function decides what a calendar may contribute, so a second source
    cannot arrive with a second, laxer policy."""
    result = sync.pull_calendar(person, session=calendar_session(), today=TODAY, db_path=db)

    stored = " ".join(
        f"{fact['key']} {fact['value']}" for fact in store.list_facts(person, db_path=db)
    )
    assert result["events_read"] == 2
    assert "away: Family holiday in Lisbon" in stored
    for leaked in ("Rua do Carmo", "BA1234", "+447442382622", "rae@example.com"):
        assert leaked not in stored


def test_both_calendar_sources_agree(db: str) -> None:
    """The same trip, as a .ics file and as Google JSON, yields the same fact."""
    ics_text = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Family holiday in Lisbon\n"
        "DTSTART;VALUE=DATE:20260912\nDTEND;VALUE=DATE:20260919\n"
        "LOCATION:14 Rua do Carmo\nEND:VEVENT\nEND:VCALENDAR"
    )

    from_ics = ics.extract(ics_text, today=TODAY)
    from_google = ics.candidates_from(google.to_events(CALENDAR_PAYLOAD), today=TODAY)

    away = [c for c in from_google if c.key.startswith("away_")]
    assert [c.value for c in from_ics] == [c.value for c in away]


def test_events_with_no_name_or_no_date_are_skipped() -> None:
    assert len(google.to_events(CALENDAR_PAYLOAD)) == 2


# --- the exit criterion ------------------------------------------------------


def test_a_real_calendar_drives_a_real_plan(db: str, person: int) -> None:
    """Phase 7's exit criterion. The calendar supplies the deadline; the person
    supplies what it is worth."""
    plan = sync.trip_from_calendar(
        person,
        target_cents=60000,
        session=calendar_session(),
        today=TODAY,
        db_path=db,
    )

    assert plan is not None
    assert plan["name"] == "Family holiday in Lisbon"
    assert plan["from_calendar"]["departs"] == "2026-09-12"
    assert plan["finish"] == "2026-09-12"  # the deadline came from the diary
    assert plan["kind"] == goals.TRIP
    # And it is still only a draft.
    assert plan["status"] == store.PLAN_DRAFT


def test_a_calendar_plan_is_never_started_automatically(db: str, person: int) -> None:
    """Reading a holiday off a diary and committing money against it would be
    deciding, from a calendar entry, that somebody is definitely going."""
    plan = sync.trip_from_calendar(
        person, target_cents=60000, session=calendar_session(), today=TODAY, db_path=db
    )

    assert store.get_plan(plan["plan_id"], db_path=db)["activated_ts"] == ""


def test_no_trip_in_the_calendar_invents_nothing(db: str, person: int) -> None:
    quiet = {"items": [{"summary": "Dentist", "start": {"date": "2026-08-20"}}]}

    assert (
        sync.trip_from_calendar(
            person, target_cents=60000, session=calendar_session(quiet), today=TODAY, db_path=db
        )
        is None
    )
    assert store.list_plans(person, db_path=db) == []


def test_the_soonest_trip_is_the_one_planned_for(db: str, person: int) -> None:
    events = google.to_events(
        {
            "items": [
                {"summary": "Trip to Rome", "start": {"date": "2027-01-05"}},
                {"summary": "Holiday in Lisbon", "start": {"date": "2026-09-12"}},
            ]
        }
    )

    plan = sync.trip_from_calendar(
        person, target_cents=60000, events=events, today=TODAY, db_path=db
    )

    assert plan["name"] == "Holiday in Lisbon"


# --- mail --------------------------------------------------------------------


def _mail_handler(bodies: dict[str, str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": key} for key in bodies]})
        message_id = request.url.path.rsplit("/", 1)[-1]
        import base64

        encoded = base64.urlsafe_b64encode(bodies[message_id].encode()).decode()
        return httpx.Response(
            200,
            json={
                "payload": {
                    "headers": [{"name": "Subject", "value": "Your statement"}],
                    "mimeType": "text/plain",
                    "body": {"data": encoded},
                }
            },
        )

    return handler


def test_a_bank_alert_in_the_inbox_becomes_a_fact(db: str, person: int) -> None:
    handler = _mail_handler(
        {"m1": "HSBC: You spent £12.50 at TESCO STORES. Your balance is £340.10"}
    )

    result = sync.pull_mail(person, session=session(handler), use_local_model=False, db_path=db)

    assert result["messages_read"] == 1
    assert result["read_by"] == ["bank"]
    values = {f["key"]: f["value"] for f in store.list_facts(person, db_path=db)}
    assert values["balance"] == "£340.10 GBP"


def test_the_subject_line_is_read_too() -> None:
    """ "Your balance is £340.10" is often the whole alert."""
    payload = {
        "payload": {
            "headers": [{"name": "Subject", "value": "Balance: £340.10"}],
            "mimeType": "text/plain",
            "body": {"data": ""},
        }
    }

    assert "Balance: £340.10" in google.message_text(payload)


def test_the_default_query_is_narrow() -> None:
    """A broad query would pull a person's correspondence in to be handed to a
    local model for no gain. The narrowness is the privacy measure."""
    assert "newer_than:30d" in google.DEFAULT_QUERY
    assert "noreply" in google.DEFAULT_QUERY


def test_mail_bodies_are_never_returned(db: str, person: int) -> None:
    """What comes back is a count and the facts — all a surface should print
    about somebody's inbox."""
    body = "HSBC: card ending 4242424242424242 spent £9.99. Balance £12.00"
    handler = _mail_handler({"m1": body})

    result = sync.pull_mail(person, session=session(handler), use_local_model=False, db_path=db)

    assert "4242424242424242" not in json.dumps(result)
    assert set(result) == {"messages_read", "read_by", "facts"}


# --- tokens ------------------------------------------------------------------


def test_an_unset_token_says_what_is_missing() -> None:
    with pytest.raises(google.GoogleError, match="GOOGLE_ACCESS_TOKEN is unset"):
        google.Session().get(f"{google.CALENDAR_BASE}/calendars/primary/events")


def test_a_401_refreshes_once_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            calls.append("refresh")
            return httpx.Response(200, json={"access_token": "fresh"})
        calls.append("get")
        if len(calls) == 1:
            return httpx.Response(401, text="expired")
        return httpx.Response(200, json={"items": []})

    google.Session(
        access_token="stale", http=httpx.Client(transport=httpx.MockTransport(handler))
    ).get(f"{google.CALENDAR_BASE}/calendars/primary/events")

    assert calls == ["get", "refresh", "get"]


def test_refresh_without_credentials_says_which_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "")

    with pytest.raises(google.GoogleError, match="GOOGLE_CLIENT_ID"):
        google.Session(access_token="x").refresh()


def test_googles_error_is_relayed_verbatim() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, text="insufficientPermissions")
    )

    with pytest.raises(google.GoogleError, match="insufficientPermissions"):
        google.Session(access_token="tok", http=httpx.Client(transport=transport)).get(
            f"{google.CALENDAR_BASE}/calendars/primary/events"
        )


def test_google_tokens_are_on_the_redaction_denylist(monkeypatch: pytest.MonkeyPatch) -> None:
    """They read a person's calendar and mail; the send-time assert must stop
    one reaching a model prompt like any other key."""
    from steward import config

    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "ya29.secret-token")

    assert "ya29.secret-token" in config.secret_values()


# --- live prices -------------------------------------------------------------

JSON_LD_PAGE = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Hand Soap",
 "offers":{"@type":"Offer","price":"4.50","priceCurrency":"GBP",
 "availability":"https://schema.org/InStock"}}
</script></head><body>£4.50</body></html>"""


def test_a_price_is_read_from_json_ld() -> None:
    found = prices.read(JSON_LD_PAGE)

    assert found.price_cents == 450
    assert found.currency == "GBP"
    assert found.source == "json-ld"
    assert found.availability == "InStock"


def test_a_price_nested_in_a_graph_is_found() -> None:
    """Schema.org nests through @graph, arrays and embedded products, and the
    price is as often three levels down as at the top."""
    page = (
        '<script type="application/ld+json">'
        '{"@graph":[{"@type":"WebPage"},{"@type":"Product","offers":'
        '[{"price":"12.99","priceCurrency":"USD"}]}]}</script>'
    )

    assert prices.read(page).price_cents == 1299


def test_a_malformed_block_does_not_hide_a_good_one() -> None:
    page = (
        '<script type="application/ld+json">{not json}</script>'
        '<script type="application/ld+json">'
        '{"@type":"Product","offers":{"price":"3.20","priceCurrency":"GBP"}}</script>'
    )

    assert prices.read(page).price_cents == 320


def test_microdata_is_the_fallback() -> None:
    page = (
        '<div itemprop="price" content="7.25"></div>'
        '<div itemprop="priceCurrency" content="GBP"></div>'
    )

    found = prices.read(page)
    assert found.price_cents == 725
    assert found.source == "microdata"


def test_a_page_with_no_structured_price_returns_nothing() -> None:
    """A number scraped from visible text is a number nobody promised, and this
    one ends up in a policy decision and then on somebody's card."""
    assert prices.read("<html><body><h1>Only £4.50 today!</h1></body></html>") is None


def test_money_is_read_as_decimal_not_float() -> None:
    """`float("19.99") * 100` is 1998.9999999999998."""
    assert prices.to_cents("19.99") == 1999
    assert prices.to_cents("1,234.56") == 123456
    assert prices.to_cents("0") is None
    assert prices.to_cents("-5") is None
    assert prices.to_cents("free") is None
    assert prices.to_cents(None) is None
    assert prices.to_cents(True) is None


def test_a_page_that_will_not_load_raises() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, text="gone"))

    with pytest.raises(prices.PriceError, match="404"):
        prices.fetch("https://shop.example/x", http=httpx.Client(transport=transport))


def test_fetching_reads_the_page() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=JSON_LD_PAGE))

    found = prices.fetch("https://shop.example/x", http=httpx.Client(transport=transport))

    assert found.price_cents == 450
