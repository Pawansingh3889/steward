"""The local model — and the check that keeps it local.

This is the one component that legitimately sees unredacted text, so the tests
that matter most are the ones about where its requests are allowed to go.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from steward.extract import local, pipeline
from steward.extract.base import INFERRED
from steward.models import FactKind


class OllamaStub:
    """A scripted Ollama over MockTransport, recording what it was sent."""

    def __init__(self, response: str = "[]", *, status: int = 200) -> None:
        self.response = response
        self.status = status
        self.requests: list[dict[str, Any]] = []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(self.status, json={"models": []})
        self.requests.append(json.loads(request.content.decode()))
        if self.status != 200:
            return httpx.Response(self.status, text="upstream is unhappy")
        return httpx.Response(200, json={"response": self.response})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handler))


# --- the loopback check ------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
        "http://127.5.5.5:11434",
    ],
)
def test_loopback_addresses_are_recognised(url: str) -> None:
    assert local.is_loopback(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://ollama.example.com",
        "http://192.168.1.50:11434",
        "http://10.0.0.9:11434",
        # The classic near-miss: a hostname that merely starts with "localhost".
        "http://localhost.evil.com:11434",
    ],
)
def test_non_loopback_addresses_are_not(url: str) -> None:
    assert not local.is_loopback(url)


def test_sending_raw_text_off_the_box_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo or a copied dotfile would otherwise turn the safest component in
    the system into the one that posts a person's unredacted life to a stranger."""
    monkeypatch.setenv("OLLAMA_BASE", "https://ollama.example.com")

    with pytest.raises(local.LocalModelError, match="refusing to send unredacted text"):
        local.extract("anything at all")


def test_the_refusal_says_what_would_actually_happen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_BASE", "https://ollama.example.com")

    with pytest.raises(local.LocalModelError) as raised:
        local.check_destination("https://ollama.example.com")

    message = str(raised.value)
    assert "raw email, calendar entries and messages" in message
    assert "STEWARD_LOCAL_LLM_ALLOW_REMOTE=1" in message


def test_an_explicit_opt_in_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone running Ollama on their own LAN is making a real choice — but
    they have to make it explicitly."""
    monkeypatch.setenv("OLLAMA_BASE", "http://192.168.1.50:11434")
    monkeypatch.setenv("STEWARD_LOCAL_LLM_ALLOW_REMOTE", "1")

    local.check_destination("http://192.168.1.50:11434")  # does not raise


@pytest.mark.parametrize("value", ["false", "no", "0", "off", ""])
def test_a_negative_opt_in_value_still_means_no(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A truthiness check on a non-empty string would read "false" as yes and
    ship someone's mail off the box."""
    monkeypatch.setenv("OLLAMA_BASE", "http://192.168.1.50:11434")
    monkeypatch.setenv("STEWARD_LOCAL_LLM_ALLOW_REMOTE", value)

    with pytest.raises(local.LocalModelError):
        local.check_destination("http://192.168.1.50:11434")


def test_availability_is_false_rather_than_fatal_for_a_remote_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_BASE", "https://ollama.example.com")

    assert local.available() is False


# --- extraction --------------------------------------------------------------


def test_free_form_text_becomes_inferred_facts() -> None:
    stub = OllamaStub(
        json.dumps(
            [{"kind": FactKind.SCHEDULE, "key": "boiler", "value": "boiler service Thursday"}]
        )
    )

    candidates = local.extract("the boiler man comes Thursday", http=stub.client())

    assert len(candidates) == 1
    assert candidates[0].value == "boiler service Thursday"
    # Never `parsed`: a model concluded this, and that has to stay visible.
    assert candidates[0].source == INFERRED


def test_the_request_is_deterministic() -> None:
    """Re-importing a mailbox must not quietly rewrite memory each time."""
    stub = OllamaStub()

    local.extract("anything", http=stub.client())

    assert stub.requests[0]["options"]["temperature"] == 0.0
    assert stub.requests[0]["stream"] is False


def test_a_code_fence_is_tolerated() -> None:
    fenced = '```json\n[{"kind": "mood", "key": "today", "value": "tired"}]\n```'
    stub = OllamaStub(fenced)

    assert local.extract("I'm shattered", http=stub.client())[0].value == "tired"


@pytest.mark.parametrize(
    "response",
    ["not json at all", "{}", '"a string"', "[", '[{"kind": "vibes", "value": "x"}]', "[42]"],
)
def test_unusable_output_yields_nothing_rather_than_a_wrong_fact(response: str) -> None:
    """An extractor that invents a fact out of a misparse writes it into memory
    looking exactly as authoritative as everything else on the screen."""
    stub = OllamaStub(response)

    assert local.extract("something", http=stub.client()) == []


def test_an_entry_missing_a_value_is_dropped() -> None:
    stub = OllamaStub(json.dumps([{"kind": FactKind.MOOD, "key": "today", "value": "  "}]))

    assert local.extract("hi", http=stub.client()) == []


def test_an_overlong_value_is_truncated_not_rejected() -> None:
    stub = OllamaStub(json.dumps([{"kind": FactKind.GOAL, "key": "k", "value": "x" * 500}]))

    assert len(local.extract("hi", http=stub.client())[0].value) == 200


def test_an_upstream_error_is_reported_not_swallowed() -> None:
    stub = OllamaStub(status=500)

    with pytest.raises(local.LocalModelError, match="500"):
        local.extract("something", http=stub.client())


# --- degradation -------------------------------------------------------------


def test_the_pipeline_degrades_visibly_when_ollama_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Nothing was learned" and "nothing could be attempted" look identical in
    a fact list and mean very different things."""
    monkeypatch.setenv("OLLAMA_BASE", "https://ollama.example.com")

    extraction = pipeline.extract_all("the boiler man comes Thursday")

    assert extraction.candidates == []
    assert "refusing to send" in extraction.degraded


def test_a_deterministic_parser_still_works_with_no_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parsers are the floor; the model is what degrades."""
    monkeypatch.setenv("OLLAMA_BASE", "https://ollama.example.com")

    extraction = pipeline.extract_all("Barclays: payment of £8.20 at CO-OP. Balance £91.80")

    assert extraction.extractor == "bank"
    assert extraction.degraded == ""
    assert extraction.candidates
