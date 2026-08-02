"""The demo console, and the claim that it adds no new way to spend.

It is a stand-in for the phone network, not a second product surface. Every
message typed goes through `Router.receive`, so the properties the SMS surface
is tested for hold here without being reimplemented — and these tests exist to
prove that is still true rather than to re-test the router.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from steward import store
from steward.models import Role
from steward.spend import purchase
from steward.web.console import Console, build_console_app

from .agent_stub import OpenAIStub, completion
from .warden_stub import WardenStub, parked, refused, released

CONSOLE = Path(__file__).resolve().parents[1] / "src" / "steward" / "web" / "console.py"

SPENDER_LINE = "+447700900002"
SPONSOR_LINE = "+447700900001"
SOAP = {
    "description": "hand soap",
    "amount_cents": 2500,
    "currency": "GBP",
    "merchant_name": "Everyday Goods",
    "merchant_url": "https://everyday.fixture.example",
    "merchant_country": "GB",
}


@pytest.fixture
def household(db: str) -> tuple[int, int]:
    sponsor = store.insert_person(
        name="Rae Whitfield", role=Role.SPONSOR, phone=SPONSOR_LINE, db_path=db
    )
    spender = store.insert_person(
        name="Ana Whitfield",
        role=Role.SPENDER,
        sponsor_id=sponsor,
        phone=SPENDER_LINE,
        db_path=db,
    )
    return sponsor, spender


def console_for(db: str, **kwargs) -> Console:
    return Console(db_path=db, **kwargs)


# --- the privacy boundary, on a screen ----------------------------------------


def test_the_sponsors_line_never_carries_the_spenders_conversation(
    db: str, household: tuple[int, int]
) -> None:
    """The property the whole product rests on, restated for a surface that
    shows both lines at once. It holds because the channel addresses a person:
    nothing steward said to Ana was ever addressed to Rae."""
    sponsor, spender = household
    model = OpenAIStub([completion(content="Here are three options.")])
    console = console_for(db, http=model.client())

    console.say(store.get_person(spender, db_path=db), "I'm out of soap")

    on_raes_line = [row["body"] for row in console.transcript(sponsor)]
    assert on_raes_line == []
    assert any("three options" in row["body"] for row in console.transcript(spender))


def test_an_escalation_reaches_the_sponsor_without_the_conversation(
    db: str, household: tuple[int, int]
) -> None:
    """What Rae is owed is the decision and the rule, not the chat."""
    sponsor, spender = household
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))
    console = console_for(db, warden=WardenStub([]))

    console.say(store.get_person(sponsor, db_path=db), "what is waiting")

    on_raes_line = " ".join(row["body"] for row in console.transcript(sponsor))
    assert "hand soap" in on_raes_line
    assert "I'm out of soap" not in on_raes_line


# --- the button is a text -----------------------------------------------------


def test_the_approve_button_sends_a_word_and_nothing_else(
    db: str, household: tuple[int, int]
) -> None:
    """The button posts "yes" from the sponsor's line. It is a shortcut for
    typing, not a second path to money — which is what lets every routing rule
    keep applying without being restated here."""
    _, spender = household
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))
    console = console_for(db, warden=WardenStub([released()]))
    client = TestClient(build_console_app(console))

    response = client.post("/say", json={"who": "sponsor", "text": "yes"})

    assert response.status_code == 200
    assert response.json()["kind"] == "approved"
    assert store.list_escalations(db_path=db)[0]["status"] == "approved"


def test_declining_through_the_console_reaches_the_policy_engine(
    db: str, household: tuple[int, int]
) -> None:
    sponsor, spender = household
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))
    stub = WardenStub([refused()])
    console = console_for(db, warden=stub)

    console.say(store.get_person(sponsor, db_path=db), "no")

    assert stub.tools_called() == ["reject_purchase"]
    assert store.list_escalations(db_path=db)[0]["status"] == "declined"


def test_a_bare_yes_with_two_waiting_still_asks_which(
    db: str, household: tuple[int, int]
) -> None:
    """Guessing would be guessing with somebody's money, and the console must
    not have quietly removed the question by putting a button on it."""
    sponsor, spender = household
    for n in (1, 2):
        # Distinct attempt ids: the escalation table makes them unique, which is
        # what stops one pay-warden attempt becoming two approvals.
        purchase.buy(
            person_id=spender, **SOAP, db_path=db, client=WardenStub([parked(f"att_{n}")])
        )
    console = console_for(db, warden=WardenStub([]))

    result = console.say(store.get_person(sponsor, db_path=db), "yes")

    assert result["kind"] == "ambiguous"
    assert all(row["status"] == "pending" for row in store.list_escalations(db_path=db))


def test_a_line_nobody_is_enrolled_on_gets_silence(db: str, household: tuple[int, int]) -> None:
    """A reply would confirm to a stranger that this number moves money. The
    console cannot reach an unknown line through its own UI, but the router is
    what enforces it and the console must not be routing around it."""
    console = console_for(db)

    handled = console.router.receive(
        __import__("steward.surface.base", fromlist=["Inbound"]).Inbound(
            sender="+447700999999", body="yes"
        )
    )

    assert handled.kind == "unknown_sender"
    assert handled.replies == []


# --- it is a separate app on purpose ------------------------------------------


def test_the_console_is_not_mounted_on_the_read_only_dashboard() -> None:
    """The dashboard has tests asserting every route is a GET and that no
    request writes. A write path beside it would cost that guarantee for a
    convenience, so the console is its own app and this is the check that it
    stays that way."""
    from steward.web import app as dashboard

    source = Path(dashboard.__file__).read_text()

    assert "console" not in source


def test_the_console_writes_only_through_the_router() -> None:
    """No handler here reaches for purchase.approve or purchase.decline.
    Everything a person does becomes an Inbound, which is what makes the routing
    rules load-bearing rather than decorative.

    Read from the syntax tree, not the text: the module docstring explains that
    it does not call `purchase.approve`, and a substring scan flags the
    explanation as the violation. Made that mistake once already, in the reader
    check in test_web.py.
    """
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(CONSOLE.read_text()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "approve" not in called
    assert "decline" not in called
    assert "receive" in called


def test_every_page_declares_itself_a_demo(db: str, household: tuple[int, int]) -> None:
    """It stands in for the phone network. Somebody landing on it from a QR code
    should not think they are looking at the product's own app."""
    console = console_for(db)
    client = TestClient(build_console_app(console))

    for path in ("/", "/spender", "/sponsor"):
        assert "DEMO" in client.get(path).text, path


def test_the_qr_codes_are_inline_and_absent_until_asked_for(
    db: str, household: tuple[int, int]
) -> None:
    """A QR that needs a round trip to render is one that fails in the room it
    was built for, so it is a data URI. And it only appears once a reachable
    address is known."""
    console = console_for(db)

    without = TestClient(build_console_app(console)).get("/").text
    with_lan = TestClient(build_console_app(console, base_url="http://10.0.0.5:8788")).get("/").text

    assert "Join from a phone" not in without
    assert "data:image/svg+xml" in with_lan
    assert "10.0.0.5:8788/spender" in with_lan


def test_a_hostile_name_cannot_inject_into_the_console(db: str) -> None:
    sponsor = store.insert_person(name="<script>alert(1)</script>", role=Role.SPONSOR, db_path=db)
    store.insert_person(
        name="Ana", role=Role.SPENDER, sponsor_id=sponsor, phone=SPENDER_LINE, db_path=db
    )
    console = console_for(db)

    body = TestClient(build_console_app(console)).get("/").text

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_page_renders_only_ids_the_script_looks_for(db: str, household: tuple[int, int]) -> None:
    """The console's JS is untested, so the payload/renderer seam is checked
    instead: every element the script reaches for must exist on the page."""
    console = console_for(db)
    body = TestClient(build_console_app(console)).get("/").text
    wanted = set(re.findall(r'getElementById\("([a-z]+)-" \+ (\w+)\)', CONSOLE.read_text()))

    assert wanted, "the script no longer builds ids the way this test assumes"
    for prefix, _ in wanted:
        assert f'id="{prefix}-spender"' in body
        assert f'id="{prefix}-sponsor"' in body


# --- order --------------------------------------------------------------------


def test_a_reply_lands_after_the_message_that_caused_it(
    db: str, household: tuple[int, int]
) -> None:
    """The transcript was two lists read back concatenated — everything typed,
    then everything answered — so every question sorted before every answer no
    matter when either was said. Three turns is the smallest case that catches
    it: with one, concatenation and sequence look identical.
    """
    _, spender = household
    model = OpenAIStub(
        [completion(content="first answer"), completion(content="second answer")]
    )
    console = console_for(db, http=model.client())
    ana = store.get_person(spender, db_path=db)

    console.say(ana, "one")
    console.say(ana, "two")

    assert [row["body"] for row in console.transcript(spender)] == [
        "one",
        "first answer",
        "two",
        "second answer",
    ]


def test_an_escalation_keeps_its_order_on_both_lines(
    db: str, household: tuple[int, int]
) -> None:
    """One turn can produce messages on two lines. Each has to sit after the
    message that caused it on its own line, and neither may appear on the other."""
    sponsor, spender = household
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))
    console = console_for(db, warden=WardenStub([]))

    console.say(store.get_person(sponsor, db_path=db), "what is waiting")

    raes = console.transcript(sponsor)
    assert raes[0]["body"] == "what is waiting"
    assert raes[0]["inbound"] is True
    assert not raes[-1]["inbound"]
    assert console.transcript(spender) == []


def test_the_log_is_a_single_sequence(db: str, household: tuple[int, int]) -> None:
    """Both lines are slices of one list, so `seq` is strictly increasing across
    the whole conversation and a renderer cannot reorder it by accident."""
    sponsor, spender = household
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))
    console = console_for(db, warden=WardenStub([]))
    console.say(store.get_person(sponsor, db_path=db), "what is waiting")

    seqs = [row["seq"] for row in console.log]

    assert seqs == sorted(seqs) == list(range(len(seqs)))


def test_the_spenders_page_carries_no_approval_panel(
    db: str, household: tuple[int, int]
) -> None:
    """It briefly did. The script fills `#pending` by id, and the spender's page
    was rendering an empty container of that name — so the client drew the
    sponsor's queue onto it, with buttons that posted as the sponsor. Rae's
    decisions on Ana's screen is the opposite of what this product claims."""
    _, spender = household
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))
    client = TestClient(build_console_app(console_for(db, warden=WardenStub([]))))

    def dom(path: str) -> str:
        """The page without its script. `drawPending` carries the button markup
        in a template literal, which never runs when the container is absent —
        so the question is what the document contains, not what the source
        mentions."""
        body = client.get(path).text
        return re.sub(r"<script>.*?</script>", "", body, flags=re.DOTALL)

    ana, rae = dom("/spender"), dom("/sponsor")

    assert 'id="pending"' not in ana
    assert "Approve" not in ana
    assert "hand soap" not in ana
    assert 'id="pending"' in rae
    assert "Approve" in rae


def test_a_payment_link_gets_a_code_to_scan(db: str, household: tuple[int, int]) -> None:
    """The link is the spender's, and their passkey is on their phone — so a
    spender reading it on a laptop cannot use it. The code sits beside the URL
    rather than replacing it."""
    from steward.web.console import payment_qr

    code = payment_qr(
        "Soap is approved. Finish it here:\n"
        "https://sandbox.collect.prava.space?session=ses_01KZ254RX3TJ1RRAN4FRSGE5C8"
    )

    assert code.startswith("data:image/svg+xml")


def test_nothing_else_gets_a_code(db: str, household: tuple[int, int]) -> None:
    """A QR is an instruction to point a camera at something. This page offers
    that only for the one link it knows is a payment."""
    from steward.web.console import payment_qr

    assert payment_qr("here are three options from Everyday Goods") == ""
    assert payment_qr("see https://everyday.fixture.example/soap") == ""


def test_the_url_in_the_code_is_the_one_pay_warden_sent(
    db: str, household: tuple[int, int]
) -> None:
    """Never invented, only matched out of a message the policy engine produced."""
    from steward.web.console import _PAYMENT_URL

    body = "Finish it here: https://sandbox.collect.prava.space?session=ses_abc123."
    found = _PAYMENT_URL.search(body)

    assert found is not None
    assert found.group(0).rstrip(".") == "https://sandbox.collect.prava.space?session=ses_abc123"


def test_the_code_survives_the_clients_redraw(db: str, household: tuple[int, int]) -> None:
    """The thread is rebuilt from /state on every poll, so anything only in the
    server's HTML lasts exactly until the first refresh. It briefly did."""
    _, spender = household
    console = console_for(db)
    console._append(
        spender,
        "steward",
        "Approved. Finish it here: https://sandbox.collect.prava.space?session=ses_1",
        inbound=False,
    )

    row = console.transcript(spender)[0]

    assert row["qr"].startswith("data:image/svg+xml")


def test_every_redraw_is_guarded_by_a_signature() -> None:
    """A poll that changes nothing must write nothing.

    `draw` had this from the start; `drawPending` did not, and it is the one
    holding the only two buttons on the page. Rewriting it every two seconds
    destroyed them and any focus resting on one, so a keyboard user tabbing to
    Approve lost it before they could press — an approval path that a mouse
    could use and a keyboard could not.

    Structural, because this suite has no JS engine. The behaviour itself was
    checked by running the real function against a stub DOM and counting writes:
    unchanged polls wrote once before the guard and zero times after.
    """
    from steward.web.console import CONSOLE_SCRIPT_HTML

    for name in ("function draw(", "function drawPending("):
        body = CONSOLE_SCRIPT_HTML[CONSOLE_SCRIPT_HTML.index(name) :]
        body = body[: body.index("\n}")]

        assert "innerHTML" in body, f"{name} no longer writes; this test is stale"
        assert "dataset.sig" in body, f"{name} redraws unconditionally"
