"""The direct interface to your own memory.

Everything here works with `OPENAI_API_KEY` unset. That is the design, not a
side effect: `steward memory list` and `steward memory forget` are how a person
inspects and corrects what is held about them, and neither may depend on the
model being configured, reachable, or willing. The agent is one consumer of
memory; it is not the gatekeeper of it.

`steward ask` is the exception and says so when the model is missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from . import config, store
from .agent import llm
from .catalogue import search
from .extract import pipeline
from .extract.base import INFERRED
from .extract.eta import Point
from .integrations import prices, sync
from .integrations.google import GoogleError
from .memory import recall
from .models import FactKind, Role, money
from .plan import goals, schedule
from .plan.schedule import PlanError
from .spend import grant, purchase, refund, warden
from .spend.warden import WardenError
from .surface.base import Inbound, RecordingChannel
from .surface.linq import LinqChannel
from .surface.router import Router

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _out(text: str = "") -> None:
    print(text, flush=True)


def _resolve_person(args: argparse.Namespace) -> dict[str, Any]:
    """Find the person a command is about, by id or phone.

    Fails loudly on an ambiguous or missing person rather than defaulting to
    "the only one in the database": that default is right until the day a
    household has two people in it, and then it silently shows one of them the
    other's memory.
    """
    if args.person:
        row = store.get_person(int(args.person), db_path=args.db)
        if row is None:
            raise SystemExit(f"no person with id {args.person}")
        return row
    if args.phone:
        row = store.person_by_phone(args.phone, db_path=args.db)
        if row is None:
            raise SystemExit(f"nobody enrolled with phone {args.phone}")
        return row
    people = store.list_people(db_path=args.db)
    if not people:
        raise SystemExit("nobody is enrolled yet — try: steward people add --name … --role spender")
    if len(people) > 1:
        listing = ", ".join(f"{p['id']}={p['name']}" for p in people)
        raise SystemExit(f"which person? pass --person or --phone. enrolled: {listing}")
    return people[0]


# --- people ------------------------------------------------------------------


def cmd_people_list(args: argparse.Namespace) -> int:
    people = store.list_people(db_path=args.db)
    if args.json:
        _out(json.dumps(people, indent=2))
        return 0
    if not people:
        _out("nobody enrolled yet.")
        return 0
    for person in people:
        funded = f" funded by {person['sponsor_id']}" if person["sponsor_id"] else ""
        contact = person["phone"] or person["email"] or "no contact"
        _out(f"  {person['id']:>3}  {BOLD}{person['name']}{RESET}  {person['role']}{funded}")
        _out(f"       {DIM}{contact}{RESET}")
    return 0


def cmd_people_add(args: argparse.Namespace) -> int:
    if args.role not in Role.ALL:
        raise SystemExit(f"role must be one of: {', '.join(Role.ALL)}")
    person_id = store.insert_person(
        name=args.name,
        role=args.role,
        sponsor_id=args.sponsor,
        phone=args.phone,
        email=args.email,
        db_path=args.db,
    )
    _out(f"enrolled {args.name} as {args.role} (id {person_id})")
    return 0


# --- memory ------------------------------------------------------------------


def cmd_memory_list(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    held = recall.everything(int(person["id"]), db_path=args.db)
    if args.json:
        _out(json.dumps(held, indent=2))
        return 0

    _out(f"\n{BOLD}what steward knows about {person['name']}{RESET}\n")
    if held["facts"]:
        _out(f"{BOLD}facts{RESET}  {DIM}(these drive decisions){RESET}")
        for fact in held["facts"]:
            _out(f"  {fact['id']:>4}  {fact['kind']:<10} {fact['key']:<18} {fact['value']}")
            provenance = "you confirmed this" if fact["confirmed"] else fact["source"]
            _out(f"        {DIM}{provenance} · since {fact['since']}{RESET}")
    else:
        _out(f"{DIM}no facts stored.{RESET}")

    if held["pending"]:
        _out()
        _out(
            f"{BOLD}waiting for you{RESET}  "
            f"{DIM}(a model read these; nothing sees them until you say so){RESET}"
        )
        for fact in held["pending"]:
            _out(f"  {fact['id']:>4}  {fact['kind']:<10} {fact['key']:<18} {fact['value']}")
        _out(f"        {DIM}steward memory confirm --fact ID   ·   or forget --fact ID{RESET}")

    _out()
    if held["episodes"]:
        _out(f"{BOLD}things you've said{RESET}  {DIM}(context only, never grounds to spend){RESET}")
        for episode in held["episodes"]:
            _out(f"  {episode['id']:>4}  {episode['text'][:80]}")
            _out(f"        {DIM}{episode['at']}{RESET}")
    else:
        _out(f"{DIM}no conversation remembered.{RESET}")

    _out(f"\n{DIM}forget any of it:  steward memory forget --fact ID   (or --episode ID){RESET}\n")
    return 0


def cmd_memory_search(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    found = recall.search(int(person["id"]), args.query, limit=args.limit, db_path=args.db)
    if args.json:
        _out(json.dumps(found, indent=2))
        return 0
    if not found:
        _out(f"{DIM}nothing resembling that — as far as steward knows, it was never said.{RESET}")
        return 0
    for episode in found:
        _out(f"  {episode['episode_id']:>4}  [{episode['similarity']:.2f}]  {episode['text']}")
    return 0


def cmd_memory_forget(args: argparse.Namespace) -> int:
    if bool(args.fact) == bool(args.episode):
        raise SystemExit("pass exactly one of --fact ID or --episode ID")
    person = _resolve_person(args)
    kind = recall.FACT if args.fact else recall.EPISODE
    item_id = int(args.fact or args.episode)
    try:
        result = recall.forget(kind, item_id, person_id=int(person["id"]), db_path=args.db)
    except store.NotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"forgotten: {kind} {item_id} — {result['was']!r}")
    _out(f"{DIM}it will not influence any future decision.{RESET}")
    return 0


def cmd_memory_confirm(args: argparse.Namespace) -> int:
    """Accept a proposal a model made. Only a person can do this — there is no
    tool for it, because an agent confirming its own guesses is the failure this
    whole mechanism exists to prevent."""
    person = _resolve_person(args)
    try:
        result = recall.confirm(int(args.fact), person_id=int(person["id"]), db_path=args.db)
    except store.NotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"confirmed: {result['key']} — {result['value']!r}")
    _out(f"{DIM}steward may now act on it.{RESET}")
    return 0


def cmd_memory_add(args: argparse.Namespace) -> int:
    """State a fact directly. Useful for setting up, and for correcting the
    agent without having to talk it round."""
    if args.kind not in FactKind.ALL:
        raise SystemExit(f"kind must be one of: {', '.join(FactKind.ALL)}")
    person = _resolve_person(args)
    fact_id = store.upsert_fact(
        person_id=int(person["id"]),
        kind=args.kind,
        key=args.key,
        value=args.value,
        source="stated",
        db_path=args.db,
    )
    _out(f"remembered {args.kind}/{args.key} (id {fact_id})")
    return 0


# --- ingest ------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> int:
    """Read a calendar, a bank alert or a message and learn from it.

    Prints what *would* be learned under --dry-run, because handing a system
    your calendar and finding out afterwards what it took is the wrong order.
    """
    person = _resolve_person(args)
    raw = Path(args.file).read_text() if args.file else sys.stdin.read()

    extraction = pipeline.extract_all(raw, use_local_model=not args.no_local_model)
    if not args.dry_run:
        pipeline.commit(int(person["id"]), extraction, db_path=args.db)

    if args.json:
        _out(json.dumps(extraction.as_dict(), indent=2))
        return 0

    _out(f"{DIM}read by: {extraction.extractor}{RESET}")
    if extraction.degraded:
        _out(f"{DIM}degraded: {extraction.degraded}{RESET}")
    if not extraction.candidates:
        _out("nothing durable to learn from that.")
        return 0
    verb = "would learn" if args.dry_run else "learned"
    _out(f"\n{verb}:")
    waiting = 0
    for candidate in extraction.candidates:
        proposal = candidate.source == INFERRED
        waiting += proposal
        _out(f"  {candidate.kind:<10} {candidate.key:<24} {candidate.value}")
        note = "a model read this — waiting for you" if proposal else f"source: {candidate.source}"
        _out(f"  {DIM}{'':<10} {note}{RESET}")
    if waiting and not args.dry_run:
        _out(
            f"\n{DIM}{waiting} waiting to be confirmed. Nothing sees them until you do:"
            f"\n  steward memory list   then   steward memory confirm --fact ID{RESET}"
        )
    return 0


# --- the catalogue -----------------------------------------------------------


def cmd_shop(args: argparse.Namespace) -> int:
    """What is available, with price and delivery. Buys nothing."""
    person = _resolve_person(args)
    destination = None
    if person["home_lat"] is not None and person["home_lon"] is not None:
        destination = Point(float(person["home_lat"]), float(person["home_lon"]))
    offers = search.find(args.query, destination=destination, limit=args.limit)
    if args.json:
        _out(json.dumps([offer.as_dict() for offer in offers], indent=2))
        return 0
    if not offers:
        _out(f"nothing in the catalogue matches {args.query!r}.")
        return 1
    _out(f"\n{BOLD}options for {args.query!r}{RESET}  {DIM}[{search.LABEL} catalogue]{RESET}\n")
    for offer in offers:
        arrival = offer.delivery.describe() if offer.delivery else "delivery time unknown"
        _out(
            f"  {offer.offer_id:<22} {offer.name:<30}"
            f" {BOLD}{_money(offer.price_cents, offer.currency)}{RESET}"
        )
        _out(f"  {'':<22} {DIM}{offer.supplier_name} · {arrival}{RESET}")
    if destination is None:
        _out(
            f"\n{DIM}no delivery estimates: steward does not know where you are."
            f"\n  steward people locate --lat 51.5074 --lon -0.1278{RESET}"
        )
    _out()
    return 0


def cmd_people_locate(args: argparse.Namespace) -> int:
    """Set roughly where someone is, for delivery estimates.

    The coordinate stays in the database and is read by one module. What the
    model is ever told is "arrives in about two days".
    """
    person = _resolve_person(args)
    if args.forget:
        store.set_home_location(int(person["id"]), None, None, db_path=args.db)
        _out(f"forgotten where {person['name']} is.")
        return 0
    if args.lat is None or args.lon is None:
        raise SystemExit("pass --lat and --lon, or --forget")
    store.set_home_location(int(person["id"]), args.lat, args.lon, db_path=args.db)
    _out(f"noted roughly where {person['name']} is.")
    _out(
        f"{DIM}used for delivery estimates only; the model is told days, never coordinates.{RESET}"
    )
    return 0


# --- spending ----------------------------------------------------------------


def cmd_spend_preview(args: argparse.Namespace) -> int:
    """Ask policy what it *would* say. Nothing is minted and nothing recorded.

    There is deliberately no `spend request` command. The real path runs through
    the agent, where it is attached to something a person actually asked for —
    a CLI flag that mints a live payment session is too easy to fire by accident
    against a finite sandbox budget.
    """
    person = _resolve_person(args)
    try:
        decision = warden.preview(
            person_id=int(person["id"]),
            description=args.description,
            amount_cents=args.amount_cents,
            currency=args.currency,
            merchant_name=args.merchant_name,
            merchant_url=args.merchant_url,
            merchant_country=args.merchant_country,
        )
    except WardenError as exc:
        raise SystemExit(str(exc)) from exc
    if args.json:
        _out(json.dumps(decision.as_dict(), indent=2))
        return 0
    _out(f"\n  {BOLD}{decision.verdict}{RESET}  [{decision.rule_id}]")
    _out(f"  {DIM}{decision.reason}{RESET}\n")
    return 0 if decision.allowed else 1


def cmd_spend_grant(args: argparse.Namespace) -> int:
    """Register a spender in the sponsor's pay-warden policy.

    Without this, enrolling somebody in steward lets them do everything except
    the one thing the product is for: pay-warden denies any agent its policy has
    never heard of.
    """
    person = _resolve_person(args)
    path = args.policy or config.pay_warden_policy()
    if not path:
        raise SystemExit("pass --policy, or set PAY_WARDEN_POLICY")
    try:
        name = grant.grant_in_file(
            path,
            int(person["id"]),
            grant.Allowance(daily_budget=args.daily, max_single_purchase=args.per_purchase),
        )
    except grant.GrantError as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"registered {person['name']} in {path} as {name}")
    _out(f"{DIM}daily {args.daily} · per purchase {args.per_purchase}{RESET}")
    _out(f"{DIM}the file is the source of truth — read it, and edit it by hand any time.{RESET}")
    return 0


# --- messaging ---------------------------------------------------------------


def _router(args: argparse.Namespace) -> Router:
    channel: Any = LinqChannel() if args.channel == "linq" else RecordingChannel()
    return Router(db_path=args.db, channel=channel)


def cmd_text(args: argparse.Namespace) -> int:
    """Deliver a message as if it had arrived from a phone.

    The same entry point a webhook would call, which is the point: the whole
    two-line flow can be rehearsed on one machine with no messaging provider at
    all, and an expired sandbox never blocks the brain.
    """
    handled = _router(args).receive(Inbound(sender=args.sender, body=args.body))
    if args.json:
        _out(json.dumps(handled.as_dict(), indent=2))
        return 0
    if handled.kind == "unknown_sender":
        # Reported to the operator, never to the sender. See surface/router.py.
        _out(f"{DIM}ignored: {handled.detail}{RESET}")
        return 1
    for reply in handled.replies:
        who = store.get_person(reply.outbound.person_id, db_path=args.db)
        _out(
            f"\n{BOLD}→ {who['name'] if who else '?'}{RESET} {DIM}({reply.to or 'no line'}){RESET}"
        )
        for line in reply.outbound.body.splitlines():
            _out(f"  {line}")
        if not reply.delivered:
            _out(f"  {DIM}[not sent: {reply.detail}]{RESET}")
    _out()
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    mode = store.SHARE_SHARED if args.on else store.SHARE_PRIVATE
    store.set_share_mode(int(person["id"]), mode, db_path=args.db)
    if mode == store.SHARE_SHARED:
        _out(f"{person['name']}'s conversation is now visible to their sponsor.")
    else:
        _out(f"{person['name']}'s conversation is private again.")
        _out(f"{DIM}decisions and approvals are still visible — those are not the chat.{RESET}")
    return 0


# --- plans ---------------------------------------------------------------------


def _show_plan(plan: dict[str, Any]) -> None:
    def money_of(cents: int) -> str:
        return _money(cents, plan["currency"])

    flag = (
        ""
        if plan["reaches_target"]
        else f"  {BOLD}short {money_of(plan['shortfall_cents'])}{RESET}"
    )
    _out(f"  {plan['plan_id']:>4}  {BOLD}{plan['name']}{RESET}  [{plan['status']}]{flag}")
    _out(
        f"        {money_of(plan['per_period_cents'])} a {schedule.noun(plan['cadence'])}"
        f" × {plan['periods']} → {money_of(plan['saved_cents'])} of"
        f" {money_of(plan['target_cents'])} by {plan['finish']}"
    )
    if plan["contributed_cents"]:
        _out(f"        {DIM}put aside so far: {money_of(plan['contributed_cents'])}{RESET}")
    for item in plan["items"]:
        needs = "  ← you book this" if item["needs_human"] else ""
        _out(f"        · {item['description']}  {money_of(item['amount_cents'])}{needs}")
    if plan["items"] and not plan["items_covered"]:
        _out(
            f"        {DIM}items add up to {money_of(plan['items_total_cents'])},"
            f" more than the target{RESET}"
        )


def cmd_plan_list(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    plans = goals.everything(int(person["id"]), db_path=args.db)
    if args.json:
        _out(json.dumps(plans, indent=2))
        return 0
    if not plans:
        _out("no plans yet.")
        return 0
    _out(f"\n{BOLD}{person['name']}'s plans{RESET}\n")
    for plan in plans:
        _show_plan(plan)
    _out(f"\n{DIM}start one:  steward plan activate --id N{RESET}\n")
    return 0


def cmd_plan_propose(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        plan = goals.propose(
            person_id=int(person["id"]),
            name=args.name,
            target_cents=args.target_cents,
            finish=date.fromisoformat(args.finish) if args.finish else None,
            per_period_cents=args.per_period_cents or None,
            cadence=args.cadence,
            kind=args.kind,
            already_saved_cents=args.already_saved,
            affordable_cents=args.affordable,
            db_path=args.db,
        )
    except (PlanError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if args.json:
        _out(json.dumps(plan, indent=2))
        return 0
    _out()
    _show_plan(plan)
    _ways(plan)
    _out(
        f"\n{DIM}it does nothing until you start it:  steward plan activate --id"
        f" {plan['plan_id']}{RESET}\n"
    )
    return 0


def _ways(plan: dict[str, Any]) -> None:
    if not plan.get("ways_to_close"):
        return
    _out(f"\n  {BOLD}three ways to close the gap{RESET}  {DIM}(your call, not mine){RESET}")
    for option in plan["ways_to_close"]:
        _out(
            f"        {option['change']:<16}"
            f" {_money(option['target_cents'], option['currency'])} at"
            f" {_money(option['per_period_cents'], option['currency'])} by {option['finish']}"
        )
        _out(f"        {'':<16} {DIM}{option['note']}{RESET}")


def cmd_plan_adjust(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        plan = goals.adjust(
            args.id,
            person_id=int(person["id"]),
            target_cents=args.target_cents or None,
            finish=date.fromisoformat(args.finish) if args.finish else None,
            per_period_cents=args.per_period_cents or None,
            cadence=args.cadence or None,
            db_path=args.db,
        )
    except (PlanError, store.NotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    _out()
    _show_plan(plan)
    _ways(plan)
    _out()
    return 0


def cmd_plan_item(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        item = goals.add_item(
            args.id,
            person_id=int(person["id"]),
            description=args.description,
            amount_cents=args.amount_cents,
            kind=args.kind,
            db_path=args.db,
        )
    except (PlanError, store.NotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"added: {args.description}")
    if item["needs_human"]:
        what = "flights" if args.kind == goals.FLIGHT else "places to stay"
        _out(f"{DIM}you book this one yourself — steward never books {what}.{RESET}")
    return 0


def cmd_plan_activate(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        started = goals.activate(args.id, person_id=int(person["id"]), db_path=args.db)
    except (PlanError, store.NotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"started: {started['name']}")
    _out(f"{DIM}steward will say when something would set it back. It will not stop you.{RESET}")
    return 0


def cmd_plan_abandon(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        stopped = goals.abandon(args.id, person_id=int(person["id"]), db_path=args.db)
    except (PlanError, store.NotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"stopped: {stopped['name']}")
    return 0


def cmd_plan_contribute(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        plan = goals.contribute(
            args.id, args.amount_cents, person_id=int(person["id"]), db_path=args.db
        )
    except (PlanError, store.NotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    _out()
    _show_plan(plan)
    _out()
    return 0


# --- integrations --------------------------------------------------------------


def cmd_pull(args: argparse.Namespace) -> int:
    """Read a Google account and keep only the facts.

    What prints is a count and what was learned. Deliberately never the mail or
    the events themselves — a surface that echoed somebody's inbox back at them
    would undo the point of reading it locally.
    """
    person = _resolve_person(args)
    try:
        if args.source == "calendar":
            result = sync.pull_calendar(int(person["id"]), db_path=args.db)
            read = f"{result['events_read']} events"
        else:
            result = sync.pull_mail(int(person["id"]), limit=args.limit, db_path=args.db)
            read = f"{result['messages_read']} messages"
    except GoogleError as exc:
        raise SystemExit(str(exc)) from exc

    if args.json:
        _out(json.dumps(result, indent=2))
        return 0
    _out(f"{DIM}read {read}{RESET}")
    if not result["facts"]:
        _out("nothing durable to learn from that.")
        return 0
    waiting = 0
    for fact in result["facts"]:
        waiting += fact["pending"]
        _out(f"  {fact['kind']:<10} {fact['key']:<26} {fact['value']}")
    if waiting:
        _out(f"\n{DIM}{waiting} waiting for you to confirm:  steward memory list{RESET}")
    return 0


def cmd_trip_from_calendar(args: argparse.Namespace) -> int:
    """Propose a trip plan sized to the next away event in the calendar."""
    person = _resolve_person(args)
    try:
        plan = sync.trip_from_calendar(
            int(person["id"]), target_cents=args.target_cents, db_path=args.db
        )
    except (GoogleError, PlanError) as exc:
        raise SystemExit(str(exc)) from exc
    if plan is None:
        _out("nothing in the calendar that looks like a trip.")
        return 1
    _out(
        f"\n{DIM}from your calendar: {plan['from_calendar']['summary']}"
        f" departs {plan['from_calendar']['departs']}{RESET}"
    )
    _show_plan(plan)
    _ways(plan)
    _out(
        f"\n{DIM}it does nothing until you start it:  steward plan activate --id"
        f" {plan['plan_id']}{RESET}\n"
    )
    return 0


def cmd_price(args: argparse.Namespace) -> int:
    """Read a price off a real product page, if it publishes one."""
    try:
        found = prices.fetch(args.url)
    except prices.PriceError as exc:
        raise SystemExit(str(exc)) from exc
    if found is None:
        _out(f"{DIM}{args.url} publishes no structured price.{RESET}")
        _out(
            f"{DIM}steward will not scrape one from the visible text — a number nobody"
            f" promised is not a price. This merchant stays modelled.{RESET}"
        )
        return 1
    if args.json:
        _out(json.dumps(found.as_dict(), indent=2))
        return 0
    _out(
        f"  {BOLD}{_money(found.price_cents, found.currency)}{RESET}"
        f"  {DIM}via {found.source}"
        f"{' · ' + found.availability if found.availability else ''}{RESET}"
    )
    return 0


# --- evaluation ----------------------------------------------------------------


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Run the whole evaluation and print it, caveats included."""
    from .evaluation import report as evaluation

    points = evaluation.sweep()
    converged = evaluation.converges_with_no_advantage()
    if args.json:
        _out(evaluation.as_json(points))
        return 0

    _out(f"\n{BOLD}does steward make anyone better off?{RESET}")
    _out(f"{DIM}primary metric, fixed before the first run: {evaluation.PRIMARY}{RESET}\n")

    mark = "passes" if converged else f"{BOLD}FAILS{RESET}"
    _out(f"  strip every advantage → arms identical: {mark}")
    if not converged:
        # Printed first and loudly: without it the numbers below are measuring
        # a decision in world.py rather than a mechanism.
        _out(f"  {BOLD}the numbers below are not evidence of anything.{RESET}")

    _out(f"\n  {BOLD}by household{RESET}  {DIM}(forgetfulness 0.5){RESET}")
    _out(f"    {'household':<14} {'without':>9} {'with':>9}   verdict")
    for row in evaluation.by_household(forgetfulness=0.5):
        _out(
            f"    {row['household']:<14} {row['without_agent']:>9.2f} {row['with_agent']:>9.2f}"
            f"   {row['better']}"
        )
    _out(f"    {DIM}the mean across these is an artefact — see docs/EVALUATION.md{RESET}")

    _out(
        f"\n  {BOLD}sweep{RESET}  {DIM}(averaged; read the per-household table above first){RESET}"
    )
    _out(f"    {'forgetfulness':>13} {'without':>9} {'with':>9}   verdict")
    for point in points:
        c = point.primary
        _out(
            f"    {point.forgetfulness:>13.1f} {c.without_agent:>9.2f} {c.with_agent:>9.2f}"
            f"   {c.better}"
        )
    _out(f"\n{DIM}what this does not show: docs/EVALUATION.md{RESET}\n")
    return 0


# --- refunds -------------------------------------------------------------------


def cmd_refund_request(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        result = refund.request(
            person_id=int(person["id"]),
            attempt_id=args.attempt,
            description=args.description,
            amount_cents=args.amount_cents,
            currency=args.currency,
            reason=args.reason,
            db_path=args.db,
        )
    except refund.RefundError as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"recorded: {result['description']} — {result['amount']}")
    _out(f"{DIM}{result['note']}.{RESET}")
    return 0


def cmd_refund_list(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    rows = refund.everything(int(person["id"]), db_path=args.db)
    if args.json:
        _out(json.dumps(rows, indent=2))
        return 0
    if not rows:
        _out("no refunds asked for.")
        return 0
    for row in rows:
        _out(
            f"  {row['id']:>4}  {row['description']:<28}"
            f" {_money(int(row['amount_cents']), str(row['currency']))}  [{row['status']}]"
        )
        _out(f"        {DIM}{row['reason']}{RESET}")
        if row["resolution"]:
            _out(f"        {DIM}→ {row['resolution']}{RESET}")
    return 0


def cmd_refund_resolve(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        result = refund.resolve(
            args.id,
            person_id=int(person["id"]),
            refunded=args.refunded,
            resolution=args.note,
            db_path=args.db,
        )
    except (refund.RefundError, store.NotFoundError, store.StoreError) as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"{result['status']}: {result['description']}")
    return 0


# --- approvals ---------------------------------------------------------------


# Kept as a name because the CLI reads better with it; the format itself lives
# in models so every surface renders money the same way.
_money = money


def cmd_approvals_list(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    waiting = purchase.pending_for_sponsor(int(person["id"]), db_path=args.db)
    if args.json:
        _out(json.dumps(waiting, indent=2))
        return 0
    if not waiting:
        _out("nothing waiting on you.")
        return 0
    _out(f"\n{BOLD}waiting for {person['name']}{RESET}\n")
    for row in waiting:
        spender = store.get_person(int(row["spender_id"]), db_path=args.db)
        who = spender["name"] if spender else f"person {row['spender_id']}"
        _out(
            f"  {row['id']:>4}  {who} wants {row['description']}"
            f"  {BOLD}{_money(int(row['amount_cents']), str(row['currency']))}{RESET}"
        )
        if row["merchant_name"]:
            _out(f"        {DIM}from {row['merchant_name']}{RESET}")
        # The rule that fired is the reason they are being asked at all, so it
        # is shown exactly as the policy engine worded it.
        _out(f"        {DIM}policy: {row['reason']}  [{row['rule_id']}]{RESET}")
    _out(f"\n{DIM}steward approvals approve --id N   ·   or decline --id N{RESET}\n")
    return 0


def cmd_approvals_approve(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        result = purchase.approve(int(args.id), sponsor_id=int(person["id"]), db_path=args.db)
    except (purchase.PurchaseError, WardenError) as exc:
        raise SystemExit(str(exc)) from exc
    _out(
        f"approved: {result['description']} — {_money(result['amount_cents'], result['currency'])}"
    )
    _out(f"\n  {BOLD}{result['payment_url']}{RESET}")
    _out(f"\n{DIM}they complete it with their passkey. Nothing is charged until they do.{RESET}")
    return 0


def cmd_approvals_decline(args: argparse.Namespace) -> int:
    person = _resolve_person(args)
    try:
        result = purchase.decline(int(args.id), sponsor_id=int(person["id"]), db_path=args.db)
    except purchase.PurchaseError as exc:
        raise SystemExit(str(exc)) from exc
    _out(f"declined: {result['description']}")
    return 0


# --- the dashboard -----------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    # Imported here so every other subcommand neither pays for starlette and
    # uvicorn on startup nor fails without them.
    import uvicorn

    from .web import build_app, scope

    try:
        house = scope.resolve(int(args.person), db_path=args.db)
        port = int(args.port) or config.web_port()
    except (scope.ScopeError, config.ConfigError) as exc:
        raise SystemExit(str(exc)) from exc

    sponsor = house.sponsor()
    _out(f"\n{BOLD}steward — {sponsor['name']}'s household{RESET}          {DIM}read only{RESET}\n")
    # The database is named because `main` has already called `init_db` on it by
    # the time this runs, which migrates whatever it was pointed at. Preventing
    # that would be worse — a process that refuses to migrate then fails every
    # query on a stale schema — but the operator should know which file moved.
    _out(f"  {DIM}database{RESET}   {args.db or config.db_path()}")
    _out(f"  {DIM}address{RESET}    http://127.0.0.1:{port}")
    _out(f"  {DIM}scope{RESET}      person {args.person}; no URL here reaches another household")
    _out(f"  {DIM}ledger{RESET}     /ledger spawns pay-warden — nothing else does")
    _out(f"\n  {DIM}nothing here writes. approving is still:{RESET}")
    _out(f"  {DIM}steward approvals approve --id N{RESET}\n")

    # 127.0.0.1, not 0.0.0.0, and not a setting. See config.web_port.
    uvicorn.run(build_app(house), host="127.0.0.1", port=port, log_level="warning")
    return 0


# --- ask ---------------------------------------------------------------------


def cmd_ask(args: argparse.Namespace) -> int:
    from .agent import loop

    person = _resolve_person(args)
    if not config.openai_api_key():
        _out("OPENAI_API_KEY is unset, so the agent cannot answer.")
        _out(f"{DIM}memory still works: try  steward memory list{RESET}")
        return 2
    try:
        result = loop.run(args.question, person_id=int(person["id"]), db_path=args.db)
    except llm.AgentError as exc:
        raise SystemExit(f"the agent could not answer: {exc}") from exc
    _out(f"\n{result['display_answer']}\n")
    if result["evidence"]:
        used = ", ".join(dict.fromkeys(e["tool"] for e in result["evidence"]))
        _out(f"{DIM}consulted: {used}  ·  run {result['run_id']}{RESET}")
    return 0


# --- wiring ------------------------------------------------------------------


def _add_person_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--person", type=int, default=0, help="person id")
    parser.add_argument("--phone", default="", help="or find them by phone")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steward", description=__doc__)
    parser.add_argument("--db", default=None, help="database path (default: $STEWARD_DB)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    people = sub.add_parser("people", help="who steward knows about").add_subparsers(
        dest="people_command", required=True
    )
    people.add_parser("list", help="list enrolled people").set_defaults(func=cmd_people_list)
    add = people.add_parser("add", help="enrol someone")
    add.add_argument("--name", required=True)
    add.add_argument("--role", required=True, help=f"one of: {', '.join(Role.ALL)}")
    add.add_argument("--sponsor", type=int, default=None, help="id of who funds them")
    add.add_argument("--phone", default="")
    add.add_argument("--email", default="")
    add.set_defaults(func=cmd_people_add)

    locating = people.add_parser("locate", help="set roughly where someone is")
    _add_person_flags(locating)
    locating.add_argument("--lat", type=float, default=None)
    locating.add_argument("--lon", type=float, default=None)
    locating.add_argument("--forget", action="store_true", help="clear it")
    locating.set_defaults(func=cmd_people_locate)

    shopping = sub.add_parser("shop", help="what is available, with price and delivery")
    _add_person_flags(shopping)
    shopping.add_argument("query")
    shopping.add_argument("--limit", type=int, default=6)
    shopping.set_defaults(func=cmd_shop)

    memory = sub.add_parser("memory", help="inspect and correct what is held about you")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)

    listing = memory_sub.add_parser("list", help="everything steward knows about a person")
    _add_person_flags(listing)
    listing.set_defaults(func=cmd_memory_list)

    searching = memory_sub.add_parser("search", help="find something that was said")
    _add_person_flags(searching)
    searching.add_argument("query")
    searching.add_argument("--limit", type=int, default=5)
    searching.set_defaults(func=cmd_memory_search)

    forgetting = memory_sub.add_parser("forget", help="delete one remembered thing")
    _add_person_flags(forgetting)
    forgetting.add_argument("--fact", type=int, default=0)
    forgetting.add_argument("--episode", type=int, default=0)
    forgetting.set_defaults(func=cmd_memory_forget)

    confirming = memory_sub.add_parser("confirm", help="accept a proposal a model made")
    _add_person_flags(confirming)
    confirming.add_argument("--fact", type=int, required=True, help="id from memory list")
    confirming.set_defaults(func=cmd_memory_confirm)

    adding = memory_sub.add_parser("add", help="state a fact directly")
    _add_person_flags(adding)
    adding.add_argument("--kind", required=True, help=f"one of: {', '.join(FactKind.ALL)}")
    adding.add_argument("--key", required=True)
    adding.add_argument("--value", required=True)
    adding.set_defaults(func=cmd_memory_add)

    ingesting = sub.add_parser("ingest", help="learn from a calendar, alert or message")
    _add_person_flags(ingesting)
    ingesting.add_argument("--file", default="", help="path to read; omit to read stdin")
    ingesting.add_argument(
        "--dry-run", action="store_true", help="show what would be learned, store nothing"
    )
    ingesting.add_argument(
        "--no-local-model", action="store_true", help="deterministic parsers only"
    )
    ingesting.set_defaults(func=cmd_ingest)

    spending = sub.add_parser("spend", help="check what policy would say")
    spending_sub = spending.add_subparsers(dest="spend_command", required=True)
    previewing = spending_sub.add_parser(
        "preview", help="dry-run a purchase against policy; mints nothing"
    )
    _add_person_flags(previewing)
    previewing.add_argument("--description", required=True)
    previewing.add_argument("--amount-cents", type=int, required=True, dest="amount_cents")
    previewing.add_argument("--currency", default="GBP")
    previewing.add_argument("--merchant-name", required=True, dest="merchant_name")
    previewing.add_argument("--merchant-url", required=True, dest="merchant_url")
    previewing.add_argument("--merchant-country", default="GB", dest="merchant_country")
    previewing.set_defaults(func=cmd_spend_preview)

    granting = spending_sub.add_parser(
        "grant", help="register a spender in the sponsor's pay-warden policy"
    )
    _add_person_flags(granting)
    granting.add_argument("--policy", default="", help="path; defaults to $PAY_WARDEN_POLICY")
    granting.add_argument("--daily", required=True, help='daily budget, e.g. "50.00"')
    granting.add_argument(
        "--per-purchase", required=True, dest="per_purchase", help="cap per purchase"
    )
    granting.set_defaults(func=cmd_spend_grant)

    texting = sub.add_parser("text", help="deliver a message as if it arrived from a phone")
    texting.add_argument("sender", help="the number it came from")
    texting.add_argument("body")
    texting.add_argument(
        "--channel",
        default="recording",
        choices=("recording", "linq"),
        help="where replies go; linq is dry-run unless STEWARD_LINQ_LIVE=1",
    )
    texting.set_defaults(func=cmd_text)

    sharing = sub.add_parser("share", help="who can see a spender's conversation")
    _add_person_flags(sharing)
    mode = sharing.add_mutually_exclusive_group(required=True)
    mode.add_argument("--on", action="store_true", help="let the sponsor see it")
    mode.add_argument("--off", action="store_true", help="make it private again")
    sharing.set_defaults(func=cmd_share)

    planning = sub.add_parser("plan", help="saving up for something")
    planning_sub = planning.add_subparsers(dest="plan_command", required=True)

    plan_list = planning_sub.add_parser("list", help="plans and drafts")
    _add_person_flags(plan_list)
    plan_list.set_defaults(func=cmd_plan_list)

    plan_new = planning_sub.add_parser("propose", help="work out a schedule; saves a draft")
    _add_person_flags(plan_new)
    plan_new.add_argument("--name", required=True)
    plan_new.add_argument("--target-cents", type=int, required=True, dest="target_cents")
    plan_new.add_argument("--finish", default="", help="YYYY-MM-DD, or omit to solve for it")
    plan_new.add_argument(
        "--per-period-cents",
        type=int,
        default=0,
        dest="per_period_cents",
        help="or omit to solve for it",
    )
    plan_new.add_argument("--cadence", default="monthly")
    plan_new.add_argument("--kind", default="goal", choices=("goal", "trip"))
    plan_new.add_argument("--already-saved", type=int, default=0, dest="already_saved")
    plan_new.add_argument("--affordable", type=int, default=0, help="what you can spare each time")
    plan_new.set_defaults(func=cmd_plan_propose)

    plan_adj = planning_sub.add_parser("adjust", help="change one number on a draft")
    _add_person_flags(plan_adj)
    plan_adj.add_argument("--id", type=int, required=True)
    plan_adj.add_argument("--target-cents", type=int, default=0, dest="target_cents")
    plan_adj.add_argument("--finish", default="")
    plan_adj.add_argument("--per-period-cents", type=int, default=0, dest="per_period_cents")
    plan_adj.add_argument("--cadence", default="")
    plan_adj.set_defaults(func=cmd_plan_adjust)

    plan_item = planning_sub.add_parser("item", help="add something a trip pays for")
    _add_person_flags(plan_item)
    plan_item.add_argument("--id", type=int, required=True)
    plan_item.add_argument("--description", required=True)
    plan_item.add_argument("--amount-cents", type=int, required=True, dest="amount_cents")
    plan_item.add_argument("--kind", default="other", choices=("flight", "accommodation", "other"))
    plan_item.set_defaults(func=cmd_plan_item)

    plan_go = planning_sub.add_parser("activate", help="start a draft")
    _add_person_flags(plan_go)
    plan_go.add_argument("--id", type=int, required=True)
    plan_go.set_defaults(func=cmd_plan_activate)

    plan_stop = planning_sub.add_parser("abandon", help="stop a plan")
    _add_person_flags(plan_stop)
    plan_stop.add_argument("--id", type=int, required=True)
    plan_stop.set_defaults(func=cmd_plan_abandon)

    plan_put = planning_sub.add_parser("contribute", help="record money you put aside")
    _add_person_flags(plan_put)
    plan_put.add_argument("--id", type=int, required=True)
    plan_put.add_argument("--amount-cents", type=int, required=True, dest="amount_cents")
    plan_put.set_defaults(func=cmd_plan_contribute)

    pulling = sub.add_parser("pull", help="read a connected Google account")
    _add_person_flags(pulling)
    pulling.add_argument("source", choices=("calendar", "mail"))
    pulling.add_argument("--limit", type=int, default=20, help="messages, for mail")
    pulling.set_defaults(func=cmd_pull)

    tripping = sub.add_parser("trip", help="plan for the next trip in your calendar")
    _add_person_flags(tripping)
    tripping.add_argument("--target-cents", type=int, required=True, dest="target_cents")
    tripping.set_defaults(func=cmd_trip_from_calendar)

    pricing = sub.add_parser("price", help="read a price off a real product page")
    pricing.add_argument("url")
    pricing.set_defaults(func=cmd_price)

    refunds = sub.add_parser("refund", help="ask for money back on something bought")
    refunds_sub = refunds.add_subparsers(dest="refund_command", required=True)

    refund_new = refunds_sub.add_parser("request", help="record that you want a refund")
    _add_person_flags(refund_new)
    refund_new.add_argument("--attempt", required=True, help="pay-warden attempt id")
    refund_new.add_argument("--description", required=True)
    refund_new.add_argument("--amount-cents", type=int, required=True, dest="amount_cents")
    refund_new.add_argument("--currency", default="GBP")
    refund_new.add_argument("--reason", required=True, help="your words; stored verbatim")
    refund_new.set_defaults(func=cmd_refund_request)

    refund_list = refunds_sub.add_parser("list", help="refunds asked for")
    _add_person_flags(refund_list)
    refund_list.set_defaults(func=cmd_refund_list)

    refund_done = refunds_sub.add_parser("resolve", help="record how it ended")
    _add_person_flags(refund_done)
    refund_done.add_argument("--id", type=int, required=True)
    outcome = refund_done.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--refunded", action="store_true")
    outcome.add_argument("--refused", action="store_true")
    refund_done.add_argument("--note", default="")
    refund_done.set_defaults(func=cmd_refund_resolve)

    evaluating = sub.add_parser("evaluate", help="does this make anyone better off?")
    evaluating.set_defaults(func=cmd_evaluate)

    approvals = sub.add_parser("approvals", help="purchases waiting on a sponsor")
    approvals_sub = approvals.add_subparsers(dest="approvals_command", required=True)

    waiting = approvals_sub.add_parser("list", help="what is waiting on you")
    _add_person_flags(waiting)
    waiting.set_defaults(func=cmd_approvals_list)

    approving = approvals_sub.add_parser("approve", help="release a parked purchase")
    _add_person_flags(approving)
    approving.add_argument("--id", type=int, required=True, help="escalation id")
    approving.set_defaults(func=cmd_approvals_approve)

    declining = approvals_sub.add_parser("decline", help="refuse a parked purchase")
    _add_person_flags(declining)
    declining.add_argument("--id", type=int, required=True, help="escalation id")
    declining.set_defaults(func=cmd_approvals_decline)

    serving = sub.add_parser("serve", help="a read-only dashboard for one sponsor")
    # Deliberately not _add_person_flags: that resolver falls back to "the only
    # person in the database", which is right for a one-shot command and wrong
    # for a process that stays up — it would bind a server to whoever is row 1.
    serving.add_argument(
        "--person",
        type=int,
        required=True,
        help="the sponsor whose household this serves; nothing else is reachable",
    )
    serving.add_argument("--port", type=int, default=0, help="override $STEWARD_WEB_PORT")
    serving.set_defaults(func=cmd_serve)

    asking = sub.add_parser("ask", help="ask the agent something")
    _add_person_flags(asking)
    asking.add_argument("question")
    asking.set_defaults(func=cmd_ask)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store.init_db(args.db)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
