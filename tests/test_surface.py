"""Phase 5: two humans transact by text.

The spender's line is a conversation; the sponsor's line carries approvals and
nothing else. Most of what is asserted here is about who is entitled to read or
decide what — the routing itself is the easy part.
"""

from __future__ import annotations

import httpx
import pytest

from steward import cli, store
from steward.models import Role
from steward.spend import purchase
from steward.surface import linq
from steward.surface.base import NO, OTHER, YES, Inbound, Outbound, RecordingChannel, intent
from steward.surface.router import Router

from .agent_stub import OpenAIStub, completion
from .warden_stub import WardenStub, parked, released

SPENDER_LINE = "+447700900002"
SPONSOR_LINE = "+447700900001"

SOAP = {
    "description": "hand soap",
    "amount_cents": 450,
    "currency": "GBP",
    "merchant_name": "Fixture Store",
    "merchant_url": "https://fixture.example",
    "merchant_country": "GB",
}


@pytest.fixture
def household(db: str) -> tuple[int, int]:
    sponsor = store.insert_person(
        name="Rae Whitfield", role=Role.SPONSOR, phone=SPONSOR_LINE, db_path=db
    )
    spender = store.insert_person(
        name="Ana Whitfield",
        role=Role.SPENDER,
        sponsor_id=sponsor,
        phone=SPENDER_LINE,
        db_path=db,
    )
    return sponsor, spender


@pytest.fixture
def channel() -> RecordingChannel:
    return RecordingChannel()


def router(db: str, channel: RecordingChannel, **kwargs) -> Router:
    return Router(db_path=db, channel=channel, **kwargs)


# --- reading a reply ---------------------------------------------------------


@pytest.mark.parametrize("body", ["yes", "Y", "OK", "approve", "Yes please", "sure, go on"])
def test_a_yes_is_read_as_yes(body: str) -> None:
    assert intent(body) == YES


@pytest.mark.parametrize("body", ["no", "N", "nope", "Decline", "no thanks"])
def test_a_no_is_read_as_no(body: str) -> None:
    assert intent(body) == NO


@pytest.mark.parametrize(
    "body",
    [
        "",
        "what is it for?",
        # The dangerous ones: a yes-word that is not the answer.
        "not sure that's fine to be honest",
        "how much did you say",
    ],
)
def test_anything_else_is_not_read_as_consent(body: str) -> None:
    """Only the first word counts. Scanning for a yes-word anywhere would
    approve a purchase on the strength of "fine" in the middle of a sentence."""
    assert intent(body) == OTHER


# --- who is allowed to talk to it --------------------------------------------


def test_an_unknown_number_gets_silence(db: str, household, channel: RecordingChannel) -> None:
    """Any reply confirms to a stranger that this number moves money."""
    handled = router(db, channel).receive(Inbound(sender="+447000000000", body="hello"))

    assert handled.kind == "unknown_sender"
    assert handled.replies == []
    assert channel.sent == []


def test_a_spender_gets_a_conversation(db: str, household, channel: RecordingChannel) -> None:
    _, spender = household
    model = OpenAIStub([completion(content="Noted — you're out of soap.")])

    handled = router(db, channel, http=model.client()).receive(
        Inbound(sender=SPENDER_LINE, body="I'm out of soap")
    )

    assert handled.kind == "answered"
    assert channel.to_person(spender) == ["Noted — you're out of soap."]


def test_a_sponsor_texting_with_nothing_pending_is_told_so(
    db: str, household, channel: RecordingChannel
) -> None:
    """Their line is for approvals; there is no conversation to have on it."""
    handled = router(db, channel).receive(Inbound(sender=SPONSOR_LINE, body="hello?"))

    assert handled.kind == "nothing_waiting"
    assert "Nothing is waiting" in handled.bodies()[0]


def test_an_agent_failure_still_answers_the_person(
    db: str, household, channel: RecordingChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silence reads as being ignored by something they just told a problem to."""
    monkeypatch.setenv("OPENAI_API_KEY", "")

    handled = router(db, channel).receive(Inbound(sender=SPENDER_LINE, body="I'm out of soap"))

    assert handled.kind == "agent_error"
    assert "nothing has been spent" in handled.bodies()[0]


# --- the escalation crossing between two lines -------------------------------


def test_a_parked_purchase_texts_the_sponsor(db: str, household, channel: RecordingChannel) -> None:
    sponsor, _ = household
    model = OpenAIStub(
        [
            completion(tool_calls=[("request_purchase", SOAP)]),
            completion(content="Asked Rae — it's waiting on them."),
        ]
    )

    router(db, channel, http=model.client(), warden=WardenStub([parked()])).receive(
        Inbound(sender=SPENDER_LINE, body="I'm out of soap")
    )

    to_sponsor = channel.to_person(sponsor)
    assert len(to_sponsor) == 1
    assert "Ana Whitfield wants hand soap" in to_sponsor[0]
    assert "£4.50 GBP" in to_sponsor[0]
    assert "Reply YES or NO" in to_sponsor[0]
    # The rule that fired, worded as the policy engine worded it.
    assert "over the single-purchase limit" in to_sponsor[0]


def test_yes_releases_it_and_the_spender_gets_the_link(
    db: str, household, channel: RecordingChannel
) -> None:
    """Phase 5's exit criterion: two humans, two lines, one settled purchase."""
    sponsor, spender = household
    model = OpenAIStub(
        [completion(tool_calls=[("request_purchase", SOAP)]), completion(content="Waiting.")]
    )
    warden = WardenStub([parked(), released(url="https://pay.example/s/7")])
    box = router(db, channel, http=model.client(), warden=warden)

    box.receive(Inbound(sender=SPENDER_LINE, body="I'm out of soap"))
    handled = box.receive(Inbound(sender=SPONSOR_LINE, body="yes"))

    assert handled.kind == "approved"
    assert "Approved" in channel.to_person(sponsor)[-1]
    # The link goes to the spender: the sponsor said yes, but it is still the
    # spender's errand and their passkey.
    assert "https://pay.example/s/7" in channel.to_person(spender)[-1]
    assert "https://pay.example/s/7" not in " ".join(channel.to_person(sponsor))


def test_no_declines_it_and_the_spender_is_told_plainly(
    db: str, household, channel: RecordingChannel
) -> None:
    _, spender = household
    model = OpenAIStub(
        [completion(tool_calls=[("request_purchase", SOAP)]), completion(content="Waiting.")]
    )
    box = router(db, channel, http=model.client(), warden=WardenStub([parked()]))
    box.receive(Inbound(sender=SPENDER_LINE, body="I'm out of soap"))

    handled = box.receive(Inbound(sender=SPONSOR_LINE, body="no"))

    assert handled.kind == "declined"
    assert "said no" in channel.to_person(spender)[-1]
    assert store.list_escalations(db_path=db)[0]["status"] == "declined"


def test_a_bare_yes_with_two_pending_asks_which(
    db: str, household, channel: RecordingChannel
) -> None:
    """Guessing which would be guessing with somebody's money."""
    _, spender = household
    for attempt in ("att_1", "att_2"):
        purchase.buy(
            person_id=spender, **SOAP, db_path=db, client=WardenStub([parked(attempt_id=attempt)])
        )

    handled = router(db, channel).receive(Inbound(sender=SPONSOR_LINE, body="yes"))

    assert handled.kind == "ambiguous"
    assert "more than one" in handled.bodies()[0]
    assert all(row["status"] == "pending" for row in store.list_escalations(db_path=db))


def test_naming_one_of_several_works(db: str, household, channel: RecordingChannel) -> None:
    _, spender = household
    first = purchase.buy(
        person_id=spender, **SOAP, db_path=db, client=WardenStub([parked(attempt_id="att_1")])
    )
    purchase.buy(
        person_id=spender, **SOAP, db_path=db, client=WardenStub([parked(attempt_id="att_2")])
    )

    handled = router(db, channel, warden=WardenStub([released()])).receive(
        Inbound(sender=SPONSOR_LINE, body=f"yes #{first['escalation_id']}")
    )

    assert handled.kind == "approved"
    assert store.get_escalation(first["escalation_id"], db_path=db)["status"] == "approved"


def test_an_unclear_reply_lists_what_is_waiting_rather_than_guessing(
    db: str, household, channel: RecordingChannel
) -> None:
    _, spender = household
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))

    handled = router(db, channel).receive(Inbound(sender=SPONSOR_LINE, body="what's it for?"))

    assert handled.kind == "unclear"
    assert "Waiting on you" in handled.bodies()[0]
    assert store.list_escalations(db_path=db)[0]["status"] == "pending"


def test_a_sponsor_cannot_decide_another_households_escalation(
    db: str, household, channel: RecordingChannel
) -> None:
    """A number belonging to one sponsor must not approve another's spending by
    naming a number."""
    _, spender = household
    theirs = purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))
    outsider = store.insert_person(
        name="Someone Else", role=Role.SPONSOR, phone="+447999999999", db_path=db
    )

    handled = router(db, channel).receive(
        Inbound(sender="+447999999999", body=f"yes #{theirs['escalation_id']}")
    )

    assert handled.kind == "nothing_waiting"
    assert store.get_escalation(theirs["escalation_id"], db_path=db)["status"] == "pending"
    assert outsider


def test_a_failed_release_tells_the_sponsor_rather_than_going_quiet(
    db: str, household, channel: RecordingChannel
) -> None:
    from steward.spend.warden import WardenError

    _, spender = household
    purchase.buy(person_id=spender, **SOAP, db_path=db, client=WardenStub([parked()]))

    handled = router(db, channel, warden=WardenStub([WardenError("warden down")])).receive(
        Inbound(sender=SPONSOR_LINE, body="yes")
    )

    assert handled.kind == "approve_failed"
    assert "couldn't release" in handled.bodies()[0]
    # And it is back to pending, so tapping again can work.
    assert store.list_escalations(db_path=db)[0]["status"] == "pending"


# --- sharing -----------------------------------------------------------------


def test_a_conversation_is_private_by_default(
    db: str, household, channel: RecordingChannel
) -> None:
    _, spender = household
    model = OpenAIStub([completion(content="Noted.")])

    router(db, channel, http=model.client()).receive(
        Inbound(sender=SPENDER_LINE, body="I'm skint until Friday")
    )

    assert store.shared_turns(spender, db_path=db) == []


def test_the_spender_can_open_and_close_it(db: str, household, channel: RecordingChannel) -> None:
    _, spender = household
    box = router(db, channel)

    box.receive(Inbound(sender=SPENDER_LINE, body="share this"))
    assert store.get_person(spender, db_path=db)["share_mode"] == store.SHARE_SHARED

    model = OpenAIStub([completion(content="Noted.")])
    router(db, channel, http=model.client()).receive(
        Inbound(sender=SPENDER_LINE, body="I need soap")
    )
    assert [t["text"] for t in store.shared_turns(spender, db_path=db)] == [
        "I need soap",
        "Noted.",
    ]

    box.receive(Inbound(sender=SPENDER_LINE, body="keep this private"))
    assert store.get_person(spender, db_path=db)["share_mode"] == store.SHARE_PRIVATE


def test_turning_sharing_on_does_not_expose_what_was_said_before(
    db: str, household, channel: RecordingChannel
) -> None:
    """Deciding visibility at read time would retroactively expose everything —
    which is not what anyone means by "share this conversation"."""
    _, spender = household
    model = OpenAIStub([completion(content="Noted.")])
    router(db, channel, http=model.client()).receive(
        Inbound(sender=SPENDER_LINE, body="something private")
    )

    router(db, channel).receive(Inbound(sender=SPENDER_LINE, body="share this"))

    assert store.shared_turns(spender, db_path=db) == []


def test_going_private_is_honest_about_what_it_does_not_hide(
    db: str, household, channel: RecordingChannel
) -> None:
    handled = router(db, channel).receive(Inbound(sender=SPENDER_LINE, body="keep this private"))

    assert "still see decisions" in handled.bodies()[0]


def test_the_agent_has_no_tool_to_change_sharing(db: str, household) -> None:
    from steward.agent.privacy import Redactor
    from steward.agent.tools import ToolBox

    _, spender = household
    box = ToolBox(person_id=spender, redactor=Redactor.build(db_path=db), db_path=db)

    names = {spec["function"]["name"] for spec in box.specs()}
    assert not any("shar" in name for name in names)


# --- Linq --------------------------------------------------------------------


def test_linq_is_dry_run_unless_explicitly_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A text reaches a real person and cannot be recalled. An integration that
    went live because a token happened to be present is how a test run becomes a
    message to somebody's parent."""
    monkeypatch.setenv("LINQ_FROM_NUMBER", "+447000000001")
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"id": "msg_1"})

    channel = linq.LinqChannel(http=httpx.Client(transport=httpx.MockTransport(handler)))
    delivery = channel.send(Outbound(person_id=1, body="hello"), to=SPENDER_LINE)

    assert delivery.delivered is False
    assert "STEWARD_LINQ_LIVE=1" in delivery.detail
    assert sent == []


def test_linq_sends_when_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINQ_FROM_NUMBER", "+447000000001")
    monkeypatch.setenv("STEWARD_LINQ_LIVE", "1")
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        sent.append(_json.loads(request.content.decode()))
        return httpx.Response(200, json={"id": "msg_1"})

    channel = linq.LinqChannel(http=httpx.Client(transport=httpx.MockTransport(handler)))
    delivery = channel.send(Outbound(person_id=1, body="hello"), to=SPENDER_LINE)

    assert delivery.delivered is True
    assert sent == [{"to": SPENDER_LINE, "from": "+447000000001", "body": "hello"}]


@pytest.mark.parametrize("value", ["false", "no", "0", ""])
def test_a_negative_live_value_still_means_dry_run(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LINQ_FROM_NUMBER", "+447000000001")
    monkeypatch.setenv("STEWARD_LINQ_LIVE", value)

    delivery = linq.LinqChannel().send(Outbound(person_id=1, body="hi"), to=SPENDER_LINE)

    assert delivery.delivered is False


def test_linq_says_what_is_missing_rather_than_failing_obscurely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINQ_API_TOKEN", "")

    delivery = linq.LinqChannel().send(Outbound(person_id=1, body="hi"), to=SPENDER_LINE)

    assert "LINQ_API_TOKEN is unset" in delivery.detail


def test_a_person_with_no_number_is_reported_not_crashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = linq.LinqChannel().send(Outbound(person_id=1, body="hi"), to="")

    assert delivery.delivered is False
    assert "no number on file" in delivery.detail


def test_a_linq_error_is_relayed_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first live attempt against an unverified endpoint is exactly when a
    summarised error costs an afternoon."""
    monkeypatch.setenv("LINQ_FROM_NUMBER", "+447000000001")
    monkeypatch.setenv("STEWARD_LINQ_LIVE", "1")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(422, text="unknown field 'body'")
    )

    delivery = linq.LinqChannel(http=httpx.Client(transport=transport)).send(
        Outbound(person_id=1, body="hi"), to=SPENDER_LINE
    )

    assert delivery.delivered is False
    assert "unknown field 'body'" in delivery.detail


# --- the CLI -----------------------------------------------------------------


def test_texting_through_the_cli(
    db: str, household, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")

    assert cli.main(["--db", db, "text", SPENDER_LINE, "I'm out of soap"]) == 0

    assert "Ana Whitfield" in capsys.readouterr().out


def test_the_cli_reports_an_unknown_sender_to_the_operator_only(
    db: str, household, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--db", db, "text", "+447000000000", "hello"]) == 1

    out = capsys.readouterr().out
    assert "ignored" in out
    assert "→" not in out  # nothing was addressed to anyone


def test_sharing_from_the_cli(db: str, household, capsys: pytest.CaptureFixture[str]) -> None:
    _, spender = household

    cli.main(["--db", db, "share", "--person", str(spender), "--on"])
    assert store.get_person(spender, db_path=db)["share_mode"] == store.SHARE_SHARED

    cli.main(["--db", db, "share", "--person", str(spender), "--off"])
    assert store.get_person(spender, db_path=db)["share_mode"] == store.SHARE_PRIVATE
    assert "not the chat" in capsys.readouterr().out
