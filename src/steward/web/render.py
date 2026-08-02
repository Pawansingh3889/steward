"""Markup, built from Python, with one exit from data into HTML.

There is no template engine here and none is wanted: a dependency that renders
strings is a lot of surface for a handful of panels, and the escaping question
would still have to be answered. It is answered here instead, structurally.

`text()` is the only function that turns data into markup. Everything else
either calls it or takes markup another helper built — and the convention that
makes that checkable is that **anything holding markup is named `*_html`**.
A test in `tests/test_web.py` walks every f-string in this package that contains
a `<`, and fails on any interpolation that is not a constant, a `*_html` name,
or a call to one of the helpers below.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from html import escape

from ..models import money
from . import style

# Statuses that carry data, and the tone each is drawn in. Declining is a
# legitimate answer someone gave, not a failure, so it is not red.
TONES = {
    "pending": "wait",
    "approved": "good",
    "allowed": "good",
    "declined": "flat",
    "denied": "bad",
    "needs_approval": "wait",
    "active": "good",
    "draft": "flat",
    "done": "good",
    "abandoned": "flat",
    "requested": "wait",
    "refunded": "good",
    "refused": "flat",
}
UNRECOGNISED = "unknown"


def text(value: object) -> str:
    """The only exit from data into markup."""
    return escape(str(value), quote=True)


def money_text(amount_cents: int, currency: str) -> str:
    """`models.money` is canonical and stays so — but `currency` comes out of a
    database and lands inside its output unescaped. One wrapper, so no panel has
    to remember that the money formatter is not an escaping one."""
    return text(money(amount_cents, currency))


def when(iso: str) -> str:
    """The one date formatter in the codebase. Everything stores raw ISO.

    Falls back to the raw value rather than inventing a date it cannot read —
    and escapes it on the way out, because an unparseable timestamp is exactly
    the kind of field that turns out to be attacker-shaped.
    """
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return text(iso)
    day = f"{moment.day} {moment.strftime('%b')}"
    if len(iso) <= 10:
        return text(f"{day} {moment.year}")
    return text(f"{day}, {moment.strftime('%H:%M')}")


def tone_of(status: str) -> str:
    """An unrecognised status is never drawn as a good one.

    Same discipline as `warden._decision`, for the same reason: a verdict the
    two sides of a protocol disagree about is not permission, and colouring it
    green would be this surface asserting something nobody said.
    """
    return TONES.get(status, UNRECOGNISED)


def badge(label: str, status: str = "") -> str:
    """A status: filled, carrying data."""
    return f'<span class="badge badge-{text(tone_of(status or label))}">{text(label)}</span>'


def chip(label: str) -> str:
    """An annotation — FIXTURE, dry-run, advisory only.

    Deliberately unlike a badge: monospace, outlined, never filled. Honest
    labelling that survives a screenshot has to survive a *glance*, and a chip
    that looked like a status would be read as one.
    """
    return f'<span class="chip">{text(label)}</span>'


def note(sentence: str) -> str:
    return f'<p class="note">{text(sentence)}</p>'


def empty(sentence: str) -> str:
    """An empty state is a sentence, never a blank panel. Half of what makes
    these pages look designed is what they do when there is nothing to show."""
    return f'<p class="empty">{text(sentence)}</p>'


def card(title: str, body_html: str, *, note_text: str = "", span: bool = False) -> str:
    footer_html = note(note_text) if note_text else ""
    classes = "card card-wide" if span else "card"
    return (
        f'<section class="{text(classes)}">'
        f"<h2>{text(title)}</h2>"
        f'<div class="card-body">{body_html}</div>'
        f"{footer_html}"
        f"</section>"
    )


def rows(headers: Sequence[str], cells_html: Iterable[Sequence[str]]) -> str:
    """A table whose cells are already markup, so a panel can put a badge in one.

    Every table gets its own horizontal scroll box, and that is why this is the
    right place for it rather than each panel. A five-column ledger does not fit
    a phone; without the box the overflow is taken by the document, so the whole
    dashboard slides sideways because one table on it was too wide.

    `tabindex="0"` because a scroll container that only a pointer can scroll is
    unreachable by keyboard — the columns past the edge would exist for mouse
    users only. It costs a tab stop on a wide screen where nothing overflows,
    which is the cheaper of the two failures.
    """
    head_html = "".join(f"<th>{text(header)}</th>" for header in headers)
    body_html = ""
    for row_html in cells_html:
        row_cells_html = "".join(f"<td>{cell_html}</td>" for cell_html in row_html)
        body_html += f"<tr>{row_cells_html}</tr>"
    table_html = f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"
    return f'<div class="scroll-x" tabindex="0">{table_html}</div>'


def deck(*panels_html: str) -> str:
    joined_html = "".join(panels_html)
    return f'<div class="deck">{joined_html}</div>'


def nav(here: str, links: Sequence[tuple[str, str]]) -> str:
    """Links are (path, label) literals from `app.py`, never anything read."""
    items_html = ""
    for path, label in links:
        current_html = ' aria-current="page"' if path == here else ""
        items_html += f'<a href="{text(path)}"{current_html}>{text(label)}</a>'
    return f'<nav class="nav">{items_html}</nav>'


def document(*, title: str, banner_html: str, nav_html: str, body_html: str) -> str:
    """One self-contained file.

    The stylesheet is inlined rather than served from `/style.css` for two
    reasons that both matter here: a screenshot must not be able to catch a
    flash of unstyled content, and a page somebody saves and sends to a
    colleague should still look like the thing they saw.
    """
    sheet_html = style.STYLESHEET
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{text(title)}</title>"
        f"<style>{sheet_html}</style>"
        "</head><body>"
        f'<div class="page">{banner_html}{nav_html}{body_html}</div>'
        "</body></html>"
    )
