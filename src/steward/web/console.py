"""A demo console: two phone lines on one screen, driving the real router.

**This is not a second product surface.** It stands in for the phone network,
the same job `RecordingChannel` already does in the terminal — because a person
trying this out cannot text a UK number, and the Linq sandbox expires. Every
message typed here becomes an `Inbound` and goes through `Router.receive`,
which is the identical path a real SMS takes.

That matters for the approve button. It does not call `purchase.approve`; it
sends the word "yes" from the sponsor's line. So every routing rule still
applies exactly as written — an unknown line gets silence, a sponsor can only
decide their own household's escalations, a bare yes with two things pending
asks which, and only the first word of a reply counts. There is no new path to
money here, because there is no new path at all.

It is deliberately a **separate app** from the sponsor dashboard. That one is
read-only and has tests asserting every route is a GET and that no request
writes; putting a write path beside it would cost that guarantee for a
convenience.

The transcript lives in memory. A reload clears the screen and not the
database — which is the honest shape, since this is standing in for a handset,
and a handset is not where the record lives.
"""

from __future__ import annotations

from typing import Any

import segno
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .. import store
from ..models import Role, utc_now_iso
from ..spend import purchase
from ..surface.base import Inbound, RecordingChannel
from ..surface.router import Router
from . import render


class Console:
    """One household, two lines, and everything said on either.

    Holds the router and the channel for the life of the process so a
    conversation accumulates across requests, the way a phone would.
    """

    def __init__(self, db_path: str | None = None, **router_kwargs: Any) -> None:
        self.db_path = db_path
        self.channel = RecordingChannel()
        self.router = Router(db_path=db_path, channel=self.channel, **router_kwargs)
        # What a human typed. Outbound is read off the channel, so the only
        # thing this needs to remember is the half the channel never sees.
        self.typed: list[dict[str, Any]] = []
        self.seen = 0

    def people(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        rows = store.list_people(db_path=self.db_path)
        sponsor = next((r for r in rows if r["role"] == Role.SPONSOR), None)
        spenders = [
            r
            for r in rows
            if r["role"] == Role.SPENDER
            and sponsor is not None
            and r["sponsor_id"] == sponsor["id"]
        ]
        return sponsor, spenders

    def say(self, person: dict[str, Any], body: str) -> dict[str, Any]:
        """Put a message on the wire as if it had been texted."""
        self.typed.append(
            {
                "person_id": int(person["id"]),
                "who": str(person["name"]),
                "body": body,
                "at": utc_now_iso(),
                "inbound": True,
            }
        )
        handled = self.router.receive(Inbound(sender=str(person["phone"]), body=body))
        return {"kind": handled.kind, "detail": handled.detail}

    def transcript(self, person_id: int) -> list[dict[str, Any]]:
        """Everything on one line, in order.

        Filtered by `person_id`, which is what makes the sponsor's pane unable
        to show the spender's conversation: the channel addresses a person, and
        nothing steward sends to Rae was ever addressed to Ana.
        """
        lines = [row for row in self.typed if row["person_id"] == person_id]
        lines += [
            {
                "person_id": person_id,
                "who": "steward",
                "body": delivery.outbound.body,
                "at": "",
                "inbound": False,
                "about": delivery.outbound.about,
            }
            for delivery in self.channel.sent
            if delivery.outbound.person_id == person_id
        ]
        return lines

    def waiting(self, sponsor_id: int) -> list[dict[str, Any]]:
        return purchase.pending_for_sponsor(sponsor_id, db_path=self.db_path)


# --- rendering ----------------------------------------------------------------


def _bubble_html(row: dict[str, Any]) -> str:
    side = "them" if row["inbound"] else "agent"
    return (
        f'<div class="bubble {render.text(side)}">'
        f'<div class="bubble-who">{render.text(row["who"])}</div>'
        f'<p>{render.text(row["body"])}</p></div>'
    )


def _line_html(person: dict[str, Any], rows: list[dict[str, Any]], role: str) -> str:
    bubbles_html = "".join(_bubble_html(row) for row in rows) or render.empty(
        "Nothing said yet."
    )
    hint = (
        "I'm out of soap, can I get some?"
        if role == "spender"
        else "yes  ·  no  ·  or just talk"
    )
    return (
        f'<section class="line" data-who="{render.text(role)}">'
        f'<header class="line-head"><span class="line-who">{render.text(person["name"])}</span>'
        f'<span class="line-num">{render.text(person["phone"] or "no line")}</span>'
        f'<span class="chip">{render.text(role)}</span></header>'
        f'<div class="thread" id="thread-{render.text(role)}">{bubbles_html}</div>'
        f'<div class="thinking" id="busy-{render.text(role)}" hidden>thinking…</div>'
        f'<form class="composer" data-who="{render.text(role)}">'
        f'<input name="text" autocomplete="off" placeholder="{render.text(hint)}">'
        "<button type=\"submit\">Send</button></form></section>"
    )


# No interpolation anywhere in here, so nothing from the database can reach it.
# Everything it renders it escapes itself.
CONSOLE_SCRIPT_HTML = """<script>
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function draw(who, rows) {
  const el = document.getElementById("thread-" + who);
  if (!el) return;
  el.innerHTML = rows.length ? rows.map(r =>
    `<div class="bubble ${r.inbound ? "them" : "agent"}">
       <div class="bubble-who">${esc(r.who)}</div><p>${esc(r.body)}</p></div>`).join("")
    : `<p class="empty">Nothing said yet.</p>`;
  el.scrollTop = el.scrollHeight;
}

async function refresh() {
  const r = await fetch("/state");
  if (!r.ok) return;
  const s = await r.json();
  if (!s.ready) return;
  draw("spender", s.spender);
  draw("sponsor", s.sponsor);
}

async function say(who, text) {
  const busy = document.getElementById("busy-" + who);
  if (busy) busy.hidden = false;
  try {
    await fetch("/say", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({who, text}),
    });
    // A reply can change what is waiting, so the page is rebuilt rather than
    // patched — the approve panel is server-rendered from the escalation table.
    location.reload();
  } finally {
    if (busy) busy.hidden = true;
  }
}

document.querySelectorAll("form.composer").forEach(form => {
  form.onsubmit = e => {
    e.preventDefault();
    const input = form.querySelector("input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    say(form.dataset.who, text);
  };
});

document.querySelectorAll("[data-say]").forEach(button => {
  button.onclick = () => say("sponsor", button.dataset.say);
});

refresh();
</script>"""


CONSOLE_HELP = (
    "Type as either person. Every message goes through the same router a real"
    " text does — including the rule that only the first word of a sponsor's"
    " reply counts."
)


def _pending_html(console: Console, sponsor: dict[str, Any]) -> str:
    waiting = console.waiting(int(sponsor["id"]))
    if not waiting:
        return ""
    row = waiting[0]
    return (
        '<div class="pending">'
        f'<div>{render.text(row["description"])} '
        f'<span class="amount">'
        f'{render.money_text(int(row["amount_cents"]), str(row["currency"]))}</span></div>'
        f'<div class="rule">{render.text(row["reason"])}</div>'
        '<div class="acts">'
        '<button class="yes" data-say="yes">Approve</button>'
        '<button class="no" data-say="no">Decline</button>'
        "</div>"
        '<p class="caveat">These send the words &ldquo;yes&rdquo; and &ldquo;no&rdquo; from this'
        " line. There is no other approval path — the button is a shortcut for typing, not a way"
        " around the router.</p>"
        "</div>"
    )


def one_line_page(console: Console, role: str) -> str:
    """A single line, full screen. What a phone gets.

    Two people on two handsets each open their own, which is the product rather
    than a concession to small screens: the whole point is that these are
    separate lines carrying different things.
    """
    sponsor, spenders = console.people()
    if sponsor is None or not spenders:
        return _nobody_page()
    person = sponsor if role == "sponsor" else spenders[0]
    pending_html = _pending_html(console, sponsor) if role == "sponsor" else ""
    body_html = (
        '<header class="banner solo"><h1>steward</h1>'
        f'<p class="who">{render.text(person["name"])} · '
        f'{render.text(role)}<span class="chip">DEMO</span></p></header>'
        f'<div class="lines solo">{_line_html(person, console.transcript(int(person["id"])), role)}</div>'
        f'<div class="controls">{pending_html}</div>'
        f"{CONSOLE_SCRIPT_HTML}"
    )
    return render.document(
        title=f"steward · {person['name']}", banner_html="", nav_html="", body_html=body_html
    )


def _nobody_page() -> str:
    body_html = render.card(
        "Nothing to demonstrate",
        render.empty(
            "This database has no sponsor with a spender. Run scripts/seed_demo.py"
            " or scripts/demo.py --keep first."
        ),
    )
    return render.document(
        title="steward console", banner_html="", nav_html="", body_html=body_html
    )


def page(console: Console, base_url: str = "") -> str:
    sponsor, spenders = console.people()
    if sponsor is None or not spenders:
        body_html = render.card(
            "Nothing to demonstrate",
            render.empty(
                "This database has no sponsor with a spender. Run scripts/seed_demo.py"
                " or scripts/demo.py --keep first."
            ),
        )
        return render.document(
            title="steward console", banner_html="", nav_html="", body_html=body_html
        )

    spender = spenders[0]
    body_html = (
        '<header class="banner"><h1>steward console</h1>'
        f'<p class="who">{render.text(CONSOLE_HELP)}</p>'
        '<div class="meta"><span class="chip">DEMO</span>'
        "<span>this page stands in for the phone network, the way RecordingChannel does"
        " in the terminal — it is not a second product surface</span></div></header>"
        '<div class="lines">'
        f'{_line_html(spender, console.transcript(int(spender["id"])), "spender")}'
        f'{_line_html(sponsor, console.transcript(int(sponsor["id"])), "sponsor")}'
        "</div>"
        f'<div class="controls">{_pending_html(console, sponsor)}</div>'
        f"{qr_html(base_url)}"
        f"{CONSOLE_SCRIPT_HTML}"
    )
    return render.document(
        title="steward console", banner_html="", nav_html="", body_html=body_html
    )


# --- the app ------------------------------------------------------------------


def qr_html(base: str) -> str:
    """A code per line, so two handsets can each pick up one.

    Rendered as an inline data URI rather than fetched: this page is opened on a
    network that may have nothing else on it, and a QR that needs a round trip
    to display is a QR that fails in the room you built it for.
    """
    if not base:
        return ""
    codes_html = ""
    for role, who in (("spender", "the person asking"), ("sponsor", "the person paying")):
        url = f"{base}/{role}"
        data_uri = segno.make(url, error="m").svg_data_uri(scale=4, dark="#000", light="#fff")
        codes_html += (
            f'<figure><img src="{render.text(data_uri)}" alt="{render.text(url)}">'
            f'<figcaption>{render.text(role)} — {render.text(who)}<br>'
            f'<code>{render.text(url)}</code></figcaption></figure>'
        )
    return render.card(
        "Join from a phone",
        f'<div class="join">{codes_html}</div>',
        note_text="Point a camera at one each. Both handsets talk to this machine over your"
        " own network; nothing here is on the internet.",
    )


def build_console_app(console: Console, *, base_url: str = "") -> Starlette:
    """Two GETs and one POST. The POST is the whole point, which is why this is
    not bolted onto the read-only dashboard."""

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(await run_in_threadpool(page, console, base_url))

    async def state(request: Request) -> JSONResponse:
        def build() -> dict[str, Any]:
            sponsor, spenders = console.people()
            if sponsor is None or not spenders:
                return {"ready": False}
            return {
                "ready": True,
                "spender": console.transcript(int(spenders[0]["id"])),
                "sponsor": console.transcript(int(sponsor["id"])),
                "waiting": len(console.waiting(int(sponsor["id"]))),
            }

        return JSONResponse(await run_in_threadpool(build))

    async def say(request: Request) -> JSONResponse:
        payload = await request.json()
        who = str(payload.get("who", ""))
        body = str(payload.get("text", "")).strip()
        if not body:
            return JSONResponse({"error": "nothing to send"}, status_code=400)

        def handle() -> dict[str, Any]:
            sponsor, spenders = console.people()
            person = sponsor if who == "sponsor" else (spenders[0] if spenders else None)
            if person is None:
                return {"error": "no such line"}
            # Blocking on purpose, in a worker thread: the agent calls a model
            # and pay-warden runs asyncio.run in a subprocess, neither of which
            # can happen on the event loop.
            return console.say(person, body)

        result = await run_in_threadpool(handle)
        status = 400 if result.get("error") else 200
        return JSONResponse(result, status_code=status)

    async def spender_line(request: Request) -> HTMLResponse:
        return HTMLResponse(await run_in_threadpool(one_line_page, console, "spender"))

    async def sponsor_line(request: Request) -> HTMLResponse:
        return HTMLResponse(await run_in_threadpool(one_line_page, console, "sponsor"))

    return Starlette(
        routes=[
            Route("/", index),
            # One page per line, so two handsets can each be one person. Literal
            # paths rather than a parameter: there are exactly two lines, and a
            # path that took a role would invite a third that is not a role.
            Route("/spender", spender_line),
            Route("/sponsor", sponsor_line),
            Route("/state", state),
            Route("/say", say, methods=["POST"]),
        ]
    )
