"""Reading a price off a real product page.

canibuy measured how well this goes on the open web and the answer was: badly.
Best on record was a C, most were F, and the failures were not exotic — pages
that render the price in JavaScript, or mark it up three inconsistent ways, or
omit it from the structured data entirely. So this reads **structured data
only**: JSON-LD first, then microdata. It never guesses a price out of visible
text, because a number scraped from a page is a number nobody promised, and
this one ends up in a policy decision and then on somebody's card.

When it cannot find a price it says so. `None` is a perfectly good answer here
and the fixture catalogue remains the fallback — a wrong price is far worse
than a missing one.

The seam is `catalogue.search`, which does not care where an offer came from.
A merchant that grades well swaps in; everything else stays modelled and stays
labelled.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

DEFAULT_TIMEOUT = 20.0
MAX_BYTES = 2_000_000  # a product page that large is not a product page

# A browser-ish identity. Not to evade anything — many merchants simply return
# an error page to a client that sends no user agent at all.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; steward/0.1; +https://github.com/Pawansingh3889)",
    "Accept": "text/html,application/xhtml+xml",
}

_JSON_LD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_MICRODATA_PRICE = re.compile(
    r'itemprop=["\']price["\'][^>]*content=["\']([\d.,]+)["\']', re.IGNORECASE
)
_MICRODATA_CURRENCY = re.compile(
    r'itemprop=["\']priceCurrency["\'][^>]*content=["\']([A-Z]{3})["\']', re.IGNORECASE
)


class PriceError(RuntimeError):
    """The page could not be fetched."""


@dataclass(frozen=True)
class LivePrice:
    price_cents: int
    currency: str
    # Which markup it came from, so a surface can say how well a merchant is
    # actually described rather than implying every price is equally solid.
    source: str
    availability: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_cents": self.price_cents,
            "currency": self.currency,
            "source": self.source,
            "availability": self.availability,
        }


def to_cents(value: Any) -> int | None:
    """A price as integer minor units, or None if it is not a price.

    Decimal, not float: this number reaches a policy engine and a card, and
    `float("19.99") * 100` is 1998.9999999999998.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0:
        return None
    return int((amount * 100).quantize(Decimal(1)))


def _offers_of(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        offers = node.get("offers")
        if isinstance(offers, dict):
            return [offers]
        if isinstance(offers, list):
            return [item for item in offers if isinstance(item, dict)]
    return []


def _walk(node: Any) -> list[dict[str, Any]]:
    """Every dict in a JSON-LD blob. Schema.org nests through @graph, arrays and
    embedded products, and the price is as often three levels down as at the top."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item))
    return found


def from_json_ld(html: str) -> LivePrice | None:
    for block in _JSON_LD.findall(html):
        try:
            parsed = json.loads(block.strip())
        except ValueError:
            # One malformed block must not hide a good one further down the page.
            continue
        for node in _walk(parsed):
            for offer in _offers_of(node):
                cents = to_cents(offer.get("price"))
                if cents is None:
                    continue
                return LivePrice(
                    price_cents=cents,
                    currency=str(offer.get("priceCurrency", "")).upper() or "GBP",
                    source="json-ld",
                    availability=str(offer.get("availability", "")).rsplit("/", 1)[-1],
                )
    return None


def from_microdata(html: str) -> LivePrice | None:
    price = _MICRODATA_PRICE.search(html)
    if not price:
        return None
    cents = to_cents(price.group(1))
    if cents is None:
        return None
    currency = _MICRODATA_CURRENCY.search(html)
    return LivePrice(
        price_cents=cents,
        currency=(currency.group(1).upper() if currency else "GBP"),
        source="microdata",
    )


def read(html: str) -> LivePrice | None:
    """Structured data only. Returns None rather than guessing."""
    return from_json_ld(html) or from_microdata(html)


def fetch(url: str, *, http: httpx.Client | None = None) -> LivePrice | None:
    """Fetch a product page and read its price, or None if it does not say.

    Raises only when the page could not be fetched at all. A page that loads and
    has no structured price is not an error — it is the common case on the open
    web, and the honest answer is that this merchant cannot be priced.
    """
    client = http or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
    try:
        response = client.get(url, headers=HEADERS)
    except httpx.HTTPError as exc:
        raise PriceError(f"could not fetch {url}: {exc}") from exc
    finally:
        if http is None:
            client.close()
    if response.status_code != 200:
        raise PriceError(f"{url} returned {response.status_code}")
    return read(response.text[:MAX_BYTES])
