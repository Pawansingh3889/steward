#!/usr/bin/env python3
"""A household worth looking at, written straight to a database.

    uv run python scripts/seed_demo.py --db demo.sqlite3 --fresh
    STEWARD_DB=demo.sqlite3 uv run python -m steward serve --person 1

This is the *fast* data source for the dashboard, not the honest one. It runs no
agent, calls no policy engine and touches no network, so it is instant and
identical every time — which is what you want while a layout is still moving.
The honest source is `scripts/demo.py --keep`, where the same page is filled by
a real model and a real pay-warden.

Two rules this file keeps, because a screenshot is a claim:

**It invents no payment session.** There is no such thing as a payment pay-warden
did not mint, and a fixture URL on a dashboard screenshot would be the single
most misleading pixel in this project. `scripts/` is not covered by
`tests/test_no_bypass.py`, which is exactly why it has to be deliberate here.

**It seeds no facts and no episodes.** Memory is the spender's own surface.
Putting it in a sponsor's demo database would imply it belongs on a sponsor's
page, and it does not.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"

POLICY = """# Rae's policy for the household. steward only ever adds spenders to it.
version: 1
currencies: [GBP]
base_currency: GBP
rates:
  GBP: "1.00"
agents:
merchants:
  allow: []
  deny:
    - "*.casino.example"
velocity:
  max_purchases: 5
  window_minutes: 60
human_approval_over: "20.00"
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="seed a demo household for the dashboard")
    parser.add_argument("--db", default="demo.sqlite3", help="database to write")
    parser.add_argument("--fresh", action="store_true", help="delete it first")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.db)
    if args.fresh:
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)

    from steward import store
    from steward.models import Role, utc_today
    from steward.plan import schedule
    from steward.spend import grant, purchase

    store.init_db(str(path))
    db = str(path)

    if store.list_people(db_path=db):
        # Refusing rather than adding a second Rae. Re-running a seed script is
        # something people do by reflex, and a database with two households that
        # look alike is worse than an error.
        print(f"{path} already has people in it — pass --fresh to start over", file=sys.stderr)
        return 1

    # --- the household, and a second one that must never appear --------------
    rae = store.insert_person(
        name="Rae Whitfield", role=Role.SPONSOR, phone="+447700900001", db_path=db
    )
    ana = store.insert_person(
        name="Ana Whitfield",
        role=Role.SPENDER,
        sponsor_id=rae,
        phone="+447700900002",
        db_path=db,
    )
    theo = store.insert_person(
        name="Theo Whitfield",
        role=Role.SPENDER,
        sponsor_id=rae,
        phone="+447700900003",
        db_path=db,
    )
    store.set_home_location(ana, 51.5074, -0.1278, db_path=db)  # London
    # A whole second household. The scope test asserts nothing of theirs is
    # reachable, and the screenshot is only honest if there was something to
    # leak: a dashboard that shows everything in an empty database proves
    # nothing about what it would show in a full one.
    marcus = store.insert_person(name="Marcus Idowu", role=Role.SPONSOR, db_path=db)
    priya = store.insert_person(
        name="Priya Idowu", role=Role.SPENDER, sponsor_id=marcus, db_path=db
    )

    # --- what policy parked --------------------------------------------------
    over_threshold = "25 GBP exceeds auto-approval threshold 20.00 GBP; a human must release it"
    store.insert_escalation(
        spender_id=ana,
        sponsor_id=rae,
        attempt_id="att_seed_soap",
        description="hand soap, 2 × 500ml",
        amount_cents=2500,
        currency="GBP",
        merchant_name="Everyday Goods",
        rule_id="human-approval",
        reason=over_threshold,
        db_path=db,
    )
    store.insert_escalation(
        spender_id=theo,
        sponsor_id=rae,
        attempt_id="att_seed_coat",
        description="winter coat",
        amount_cents=4800,
        currency="GBP",
        merchant_name="Bulkline Direct",
        rule_id="max-single-purchase",
        reason="48 GBP exceeds max_single_purchase 30.00 GBP for steward:person_3",
        db_path=db,
    )
    paper = store.insert_escalation(
        spender_id=ana,
        sponsor_id=rae,
        attempt_id="att_seed_paper",
        description="printer paper, 500 sheets",
        amount_cents=1240,
        currency="GBP",
        merchant_name="Corner Shop Express",
        rule_id="human-approval",
        reason="12.40 GBP exceeds auto-approval threshold 10.00 GBP; a human must release it",
        db_path=db,
    )
    headphones = store.insert_escalation(
        spender_id=theo,
        sponsor_id=rae,
        attempt_id="att_seed_headphones",
        description="wireless headphones",
        amount_cents=8900,
        currency="GBP",
        merchant_name="Bulkline Direct",
        rule_id="max-single-purchase",
        reason="89 GBP exceeds max_single_purchase 30.00 GBP for steward:person_3",
        db_path=db,
    )
    # No payment_url, on the approved one least of all. See the module docstring.
    store.decide_escalation(paper, status=purchase.APPROVED, db_path=db)
    store.decide_escalation(headphones, status=purchase.DECLINED, db_path=db)
    store.insert_escalation(
        spender_id=priya,
        sponsor_id=marcus,
        attempt_id="att_seed_tyres",
        description="HOUSEHOLD-TWO-CANARY winter tyres",
        amount_cents=24000,
        currency="GBP",
        merchant_name="Bulkline Direct",
        rule_id="human-approval",
        reason="240 GBP exceeds auto-approval threshold 20.00 GBP; a human must release it",
        db_path=db,
    )

    # --- the conversation, and the sliver of it Ana chose to share -----------
    private = (
        "I've been anxious about money since my shift change",
        "can you not mention the deposit to Rae",
    )
    for said in private:
        store.insert_turn(person_id=ana, speaker="person", text=said, db_path=db)
    shared = (
        ("person", "I'm out of soap, can I get some?"),
        ("steward", "Asked Rae about that one — I'll let you know."),
        ("person", "the Everyday Goods one please"),
    )
    for speaker, said in shared:
        store.insert_turn(
            person_id=ana, speaker=speaker, text=said, shared_with_sponsor=True, db_path=db
        )
    # Sharing off, three turns still visible. That is the interesting state:
    # sharing applies per turn from the moment it is set, so switching it off
    # does not retract what was already given — the same property as switching
    # it on not exposing what came before, seen from the other end.
    store.set_share_mode(ana, store.SHARE_PRIVATE, db_path=db)
    # Theo shares nothing at all, so the pure-absence case renders beside it.
    store.insert_turn(person_id=theo, speaker="person", text="need a coat for winter", db_path=db)

    # --- plans, dated from today ---------------------------------------------
    # Relative, never hard-coded: `goals.view` solves the schedule at read time,
    # so a fixed finish date drifts into the past and turns the page into a 500
    # some months after this file was written.
    today = utc_today()
    lisbon = store.insert_plan(
        person_id=ana,
        name="Lisbon",
        kind="trip",
        target_cents=60000,
        currency="GBP",
        cadence=schedule.MONTHLY,
        per_period_cents=15000,
        start_date=today.isoformat(),
        # Three more contributions on top of the one below is exactly the
        # target, so this plan reads as one that works. The Bike plan is the
        # one that does not add up; showing both is the point.
        finish_date=schedule.add_periods(today, schedule.MONTHLY, 3).isoformat(),
        db_path=db,
    )
    for description, cents, kind in (
        ("flights, two seats", 18000, "flight"),
        ("four nights, Alfama", 22000, "accommodation"),
        ("travel insurance", 4000, "other"),
    ):
        store.insert_plan_item(
            plan_id=lisbon, description=description, amount_cents=cents, kind=kind, db_path=db
        )
    store.set_plan_status(
        lisbon,
        person_id=ana,
        status=store.PLAN_ACTIVE,
        expect=store.PLAN_DRAFT,
        db_path=db,
    )
    store.add_contribution(lisbon, 15000, person_id=ana, db_path=db)
    # Short of its target on purpose, so the shortfall renders.
    store.insert_plan(
        person_id=theo,
        name="Bike",
        kind="goal",
        target_cents=32000,
        currency="GBP",
        cadence=schedule.MONTHLY,
        per_period_cents=4000,
        start_date=today.isoformat(),
        finish_date=schedule.add_periods(today, schedule.MONTHLY, 3).isoformat(),
        db_path=db,
    )

    # --- one refund, in her own words ---------------------------------------
    store.insert_refund(
        person_id=ana,
        attempt_id="att_seed_paper",
        description="printer paper, 500 sheets",
        amount_cents=1240,
        currency="GBP",
        reason="Only 200 sheets arrived and the box had been opened.",
        db_path=db,
    )

    # --- corrections, so the pilot counts are not all zero -------------------
    store.insert_correction(
        person_id=ana,
        kind=store.DELETED_BELIEF,
        subject="supply/shampoo",
        detail="she had already replaced it",
        db_path=db,
    )
    store.insert_correction(
        person_id=ana,
        kind=store.REJECTED_PROPOSAL,
        subject="identity/delivery_address",
        detail="the model read a friend's address off an email",
        db_path=db,
    )

    # --- a policy that registers them ----------------------------------------
    # Through `grant.grant_in_file`, the same call `steward spend grant` makes,
    # so the dashboard's "registered to spend" panel is reporting on a file that
    # was really written by the real code path rather than on a fixture string.
    policy = path.with_name(f"{path.stem}-household.yaml")
    policy.write_text(POLICY)
    grant.grant_in_file(
        policy, ana, grant.Allowance(daily_budget="50.00", max_single_purchase="30.00")
    )
    grant.grant_in_file(
        policy, theo, grant.Allowance(daily_budget="30.00", max_single_purchase="30.00")
    )

    if not args.quiet:
        print(f"\n{BOLD}seeded {path}{RESET}\n")
        print(f"  Rae Whitfield    sponsor  id {rae}   {DIM}← the dashboard's scope{RESET}")
        print(f"  Ana Whitfield    spender  id {ana}   {DIM}3 turns shared, 2 private{RESET}")
        print(f"  Theo Whitfield   spender  id {theo}   {DIM}nothing shared{RESET}")
        print(
            f"  Marcus Idowu     sponsor  id {marcus}   "
            f"{DIM}← a second household, which must never appear{RESET}\n"
        )
        print(f"  {DIM}policy written to {policy}{RESET}\n")
        print(
            f"  {BOLD}STEWARD_DB={path} PAY_WARDEN_POLICY={policy}"
            f" uv run python -m steward serve --person {rae}{RESET}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
