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
from pathlib import Path
from typing import Any

from . import config, store
from .agent import llm
from .extract import pipeline
from .extract.base import INFERRED
from .memory import recall
from .models import FactKind, Role
from .spend import purchase, warden
from .spend.warden import WardenError

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


# --- approvals ---------------------------------------------------------------


def _money(amount_cents: int, currency: str) -> str:
    symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(currency, "")
    return f"{symbol}{amount_cents / 100:,.2f} {currency}".strip()


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
