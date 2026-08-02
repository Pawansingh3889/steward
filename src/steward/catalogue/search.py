"""Finding what someone asked for, and pricing it honestly.

Two properties this module exists to guarantee, both about who decides what.

**The agent does not choose.** `find` returns every matching offer, ordered but
not filtered down to a recommendation, and the tool that spends money takes an
offer id rather than a description. The plan puts autonomy in enforcement and
payment, not in selection: steward finds the options and handles the money, and
a person picks. An agent that silently bought the cheapest one would be making
a values judgement — cheapest is not best when you are out of soap today — on
behalf of somebody who was never asked.

**The model does not set the price.** `quote` looks the price up from the
catalogue at purchase time and compares it against what the person was shown.
If the model misremembers £4.50 as £4.05, or a price moved between the offer
and the tap, the purchase is refused and the options are shown again. This is
canibuy's `pricestage.py` idea, which re-checks price before minting for exactly
this reason: the number that reaches the policy engine has to be the number the
merchant will actually charge.

Ordering is by delivery, then price, then supplier name. Delivery first because
"I am out of soap" is a problem with a deadline; alphabetical last so the order
is stable across runs and no supplier wins a tie by being listed first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..extract import eta
from ..extract.eta import Delivery, Point
from . import fixtures
from .fixtures import Product, Supplier

# Every offer says so, on every surface. Same discipline as payoptimize
# labelling its simulated rails: a demo that lets a modelled thing pass for a
# real one is the one dishonesty that discredits everything around it.
LABEL = "FIXTURE"

DEFAULT_LIMIT = 6


class PriceMoved(RuntimeError):
    """The catalogue price is not the price the person was shown."""


@dataclass(frozen=True)
class Offer:
    """One supplier's answer to "can I have some soap?"."""

    offer_id: str
    sku: str
    name: str
    supplier_id: str
    supplier_name: str
    supplier_url: str
    supplier_country: str
    price_cents: int
    currency: str
    # None when we do not know where the person is. Guessing would be worse
    # than saying so: a delivery estimate is the half of this comparison that
    # cannot be checked at a glance.
    delivery: Delivery | None
    label: str = LABEL

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "item": self.name,
            "supplier": self.supplier_name,
            "price_cents": self.price_cents,
            "currency": self.currency,
            "delivery": self.delivery.describe() if self.delivery else "delivery time unknown",
            "delivery_days": self.delivery.days if self.delivery else None,
            # Repeated on every offer rather than stated once in a preamble the
            # model may not pass on.
            "label": self.label,
        }


def offer_id(product: Product) -> str:
    """Stable and derivable, so no offer table is needed and a stale id from an
    old conversation still resolves to the same shelf."""
    return f"{product.supplier_id}:{product.sku}"


def _matches(product: Product, query: str) -> bool:
    wanted = query.lower().strip()
    if not wanted:
        return False
    return any(term in wanted or wanted in term for term in product.terms)


def _build(product: Product, supplier: Supplier, destination: Point | None) -> Offer:
    delivery = (
        eta.estimate(origin=supplier.origin, destination=destination, carrier=supplier.carrier)
        if destination is not None
        else None
    )
    return Offer(
        offer_id=offer_id(product),
        sku=product.sku,
        name=product.name,
        supplier_id=supplier.supplier_id,
        supplier_name=supplier.name,
        supplier_url=supplier.url,
        supplier_country=supplier.country,
        price_cents=product.price_cents,
        currency=product.currency,
        delivery=delivery,
    )


def find(
    query: str,
    *,
    destination: Point | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Offer]:
    """Every offer matching a query, best-delivery first. Never a shortlist of
    one — see the module docstring."""
    offers: list[Offer] = []
    for product in fixtures.PRODUCTS:
        if not product.in_stock or not _matches(product, query):
            continue
        supplier = fixtures.supplier(product.supplier_id)
        if supplier is None:
            # A product pointing at a supplier that does not exist is a broken
            # catalogue, not something to quietly serve.
            raise RuntimeError(
                f"product {product.sku} names unknown supplier {product.supplier_id}"
            )
        offers.append(_build(product, supplier, destination))

    offers.sort(
        key=lambda offer: (
            # Unknown delivery sorts last: it is the least useful answer, not
            # the fastest one.
            offer.delivery.days if offer.delivery else 10**6,
            offer.price_cents,
            offer.supplier_name,
        )
    )
    return offers[:limit]


def get(offer_id_value: str, *, destination: Point | None = None) -> Offer | None:
    supplier_id, _, sku = offer_id_value.partition(":")
    product = fixtures.product(sku)
    supplier = fixtures.supplier(supplier_id)
    if product is None or supplier is None or product.supplier_id != supplier_id:
        return None
    if not product.in_stock:
        return None
    return _build(product, supplier, destination)


def quote(
    offer_id_value: str, shown_price_cents: int, *, destination: Point | None = None
) -> Offer:
    """Re-price an offer at the moment of purchase.

    Raises `PriceMoved` when the catalogue disagrees with what the person was
    shown. The caller's job is then to show the options again, not to pay the
    new price on their behalf — an agreement to spend £4.50 is not an agreement
    to spend whatever it costs.
    """
    offer = get(offer_id_value, destination=destination)
    if offer is None:
        raise PriceMoved(f"{offer_id_value} is no longer available")
    if offer.price_cents != shown_price_cents:
        raise PriceMoved(
            f"the price changed: you were shown {shown_price_cents} and it is now"
            f" {offer.price_cents} {offer.currency}"
        )
    return offer
