"""Config: the only module that reads os.environ.

Mostly checking that a missing value becomes a sentence rather than a plausible
default — the failure mode this project cannot afford is guessing.
"""

from __future__ import annotations

import pytest

from steward import config


def test_the_openai_base_carries_no_version_segment() -> None:
    """`llm.complete` appends `/v1/chat/completions`. A base ending in `/v1`
    would send it to `/v1/v1/...`, which fails as a confusing 404."""
    assert not config.DEFAULT_OPENAI_BASE.endswith("/v1")


def test_a_trailing_slash_cannot_produce_a_double_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_BASE", "https://proxy.invalid/")

    assert config.openai_api_base() == "https://proxy.invalid"


def test_an_unset_key_is_empty_rather_than_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A working install without a model: the agent surfaces say so, and
    everything that is not the model still runs."""
    monkeypatch.setenv("OPENAI_API_KEY", "")

    assert config.openai_api_key() == ""


def test_a_missing_pay_warden_command_names_the_consequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every purchase is policy-checked, so an unconfigured pay-warden means no
    spending at all — the error has to say that, not just name a variable."""
    monkeypatch.setenv("PAY_WARDEN_COMMAND", "")

    with pytest.raises(config.ConfigError, match="no path to spending"):
        config.pay_warden_command()


def test_pay_warden_args_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAY_WARDEN_COMMAND", "uv")
    monkeypatch.setenv("PAY_WARDEN_ARGS", "run --project ../pay-warden python -m pay_warden.server")

    assert config.pay_warden_command()[:2] == ["uv", "run"]


def test_secret_values_skips_the_unset_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty string in the denylist would match every request body — the
    send-time assert would refuse to send anything at all."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-abc")
    monkeypatch.setenv("LINQ_API_TOKEN", "")
    monkeypatch.setenv("PRAVA_SECRET_KEY", "")

    assert config.secret_values() == ("sk-abc",)


def test_secrets_are_read_live_so_rotation_takes_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
    assert "sk-old" in config.secret_values()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-new")
    assert "sk-new" in config.secret_values()
    assert "sk-old" not in config.secret_values()
