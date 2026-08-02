"""Phase 4: "I'm out of soap" → options → choice → settled.

Two properties carry the weight here, and both are about who decides:

  the agent does not choose  — find_options returns everything, and the tool
                               that spends takes an offer id, not a description.
  the model does not price   — buy_offer re-reads the price from the catalogue
                               and refuses if it moved.

The rest is arithmetic and ordering.
"""

from __future__ import annotations

import pytest

from steward import cli, store
from steward.agent import loop
from steward.agent.privacy import Redactor
from steward.agent.tools import ToolBox
from steward.catalogue import fixtures, search
from steward.extract.eta import Point
from steward.models import Role
from steward.spend import warden

from .agent_stub import OpenAIStub, completion
from .warden_stub import WardenStub, allowed, parked

LONDON = Point(51.5074, -0.1278)


@pytest.fixture
def household(db: str) -> tuple[int, int]:
    sponsor = store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, db_path=db)
    spender = store.insert_person(
        name="Ana Whitfield", role=Role.SPENDER, sponsor_id=sponsor, db_path=db
    )
    store.set_home_location(spender, LONDON.latitude, LONDON.longitude, db_path=db)
    return sponsor, spender


@pytest.fixture
def box(db: str, household: tuple[int, int]) -> ToolBox:
    _, spender = household
    return ToolBox(person_id=spender, redactor=Redactor.build(db_path=db), db_path=db)


# --- the catalogue itself ----------------------------------------------------


def test_every_product_names_a_supplier_that_exists() -> None:
    """A dangling supplier reference is a broken catalogue, and `find` raises on
    one rather than quietly serving the rest."""
    known = {supplier.supplier_id for supplier in fixtures.SUPPLIERS}

    assert all(product.supplier_id in known for product in fixtures.PRODUCTS)


def test_suppliers_differ_on_the_axes_a_person_trades_off() -> None:
    """A comparison where one supplier dominates on everything teaches nothing
    about whether people can use this."""
    offers = search.find("soap", destination=LONDON)

    assert len({offer.supplier_id for offer in offers}) > 1
    assert len({offer.price_cents for offer in offers}) > 1
    assert len({offer.delivery.days for offer in offers}) > 1
    # And the cheapest is not the fastest, or there would be no decision to make.
    cheapest = min(offers, key=lambda o: o.price_cents)
    fastest = min(offers, key=lambda o: o.delivery.days)
    assert cheapest.supplier_id != fastest.supplier_id


@pytest.mark.parametrize(
    "query", ["soap", "shampoo", "laundry", "kitchen roll", "toothpaste", "coffee"]
)
def test_no_offer_is_dominated_by_another(query: str) -> None:
    """Every option is on the Pareto frontier **from the reference location**:
    cheaper, or faster, or both.

    An option that is dearer AND slower than another is one nobody should ever
    pick, so including it tests whether a person notices padding rather than
    whether they can make a trade-off. This caught the first draft of the
    catalogue, where the corner shop happened to be cheapest and fastest for
    soap and there was no decision to make at all.

    Scoped to London on purpose, because the property cannot hold everywhere and
    should not: from Glasgow the local bulk supplier really is both cheapest and
    fastest, and a fixture that contorted itself to avoid that would be modelling
    something untrue about geography. What is being asserted is that the worked
    example poses a genuine choice, not that no arrangement of the world ever
    resolves one.
    """
    offers = search.find(query, destination=LONDON)
    assert offers

    for offer in offers:
        dominated_by = [
            other
            for other in offers
            if other.offer_id != offer.offer_id
            and other.price_cents <= offer.price_cents
            and other.delivery.days <= offer.delivery.days
        ]
        assert not dominated_by, (
            f"{offer.supplier_name} {offer.name}"
            f" ({offer.price_cents}p, {offer.delivery.days}d) is dominated by"
            f" {[(o.supplier_name, o.price_cents, o.delivery.days) for o in dominated_by]}"
        )


def test_every_offer_is_labelled_a_fixture() -> None:
    """Repeated on every offer rather than in a preamble the model may not pass
    on. Same discipline as payoptimize labelling its simulated rails."""
    for offer in search.find("soap", destination=LONDON):
        assert offer.as_dict()["label"] == "FIXTURE"


def test_out_of_stock_is_never_offered() -> None:
    assert all(offer.sku != "cs-coff-1" for offer in search.find("coffee", destination=LONDON))


def test_matching_is_over_the_words_people_use() -> None:
    """Nobody asks for "Pure Botanics Hand Wash"."""
    assert search.find("hand wash", destination=LONDON)
    assert search.find("washing", destination=LONDON)
    assert search.find("paper towel", destination=LONDON)
    assert search.find("a unicorn", destination=LONDON) == []
    assert search.find("", destination=LONDON) == []


# --- ordering and delivery ---------------------------------------------------


def test_ordered_by_delivery_then_price() -> None:
    """ "I am out of soap" is a problem with a deadline."""
    offers = search.find("soap", destination=LONDON)

    keys = [(o.delivery.days, o.price_cents) for o in offers]
    assert keys == sorted(keys)


def test_the_nearest_express_supplier_arrives_first() -> None:
    offers = search.find("soap", destination=LONDON)

    assert offers[0].supplier_id == "cornershop"  # London, express


def test_where_you_are_changes_what_arrives_first() -> None:
    """The point of modelling delivery at all."""
    glasgow = Point(55.8642, -4.2518)

    from_london = search.find("laundry", destination=LONDON)
    from_glasgow = search.find("laundry", destination=glasgow)

    london_days = {o.supplier_id: o.delivery.days for o in from_london}
    glasgow_days = {o.supplier_id: o.delivery.days for o in from_glasgow}
    assert glasgow_days["bulkline"] < london_days["bulkline"]  # Bulkline ships from Glasgow


def test_with_no_location_delivery_is_unknown_rather_than_guessed(db: str) -> None:
    """A delivery estimate is the half of this comparison a person cannot check
    at a glance, so inventing one is worse than admitting we cannot."""
    offers = search.find("soap", destination=None)

    assert all(offer.delivery is None for offer in offers)
    assert all(o.as_dict()["delivery"] == "delivery time unknown" for o in offers)
    assert all(o.as_dict()["delivery_days"] is None for o in offers)


def test_unknown_delivery_sorts_last_not_first(db: str) -> None:
    """It is the least useful answer, not the fastest one."""
    offers = search.find("soap", destination=None)

    # With no delivery known, price decides — never an accidental "0 days" win.
    assert [o.price_cents for o in offers] == sorted(o.price_cents for o in offers)


def test_no_offer_carries_a_coordinate_or_a_distance() -> None:
    """One distance from a known warehouse is a circle; three are an address —
    and comparing three suppliers produces exactly three."""
    rendered = str([offer.as_dict() for offer in search.find("soap", destination=LONDON)])

    for fragment in ("51.5", "-0.12", "55.86", "52.48", "km", "latitude"):
        assert fragment not in rendered


# --- pricing integrity -------------------------------------------------------


def test_an_offer_id_is_stable() -> None:
    """So a choice made a few turns ago still resolves to the same shelf."""
    first = search.find("soap", destination=LONDON)[0]

    assert search.get(first.offer_id, destination=LONDON).price_cents == first.price_cents


def test_quoting_at_the_shown_price_succeeds() -> None:
    offer = search.find("soap", destination=LONDON)[0]

    assert search.quote(offer.offer_id, offer.price_cents, destination=LONDON) == offer


def test_quoting_at_a_wrong_price_is_refused() -> None:
    """An agreement to spend £4.50 is not an agreement to spend whatever it costs."""
    offer = search.find("soap", destination=LONDON)[0]

    with pytest.raises(search.PriceMoved, match="price changed"):
        search.quote(offer.offer_id, offer.price_cents - 45, destination=LONDON)


def test_quoting_something_unavailable_is_refused() -> None:
    with pytest.raises(search.PriceMoved, match="no longer available"):
        search.quote("cornershop:cs-coff-1", 620)  # out of stock
    with pytest.raises(search.PriceMoved):
        search.quote("nonsense:nope", 100)
    # A real sku under the wrong supplier must not resolve.
    with pytest.raises(search.PriceMoved):
        search.quote("bulkline:cs-soap-1", 289)


# --- the tools ---------------------------------------------------------------


def test_find_options_returns_everything_and_says_to_show_it_all(box: ToolBox) -> None:
    result = box.dispatch("find_options", {"query": "soap"})

    assert result["count"] == 3
    assert "show ALL of these" in result["note"]
    assert all(option["label"] == "FIXTURE" for option in result["options"])


def test_find_options_says_so_when_nothing_matches(box: ToolBox) -> None:
    result = box.dispatch("find_options", {"query": "a unicorn"})

    assert result["count"] == 0
    assert "nothing in the catalogue" in result["note"]


def test_the_tool_description_forbids_choosing(box: ToolBox) -> None:
    """Autonomy lives in enforcement and payment, not in selection."""
    spec = next(s for s in box.specs() if s["function"]["name"] == "find_options")
    doc = spec["function"]["description"]

    assert "do not pick for them" in doc
    assert "Cheapest is not best" in doc


def test_buying_uses_the_catalogue_price_not_the_models_number(box: ToolBox, db: str) -> None:
    """The line that stops a misremembered number becoming the number the policy
    engine evaluates and the person pays."""
    offer = search.find("soap", destination=LONDON)[0]
    box.warden = WardenStub([allowed()])

    box.dispatch("buy_offer", {"offer_id": offer.offer_id, "price_cents": offer.price_cents})

    sent = box.warden.last("request_purchase")
    assert sent["total_amount"] == warden.amount_to_decimal(offer.price_cents)
    assert sent["merchant_name"] == offer.supplier_name


def test_buying_at_a_price_that_moved_is_refused_and_asks_for_a_recheck(
    box: ToolBox,
) -> None:
    offer = search.find("soap", destination=LONDON)[0]
    box.warden = WardenStub([])  # must never be reached

    result = box.dispatch("buy_offer", {"offer_id": offer.offer_id, "price_cents": 1})

    assert result["verdict"] == "price_moved"
    assert result["recheck"] is True
    assert box.warden.calls == []


def test_buying_needs_an_offer_and_a_price(box: ToolBox) -> None:
    assert "required" in box.dispatch("buy_offer", {})["error"]
    assert "required" in box.dispatch("buy_offer", {"offer_id": "everyday:ev-soap-2"})["error"]


def test_a_bought_offer_can_still_be_parked_by_policy(box: ToolBox, db: str) -> None:
    offer = search.find("coffee", destination=LONDON)[0]
    box.warden = WardenStub([parked()])

    result = box.dispatch(
        "buy_offer", {"offer_id": offer.offer_id, "price_cents": offer.price_cents}
    )

    assert result["verdict"] == warden.NEEDS_APPROVAL
    assert len(store.list_escalations(db_path=db)) == 1


# --- the whole errand --------------------------------------------------------


def test_out_of_soap_to_settled(db: str, household: tuple[int, int]) -> None:
    """Phase 4's exit criterion, end to end."""
    _, spender = household
    chosen = search.find("soap", destination=LONDON)[1]  # not the first: they picked
    model = OpenAIStub(
        [
            completion(tool_calls=[("find_options", {"query": "soap"})]),
            completion(content="Three options — which do you want?"),
        ]
    )
    first = loop.run("I'm out of soap", person_id=spender, db_path=db, http=model.client())
    assert first["evidence"][0]["result"]["count"] == 3

    model = OpenAIStub(
        [
            completion(
                tool_calls=[
                    ("buy_offer", {"offer_id": chosen.offer_id, "price_cents": chosen.price_cents})
                ]
            ),
            completion(content="Done — here's the link."),
        ]
    )
    second = loop.run(
        "the Everyday one please",
        person_id=spender,
        db_path=db,
        http=model.client(),
        warden=WardenStub([allowed(url="https://pay.example/s/4")]),
    )

    assert second["evidence"][0]["result"]["payment_url"] == "https://pay.example/s/4"
    assert second["evidence"][0]["result"]["verdict"] == warden.ALLOWED


def test_the_system_prompt_tells_the_model_not_to_decide() -> None:
    assert "Do not pick an option on their behalf" in loop.SYSTEM_PROMPT
    assert "deciding is theirs" in loop.SYSTEM_PROMPT


# --- the person's surface ----------------------------------------------------


def test_shop_shows_price_and_delivery(
    db: str, household: tuple[int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    _, spender = household

    assert cli.main(["--db", db, "shop", "soap", "--person", str(spender)]) == 0

    out = capsys.readouterr().out
    assert "FIXTURE" in out
    assert "Corner Shop Express" in out
    assert "£" in out
    assert "arrives" in out


def test_shop_without_a_location_says_how_to_fix_it(
    db: str, household: tuple[int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    _, spender = household
    store.set_home_location(spender, None, None, db_path=db)

    cli.main(["--db", db, "shop", "soap", "--person", str(spender)])

    out = capsys.readouterr().out
    assert "does not know where you are" in out
    assert "steward people locate" in out


def test_shop_reports_nothing_found(
    db: str, household: tuple[int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    _, spender = household

    assert cli.main(["--db", db, "shop", "a unicorn", "--person", str(spender)]) == 1
    assert "nothing in the catalogue" in capsys.readouterr().out


def test_a_location_can_be_set_and_forgotten(db: str, household: tuple[int, int]) -> None:
    """Forgetting where you are has to be as easy as saying it."""
    _, spender = household

    cli.main(["--db", db, "people", "locate", "--person", str(spender), "--forget"])
    assert store.get_person(spender, db_path=db)["home_lat"] is None

    cli.main(
        ["--db", db, "people", "locate", "--person", str(spender), "--lat", "51.5", "--lon", "-0.1"]
    )
    assert store.get_person(spender, db_path=db)["home_lat"] == pytest.approx(51.5)


def test_locating_needs_both_coordinates(db: str, household: tuple[int, int]) -> None:
    _, spender = household

    with pytest.raises(SystemExit, match="--lat and --lon"):
        cli.main(["--db", db, "people", "locate", "--person", str(spender), "--lat", "51.5"])
