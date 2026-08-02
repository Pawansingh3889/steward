"""How long a delivery takes, worked out here so coordinates stay here.

The plan allows the device's location *because* it is processed locally: the
model is told "arrives in about two days" and never sees where the person
lives. This module is that processing step, and it is the only code in the
system that touches a coordinate.

**The subtle part, and the reason this returns days rather than distance.**
Emitting "142 km from the Manchester depot" feels safe — it is a single number,
and it names no address. But a distance from a known warehouse is a circle. Two
distances are a pair of points. Three are a home address, and a comparison
across suppliers naturally produces three. So `estimate` returns a band in days
and a coarse zone name, and `distance_km` stays module-private with a test that
asserts no coordinate or distance appears in what leaves. Any future field added
to `Delivery` has to survive that same question.

The model is a plain one: great-circle distance, banded into zones, plus the
carrier's own dispatch and per-zone service days. It is not trying to beat a
carrier's own estimate — it is trying to rank three suppliers honestly without
asking any of them where the customer lives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0

# Zone bands, in kilometres. Named rather than numeric in everything that
# leaves this module: "same region" is useful to a person choosing a supplier,
# and unlike "37 km" it cannot be intersected with another one.
ZONES: tuple[tuple[float, str, int], ...] = (
    (40.0, "local", 0),
    (200.0, "same region", 1),
    (800.0, "national", 2),
    (3000.0, "far", 4),
    (math.inf, "international", 7),
)


@dataclass(frozen=True)
class Point:
    """A coordinate. Instances of this type never cross the boundary; the type
    exists so that "did a coordinate escape?" is a question about one class."""

    latitude: float
    longitude: float


@dataclass(frozen=True)
class Carrier:
    name: str
    # Days before the parcel moves at all — the part suppliers actually differ
    # on, and the part a person feels.
    dispatch_days: int = 1
    # Multiplies the zone's transit days. An express carrier is not faster over
    # the ground, it just skips sorting steps.
    speed: float = 1.0


STANDARD = Carrier("standard")
EXPRESS = Carrier("express", dispatch_days=0, speed=0.5)


@dataclass(frozen=True)
class Delivery:
    """What the agent is allowed to know about a delivery.

    Every field here is safe to send. That is the invariant a test enforces —
    if a field is added, it has to be defensible on its own, because the
    boundary is the shape of this dataclass and nothing else.
    """

    days: int
    zone: str
    carrier: str

    def describe(self) -> str:
        if self.days <= 1:
            return "arrives tomorrow"
        return f"arrives in about {self.days} days"


def _distance_km(origin: Point, destination: Point) -> float:
    """Great-circle distance. Module-private on purpose — see the docstring."""
    lat1, lon1 = math.radians(origin.latitude), math.radians(origin.longitude)
    lat2, lon2 = math.radians(destination.latitude), math.radians(destination.longitude)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _zone(distance_km: float) -> tuple[str, int]:
    for limit, name, transit_days in ZONES:
        if distance_km <= limit:
            return name, transit_days
    return ZONES[-1][1], ZONES[-1][2]


def estimate(*, origin: Point, destination: Point, carrier: Carrier = STANDARD) -> Delivery:
    """Two coordinates in; days and a zone name out. Nothing else.

    `destination` is the person's device location. It goes no further than this
    function's stack frame.
    """
    zone_name, transit_days = _zone(_distance_km(origin, destination))
    days = carrier.dispatch_days + math.ceil(transit_days * carrier.speed)
    return Delivery(days=max(1, days), zone=zone_name, carrier=carrier.name)


def rank(deliveries: list[tuple[str, Delivery]]) -> list[tuple[str, Delivery]]:
    """Fastest first, ties alphabetical so the order is stable across runs and a
    supplier cannot win a tie by being listed first."""
    return sorted(deliveries, key=lambda pair: (pair[1].days, pair[0]))
