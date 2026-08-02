"""Phase 2's exit criterion: seed raw PII, assert its absence in every request.

The other privacy tests check one mechanism each. This one checks the *system*:
it plants marked personal data in every kind of raw material steward accepts,
runs the whole path — extract, commit, recall, converse — and then reads every
byte the process tried to send to OpenAI and looks for the markers.

It is written as a property rather than a set of examples on purpose. The
examples were chosen by the same person who wrote the redactor, which makes them
exactly the cases the redactor already handles; the randomized sweep at the
bottom combines canaries into material nobody designed the defences against.

What this test does NOT claim, stated plainly because a security test that
oversells itself is worse than none: a street address written in free-form prose
and echoed back by the local model into an `inferred` fact would not be caught
by any pattern here. Addresses have no reliable syntax. The defences against
that one are structural and partial — `extract/ics.py` never emits a LOCATION,
and `extract/eta.py` never emits a coordinate or a distance — and both are
asserted below. It remains the sharpest edge in the system.
"""

from __future__ import annotations

import random

import pytest

from steward import store
from steward.agent import loop
from steward.extract import eta, pipeline
from steward.memory import episodic
from steward.models import FactKind, Role

from .agent_stub import OpenAIStub, completion

# Every canary is a value that must never appear in an outgoing request body.
# The key names what it is, so a failure says which class of data escaped.
CANARIES = {
    # Enrolled people. These are covered by the alias list, which knows their
    # exact values from the database.
    "name": "Ana Whitfield",
    "sponsor_name": "Rae Whitfield",
    "phone": "+447700900002",
    "sponsor_phone": "+447700900001",
    "email": "ana@example.com",
    "sponsor_email": "rae@example.com",
    # Nobody the database has ever heard of, which is the harder case and the
    # commoner one: a person's messages are full of other people's contact
    # details, and those people never enrolled in anything. Only the pattern
    # classes stand between these and the wire.
    #
    # An earlier version of this file had no such canaries, and deleting the
    # E.164 pattern from the redactor did not fail a single test — the enrolled
    # phone was being caught by the alias list instead, so the pattern it was
    # meant to exercise was never exercised at all.
    "stranger_phone": "+447442382622",
    "stranger_email": "plumber@trade.example.org",
    "pan": "4242424242424242",
    "coordinates": "51.5074, -0.1278",
    "api_key": "sk-live-canary-abc123",
}

CALENDAR = f"""BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Family holiday
DTSTART;VALUE=DATE:20260812
DTEND;VALUE=DATE:20260819
LOCATION:14 Rua do Carmo, Lisboa
DESCRIPTION:Call {CANARIES["phone"]} on arrival. Card {CANARIES["pan"]}.
ATTENDEE;CN={CANARIES["sponsor_name"]}:mailto:{CANARIES["sponsor_email"]}
END:VEVENT
END:VCALENDAR
"""

BANK_ALERT = (
    f"HSBC: card ending {CANARIES['pan']} spent £12.50 at TESCO. Balance £340.10. "
    f"Queries: {CANARIES['stranger_phone']}"
)

FREE_TEXT = (
    f"I'm {CANARIES['name']}, reach me on {CANARIES['phone']} or {CANARIES['email']}. "
    f"Phone's at {CANARIES['coordinates']}. Out of soap. "
    f"The plumber is {CANARIES['stranger_phone']}, or {CANARIES['stranger_email']}."
)


@pytest.fixture(autouse=True)
def canary_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API key is itself a canary: a redactor that forwarded it would be
    handing OpenAI a credential in a message body."""
    monkeypatch.setenv("OPENAI_API_KEY", CANARIES["api_key"])


@pytest.fixture
def household(db: str) -> int:
    sponsor = store.insert_person(
        name=CANARIES["sponsor_name"],
        role=Role.SPONSOR,
        phone=CANARIES["sponsor_phone"],
        email=CANARIES["sponsor_email"],
        db_path=db,
    )
    return store.insert_person(
        name=CANARIES["name"],
        role=Role.SPENDER,
        sponsor_id=sponsor,
        phone=CANARIES["phone"],
        email=CANARIES["email"],
        db_path=db,
    )


def assert_no_canaries(sent: str) -> None:
    leaked = [name for name, value in CANARIES.items() if value in sent]
    assert not leaked, f"these crossed the boundary: {leaked}"


def converse(db: str, person_id: int, question: str) -> OpenAIStub:
    """Run one conversation in which the model reads everything it can."""
    stub = OpenAIStub(
        [
            completion(tool_calls=[("recall_facts", {})]),
            completion(tool_calls=[("search_memory", {"query": "soap"})]),
            completion(tool_calls=[("recent_conversation", {})]),
            completion(content="Understood."),
        ]
    )
    loop.run(question, person_id=person_id, db_path=db, http=stub.client())
    return stub


# --- the seeded path ---------------------------------------------------------


def test_a_calendar_full_of_pii_reaches_no_request_body(db: str, household: int) -> None:
    pipeline.ingest(household, CALENDAR, db_path=db)

    stub = converse(db, household, "what's coming up?")

    assert_no_canaries(stub.bodies())


def test_a_bank_alert_full_of_pii_reaches_no_request_body(db: str, household: int) -> None:
    pipeline.ingest(household, BANK_ALERT, db_path=db)

    stub = converse(db, household, "how am I doing for money?")

    assert_no_canaries(stub.bodies())


def test_raw_conversation_stored_as_episodes_reaches_no_request_body(
    db: str, household: int
) -> None:
    """Episodes are stored unredacted by design — this is the test that the
    design is safe, because redaction happens on the way out."""
    episodic.remember(person_id=household, text=FREE_TEXT, db_path=db)

    stub = converse(db, household, "what did I tell you?")

    assert_no_canaries(stub.bodies())
    # And confirm the episode really was stored raw, so this proved something.
    assert CANARIES["phone"] in store.list_episodes(household, db_path=db)[0]["text"]


def test_pii_stated_directly_as_a_fact_reaches_no_request_body(db: str, household: int) -> None:
    for key, value in CANARIES.items():
        store.upsert_fact(
            person_id=household,
            kind=FactKind.IDENTITY,
            key=key,
            value=f"my {key} is {value}",
            db_path=db,
        )

    stub = converse(db, household, "what do you know about me?")

    assert_no_canaries(stub.bodies())


def test_the_question_itself_is_scrubbed(db: str, household: int) -> None:
    stub = converse(db, household, FREE_TEXT)

    assert_no_canaries(stub.bodies())


def test_everything_at_once(db: str, household: int) -> None:
    """The realistic case: a person who has been using this for a while."""
    pipeline.ingest(household, CALENDAR, db_path=db)
    pipeline.ingest(household, BANK_ALERT, db_path=db)
    episodic.remember(person_id=household, text=FREE_TEXT, db_path=db)
    store.insert_turn(person_id=household, speaker="person", text=FREE_TEXT, db_path=db)

    stub = converse(db, household, FREE_TEXT)

    assert_no_canaries(stub.bodies())


# --- structural defences the patterns cannot provide -------------------------


def test_a_calendar_location_is_never_committed_to_memory(db: str, household: int) -> None:
    """Street addresses have no reliable syntax, so no pattern will catch one.
    The defence is that this field is never extracted in the first place."""
    pipeline.ingest(household, CALENDAR, db_path=db)

    stored = " ".join(fact["value"] for fact in store.list_facts(household, db_path=db))

    assert "Rua do Carmo" not in stored
    assert "Lisboa" not in stored


def test_a_delivery_estimate_carries_no_coordinate_or_distance() -> None:
    """One distance from a known warehouse is a circle; three are an address."""
    delivery = eta.estimate(
        origin=eta.Point(53.4808, -2.2426), destination=eta.Point(51.5074, -0.1278)
    )

    rendered = f"{vars(delivery)}{delivery.describe()}"
    for fragment in ("51.5", "-0.12", "53.4", "-2.24", "262", "km"):
        assert fragment not in rendered


# --- the randomized sweep ----------------------------------------------------


def _material(rng: random.Random) -> str:
    """Material nobody designed the defences against."""
    chosen = rng.sample(sorted(CANARIES.values()), k=rng.randint(1, len(CANARIES)))
    filler = rng.choice(
        [
            "I need soap by Friday",
            "the boiler man comes Thursday",
            "can I have £40 for a coat",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Trip\nDTSTART;VALUE=DATE:20260812\nEND:VEVENT",
            "HSBC: you spent £9.99 at CO-OP. Balance £12.00",
        ]
    )
    parts = [filler, *chosen]
    rng.shuffle(parts)
    # Separators matter: a value glued to punctuation is a different string to
    # `in`, and that is exactly where a naive replace-based redactor fails.
    return rng.choice([" ", "\n", ", ", " — ", ":"]).join(parts)


@pytest.mark.parametrize("seed", range(40))
def test_no_arrangement_of_personal_data_survives_the_boundary(
    db: str, household: int, seed: int
) -> None:
    rng = random.Random(seed)
    material = _material(rng)

    pipeline.ingest(household, material, use_local_model=False, db_path=db)
    episodic.remember(person_id=household, text=material, db_path=db)
    stub = converse(db, household, material)

    assert_no_canaries(stub.bodies())
