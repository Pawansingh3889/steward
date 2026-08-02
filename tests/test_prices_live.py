"""Reading prices off the real open web. Opt-in, and never in the default suite.

    STEWARD_LIVE_PRICES=1 uv run pytest tests/test_prices_live.py -v

Plain public GETs of product pages — no credentials, no personal data, nothing
written. Excluded from the default suite anyway, because a test that depends on
somebody else's website is a test that fails for reasons that have nothing to do
with this code.

What it is for: canibuy graded merchants for agent-readiness, and the whole
fixture-catalogue argument rests on those grades. This checks the grades still
describe reality, from the other direction — a merchant that grades well should
be readable and one that grades badly should not be, and if that ever flips, the
argument for a modelled catalogue needs revisiting rather than repeating.

A grade is a whole-journey verdict and reading a price is one stage of it, so
the two can move apart. adafruit fell from C to F between two sweeps a day
apart and still publishes a perfectly readable price: what changed was further
down the journey, not here. That is worth knowing before treating a grade as a
proxy for any single capability.

Last run 2026-08-02, all three as expected:

    adafruit.com          F    35.00 USD via json-ld, InStock
    sparkfun.com          C     7.50 USD via json-ld
    bluebottlecoffee.com  F    loads, no structured price → stays modelled
"""

from __future__ import annotations

import os

import pytest

from steward.integrations import prices

pytestmark = [
    # Both, deliberately: the marker gets past conftest's no-network guard, and
    # the skipif means a default run never gets that far.
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("STEWARD_LIVE_PRICES") != "1",
        reason="set STEWARD_LIVE_PRICES=1 to fetch real merchant pages",
    ),
]

# Graded by canibuy. The grade is the prediction; the assertion is the check, and
# when they disagree the check wins — adafruit grades F now and still prices
# cleanly, because its F is about what happens after the price.
READABLE = [
    ("adafruit.com", "https://www.adafruit.com/product/3055"),
    ("sparkfun.com", "https://www.sparkfun.com/products/13678"),
]
UNREADABLE = [
    ("bluebottlecoffee.com", "https://bluebottlecoffee.com/us/eng/product/hayes-valley-espresso"),
]


@pytest.mark.parametrize("name, url", READABLE)
def test_a_well_graded_merchant_can_be_priced(name: str, url: str) -> None:
    try:
        found = prices.fetch(url)
    except prices.PriceError as exc:
        pytest.skip(f"{name} unreachable: {exc}")

    assert found is not None, f"{name} graded C but has no structured price any more"
    assert found.price_cents > 0
    assert len(found.currency) == 3
    assert found.source in ("json-ld", "microdata")


@pytest.mark.parametrize("name, url", UNREADABLE)
def test_a_badly_graded_merchant_falls_back_rather_than_guessing(name: str, url: str) -> None:
    """The failure mode that matters: the page loads, a price is visible to a
    human, and the reader must still return None rather than scrape a number
    nobody promised."""
    try:
        found = prices.fetch(url)
    except prices.PriceError as exc:
        pytest.skip(f"{name} unreachable: {exc}")

    assert found is None, f"{name} graded F but now publishes a structured price — regrade it"
