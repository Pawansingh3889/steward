"""The real MCP wire to pay-warden. Opt-in, and never in the default suite.

Everything else about spending is tested against a stub, which is right: the
suite must not spawn subprocesses, depend on a sibling checkout, or take a
second per case. But a stub cannot tell you whether steward's `mcp` 2.x client
can actually talk to pay-warden's 1.x server, and that assumption is the one
this whole phase rests on. So it gets a test, run deliberately:

    STEWARD_WARDEN_IT=1 \\
    PAY_WARDEN_COMMAND=uv \\
    PAY_WARDEN_ARGS="run --project ../pay-warden python -m pay_warden.server" \\
    PAY_WARDEN_CWD=../pay-warden \\
    PAY_WARDEN_POLICY=/abs/path/to/policy.yaml \\
    uv run pytest tests/test_warden_integration.py -v

**Only `preview_purchase` is exercised.** It is a dry run: no Prava session is
minted and nothing is recorded. `request_purchase` would draw on a finite
sandbox transaction budget, which no test may ever spend.
"""

from __future__ import annotations

import os

import pytest

from steward.spend import warden

pytestmark = pytest.mark.skipif(
    os.environ.get("STEWARD_WARDEN_IT") != "1",
    reason="set STEWARD_WARDEN_IT=1 to run against a real pay-warden subprocess",
)

# Captured at import, before conftest's autouse `env` fixture pins these to
# harmless test values. Reading them inside a fixture would get "true" — the
# stub command — and this file would silently test nothing.
REAL_COMMAND = os.environ.get("PAY_WARDEN_COMMAND", "")
REAL_ARGS = os.environ.get("PAY_WARDEN_ARGS", "").split()
REAL_CWD = os.environ.get("PAY_WARDEN_CWD") or None

MERCHANT = {
    "merchant_name": "Fixture Store",
    "merchant_url": "https://fixture.example",
    "merchant_country": "GB",
}


@pytest.fixture
def real_warden() -> warden.StdioWarden:
    """Built from the captured environment rather than from config, which the
    autouse fixture has already redirected."""
    if not REAL_COMMAND:
        pytest.skip("PAY_WARDEN_COMMAND is not set")
    return warden.StdioWarden(command=[REAL_COMMAND, *REAL_ARGS], cwd=REAL_CWD)


def preview(real_warden: warden.StdioWarden, cents: int, **overrides) -> warden.Decision:
    return warden.preview(
        person_id=2,
        description="hand soap",
        amount_cents=cents,
        currency="GBP",
        warden=real_warden,
        **{**MERCHANT, **overrides},
    )


def test_the_two_mcp_versions_can_talk(real_warden: warden.StdioWarden) -> None:
    """steward is on mcp 2.x, pay-warden on 1.x. The protocol is the contract
    between them; this is the test that the contract holds."""
    decision = preview(real_warden, 450)

    assert decision.verdict in warden.VERDICTS
    assert decision.rule_id  # a real rule fired, not a default


def test_an_unregistered_spender_is_denied_rather_than_allowed(
    real_warden: warden.StdioWarden,
) -> None:
    """pay-warden fails closed on an agent its policy has never heard of. Worth
    pinning: it means enrolling someone in steward is not enough to let them
    spend, which is the correct and initially surprising behaviour."""
    decision = warden.preview(
        person_id=999_999,
        description="hand soap",
        amount_cents=450,
        currency="GBP",
        warden=real_warden,
        **MERCHANT,
    )

    assert decision.verdict == warden.DENIED
    assert "not registered" in decision.reason


def test_a_denied_merchant_is_denied(real_warden: warden.StdioWarden) -> None:
    decision = preview(real_warden, 500, merchant_url="https://big.casino.example")

    assert decision.verdict == warden.DENIED
