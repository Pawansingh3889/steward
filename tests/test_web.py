"""The sponsor dashboard, and why it is safe without a login.

It has no authentication. That is defensible only because there is nothing on it
to reach — no writes, and no route that names a household — and both halves are
claims about what the code *can* do rather than what one request did. So most of
what follows is structural: route tables, AST walks, and canaries planted in the
data that must not come back out.

The `no_network` guard does not block any of this. Starlette's TestClient talks
to the app in-process through httpx2, a different package from the httpx the
guard patches, and nothing here opens a socket.
"""

from __future__ import annotations

import ast
import asyncio
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from steward import store
from steward.catalogue.search import LABEL as FIXTURE
from steward.models import Role, utc_today
from steward.plan import schedule
from steward.spend import purchase
from steward.spend.warden import WardenError, agent_name
from steward.web import build_app, panels, render, scope

from .warden_stub import WardenStub

WEB = Path(__file__).resolve().parents[1] / "src" / "steward" / "web"

# Planted in what the spender said in private. If either ever appears on any
# route, the boundary this whole surface exists to draw has failed.
PRIVATE = "MUST-NOT-APPEAR anxious about the rent"
PRIVATE_TOO = "MUST-NOT-APPEAR-2 do not tell Rae about the deposit"
OTHER_HOUSEHOLD = "HOUSEHOLD-TWO-CANARY winter tyres"

ROUTES = ("/", "/privacy", "/ledger", "/pilot", "/nope")


@pytest.fixture
def house(db: str) -> scope.Household:
    """Rae, two spenders, and an entire second household beside them.

    The second household is not decoration: a dashboard that shows nothing of
    other people's when there are no other people proves nothing at all.
    """
    rae = store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, db_path=db)
    ana = store.insert_person(name="Ana Whitfield", role=Role.SPENDER, sponsor_id=rae, db_path=db)
    theo = store.insert_person(name="Theo Whitfield", role=Role.SPENDER, sponsor_id=rae, db_path=db)
    marcus = store.insert_person(name="Marcus Idowu", role=Role.SPONSOR, db_path=db)
    priya = store.insert_person(
        name="Priya Idowu", role=Role.SPENDER, sponsor_id=marcus, db_path=db
    )

    store.insert_escalation(
        spender_id=ana,
        sponsor_id=rae,
        attempt_id="att_soap",
        description="hand soap, 2 × 500ml",
        amount_cents=2500,
        currency="GBP",
        merchant_name="Everyday Goods",
        rule_id="human-approval",
        reason="25 GBP exceeds auto-approval threshold 20.00 GBP; a human must release it",
        db_path=db,
    )
    paper = store.insert_escalation(
        spender_id=ana,
        sponsor_id=rae,
        attempt_id="att_paper",
        description="printer paper",
        amount_cents=1240,
        currency="GBP",
        merchant_name="Corner Shop Express",
        db_path=db,
    )
    store.decide_escalation(paper, status=purchase.APPROVED, db_path=db)
    store.insert_escalation(
        spender_id=priya,
        sponsor_id=marcus,
        attempt_id="att_tyres",
        description=OTHER_HOUSEHOLD,
        amount_cents=24000,
        currency="GBP",
        db_path=db,
    )

    store.insert_turn(person_id=ana, speaker="person", text=PRIVATE, db_path=db)
    store.insert_turn(person_id=ana, speaker="person", text=PRIVATE_TOO, db_path=db)
    store.insert_turn(
        person_id=ana,
        speaker="person",
        text="I'm out of soap, can I get some?",
        shared_with_sponsor=True,
        db_path=db,
    )
    store.set_share_mode(ana, store.SHARE_PRIVATE, db_path=db)
    store.insert_turn(person_id=theo, speaker="person", text=PRIVATE, db_path=db)

    # Relative to today, never hard-coded: `goals.view` solves the schedule at
    # read time, so a fixed finish date makes this suite fail in a few months.
    start = utc_today()
    lisbon = store.insert_plan(
        person_id=ana,
        name="Lisbon",
        kind="trip",
        target_cents=60000,
        currency="GBP",
        cadence=schedule.MONTHLY,
        per_period_cents=15000,
        start_date=start.isoformat(),
        finish_date=schedule.add_periods(start, schedule.MONTHLY, 4).isoformat(),
        db_path=db,
    )
    store.insert_plan_item(
        plan_id=lisbon, description="flights", amount_cents=18000, kind="flight", db_path=db
    )
    store.insert_refund(
        person_id=ana,
        attempt_id="att_paper",
        description="printer paper",
        amount_cents=1240,
        currency="GBP",
        reason="Only 200 sheets arrived.",
        db_path=db,
    )
    store.insert_correction(
        person_id=ana, kind=store.DELETED_BELIEF, subject="supply/shampoo", db_path=db
    )
    return scope.Household(sponsor_id=rae, db_path=db)


class EmptyLedger:
    """pay-warden with nothing to report, however many times it is asked.

    `WardenStub` scripts a fixed number of replies, which is right when the
    count is the thing under test. Here several tests fetch every route twice,
    and running out of replies would fail them for the wrong reason.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        return []


@pytest.fixture
def stub() -> EmptyLedger:
    return EmptyLedger()


@pytest.fixture
def client(house: scope.Household, stub: EmptyLedger) -> TestClient:
    return TestClient(build_app(house, warden_client=stub), raise_server_exceptions=True)


def every_page(client: TestClient) -> str:
    return "".join(client.get(path).text for path in ROUTES)


# --- the boundary ------------------------------------------------------------


def test_a_private_turn_has_no_reader_on_any_sponsor_route(client: TestClient) -> None:
    """The property the product is sold on. Not "the template omits it" — there
    is no code path from a sponsor page to an unshared turn at all."""
    everything = every_page(client)

    assert PRIVATE not in everything
    assert PRIVATE_TOO not in everything


def test_only_the_turns_the_spender_shared_appear(client: TestClient) -> None:
    body = client.get("/privacy").text

    assert "out of soap" in body
    assert PRIVATE not in body


FORBIDDEN_READERS = frozenset(
    {"recent_turns", "list_facts", "get_fact", "list_episodes", "search_episodes"}
)
# `recall.everything` and `refund.everything` share a final name, so the module
# is what gets banned: unimportable means uncallable, and `refund.everything`
# stays available.
FORBIDDEN_MODULES = frozenset({"recall", "episodic", "embed"})


def _readers_used(path: Path) -> set[str]:
    """Every function this file calls, by its final name, from the syntax tree.

    Read rather than grepped, so the *prose* in these modules can name the
    readers it is explaining that it does not use. The first version of this
    test scanned text and failed on its own docstring.
    """
    tree = ast.parse(path.read_text())
    used = {_final_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            used.update(alias.name for alias in node.names)
    return used - {""}


def test_web_code_never_reaches_the_unfiltered_readers() -> None:
    """`store.shared_turns` is the only turn reader allowed in this package.

    Structural rather than behavioural, so it covers modules nobody has written
    yet: the day someone adds a panel that calls `recent_turns` because it was
    convenient, this fails before the page is ever loaded.
    """
    for path in WEB.rglob("*.py"):
        used = _readers_used(path)

        assert not (used & FORBIDDEN_READERS), f"{path.name} calls {used & FORBIDDEN_READERS}"
        imported = {
            alias
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.ImportFrom)
            for alias in ([node.module or ""] + [a.name for a in node.names])
        }
        assert not (imported & FORBIDDEN_MODULES), f"{path.name} imports a memory module"


def test_turning_sharing_off_does_not_retract_what_was_already_shared(
    client: TestClient,
) -> None:
    """Sharing applies per turn from the moment it is set. Ana's mode is private
    and three turns are still visible, which is the same property as "turning it
    on does not expose what came before" seen from the other end."""
    body = client.get("/privacy").text

    assert "out of soap" in body
    assert "sharing is currently" in body
    assert "<strong>off</strong>" in body


def test_the_page_does_not_count_what_it_will_not_show(house: scope.Household) -> None:
    """`pilot.summary` counts messages. That count is dropped at the seam rather
    than merely left unrendered: it carries no text, but how often somebody
    messaged their agent is conversation metadata, and this is the one surface
    where that distinction is the product."""
    counted = panels.pilot_counts(house)

    assert counted
    for entry in counted:
        assert set(entry) == {
            "name",
            "pair",
            "raised",
            "decided",
            "undecided",
            "corrections",
            "agent_failures",
        }


def test_a_shared_turn_is_labelled_as_the_spenders_choice(client: TestClient) -> None:
    """So nothing on the page reads as surveillance: every visible turn says who
    decided it should be visible."""
    assert "shared by Ana" in client.get("/privacy").text


# --- the scope ---------------------------------------------------------------


def test_no_route_takes_a_household_id(house: scope.Household) -> None:
    """There is no authorisation check to delete, because no handler is ever
    given a person to check."""
    app = build_app(house)

    assert all("{" not in route.path for route in app.routes)


def test_a_query_string_cannot_widen_the_scope(client: TestClient) -> None:
    """Byte-identical, not merely both 200: that proves the parameter was
    ignored rather than handled and rejected."""
    assert client.get("/?person=5").text == client.get("/").text


def test_a_guessed_path_reaches_no_household(client: TestClient) -> None:
    for path in ("/household/4", "/sponsor/4", "/person/5"):
        response = client.get(path)

        assert response.status_code == 404
        assert "no URL on this server takes a person id" in response.text


def test_another_households_escalation_is_never_rendered(client: TestClient) -> None:
    assert OTHER_HOUSEHOLD not in every_page(client)
    assert "Priya" not in every_page(client)


def test_serving_a_spender_is_refused(db: str, house: scope.Household) -> None:
    """The one way the scope can be wrong while still looking right: every query
    would come back empty or, worse, return that spender's own conversation
    under a heading about what their sponsor may read."""
    spender = house.spenders()[0]

    with pytest.raises(scope.ScopeError, match="spender"):
        scope.resolve(int(spender["id"]), db_path=db)


def test_serving_somebody_who_does_not_exist_is_refused(db: str) -> None:
    with pytest.raises(scope.ScopeError, match="no person"):
        scope.resolve(999, db_path=db)


def test_the_ledger_asks_pay_warden_only_about_this_household(
    client: TestClient, stub: EmptyLedger, house: scope.Household
) -> None:
    """pay-warden's audit database is shared by every agent it has ever answered
    for, so an unfiltered `get_audit_log` returns other households' purchases."""
    client.get("/ledger")

    asked = {arguments.get("agent") for tool, arguments in stub.calls if tool == "get_audit_log"}
    assert asked == {agent_name(int(row["id"])) for row in house.spenders()}
    assert None not in asked


# --- read-only ---------------------------------------------------------------


def test_every_route_is_a_get(house: scope.Household) -> None:
    app = build_app(house)

    for route in app.routes:
        assert set(getattr(route, "methods", set())) <= {"GET", "HEAD"}, route.path


def test_nothing_a_page_does_writes_to_the_database(client: TestClient, db: str) -> None:
    """Read-only asserted against the database rather than against the routes,
    so it holds however the pages are rebuilt later."""

    def counts() -> dict[str, int]:
        with sqlite3.connect(db) as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                    " AND name NOT LIKE 'sqlite_%'"
                )
            ]
            return {
                name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in tables
            }

    before = counts()
    every_page(client)

    assert counts() == before


def test_a_post_is_refused(client: TestClient) -> None:
    assert client.post("/").status_code == 405


# --- the ledger --------------------------------------------------------------


class LoopSpy:
    """A warden that fails the test if it is called on the event loop.

    `StdioWarden.call` runs `asyncio.run(...)`, which raises inside a running
    loop. Starlette gives a sync endpoint a worker thread and an `async def` one
    the loop itself, so turning `def ledger` into `async def ledger` would break
    every ledger request in production and nowhere else — the stub the other
    tests use would never notice.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.calls.append(tool)
            return []
        raise AssertionError("the ledger ran on the event loop; StdioWarden would raise here")


def test_the_ledger_runs_off_the_event_loop(house: scope.Household) -> None:
    spy = LoopSpy()

    TestClient(build_app(house, warden_client=spy)).get("/ledger")

    assert spy.calls == ["get_audit_log", "get_audit_log"]


def test_an_unreachable_pay_warden_is_a_panel_not_a_stack_trace(
    house: scope.Household,
) -> None:
    broken = WardenStub([WardenError("connection reset by peer"), WardenError("connection reset")])

    response = TestClient(build_app(house, warden_client=broken)).get("/ledger")

    assert response.status_code == 200
    assert "connection reset by peer" in response.text
    assert "Traceback" not in response.text


def test_an_unconfigured_pay_warden_says_what_to_set(
    house: scope.Household, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ConfigError`'s message is already a whole sentence naming the variable.
    Replacing it with "unavailable" would throw away the only useful thing on
    the page."""
    monkeypatch.delenv("PAY_WARDEN_COMMAND")

    response = TestClient(build_app(house)).get("/ledger")

    assert response.status_code == 200
    assert "PAY_WARDEN_COMMAND is unset" in response.text


def test_an_unknown_verdict_is_not_rendered_as_allowed(house: scope.Household) -> None:
    """Same discipline as `warden._decision`: a verdict the two sides disagree
    about is not permission, and green would be this page asserting it was."""
    row = [{"description": "soap", "verdict": "probably_fine", "total_amount": "3.80"}]
    odd = WardenStub([row, []])

    # The panel, not the page: the stylesheet is inlined, so every badge class
    # in the design system appears in a document whatever it renders.
    body = panels.ledger(house, client=odd)

    assert "badge-unknown" in body
    assert "probably_fine" in body
    assert "badge-good" not in body


def test_a_decimal_amount_from_pay_warden_renders_as_money(house: scope.Household) -> None:
    """pay-warden's rows carry a decimal string; `models.money` takes minor
    units. Junk becomes an admission rather than £0.00 — a ledger that renders
    an unreadable amount as zero is worse than one that says it could not read
    it."""
    rows = [
        {"description": "soap", "verdict": "allowed", "total_amount": "3.8", "currency": "GBP"},
        {"description": "coat", "verdict": "allowed", "total_amount": "banana", "currency": "GBP"},
    ]
    priced = WardenStub([rows, []])

    body = TestClient(build_app(house, warden_client=priced)).get("/ledger").text

    assert "£3.80 GBP" in body
    assert "£0.00" not in body
    assert "amount unreadable" in body


def test_no_page_shows_a_payment_link(house: scope.Household, db: str) -> None:
    """The README's rule is that the link goes to the spender, not to the
    sponsor who approved it. Printing it here would contradict the argument in
    the act of illustrating it — and it is a live payment link on a page with no
    login."""
    url = "https://pay.example/session/9"
    escalation = store.escalation_by_attempt("att_soap", db_path=db)
    assert escalation is not None
    store.decide_escalation(
        int(escalation["id"]), status=purchase.APPROVED, payment_url=url, db_path=db
    )
    minted = WardenStub(
        [
            [
                {
                    "description": "soap",
                    "verdict": "allowed",
                    "session_id": "ses_9",
                    "payment_url": url,
                }
            ],
            [],
        ]
    )

    body = every_page(TestClient(build_app(house, warden_client=minted)))

    assert url not in body
    assert "pay.example" not in body


# --- honest labelling --------------------------------------------------------


def test_a_modelled_merchant_is_labelled_as_one(client: TestClient) -> None:
    body = client.get("/").text

    assert "Everyday Goods" in body
    assert FIXTURE in body


def test_a_merchant_that_is_not_modelled_carries_no_fixture_label() -> None:
    """The label has to mean something, which it stops doing if everything
    wears it."""
    assert FIXTURE in panels._merchant_html("Everyday Goods")
    assert FIXTURE not in panels._merchant_html("Some Real Shop Ltd")


def test_the_plans_panel_says_it_is_advisory(client: TestClient) -> None:
    assert "Advisory only" in client.get("/").text


def test_the_refunds_panel_says_no_merchant_was_contacted(client: TestClient) -> None:
    body = client.get("/").text

    assert "has not contacted the merchant" in body


def test_the_page_says_releasing_is_still_a_command(client: TestClient) -> None:
    """A sponsor who thinks this page can approve will wait on it."""
    assert "steward approvals approve --id N" in client.get("/").text


# --- escaping ----------------------------------------------------------------


def test_a_hostile_name_cannot_inject_markup(db: str) -> None:
    """Names, merchants and policy reasons all reach the page from a database,
    and a policy reason is written by another program entirely."""
    rae = store.insert_person(name="Rae", role=Role.SPONSOR, db_path=db)
    nasty = store.insert_person(
        name="<script>alert(1)</script>", role=Role.SPENDER, sponsor_id=rae, db_path=db
    )
    store.insert_escalation(
        spender_id=nasty,
        sponsor_id=rae,
        attempt_id="att_nasty",
        description='"><img onerror=x src=y>',
        amount_cents=100,
        currency="GBP",
        merchant_name='"><img onerror=x src=y>',
        # The stylesheet is inlined into a <style> element, so a closing tag in
        # a policy reason would end it and leave the rest of the page as CSS.
        reason="rule fired </style><script>alert(2)</script>",
        db_path=db,
    )

    body = TestClient(build_app(scope.Household(sponsor_id=rae, db_path=db))).get("/").text

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(2)</script>" not in body
    assert "</style><script>" not in body
    assert "<img onerror" not in body


ESCAPING_HELPERS = frozenset(
    {
        "text",
        "money_text",
        "when",
        "badge",
        "chip",
        "card",
        "rows",
        "note",
        "empty",
        "deck",
        "nav",
        "document",
    }
)


def _final_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def unescaped_interpolations(path: Path) -> list[str]:
    """Interpolations into markup that did not go through an escaper.

    Only f-strings whose literal parts contain a `<` are inspected: those are
    the ones building markup. An f-string with no angle bracket cannot open a
    tag, and its result reaches a page only through one of the helpers below.

    Three things are allowed inside markup: a constant, a name ending in
    `_html` (the convention that says "this is markup, not data"), and a call to
    an escaping helper or to a function whose own name ends in `_html`.

    The suffix is matched case-insensitively, so a module constant holding a
    block of markup can be `SCRIPT_HTML` and still read as shouting. The
    convention is about what the name promises, not how it is cased.
    Deliberately absent: `money`. It interpolates a database-sourced currency
    into its own output, so `render.money_text` is the safe one.
    """
    offenders: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.JoinedStr):
            continue
        literal = "".join(part.value for part in node.values if isinstance(part, ast.Constant))
        if "<" not in literal:
            continue
        for part in node.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            value = part.value
            if isinstance(value, ast.Constant):
                continue
            if isinstance(value, ast.Call):
                name = _final_name(value.func)
                if name in ESCAPING_HELPERS or name.lower().endswith("_html"):
                    continue
            elif _final_name(value).lower().endswith("_html"):
                continue
            offenders.append(f"{path.name}:{part.lineno} {ast.unparse(value)}")
    return offenders


def test_every_interpolation_into_markup_is_escaped() -> None:
    """`render.text` is the only exit from data into HTML, enforced rather than
    remembered. Without this the package is one convenient f-string away from
    an injection, and the person whose name is in the database did not choose
    to have it rendered as markup."""
    found: list[str] = []
    for path in WEB.rglob("*.py"):
        found.extend(unescaped_interpolations(path))

    assert not found, "unescaped interpolation into markup: " + "; ".join(found)


def test_that_check_can_actually_fail(tmp_path: Path) -> None:
    """A fitness test nobody has seen fail is a comment."""
    planted = tmp_path / "bad.py"
    planted.write_text("body = f\"<p>{row['name']}</p>\"\n")

    assert unescaped_interpolations(planted)


def test_the_stylesheet_cannot_close_its_own_element() -> None:
    """It is inlined into a <style>, so a stray closing tag would turn the rest
    of the document into CSS."""
    from steward.web import style

    assert "</style" not in style.STYLESHEET.lower()


# --- colour ------------------------------------------------------------------
# style.py states the contrast rule in its docstring. This is the rule, enforced
# — the same move as the escaping walk above, for the same reason: a palette
# that was right once and is later nudged by eye stops being right *silently*,
# and never for the person doing the nudging.

AA_TEXT = 4.5  # normal text
AA_UI = 3.0  # large text, and the ring or boundary that locates a control


def _luminance(colour: str) -> float:
    def channel(value: int) -> float:
        part = value / 255
        return part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4

    raw = colour.lstrip("#")
    red, green, blue = (int(raw[at : at + 2], 16) for at in (0, 2, 4))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(foreground: str, background: str) -> float:
    first, second = _luminance(foreground), _luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _palettes() -> dict[str, dict[str, str]]:
    """Both themes, read back out of the stylesheet that actually ships.

    Not a copy of the values: a test holding its own colours would keep passing
    after somebody changed the real ones, which is the failure it exists to
    catch.
    """
    from steward.web import style

    def read(chunk: str) -> dict[str, str]:
        return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-f]{6})", chunk))

    sheet = style.STYLESHEET
    opens = sheet.index("@media (prefers-color-scheme: dark)")
    closes = sheet.index("\n* {", opens)
    light = read(sheet[:opens])
    # Dark redeclares only some tokens; the rest carry over, exactly as the
    # cascade resolves them in a browser.
    return {"light": light, "dark": light | read(sheet[opens:closes])}


# Every pair these pages actually draw, and what each has to clear.
PAIRS = [
    ("ink on panel", "--ink", "--panel", AA_TEXT),
    ("ink-soft on panel", "--ink-soft", "--panel", AA_TEXT),
    ("ink-faint on panel", "--ink-faint", "--panel", AA_TEXT),
    ("ink-faint on sunken", "--ink-faint", "--panel-sunken", AA_TEXT),
    ("ink-faint on bg", "--ink-faint", "--bg", AA_TEXT),
    ("badge good", "--good-ink", "--good-bg", AA_TEXT),
    ("badge wait", "--wait-ink", "--wait-bg", AA_TEXT),
    ("badge bad", "--bad-ink", "--bad-bg", AA_TEXT),
    ("badge flat", "--flat-ink", "--flat-bg", AA_TEXT),
    ("accent link on panel", "--accent", "--panel", AA_TEXT),
    ("accent link on bg", "--accent", "--bg", AA_TEXT),
    ("sent bubble", "--accent-on-fill", "--accent-fill", AA_TEXT),
    ("sent bubble label", "--accent-on-fill-muted", "--accent-fill", AA_TEXT),
    ("focus ring on panel", "--accent", "--panel", AA_UI),
    ("focus ring on sunken", "--accent", "--panel-sunken", AA_UI),
    ("focus ring on bg", "--accent", "--bg", AA_UI),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_colour_pair_clears_wcag_aa(theme: str) -> None:
    palette = _palettes()[theme]

    failed = [
        f"{label} {contrast(palette[ink], palette[on]):.2f}:1 (needs {need})"
        for label, ink, on, need in PAIRS
        if contrast(palette[ink], palette[on]) < need
    ]

    assert not failed, f"{theme}: " + "; ".join(failed)


def test_the_contrast_check_can_actually_fail() -> None:
    """A fitness test nobody has seen fail is a comment. White on #7ba0ff is
    the exact bug this was written for: --accent was doing duty as a fill."""
    assert contrast("#ffffff", "#7ba0ff") < AA_TEXT
    assert contrast("#ffffff", "#1f4fd8") >= AA_TEXT


def test_the_accent_is_never_used_as_a_fill() -> None:
    """The split only holds while nothing paints a surface with the foreground
    token again. --accent behind white is 2.53:1 in dark mode."""
    from steward.web import style

    for line in style.STYLESHEET.splitlines():
        drawing = line.split("/*")[0]
        assert "background: var(--accent)" not in drawing, line
        assert "background-color: var(--accent)" not in drawing, line


# --- empty states ------------------------------------------------------------


def test_a_household_with_nothing_in_it_still_renders(db: str) -> None:
    """Empty states are half of what makes these pages look designed, and a
    sponsor enrolling their first spender sees this one first."""
    alone = store.insert_person(name="Nobody", role=Role.SPONSOR, db_path=db)

    client = TestClient(build_app(scope.Household(sponsor_id=alone, db_path=db)))
    body = "".join(client.get(path).text for path in ("/", "/privacy", "/ledger", "/pilot"))

    assert client.get("/").status_code == 200
    assert "Nothing is parked" in body
    assert "No plans yet" in body
    assert "Nobody is enrolled" in body


def test_a_spender_who_shared_nothing_says_so(client: TestClient) -> None:
    body = client.get("/privacy").text

    assert "it is Theo" in body
    assert "to change — from their own line, by text" in body


# --- rendering -------------------------------------------------------------


def test_money_renders_through_the_shared_formatter() -> None:
    assert render.money_text(450, "GBP") == "£4.50 GBP"


def test_an_unreadable_date_is_shown_rather_than_invented() -> None:
    assert render.when("not a date") == "not a date"


def test_an_unknown_status_gets_the_unknown_tone() -> None:
    assert render.tone_of("pending") == "wait"
    assert render.tone_of("something new") == render.UNRECOGNISED
