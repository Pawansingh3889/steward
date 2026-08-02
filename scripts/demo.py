#!/usr/bin/env python3
"""The whole thing, end to end, in one command.

    uv run python scripts/demo.py

Two humans, two lines, a real language model, and a real policy engine in
another process. Nothing is stubbed except the phone network — messages print
to the terminal instead of going to a handset, which is what `RecordingChannel`
is for. Add `--linq` to send them for real.

**It does not spend by default, and that is deliberate.** pay-warden mints a
Prava session the moment it *allows* a purchase, and the sandbox has a finite
number of those. So the demo's policy sets a low approval threshold: every
purchase parks for the sponsor, which costs nothing, and the story runs all the
way to somebody being asked. Only `--release` goes further and lets the sponsor
actually approve — which mints one real session. The script says so before it
does it.

What you should watch for, because it is the product:

  * three options with price and delivery, and the agent picking none of them
  * the price that reaches the policy engine coming from the catalogue, not the
    model
  * the sponsor's message carrying the policy's own wording, not a paraphrase
  * the payment link going to the spender, not the sponsor who approved it
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

BOLD, DIM, GREEN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"

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
# Low on purpose for the demo: every purchase parks for a human, which is the
# interesting path and also the one that mints nothing.
human_approval_over: "1.00"
"""


def say(text: str = "") -> None:
    print(text, flush=True)


def rule(title: str) -> None:
    say(f"\n{BOLD}{'─' * 3} {title} {'─' * max(3, 64 - len(title))}{RESET}")


def check_env(live_release: bool) -> None:
    missing = []
    if not os.environ.get("OPENAI_API_KEY"):
        missing.append(
            "OPENAI_API_KEY   — the agent needs it to think.\n"
            "        export OPENAI_API_KEY=$(grep -m1 ^OPENAI_API_KEY= "
            "~/projects/payoptimize/.env | cut -d= -f2-)"
        )
    if not os.environ.get("PAY_WARDEN_COMMAND"):
        missing.append(
            "PAY_WARDEN_COMMAND / _ARGS / _CWD — how to launch the policy engine.\n"
            "        export PAY_WARDEN_COMMAND=uv\n"
            '        export PAY_WARDEN_ARGS="run --project ../pay-warden python -m '
            'pay_warden.server"\n'
            "        export PAY_WARDEN_CWD=$HOME/projects/pay-warden"
        )
    if missing:
        say(f"{YELLOW}Set these first:{RESET}\n")
        for item in missing:
            say(f"  • {item}\n")
        raise SystemExit(1)
    if live_release:
        say(
            f"{YELLOW}--release is on: approving will mint a REAL Prava sandbox session"
            f" and draw down a finite budget.{RESET}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        action="store_true",
        help="let the sponsor approve, which mints a real Prava session",
    )
    parser.add_argument("--linq", action="store_true", help="send messages to real phones")
    parser.add_argument("--keep", action="store_true", help="keep the demo database")
    args = parser.parse_args()
    check_env(args.release)

    # A throwaway household each run, so the demo is the same story every time
    # and never accumulates state that makes the second run different.
    workdir = Path(tempfile.mkdtemp(prefix="steward-demo-"))
    policy = workdir / "household.yaml"
    policy.write_text(POLICY)
    os.environ["STEWARD_DB"] = str(workdir / "demo.sqlite3")
    os.environ["PAY_WARDEN_POLICY"] = str(policy)
    os.environ.setdefault("PAY_WARDEN_DB", str(workdir / "warden.sqlite3"))

    from steward import store
    from steward.models import Role
    from steward.spend import grant, purchase, warden
    from steward.surface.base import Inbound, RecordingChannel
    from steward.surface.linq import LinqChannel
    from steward.surface.router import Router

    channel = LinqChannel() if args.linq else RecordingChannel()
    router = Router(channel=channel)

    # --- the household ------------------------------------------------------
    rule("a household")
    store.init_db()
    rae = store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, phone="+447700900001")
    ana = store.insert_person(
        name="Ana Whitfield", role=Role.SPENDER, sponsor_id=rae, phone="+447700900002"
    )
    store.set_home_location(ana, 51.5074, -0.1278)  # London
    say("  Rae (sponsor)  +447700900001   funds Ana")
    say("  Ana (spender)  +447700900002   in London")
    say(
        f"  {DIM}the coordinate never leaves this machine — the model is told 'arrives in"
        f" 2 days'{RESET}"
    )

    grant.grant_in_file(
        policy, ana, grant.Allowance(daily_budget="50.00", max_single_purchase="30.00")
    )
    say("\n  Rae's policy now registers Ana:")
    say(f"  {DIM}{policy}{RESET}")
    for line in policy.read_text().splitlines():
        if "steward:person" in line or "budget" in line or "purchase" in line:
            say(f"    {DIM}{line.strip()}{RESET}")

    # --- is the policy engine really there? ---------------------------------
    rule("the policy engine, in another process")
    try:
        decision = warden.preview(
            person_id=ana,
            description="hand soap",
            amount_cents=380,
            currency="GBP",
            merchant_name="Everyday Goods",
            merchant_url="https://everyday.fixture.example",
            merchant_country="GB",
        )
    except warden.WardenError as exc:
        say(f"  {YELLOW}pay-warden is not reachable: {exc}{RESET}")
        return 1
    say(f"  a £3.80 soap would be: {BOLD}{decision.verdict}{RESET}  [{decision.rule_id}]")
    say(f"  {DIM}{decision.reason}{RESET}")
    say(f"  {DIM}nothing was minted — preview is a dry run{RESET}")

    # --- Ana texts ----------------------------------------------------------
    rule("Ana texts her line")
    say(f"  {BOLD}Ana:{RESET} I've run out of soap\n")
    handled = router.receive(Inbound(sender="+447700900002", body="I've run out of soap"))
    show(handled, store)

    rule("Ana picks one")
    say(f"  {BOLD}Ana:{RESET} the Everyday Goods one please\n")
    handled = router.receive(Inbound(sender="+447700900002", body="the Everyday Goods one please"))
    show(handled, store)

    waiting = purchase.pending_for_sponsor(rae)
    if not waiting:
        say(
            f"\n  {YELLOW}nothing parked — the agent may not have bought anything."
            f" Read the transcript above.{RESET}"
        )
        return 0

    # --- Rae decides --------------------------------------------------------
    rule("Rae's line — approvals only, never the conversation")
    if not args.release:
        say(
            f"  {DIM}stopping here: approving mints a real Prava session."
            f" Re-run with --release to go all the way.{RESET}"
        )
        say(
            f"\n  what Rae is holding: #{waiting[0]['id']}  {waiting[0]['description']}"
            f"  {waiting[0]['amount_cents'] / 100:.2f} {waiting[0]['currency']}"
        )
        say(f"  {DIM}policy said: {waiting[0]['reason']}{RESET}")
        say(
            f"\n  {DIM}and what Rae can read of Ana's chat:"
            f" {store.shared_turns(ana) or 'nothing — it is private'}{RESET}"
        )
        return 0

    say(f"  {BOLD}Rae:{RESET} yes\n")
    handled = router.receive(Inbound(sender="+447700900001", body="yes"))
    show(handled, store)
    if handled.kind != "approved":
        # Printing the happy line regardless is exactly the dishonesty this
        # project spends its time avoiding. An earlier version did.
        say(f"  {YELLOW}the release did not happen — read the error above.{RESET}")
        return 1
    say(f"  {GREEN}the link went to Ana, not to Rae who approved it.{RESET}")
    return 0


def show(handled, store) -> None:
    """Print what each message caused, addressed to whoever received it."""
    for reply in handled.replies:
        who = store.get_person(reply.outbound.person_id)
        name = who["name"] if who else "?"
        say(f"  {BOLD}→ {name}{RESET} {DIM}({reply.to or 'no line'}){RESET}")
        for line in reply.outbound.body.splitlines():
            say(f"    {line}")
        if not reply.delivered:
            say(f"    {DIM}[not sent: {reply.detail}]{RESET}")
        say()


def run() -> int:
    workdir: Path | None = None
    keep = "--keep" in sys.argv
    try:
        return main()
    finally:
        if not keep:
            for path in Path(tempfile.gettempdir()).glob("steward-demo-*"):
                shutil.rmtree(path, ignore_errors=True)
        elif workdir:
            say(f"{DIM}kept: {workdir}{RESET}")


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except KeyboardInterrupt:
        say("\ninterrupted.")
        raise SystemExit(130) from None
