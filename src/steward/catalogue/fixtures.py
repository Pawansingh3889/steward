"""A controlled storefront, labelled as one.

Every offer this system can make comes from here, and every surface says
FIXTURE. That is not modesty, it is the honest form of a real finding: canibuy
probed the open web for agent-readiness and the best merchant on record graded
**C**, most graded **F**, and none of them sell household essentials.

    bluebottlecoffee.com  F     monoprice.com     F
    adafruit.com          C     bhphotovideo.com  F
    sparkfun.com          C     sweetwater.com    F

So the choice is between a demo that quietly pretends the web is ready and one
that models the catalogue and says so. Phase 7 swaps live price fetching in for
any merchant that grades well; the seam is `search.find`, which does not care
where an offer came from.

The suppliers below differ on the axes a person actually trades off — price,
dispatch speed, and how far away they are — because a comparison where one
supplier dominates on everything teaches nothing about whether people can use
this. Locations are real city coordinates so the distance model has something
true to work on; they are the *supplier's* location, never the customer's.

canibuy's `fixture_store.py` is not extended here. It serves one product over
HTTP so a probe can test whether a page is machine-readable — a different job
from holding a catalogue, and it stays the right tool for phase 7's fetching
work.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..extract.eta import EXPRESS, STANDARD, Carrier, Point


@dataclass(frozen=True)
class Supplier:
    supplier_id: str
    name: str
    url: str
    country: str
    # Where it ships from. Feeds the distance model in extract/eta.py; the
    # result that leaves is a number of days, never this coordinate or the
    # customer's.
    origin: Point
    carrier: Carrier


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    # What it is, in the words a person would use. Matching is over these, not
    # over the marketing name — nobody asks for "Pure Botanics Hand Wash".
    terms: tuple[str, ...]
    price_cents: int
    currency: str
    supplier_id: str
    in_stock: bool = True


SUPPLIERS: tuple[Supplier, ...] = (
    # Cheapest, slowest, furthest away.
    Supplier(
        "bulkline",
        "Bulkline Direct",
        "https://bulkline.fixture.example",
        "GB",
        Point(55.8642, -4.2518),  # Glasgow
        STANDARD,
    ),
    # Middle on both, and the one a sensible person usually picks.
    Supplier(
        "everyday",
        "Everyday Goods",
        "https://everyday.fixture.example",
        "GB",
        Point(52.4862, -1.8904),  # Birmingham
        STANDARD,
    ),
    # Dearest and fastest: local, and on an express carrier.
    Supplier(
        "cornershop",
        "Corner Shop Express",
        "https://cornershop.fixture.example",
        "GB",
        Point(51.5074, -0.1278),  # London
        EXPRESS,
    ),
)

PRODUCTS: tuple[Product, ...] = (
    # soap — the worked example throughout the project
    Product(
        "bl-soap-2",
        "Hand Soap, 2 × 500ml",
        ("soap", "hand soap", "hand wash"),
        320,
        "GBP",
        "bulkline",
    ),
    Product(
        "ev-soap-2",
        "Hand Soap Refill, 2 × 500ml",
        ("soap", "hand soap", "hand wash"),
        380,
        "GBP",
        "everyday",
    ),
    Product(
        "cs-soap-1",
        "Hand Soap, 500ml",
        ("soap", "hand soap", "hand wash"),
        420,
        "GBP",
        "cornershop",
    ),
    # shampoo
    Product("bl-sham-1", "Shampoo, 1L", ("shampoo", "hair"), 340, "GBP", "bulkline"),
    Product("ev-sham-1", "Shampoo, 400ml", ("shampoo", "hair"), 399, "GBP", "everyday"),
    # laundry
    Product(
        "bl-laun-1",
        "Laundry Liquid, 3L",
        ("laundry", "washing", "detergent"),
        690,
        "GBP",
        "bulkline",
    ),
    Product(
        "ev-laun-1",
        "Laundry Liquid, 1.5L",
        ("laundry", "washing", "detergent"),
        725,
        "GBP",
        "everyday",
    ),
    # kitchen
    Product(
        "ev-roll-1",
        "Kitchen Roll, 4 pack",
        ("kitchen roll", "paper towel", "kitchen"),
        380,
        "GBP",
        "everyday",
    ),
    Product(
        "cs-roll-1",
        "Kitchen Roll, 2 pack",
        ("kitchen roll", "paper towel", "kitchen"),
        420,
        "GBP",
        "cornershop",
    ),
    # toothpaste
    Product(
        "ev-tooth-1",
        "Toothpaste, 100ml",
        ("toothpaste", "toothbrush", "dental"),
        250,
        "GBP",
        "everyday",
    ),
    Product("cs-tooth-1", "Toothpaste, 75ml", ("toothpaste", "dental"), 310, "GBP", "cornershop"),
    # coffee, so the catalogue is not only cleaning products
    Product("bl-coff-1", "Ground Coffee, 1kg", ("coffee", "ground coffee"), 790, "GBP", "bulkline"),
    Product(
        "ev-coff-1", "Ground Coffee, 500g", ("coffee", "ground coffee"), 850, "GBP", "everyday"
    ),
    # something out of stock, because an empty shelf is a real outcome
    Product(
        "cs-coff-1",
        "Ground Coffee, 227g",
        ("coffee", "ground coffee"),
        620,
        "GBP",
        "cornershop",
        in_stock=False,
    ),
)

_BY_ID = {supplier.supplier_id: supplier for supplier in SUPPLIERS}
_BY_SKU = {product.sku: product for product in PRODUCTS}


def supplier(supplier_id: str) -> Supplier | None:
    return _BY_ID.get(supplier_id)


def product(sku: str) -> Product | None:
    return _BY_SKU.get(sku)
