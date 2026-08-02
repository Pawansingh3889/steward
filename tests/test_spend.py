"""Phase 3: a purchase is blocked, escalated, approved, paid.

The property under test throughout is that **steward never decides**. Every
branch here is driven by a verdict pay-warden returned, and the tests that
matter most are the ones proving there is no way round it: no approval tool for
the model, no minting on an unrecognised verdict, no spending when the policy
engine is unreachable.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from steward import cli, store
from steward.agent import loop
from steward.agent.privacy import Redactor
from steward.agent.tools import ToolBox
from steward.models import Role
from steward.spend import purchase, warden

from .agent_stub import OpenAIStub, completion
from .warden_stub import WardenStub, allowed, denied, parked, released

SOAP = {
    "description": "hand soap, 2 bottles",
    "amount_cents": 450,
    "currency": "GBP",
    "merchant_name": "Fixture Store",
    "merchant_url": "https://fixture.example",
    "merchant_country": "GB",
}


@pytest.fixture
def household(db: str) -> tuple[int, int]:
    sponsor = store.insert_person(name="Rae Whitfield", role=Role.SPONSOR, db_path=db)
    spender = store.insert_person(
        name="Ana Whitfield", role=Role.SPENDER, sponsor_id=sponsor, db_path=db
    )
    return sponsor, spender


# --- the wire format ---------------------------------------------------------


def test_money_crosses_as_a_decimal_string() -> None:
    """Integer minor units on this side, decimal on pay-warden's. This is the
    only place the two representations meet, so rounding can only be wrong here."""
    assert warden.amount_to_decimal(1250) == "12.5"
    assert warden.amount_to_decimal(450) == "4.5"
    assert warden.amount_to_decimal(1) == "0.01"
    assert warden.amount_to_decimal(100000) == "1000"


def test_the_agent_name_is_a_pseudonym_and_per_person() -> None:
    """Budgets are scoped by this string, so one shared name would let one
    spender exhaust another's allowance — and pay-warden has no need of anyone's
    real name to enforce a budget."""
    assert warden.agent_name(2) == "steward:person_2"
    assert warden.agent_name(2) != warden.agent_name(3)


def test_the_request_carries_no_name(db: str, household: tuple[int, int]) -> None:
    _, spender = household
    stub = WardenStub([allowed()])

    purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)

    sent = str(stub.last("request_purchase"))
    assert "Ana Whitfield" not in sent
    assert "steward:person_" in sent


def test_the_products_list_matches_the_total(db: str, household: tuple[int, int]) -> None:
    _, spender = household
    stub = WardenStub([allowed()])

    purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)

    sent = stub.last("request_purchase")
    assert sent["total_amount"] == "4.5"
    assert sent["products"] == [
        {"description": "hand soap, 2 bottles", "unit_price": "4.5", "quantity": 1}
    ]


# --- the three verdicts ------------------------------------------------------


def test_an_allowed_purchase_returns_a_payment_link(db: str, household: tuple[int, int]) -> None:
    _, spender = household
    stub = WardenStub([allowed(url="https://pay.example/s/1")])

    outcome = purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)

    assert outcome["verdict"] == warden.ALLOWED
    assert outcome["payment_url"] == "https://pay.example/s/1"
    assert outcome["escalation_id"] == 0  # the sponsor was never involved
    assert store.list_escalations(db_path=db) == []


def test_a_denied_purchase_relays_the_rule_that_fired(db: str, household: tuple[int, int]) -> None:
    """An agent that editorialises a denial into "I couldn't find that" teaches
    people the system is broken rather than that a limit exists."""
    _, spender = household
    stub = WardenStub([denied(reason="merchant is not on the allowlist")])

    outcome = purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)

    assert outcome["verdict"] == warden.DENIED
    assert outcome["reason"] == "merchant is not on the allowlist"
    assert outcome["rule_id"] == "merchant_allowlist"
    assert outcome["payment_url"] == ""
    assert store.list_escalations(db_path=db) == []  # a denial is not an escalation


def test_a_parked_purchase_creates_an_escalation_for_the_sponsor(
    db: str, household: tuple[int, int]
) -> None:
    sponsor, spender = household
    stub = WardenStub([parked(attempt_id="att_77", reason="over the single-purchase limit")])

    outcome = purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)

    assert outcome["verdict"] == warden.NEEDS_APPROVAL
    row = store.get_escalation(outcome["escalation_id"], db_path=db)
    assert row is not None
    assert row["sponsor_id"] == sponsor
    assert row["spender_id"] == spender
    assert row["attempt_id"] == "att_77"
    assert row["status"] == purchase.PENDING
    assert row["reason"] == "over the single-purchase limit"


# --- blocked → escalated → approved → paid ------------------------------------


def test_the_whole_flow(db: str, household: tuple[int, int]) -> None:
    """Phase 3's exit criterion, end to end."""
    sponsor, spender = household
    stub = WardenStub([parked(attempt_id="att_77"), released(url="https://pay.example/s/9")])

    blocked = purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)
    assert blocked["verdict"] == warden.NEEDS_APPROVAL

    waiting = purchase.pending_for_sponsor(sponsor, db_path=db)
    assert [int(row["id"]) for row in waiting] == [blocked["escalation_id"]]

    paid = purchase.approve(blocked["escalation_id"], sponsor_id=sponsor, db_path=db, client=stub)

    assert paid["payment_url"] == "https://pay.example/s/9"
    assert stub.tools_called() == ["request_purchase", "approve_purchase"]
    # Released against pay-warden's own handle, not anything rebuilt here.
    assert stub.last("approve_purchase") == {"attempt_id": "att_77"}
    assert purchase.pending_for_sponsor(sponsor, db_path=db) == []
    assert store.get_escalation(blocked["escalation_id"], db_path=db)["status"] == "approved"


def test_declining_never_touches_pay_warden(db: str, household: tuple[int, int]) -> None:
    """An attempt it parked and nobody released simply stays parked, which is
    already the correct state."""
    sponsor, spender = household
    stub = WardenStub([parked()])
    blocked = purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)

    purchase.decline(blocked["escalation_id"], sponsor_id=sponsor, db_path=db)

    assert stub.tools_called() == ["request_purchase"]
    assert store.get_escalation(blocked["escalation_id"], db_path=db)["status"] == "declined"


# --- the ways this could go wrong --------------------------------------------


def test_approving_twice_mints_only_one_session(db: str, household: tuple[int, int]) -> None:
    """The status transition is claimed in SQL before pay-warden is called, so
    the second caller never reaches the network."""
    sponsor, spender = household
    stub = WardenStub([parked(), released()])
    blocked = purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)
    purchase.approve(blocked["escalation_id"], sponsor_id=sponsor, db_path=db, client=stub)

    with pytest.raises(purchase.PurchaseError, match="already approved"):
        purchase.approve(blocked["escalation_id"], sponsor_id=sponsor, db_path=db, client=stub)

    assert stub.tools_called().count("approve_purchase") == 1


def test_a_failed_release_puts_the_approval_back(db: str, household: tuple[int, int]) -> None:
    """Better a sponsor who has to tap twice than one whose approval silently
    bought nothing."""
    sponsor, spender = household
    stub = WardenStub([parked(), warden.WardenError("pay-warden fell over")])
    blocked = purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)

    with pytest.raises(warden.WardenError):
        purchase.approve(blocked["escalation_id"], sponsor_id=sponsor, db_path=db, client=stub)

    row = store.get_escalation(blocked["escalation_id"], db_path=db)
    assert row["status"] == purchase.PENDING
    assert purchase.pending_for_sponsor(sponsor, db_path=db)


def test_another_persons_escalation_cannot_be_approved(db: str, household: tuple[int, int]) -> None:
    _, spender = household
    stranger = store.insert_person(name="Someone Else", role=Role.SPONSOR, db_path=db)
    stub = WardenStub([parked()])
    blocked = purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)

    with pytest.raises(purchase.PurchaseError, match="awaiting you"):
        purchase.approve(blocked["escalation_id"], sponsor_id=stranger, db_path=db, client=stub)

    assert store.get_escalation(blocked["escalation_id"], db_path=db)["status"] == "pending"


def test_an_unknown_verdict_is_never_read_as_permission(
    db: str, household: tuple[int, int]
) -> None:
    """The safe reading of a disagreement about permission is that permission
    was not given."""
    _, spender = household
    stub = WardenStub([{"verdict": "probably_fine", "rule_id": "x", "reason": "y"}])

    with pytest.raises(warden.WardenError, match="unknown verdict"):
        purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)


def test_a_park_without_an_attempt_id_is_refused(db: str, household: tuple[int, int]) -> None:
    """An escalation the sponsor can approve but nobody can act on is worse than
    a clean failure."""
    _, spender = household
    stub = WardenStub([{"verdict": warden.NEEDS_APPROVAL, "rule_id": "r", "reason": "why"}])

    with pytest.raises(purchase.PurchaseError, match="without an attempt id"):
        purchase.buy(person_id=spender, **SOAP, db_path=db, client=stub)


def test_a_spender_with_no_sponsor_cannot_escalate(db: str) -> None:
    """A system that quietly auto-approved here would be worst exactly where it
    matters most."""
    orphan = store.insert_person(name="Nobody's Child", role=Role.SPENDER, db_path=db)
    stub = WardenStub([parked()])

    with pytest.raises(purchase.PurchaseError, match="no sponsor"):
        purchase.buy(person_id=orphan, **SOAP, db_path=db, client=stub)


def test_a_sponsor_cannot_spend_against_their_own_policy(
    db: str, household: tuple[int, int]
) -> None:
    sponsor, _ = household

    with pytest.raises(purchase.PurchaseError, match="only a spender"):
        purchase.buy(person_id=sponsor, **SOAP, db_path=db, client=WardenStub([allowed()]))


def test_a_free_purchase_is_refused(db: str, household: tuple[int, int]) -> None:
    _, spender = household

    with pytest.raises(purchase.PurchaseError, match="has to cost something"):
        purchase.buy(
            person_id=spender, **{**SOAP, "amount_cents": 0}, db_path=db, client=WardenStub()
        )


# --- what the agent can and cannot do ----------------------------------------


@pytest.fixture
def box(db: str, household: tuple[int, int]) -> ToolBox:
    _, spender = household
    return ToolBox(person_id=spender, redactor=Redactor.build(db_path=db), db_path=db)


def test_the_agent_has_no_way_to_approve(box: ToolBox) -> None:
    """An agent able to approve its own escalations would make the sponsor's
    policy a suggestion."""
    names = {spec["function"]["name"] for spec in box.specs()}

    assert "request_purchase" in names
    assert not any("approve" in name or "release" in name for name in names)
    assert "error" in box.dispatch("approve_purchase", {"escalation_id": 1})


def test_the_agent_can_request_and_is_told_what_happened(
    db: str, household: tuple[int, int], box: ToolBox
) -> None:
    box.warden = WardenStub([parked(reason="over the single-purchase limit")])

    result = box.dispatch("request_purchase", SOAP)

    assert result["verdict"] == warden.NEEDS_APPROVAL
    assert "single-purchase limit" in result["reason"]
    assert box.writes_log[-1]["action"] == "purchase"


def test_an_unreachable_policy_engine_is_not_permission_to_spend(
    db: str, household: tuple[int, int], box: ToolBox
) -> None:
    box.warden = WardenStub([warden.WardenError("could not reach pay-warden")])

    result = box.dispatch("request_purchase", SOAP)

    assert result["verdict"] == "unavailable"
    assert "could not reach" in result["error"]


def test_the_tool_refuses_an_incomplete_request(box: ToolBox) -> None:
    assert "required" in box.dispatch("request_purchase", {"description": "soap"})["error"]
    assert (
        "required"
        in box.dispatch("request_purchase", {"description": "soap", "amount_cents": 100})["error"]
    )


def test_the_tool_description_tells_the_model_what_parked_means(box: ToolBox) -> None:
    """An agent that reads needs_approval as failure tells someone "no" when the
    real answer is "wait"."""
    spec = next(s for s in box.specs() if s["function"]["name"] == "request_purchase")
    doc = spec["function"]["description"]

    assert "needs_approval" in doc
    assert "NOT that it failed" in doc
    assert "1250" in doc  # integer minor units, spelled out


def test_a_purchase_through_a_whole_conversation(db: str, household: tuple[int, int]) -> None:
    _, spender = household
    stub = OpenAIStub(
        [
            completion(tool_calls=[("request_purchase", SOAP)]),
            completion(content="Asked Rae — it's waiting on them."),
        ]
    )
    box_warden = WardenStub([parked()])

    result = loop.run(
        "I'm out of soap",
        person_id=spender,
        db_path=db,
        http=stub.client(),
        warden=box_warden,
    )

    assert result["evidence"][0]["result"]["verdict"] == warden.NEEDS_APPROVAL
    assert len(store.list_escalations(db_path=db)) == 1


# --- the sponsor's surface ----------------------------------------------------


def test_the_sponsor_sees_what_is_waiting(
    db: str, household: tuple[int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    sponsor, spender = household
    purchase.buy(
        person_id=spender, **SOAP, db_path=db, client=WardenStub([parked(reason="over £4")])
    )

    cli.main(["--db", db, "approvals", "list", "--person", str(sponsor)])

    out = capsys.readouterr().out
    assert "Ana Whitfield wants hand soap" in out
    assert "£4.50 GBP" in out
    assert "over £4" in out  # the rule, worded as the policy engine worded it


def test_nothing_waiting_says_so(
    db: str, household: tuple[int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    sponsor, _ = household

    assert cli.main(["--db", db, "approvals", "list", "--person", str(sponsor)]) == 0
    assert "nothing waiting on you" in capsys.readouterr().out


def test_the_sponsor_cannot_see_another_households_escalations(
    db: str, household: tuple[int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    _, spender = household
    stranger = store.insert_person(name="Someone Else", role=Role.SPONSOR, db_path=db)
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))

    cli.main(["--db", db, "approvals", "list", "--person", str(stranger)])

    assert "nothing waiting on you" in capsys.readouterr().out


def test_declining_from_the_cli(db: str, household: tuple[int, int]) -> None:
    sponsor, spender = household
    blocked = purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))

    assert (
        cli.main(
            [
                "--db",
                db,
                "approvals",
                "decline",
                "--person",
                str(sponsor),
                "--id",
                str(blocked["escalation_id"]),
            ]
        )
        == 0
    )
    assert store.get_escalation(blocked["escalation_id"], db_path=db)["status"] == "declined"


def test_approving_something_already_decided_fails_loudly(
    db: str, household: tuple[int, int]
) -> None:
    sponsor, spender = household
    blocked = purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))
    purchase.decline(blocked["escalation_id"], sponsor_id=sponsor, db_path=db)

    with pytest.raises(SystemExit, match="already declined"):
        cli.main(
            [
                "--db",
                db,
                "approvals",
                "approve",
                "--person",
                str(sponsor),
                "--id",
                str(blocked["escalation_id"]),
            ]
        )


# --- refunds -----------------------------------------------------------------


def test_a_refund_must_point_at_a_purchase_that_happened(db: str, household) -> None:
    from steward.spend import refund

    _, spender = household
    with pytest.raises(refund.RefundError, match="purchase that happened"):
        refund.request(
            person_id=spender,
            attempt_id="",
            description="soap",
            amount_cents=450,
            reason="never arrived",
            db_path=db,
        )


def test_a_refund_needs_a_reason(db: str, household) -> None:
    from steward.spend import refund

    _, spender = household
    with pytest.raises(refund.RefundError, match="not a request"):
        refund.request(
            person_id=spender,
            attempt_id="att_1",
            description="soap",
            amount_cents=450,
            reason="   ",
            db_path=db,
        )


def test_the_reason_is_stored_verbatim(db: str, household) -> None:
    """A model must not summarise a complaint: the paraphrase is a different
    complaint, and this is the text a merchant might eventually read."""
    from steward.spend import refund

    _, spender = household
    words = "arrived leaking and the box was soaked through"
    result = refund.request(
        person_id=spender,
        attempt_id="att_1",
        description="soap",
        amount_cents=450,
        reason=words,
        db_path=db,
    )

    assert store.get_refund(result["refund_id"], db_path=db)["reason"] == words


def test_steward_says_it_has_not_contacted_anybody(db: str, household) -> None:
    """Somebody who thinks a claim has been filed will not chase it themselves."""
    from steward.spend import refund

    _, spender = household
    result = refund.request(
        person_id=spender,
        attempt_id="att_1",
        description="soap",
        amount_cents=450,
        reason="never arrived",
        db_path=db,
    )

    assert "has not contacted the merchant" in result["note"]


def test_a_refund_is_resolved_once(db: str, household) -> None:
    from steward.spend import refund

    _, spender = household
    made = refund.request(
        person_id=spender,
        attempt_id="att_1",
        description="soap",
        amount_cents=450,
        reason="never arrived",
        db_path=db,
    )

    refund.resolve(made["refund_id"], person_id=spender, refunded=True, db_path=db)
    with pytest.raises(store.NotFoundError):
        refund.resolve(made["refund_id"], person_id=spender, refunded=False, db_path=db)


def test_another_persons_refund_is_out_of_reach(db: str, household) -> None:
    from steward.spend import refund

    _, spender = household
    stranger = store.insert_person(name="Someone Else", role=Role.SPENDER, db_path=db)
    theirs = refund.request(
        person_id=stranger,
        attempt_id="att_9",
        description="soap",
        amount_cents=450,
        reason="never arrived",
        db_path=db,
    )

    with pytest.raises(refund.RefundError, match="belonging to you"):
        refund.resolve(theirs["refund_id"], person_id=spender, refunded=True, db_path=db)


def test_the_agent_has_no_refund_tool(db: str, household) -> None:
    """Asking for money back is a claim in somebody's name. It stays a person's
    act, like every other authority-bearing thing here."""
    from steward.agent.privacy import Redactor
    from steward.agent.tools import ToolBox

    _, spender = household
    box = ToolBox(person_id=spender, redactor=Redactor.build(db_path=db), db_path=db)

    names = {spec["function"]["name"] for spec in box.specs()}
    assert not any("refund" in name or "dispute" in name for name in names)


# --- the audit log ------------------------------------------------------------


class _NoContent:
    """What FastMCP returns for a tool whose list came back empty.

    One content block per item means zero items is zero blocks — which at the
    transport layer is indistinguishable from no reply at all.
    """

    isError = False
    structuredContent = None
    content: ClassVar[list[object]] = []


def test_an_empty_audit_log_is_not_an_unreachable_engine() -> None:
    """Somebody who has never bought anything must not read as a broken policy
    engine. The ledger surface showed this immediately: every household with no
    history rendered "pay-warden could not be reached"."""

    class Silent:
        def call(self, tool: str, arguments: dict) -> object:
            from steward.spend.warden import _unwrap

            return _unwrap(_NoContent())

    assert warden.audit_log(person_id=2, warden=Silent()) == []


def test_an_empty_reply_to_a_purchase_is_still_a_failure(
    db: str, household: tuple[int, int]
) -> None:
    """The other half, and the one that matters: silence is not permission.
    Only a caller that asked for a *list* may read no content as "none"."""
    _, spender = household

    class Silent:
        def call(self, tool: str, arguments: dict) -> object:
            from steward.spend.warden import _unwrap

            return _unwrap(_NoContent())

    with pytest.raises(warden.WardenError, match="empty response"):
        purchase.buy(person_id=spender, **SOAP, db_path=db, client=Silent())


def test_the_audit_log_is_always_asked_about_one_person() -> None:
    """pay-warden's audit database is shared by every agent it has answered for,
    so an unfiltered read crosses households."""
    stub = WardenStub([[]])

    warden.audit_log(person_id=7, warden=stub)

    assert stub.last("get_audit_log")["agent"] == warden.agent_name(7)


def test_an_error_from_reading_a_reply_survives_anyios_task_group() -> None:
    """anyio wraps whatever a task raised in an ExceptionGroup, so the bare
    `except WardenError` in `StdioWarden.call` never sees one raised while
    parsing pay-warden's answer.

    Two things were wrong without this. Such errors came back as "could not
    reach pay-warden", which is false — it was reached, and it answered — and
    the exception's own class was lost, which is what lets `audit_log` tell an
    empty list from a broken engine.
    """
    from steward.spend.warden import EmptyResponse, _warden_error_in

    inner = EmptyResponse("pay-warden returned an empty response")
    wrapped = ExceptionGroup("unhandled errors in a TaskGroup", [ExceptionGroup("inner", [inner])])

    assert _warden_error_in(wrapped) is inner
    assert _warden_error_in(ExceptionGroup("no wardens here", [ValueError("x")])) is None


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Blocks:
    """FastMCP's shape for a returned list: one content block per item."""

    isError = False
    structuredContent = None

    def __init__(self, *texts: str) -> None:
        self.content = [_Block(text) for text in texts]


def test_a_multi_row_audit_log_is_read_as_rows() -> None:
    """Blocks are decoded one at a time.

    Joining them and parsing once produced `{...}{...}`, which is not JSON, so
    any log with two or more rows came back as "unparseable content".
    """
    from steward.spend.warden import _unwrap

    class Two:
        def call(self, tool: str, arguments: dict) -> object:
            return _unwrap(_Blocks('{"verdict": "denied"}', '{"verdict": "allowed"}'))

    rows = warden.audit_log(person_id=2, warden=Two())

    assert [row["verdict"] for row in rows] == ["denied", "allowed"]


def test_a_single_row_audit_log_is_still_a_list() -> None:
    """One row arrives as one block, which is the same shape a dict-returning
    tool produces — so it decoded to a bare dict and the old `isinstance(...,
    list)` guard silently dropped it. Between this and the multi-row bug,
    get_audit_log had never once returned a row."""
    from steward.spend.warden import _unwrap

    class One:
        def call(self, tool: str, arguments: dict) -> object:
            return _unwrap(_Blocks('{"verdict": "needs_approval", "total_amount": "25"}'))

    rows = warden.audit_log(person_id=2, warden=One())

    assert len(rows) == 1
    assert rows[0]["total_amount"] == "25"


def test_a_single_object_reply_is_unchanged_for_a_purchase() -> None:
    """The one-block case must stay a bare value, or every other caller breaks."""
    from steward.spend.warden import _unwrap

    payload = _unwrap(_Blocks('{"verdict": "allowed", "rule_id": "r", "reason": "why"}'))

    assert isinstance(payload, dict)
    assert payload["verdict"] == "allowed"
