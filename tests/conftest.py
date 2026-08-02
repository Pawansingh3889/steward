"""Test environment: a throwaway database and hosts that cannot resolve.

Every model call in the suite goes through an httpx.MockTransport. The
unregistrable bases below plus the no-network guard mean anything that slips
past a mock dies loudly instead of spending real OpenAI credits — this project
calls a reasoning model in a loop, so an un-mocked test is a bill, not a flake.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "steward.sqlite3"
    monkeypatch.setenv("STEWARD_DB", str(db))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-unit")
    monkeypatch.setenv("OPENAI_API_BASE", "https://openai.invalid")
    monkeypatch.setenv("STEWARD_AGENT_MODEL", "gpt-5")
    monkeypatch.setenv("LINQ_API_TOKEN", "linq-test-token")
    monkeypatch.setenv("LINQ_API_BASE", "https://linq.invalid")
    monkeypatch.setenv("PAY_WARDEN_COMMAND", "true")
    monkeypatch.setenv("PAY_WARDEN_ARGS", "")
    # Loopback, so extract/local.py's destination check passes and the tests
    # exercise the real path; the no-network guard still stops the request.
    monkeypatch.setenv("OLLAMA_BASE", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")
    monkeypatch.setenv("STEWARD_LOCAL_LLM_ALLOW_REMOTE", "")
    # Pinned for the same reason, and it bites harder: unset is a *different
    # embedder*, so a developer with this exported would have the memory tests
    # reach for Ollama and die on the no-network guard below.
    monkeypatch.setenv("STEWARD_EMBEDDING_MODEL", "")
    # config._load_env() reads a real .env if one exists and setdefault means a
    # developer's live key would win over the pins above. Point it at a path
    # that cannot exist so the suite is identical on every machine.
    monkeypatch.setattr("steward.config._load_env", lambda: None)
    return db


@pytest.fixture(autouse=True)
def no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a real socket impossible, rather than merely unlikely.

    A test marked `@pytest.mark.live` opts out. That marker is the only way to
    reach the network from this suite, it has to be written on the test itself,
    and grepping for it lists every test that can — which is the property that
    matters. Those tests are also skipped unless their own environment variable
    is set, so the default run still cannot touch anything.

    The unregistrable hosts above stop a leak from reaching anyone, but it would
    still fail as a DNS error somewhere confusing. This turns any un-mocked
    request into an immediate, obvious failure naming the test that made it.

    Starlette's TestClient uses ASGITransport and httpx.MockTransport is its own
    class, so neither is affected.
    """
    if request.node.get_closest_marker("live"):
        return

    def deny(self: object, request: object, *args: object, **kwargs: object) -> None:
        raise RuntimeError(
            f"a test tried to open a real network connection to {request} — "
            "use httpx.MockTransport via the http= seam"
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)


@pytest.fixture
def db(env: Path) -> str:
    """The DB path as store.* wants it. Distinct per test via tmp_path."""
    return str(env)
