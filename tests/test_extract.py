"""The deterministic extractors: calendar, bank alerts, delivery estimates.

What these mostly assert is what does *not* come out. An extractor's bugs are
usually over-extraction, and over-extraction here means personal data crossing
a boundary the whole project is built on.
"""

from __future__ import annotations

from datetime import date

import pytest

from steward import store
from steward.extract import bank, eta, ics, pipeline
from steward.extract.base import INFERRED, PARSED, slug
from steward.models import FactKind, Role

TODAY = date(2026, 8, 2)

CALENDAR = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Family holiday in Lisbon
DTSTART;VALUE=DATE:20260812
DTEND;VALUE=DATE:20260819
LOCATION:14 Rua do Carmo\\, Lisboa 1200-092
DESCRIPTION:Flight BA1234 dep 06:40. Passport in the blue folder.
ATTENDEE;CN=Rae Whitfield:mailto:rae@example.com
END:VEVENT
BEGIN:VEVENT
SUMMARY:Dentist
DTSTART;VALUE=DATE:20260820
END:VEVENT
BEGIN:VEVENT
SUMMARY:Last year's thing
DTSTART;VALUE=DATE:20250101
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def person(db: str) -> int:
    return store.insert_person(name="Ana Whitfield", role=Role.SPENDER, db_path=db)


# --- ics ---------------------------------------------------------------------


def test_folded_lines_are_rejoined() -> None:
    """Miss RFC 5545 folding and a long summary becomes two junk properties."""
    folded = "SUMMARY:A very long summary that the\n  calendar wrapped"

    assert ics.unfold(folded) == ["SUMMARY:A very long summary that the calendar wrapped"]


def test_escapes_are_undone_in_the_right_order() -> None:
    """Backslash last, or every other unescape eats its own escape character."""
    assert ics.unescape(r"a\,b\;c\nd\\e") == "a,b;c\nd\\e"


def test_events_are_read_with_their_dates() -> None:
    events = ics.parse_events(CALENDAR)

    assert [event.summary for event in events] == [
        "Family holiday in Lisbon",
        "Dentist",
        "Last year's thing",
    ]
    assert events[0].start == date(2026, 8, 12)
    assert events[0].end == date(2026, 8, 19)
    assert events[0].all_day is True


def test_a_holiday_becomes_an_away_fact() -> None:
    candidates = ics.extract(CALENDAR, today=TODAY)

    away = [c for c in candidates if c.key.startswith("away_")]
    assert len(away) == 1
    assert away[0].kind == FactKind.SCHEDULE
    assert away[0].value == "away: Family holiday in Lisbon (2026-08-12 to 2026-08-19)"
    assert away[0].source == PARSED


def test_the_address_description_and_attendees_never_come_out() -> None:
    """The richest fields in the file, and the ones a person would be most upset
    to find in a log."""
    extracted = " ".join(c.value + c.key for c in ics.extract(CALENDAR, today=TODAY))

    assert "Rua do Carmo" not in extracted
    assert "rae@example.com" not in extracted
    assert "BA1234" not in extracted
    assert "Passport" not in extracted


def test_a_location_is_recorded_only_as_a_boolean() -> None:
    events = ics.parse_events(CALENDAR)

    assert events[0].has_location is True
    assert events[1].has_location is False


def test_past_events_are_dropped(db: str) -> None:
    """An agent reasoning about last March's dentist appointment is carrying
    weight for no one."""
    values = [c.value for c in ics.extract(CALENDAR, today=TODAY)]

    assert not any("Last year" in value for value in values)


def test_distant_events_are_ignored() -> None:
    assert ics.extract(CALENDAR, today=TODAY, horizon_days=5) == []


def test_a_malformed_date_does_not_crash_the_import() -> None:
    broken = "BEGIN:VEVENT\nSUMMARY:Broken\nDTSTART;VALUE=DATE:20261340\nEND:VEVENT"

    assert ics.parse_events(broken) == []


def test_an_event_with_no_summary_is_skipped() -> None:
    nameless = "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20260812\nEND:VEVENT"

    assert ics.parse_events(nameless) == []


def test_reimporting_the_same_calendar_updates_rather_than_duplicates(person: int, db: str) -> None:
    """Stable slugs exist for exactly this."""
    for _ in range(3):
        pipeline.commit(person, pipeline.extract_all(CALENDAR), db_path=db)

    keys = [fact["key"] for fact in store.list_facts(person, db_path=db)]
    assert len(keys) == len(set(keys))


def test_slugs_are_stable_and_bounded() -> None:
    assert slug("Family holiday in Lisbon!!") == "family_holiday_in_lisbon"
    assert slug("") == "untitled"
    assert len(slug("x" * 200)) <= 32


# --- bank alerts -------------------------------------------------------------


def test_a_balance_and_a_spend_are_read() -> None:
    alert = "HSBC: You spent £12.50 at TESCO STORES on 02/08. Your balance is £340.10"

    candidates = {c.key: c.value for c in bank.extract(alert)}

    assert candidates["last_spend"].startswith("£12.50 GBP")
    assert "Tesco Stores" in candidates["last_spend"]
    assert candidates["balance"] == "£340.10 GBP"


def test_amounts_are_integer_minor_units() -> None:
    """Floats are how money quietly becomes 12.499999999999998."""
    assert bank.find_amounts("£12.50")[0][1].minor_units == 1250
    assert bank.find_amounts("$1,234.56")[0][1].minor_units == 123456
    assert bank.find_amounts("€9")[0][1].minor_units == 900


def test_a_trailing_currency_code_is_understood() -> None:
    assert bank.find_amounts("you paid 45.00 GBP")[0][1].currency == "GBP"


def test_the_merchant_name_stops_where_the_sentence_does() -> None:
    """Caught by hand: the capture ran straight through "TESCO STORES. Balance"
    and stored the next clause as part of the shop's name."""
    alert = "HSBC: You spent £12.50 at TESCO STORES on 02/08. Your balance is £340.10"

    spend = next(c for c in bank.extract(alert) if c.key == "last_spend")

    assert spend.value == "£12.50 GBP at Tesco Stores"


def test_a_dot_inside_a_merchant_name_survives() -> None:
    alert = "Monzo: you spent £30.00 at AMAZON.CO.UK. Balance £70.00"

    spend = next(c for c in bank.extract(alert) if c.key == "last_spend")

    assert "Amazon.Co.Uk" in spend.value
    assert "Balance" not in spend.value


def test_a_message_with_no_money_yields_nothing() -> None:
    assert bank.extract("Your statement is ready to view online") == []


def test_the_card_number_is_never_extracted() -> None:
    alert = "Card ending 4242424242424242 spent £9.99. Balance £100.00"

    extracted = " ".join(c.value for c in bank.extract(alert))

    assert "4242" not in extracted


# --- delivery estimates ------------------------------------------------------

LONDON = eta.Point(51.5074, -0.1278)
MANCHESTER = eta.Point(53.4808, -2.2426)
SYDNEY = eta.Point(-33.8688, 151.2093)


def test_what_leaves_this_module_is_days_and_a_zone_and_nothing_else() -> None:
    """A distance from a known warehouse is a circle; three are a home address.
    Any field added to Delivery has to survive that question."""
    delivery = eta.estimate(origin=MANCHESTER, destination=LONDON)

    assert set(vars(delivery)) == {"days", "zone", "carrier"}
    assert isinstance(delivery.days, int)


def test_further_away_takes_longer() -> None:
    near = eta.estimate(origin=LONDON, destination=LONDON)
    far = eta.estimate(origin=SYDNEY, destination=LONDON)

    assert near.days < far.days
    assert near.zone == "local"
    assert far.zone == "international"


def test_express_beats_standard_from_the_same_place() -> None:
    standard = eta.estimate(origin=MANCHESTER, destination=LONDON, carrier=eta.STANDARD)
    express = eta.estimate(origin=MANCHESTER, destination=LONDON, carrier=eta.EXPRESS)

    assert express.days < standard.days


def test_nothing_ever_arrives_in_zero_days() -> None:
    assert eta.estimate(origin=LONDON, destination=LONDON, carrier=eta.EXPRESS).days >= 1


def test_a_delivery_describes_itself_in_human_terms() -> None:
    assert eta.estimate(origin=LONDON, destination=LONDON).describe() == "arrives tomorrow"
    assert "about" in eta.estimate(origin=SYDNEY, destination=LONDON).describe()


def test_ranking_is_stable_and_ties_do_not_favour_the_first_listed() -> None:
    fast = eta.Delivery(days=2, zone="local", carrier="standard")
    slow = eta.Delivery(days=5, zone="far", carrier="standard")

    assert eta.rank([("zeta", fast), ("alpha", slow), ("alpha2", fast)]) == [
        ("alpha2", fast),
        ("zeta", fast),
        ("alpha", slow),
    ]


# --- the pipeline ------------------------------------------------------------


def test_a_calendar_is_recognised_and_never_reaches_the_model() -> None:
    """Handing a machine-written document to a language model would be slower,
    non-deterministic, and occasionally wrong about a date."""
    extraction = pipeline.extract_all(CALENDAR)

    assert extraction.extractor == "ics"


def test_a_bank_alert_is_recognised() -> None:
    alert = "Barclays: payment of £8.20 at CO-OP. Balance £91.80"

    assert pipeline.extract_all(alert).extractor == "bank"


def test_a_long_document_is_not_mistaken_for_a_bank_alert() -> None:
    """ "A bank alert is a notification, not a document" — a rambling email that
    happens to mention a balance must fall through to the model."""
    essay = "balance " + ("word " * 200) + "£10.00"

    assert not pipeline.looks_like_bank_alert(essay)


def test_free_form_text_falls_through_to_the_local_model(db: str) -> None:
    extraction = pipeline.extract_all("the boiler man comes Thursday", use_local_model=False)

    assert extraction.extractor == "none"
    assert extraction.candidates == []


def test_empty_material_yields_nothing() -> None:
    assert pipeline.extract_all("   ").candidates == []


def test_committing_writes_facts_with_the_source_they_were_learned_by(person: int, db: str) -> None:
    """A system that forgets which of its beliefs it guessed will eventually
    spend money on one."""
    result = pipeline.ingest(person, CALENDAR, db_path=db)

    assert result["extractor"] == "ics"
    stored = store.list_facts(person, db_path=db)
    assert stored
    assert all(fact["source"] == PARSED for fact in stored)
    assert INFERRED not in {fact["source"] for fact in stored}
