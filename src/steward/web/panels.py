"""One builder per panel, and the only place this package reads anything.

Every `store.*` call on the sponsor side of steward is in this file, which is
what makes "the dashboard cannot read a private turn" checkable by reading one
module rather than by trusting a convention. `tests/test_web.py` scans the
package for the readers that are *not* allowed here.

Nothing derives its own numbers. Plan progress comes from `goals.view`, money
from `models.money`, delivery from `extract.eta` — the same functions the CLI
and the SMS surface use, so a sponsor reading a figure here and the same figure
in a text message is reading one implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config, pilot, store
from ..catalogue import fixtures
from ..catalogue.search import LABEL as CATALOGUE_LABEL
from ..plan import goals, schedule
from ..plan.schedule import PlanError
from ..spend import grant, purchase, refund, warden
from ..spend.warden import Warden, WardenError
from . import render
from .scope import Household

LEDGER_LIMIT = 25

# Which merchant names are modelled. Read off the catalogue itself rather than
# listed here, so a supplier added there cannot quietly lose its label.
FIXTURE_MERCHANTS = frozenset(supplier.name for supplier in fixtures.SUPPLIERS)


def _spender_names(house: Household) -> dict[int, str]:
    return {int(row["id"]): str(row["name"]) for row in house.spenders()}


def _who(names: dict[int, str], person_id: int) -> str:
    return names.get(person_id, f"person {person_id}")


def _merchant_html(name: str) -> str:
    """A modelled merchant says so, right where its name is.

    The honest form of a real finding: canibuy graded the open web for
    agent-readiness and nothing that sells household essentials passed. Saying
    it once in a footnote would let a screenshot crop it out.
    """
    if not name:
        return render.text("—")
    if name in FIXTURE_MERCHANTS:
        return f"{render.text(name)} {render.chip(CATALOGUE_LABEL)}"
    return render.text(name)


# --- the header --------------------------------------------------------------


def banner(house: Household) -> str:
    sponsor = house.sponsor()
    spenders = house.spenders()
    funds = ", ".join(str(row["name"]) for row in spenders) or "nobody yet"
    return (
        '<header class="banner">'
        f'<h1>{render.text(sponsor["name"])}<span class="readonly">read only</span></h1>'
        f'<p class="who">Sponsor. Funds {render.text(funds)}.</p>'
        '<div class="meta">'
        f"<span>{render.text(f'household of {len(spenders) + 1}')}</span>"
        "<span>this process serves one household; no URL here reaches another</span>"
        "</div>"
        "</header>"
    )


# --- decisions ---------------------------------------------------------------


def waiting_on_you(house: Household) -> str:
    """What the policy engine parked, and the rule that parked it."""
    names = _spender_names(house)
    parked = purchase.pending_for_sponsor(house.sponsor_id, db_path=house.db_path)
    if not parked:
        return render.card(
            "Waiting on you",
            render.empty("Nothing is parked. Every purchase so far cleared policy on its own."),
        )

    cells = []
    for row in parked:
        rule_html = (
            f'<span class="rule">{render.text(row["reason"])}'
            f' <span class="rule-id">[{render.text(row["rule_id"])}]</span></span>'
        )
        cells.append(
            (
                f'<span class="who-cell">{render.text(_who(names, int(row["spender_id"])))}</span>',
                (
                    f"{render.text(row['description'])}"
                    f'<span class="sub">{_merchant_html(str(row["merchant_name"]))}</span>'
                    f"{rule_html}"
                ),
                (
                    f'<span class="amount">'
                    f"{render.money_text(int(row['amount_cents']), str(row['currency']))}</span>"
                ),
                render.badge(str(row["status"])),
            )
        )
    return render.card(
        "Waiting on you",
        render.rows(("who", "what", "amount", ""), cells),
        note_text=(
            "The rule is shown as the policy engine worded it. Releasing one is still"
            " steward approvals approve --id N — nothing on this page can."
        ),
    )


def decided(house: Household, *, limit: int = 12) -> str:
    """Everything already answered, so the queue above reads as a queue."""
    names = _spender_names(house)
    settled = [
        row
        for row in store.list_escalations(sponsor_id=house.sponsor_id, db_path=house.db_path)
        if row["status"] != purchase.PENDING
    ][:limit]
    if not settled:
        return render.card("Already decided", render.empty("Nothing has been answered yet."))

    cells = []
    for row in settled:
        cells.append(
            (
                f'<span class="who-cell">{render.text(_who(names, int(row["spender_id"])))}</span>',
                (
                    f"{render.text(row['description'])}"
                    f'<span class="sub">{_merchant_html(str(row["merchant_name"]))}</span>'
                ),
                (
                    f'<span class="amount">'
                    f"{render.money_text(int(row['amount_cents']), str(row['currency']))}</span>"
                ),
                render.badge(str(row["status"])),
                f'<span class="sub">{render.when(str(row["decided_ts"]))}</span>',
            )
        )
    return render.card("Already decided", render.rows(("who", "what", "amount", "", "when"), cells))


# --- the policy file ---------------------------------------------------------


def registered_in_policy(house: Household) -> str:
    """Enrolling somebody in steward does not let them spend.

    pay-warden denies any agent its policy has never heard of, so this is the
    first thing to check when every purchase comes back `unknown-agent`. It is
    a file read — no subprocess — so it belongs on the front page.
    """
    path = config.pay_warden_policy()
    if not path:
        return render.card(
            "Registered to spend",
            _degraded_html(
                "PAY_WARDEN_POLICY is unset",
                "Without the policy file there is no way to tell, from here, whether"
                " these people can spend at all.",
            ),
        )
    try:
        registered = set(grant.read_agents(Path(path).read_text()))
    except OSError as exc:
        return render.card(
            "Registered to spend", _degraded_html("The policy file could not be read", str(exc))
        )

    items_html = ""
    for row in house.spenders():
        agent = warden.agent_name(int(row["id"]))
        if agent in registered:
            state_html = (
                f"{render.badge('registered', 'approved')} <code>{render.text(agent)}</code>"
            )
        else:
            state_html = (
                f"{render.badge('not registered', 'denied')} "
                '<span class="sub">every purchase is denied <code>unknown-agent</code></span>'
            )
        items_html += (
            f'<div class="plan"><div class="plan-head">'
            f'<span class="plan-name">{render.text(row["name"])}</span>{state_html}'
            f"</div></div>"
        )
    if not items_html:
        return render.card("Registered to spend", render.empty("Nobody is enrolled yet."))
    return render.card(
        "Registered to spend",
        items_html,
        note_text=f"read from {path} — the file is the source of truth, and it is hand-editable",
    )


# --- plans -------------------------------------------------------------------


def saving_for(house: Household) -> str:
    names = _spender_names(house)
    blocks_html = ""
    for person_id, name in names.items():
        for row in store.list_plans(person_id, db_path=house.db_path):
            blocks_html += _plan_html(name, row, db_path=house.db_path)
    if not blocks_html:
        return render.card("Saving for", render.empty("No plans yet."))
    return render.card(
        "Saving for",
        blocks_html,
        note_text=(
            "Advisory only. steward holds no money, so a plan warns when a purchase sets"
            " it back and never blocks one — pay-warden still makes the decision."
        ),
    )


def _plan_html(who: str, row: dict[str, Any], *, db_path: str | None) -> str:
    """One plan, recomputed by `goals.view` — the same numbers the CLI prints.

    Called per plan rather than through `goals.everything` on purpose: `view`
    solves the schedule at read time and raises on numbers it cannot reconcile,
    and the wrapper is a list comprehension, so one unsolvable plan would take
    every other plan of that person's down with it.
    """
    try:
        plan = goals.view(row, db_path=db_path)
    except PlanError as exc:
        return (
            f'<div class="plan"><div class="plan-head">'
            f'<span class="plan-name">{render.text(row["name"])}</span>'
            f"{render.badge('does not add up', 'denied')}</div>"
            f'<p class="plan-line">{render.text(str(exc))}</p></div>'
        )

    currency = str(plan["currency"])
    short_html = ""
    if not plan["reaches_target"]:
        short_html = (
            f'<span class="amount">short '
            f"{render.money_text(int(plan['shortfall_cents']), currency)}</span>"
        )
    line_html = (
        f"{render.money_text(int(plan['per_period_cents']), currency)} a"
        f" {render.text(schedule.noun(str(plan['cadence'])))}"
        f" × {render.text(plan['periods'])} →"
        f" {render.money_text(int(plan['saved_cents']), currency)} of"
        f" {render.money_text(int(plan['target_cents']), currency)}"
        f" by {render.when(str(plan['finish']))}"
    )
    items_html = ""
    for item in plan["items"]:
        books_html = (
            f'<span class="books-it"> ← {render.text(who)} books this</span>'
            if item["needs_human"]
            else ""
        )
        items_html += (
            f"<li>{render.text(item['description'])} "
            f"{render.money_text(int(item['amount_cents']), currency)}{books_html}</li>"
        )
    if items_html:
        items_html = f'<ul class="plan-items">{items_html}</ul>'
    saved_html = ""
    if plan["contributed_cents"]:
        saved_html = (
            f'<p class="plan-line">put aside so far: '
            f"{render.money_text(int(plan['contributed_cents']), currency)}</p>"
        )
    return (
        f'<div class="plan"><div class="plan-head">'
        f'<span class="plan-name">{render.text(plan["name"])}</span>'
        f"{render.badge(str(plan['status']))}"
        f'<span class="sub">{render.text(who)}</span>{short_html}</div>'
        f'<p class="plan-line">{line_html}</p>{saved_html}{items_html}</div>'
    )


# --- refunds -----------------------------------------------------------------


def money_back(house: Household) -> str:
    names = _spender_names(house)
    cells = []
    for person_id, name in names.items():
        for row in refund.everything(person_id, db_path=house.db_path):
            cells.append(
                (
                    f'<span class="who-cell">{render.text(name)}</span>',
                    (
                        f"{render.text(row['description'])}"
                        f'<span class="rule">{render.text(row["reason"])}</span>'
                    ),
                    (
                        f'<span class="amount">'
                        f"{render.money_text(int(row['amount_cents']), str(row['currency']))}"
                        "</span>"
                    ),
                    render.badge(str(row["status"])),
                )
            )
    if not cells:
        return render.card("Asked for back", render.empty("No refund requests."))
    return render.card(
        "Asked for back",
        render.rows(("who", "what", "amount", ""), cells),
        note_text=(
            "Recorded in the person's own words. steward has not contacted the merchant,"
            " opened a dispute or spoken to a bank, and will not."
        ),
    )


# --- the boundary ------------------------------------------------------------


def the_boundary(house: Household, *, full: bool = False) -> str:
    """The panel this whole surface exists for.

    `store.shared_turns` is the only turn reader called anywhere in this
    package. Not `recent_turns` with a filter — a different function, so there
    is no argument anyone could get wrong later.

    It deliberately never says *how much* it is withholding. "14 messages you
    cannot see" is itself conversation metadata: volume and timing are most of
    what a message log tells you about somebody. The panel names the kind of
    thing withheld, never the quantity.
    """
    sponsor_name = str(house.sponsor()["name"])
    # The argument is made once, at the top, rather than repeated beside every
    # spender. Said twice it reads as boilerplate, which is the opposite of what
    # a boundary somebody is being asked to trust should read as.
    intro_html = (
        f'<div class="intro"><p>{render.text(sponsor_name)} sees decisions and the rule'
        " behind each, the ledger pay-warden wrote, plans, and refund requests. Not the"
        " conversation. That is not a permission that could be raised — this surface has"
        " no reader for it. <code>store.shared_turns()</code> is the only turn reader on"
        " the sponsor side of this codebase, and it returns exactly the turns a spender"
        " marked as shared, one at a time.</p></div>"
    )
    blocks_html = ""
    for row in house.spenders():
        person_id, name = int(row["id"]), str(row["name"])
        first = name.split()[0]
        turns_html = ""
        for turn in store.shared_turns(person_id, db_path=house.db_path):
            speaker = name if turn["speaker"] == "person" else "steward"
            turns_html += (
                f'<div class="turn"><div class="turn-head">'
                f'<span class="turn-who">{render.text(speaker)}</span>'
                f"<span>{render.when(str(turn['ts']))}</span>"
                f"{render.badge('shared by ' + first, 'approved')}</div>"
                f'<p class="turn-body">{render.text(turn["text"])}</p></div>'
            )
        if not turns_html:
            turns_html = render.empty(
                f"Nothing. That is the default, and it is {first}'s to change — from their"
                " own line, by text. There is no control here, and that is the point: a"
                " sponsor who could open a conversation from their own dashboard would"
                " make the setting decorative."
            )
        # Said even when — especially when — turns are visible beside it. Sharing
        # is per turn, so switching it off does not retract what was already
        # given, which is "switching it on does not expose what came before"
        # read from the other end.
        state = "on" if row["share_mode"] == store.SHARE_SHARED else "off"
        blocks_html += (
            f'<div class="boundary">'
            f'<div class="withheld"><h3>{render.text(name)}’s conversation</h3>'
            f"<p>Withheld. There is nothing to expand and no request to make:"
            f" {render.text(sponsor_name)} has no way to read this from here, and neither"
            " does the model that answers on the other line.</p></div>"
            f'<div class="shared"><h3>What {render.text(first)} chose to share</h3>'
            f"{turns_html}"
            f'<p class="sharing-state">sharing is currently'
            f" <strong>{render.text(state)}</strong> for {render.text(first)}"
            " — it applies per turn, from the moment it is set, so turning it off does"
            " not retract what was already shared</p></div></div>"
        )
    if not blocks_html:
        blocks_html = render.empty("Nobody is enrolled in this household yet.")
    title = "The boundary" if full else "The boundary, in short"
    return render.card(title, f"{intro_html}{blocks_html}", span=True)


# --- the ledger --------------------------------------------------------------


def _degraded_html(headline: str, said: str) -> str:
    return (
        f'<div class="degraded"><strong>{render.text(headline)}</strong>'
        f'<span class="said">{render.text(said)}</span></div>'
    )


def _minor_units(decimal_string: object) -> int | None:
    """The inverse of `warden.amount_to_decimal`.

    pay-warden's audit rows carry an amount as a decimal string, because that is
    what its policy arithmetic is in. None rather than 0 on anything unreadable:
    a ledger that renders an amount it could not parse as £0.00 is worse than
    one that admits it.
    """
    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    try:
        return int(Decimal(str(decimal_string)).scaleb(2).to_integral_value(ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None


def ledger(house: Household, *, client: Warden | None = None) -> str:
    """pay-warden's own audit rows, asked for one spender at a time.

    Never `person_id=None`. pay-warden's audit database is shared by every agent
    it has ever answered for, so an unfiltered read returns other households'
    purchases — the one thing this surface is built not to do.

    Failures are caught per spender, so an unreachable engine degrades one
    section rather than blanking the page, and the exception's own words are
    shown: `ConfigError` already says exactly what to set, and replacing that
    with "unavailable" throws away the only useful thing on the screen.
    """
    blocks_html = ""
    for row in house.spenders():
        person_id, name = int(row["id"]), str(row["name"])
        try:
            entries = warden.audit_log(person_id=person_id, limit=LEDGER_LIMIT, warden=client)
        except config.ConfigError as exc:
            blocks_html += render.card(
                name, _degraded_html("pay-warden is not configured", str(exc))
            )
            continue
        except WardenError as exc:
            blocks_html += render.card(
                name, _degraded_html("pay-warden could not be reached", str(exc))
            )
            continue
        blocks_html += render.card(name, _ledger_rows_html(entries))
    if not blocks_html:
        blocks_html = render.card("Ledger", render.empty("Nobody is enrolled in this household."))
    return blocks_html


def _what_was_bought(entry: dict[str, Any]) -> str:
    """pay-warden records the line items, not a description.

    Its audit row carries `products` as a JSON string, because that is the
    shape the policy arithmetic ran over. Unreadable means "—" rather than a
    guess: this column is the only place a sponsor learns what was asked for.
    """
    try:
        products = json.loads(str(entry.get("products", "")))
    except (ValueError, TypeError):
        return ""
    if not isinstance(products, list):
        return ""
    return ", ".join(
        str(item["description"])
        for item in products
        if isinstance(item, dict) and item.get("description")
    )


def _ledger_rows_html(entries: list[Any]) -> str:
    if not entries:
        return render.empty("No attempts recorded for this person yet.")
    cells = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        verdict = str(entry.get("verdict", ""))
        cents = _minor_units(entry.get("total_amount"))
        amount_html = (
            f'<span class="amount">'
            f"{render.money_text(cents, str(entry.get('currency', '')))}</span>"
            if cents is not None
            else '<span class="sub">amount unreadable</span>'
        )
        # Never the payment URL, on any route. It is the spender's to follow —
        # that the link goes to them and not to the sponsor who approved it is
        # the thing this page is demonstrating, so printing it here would
        # contradict the argument in the act of illustrating it.
        session_html = (
            render.text("session minted — the link went to the spender")
            if entry.get("session_id")
            else render.text("—")
        )
        bought = _what_was_bought(entry) or "—"
        # The rule and its wording, exactly as the policy engine wrote them.
        # A sponsor reading "denied" without the reason learns nothing they can
        # act on, and the reason is the one part of this row steward did not
        # author.
        rule_html = ""
        if entry.get("reason"):
            rule_html = (
                f'<span class="rule">{render.text(entry["reason"])}'
                f' <span class="rule-id">[{render.text(entry.get("rule_id", ""))}]</span></span>'
            )
        cells.append(
            (
                f"{render.text(bought)}{rule_html}",
                _merchant_html(str(entry.get("merchant_name", ""))),
                amount_html,
                render.badge(verdict or "unrecorded"),
                (
                    f'<span class="sub">{render.when(str(entry.get("ts", "")))}</span>'
                    f'<span class="sub">{session_html}</span>'
                ),
            )
        )
    if not cells:
        return render.empty("pay-warden returned nothing this surface could read.")
    return render.rows(("what", "merchant", "amount", "verdict", "when"), cells)


# --- the pilot counts --------------------------------------------------------


def pilot_counts(house: Household) -> list[dict[str, Any]]:
    """The numbers a pilot check-in reads, per spender.

    `pilot.summary` also counts messages. That count is deliberately dropped
    here rather than merely unrendered: it is text-free, but the number of times
    somebody messaged their agent is conversation metadata, and this is the one
    surface where that distinction is the product.
    """
    found = []
    for row in house.spenders():
        summary = pilot.summary(int(row["id"]), db_path=house.db_path)
        found.append(
            {
                "name": str(row["name"]),
                # From the person, not the event stream: `summary` reports an
                # empty pair for somebody who has done nothing yet, and a
                # blank where a pseudonym belongs reads as a bug.
                "pair": pilot.pair_id(row),
                "raised": summary["escalations_raised"],
                "decided": summary["escalations_decided"],
                "undecided": summary["escalations_undecided"],
                "corrections": summary["corrections"],
                "agent_failures": summary["agent_failures"],
            }
        )
    return found


_COUNT_LABELS = (
    ("raised", "escalations raised"),
    ("decided", "decided"),
    ("undecided", "still waiting"),
    ("corrections", "corrections"),
    ("agent_failures", "agent failures"),
)


def pilot_panel(house: Household) -> str:
    counted = pilot_counts(house)
    if not counted:
        return render.card("Pilot counts", render.empty("Nobody is enrolled in this household."))
    blocks_html = ""
    for entry in counted:
        tiles_html = ""
        for key, label in _COUNT_LABELS:
            tiles_html += (
                f'<div class="count"><span class="n">{render.text(entry[key])}</span>'
                f'<span class="label">{render.text(label)}</span></div>'
            )
        blocks_html += render.card(
            f"{entry['name']} · {entry['pair']}", f'<div class="counts">{tiles_html}</div>'
        )
    return blocks_html
