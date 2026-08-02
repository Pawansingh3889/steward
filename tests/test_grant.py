"""Registering a spender in the sponsor's policy.

Phase 3 found that pay-warden denies any agent its policy has never heard of,
so this is what turns an enrolled person into one who can actually spend. The
file stays the source of truth, so what matters most here is what the edit
leaves alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from steward import cli, store
from steward.models import Role
from steward.spend import grant

POLICY = """# pay-warden policy — the sponsor writes this, and can read it back.
version: 1

currencies: [GBP, USD]
base_currency: GBP
rates:
  GBP: "1.00"
  USD: "0.79"

agents:
  # Ana's allowance. Raised in March when she started commuting.
  "steward:person_9":
    daily_budget: "40.00"
    max_single_purchase: "25.00"

merchants:
  allow: []
  deny:
    - "*.casino.example"

velocity:
  max_purchases: 5
  window_minutes: 60

human_approval_over: "20.00"
"""

ALLOWANCE = grant.Allowance(daily_budget="50.00", max_single_purchase="20.00")


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(POLICY)
    return path


# --- reading -----------------------------------------------------------------


def test_existing_agents_are_found() -> None:
    assert grant.read_agents(POLICY) == ["steward:person_9"]


def test_a_policy_with_no_agents_block_is_reported() -> None:
    with pytest.raises(grant.GrantError, match="no `agents:` section"):
        grant.grant("version: 1\nmerchants:\n  allow: []\n", 2, ALLOWANCE)


def test_keys_after_the_agents_block_are_not_mistaken_for_agents() -> None:
    """`merchants:` is a sibling key, not a spender."""
    assert "merchants" not in grant.read_agents(POLICY)
    assert "velocity" not in grant.read_agents(POLICY)


# --- writing -----------------------------------------------------------------


def test_a_spender_is_registered_under_their_agent_name() -> None:
    updated = grant.grant(POLICY, 2, ALLOWANCE)

    assert "steward:person_2" in grant.read_agents(updated)
    assert '"steward:person_2":' in updated
    assert 'daily_budget: "50.00"' in updated


def test_everything_else_survives_the_edit() -> None:
    """The comments are most of a policy file's value — `yaml.safe_dump` would
    strip every one of them."""
    updated = grant.grant(POLICY, 2, ALLOWANCE)

    assert "# Ana's allowance. Raised in March when she started commuting." in updated
    assert "# pay-warden policy" in updated
    assert '"*.casino.example"' in updated
    assert 'human_approval_over: "20.00"' in updated
    # And the person who was already there is untouched.
    assert 'daily_budget: "40.00"' in updated


def test_re_granting_refuses_rather_than_overwriting() -> None:
    """Silently resetting limits a sponsor hand-edited would be the worst kind
    of helpfulness."""
    with pytest.raises(grant.GrantError, match="already in this policy"):
        grant.grant(POLICY, 9, ALLOWANCE)


def test_the_result_is_still_parseable_by_the_same_reader() -> None:
    once = grant.grant(POLICY, 2, ALLOWANCE)
    twice = grant.grant(once, 3, ALLOWANCE)

    assert grant.read_agents(twice) == [
        "steward:person_3",
        "steward:person_2",
        "steward:person_9",
    ]


# --- limits ------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["£50", "50.000", "fifty", "", "50.00 GBP", "-5.00"])
def test_an_amount_that_is_not_an_amount_is_refused(bad: str) -> None:
    with pytest.raises(grant.GrantError, match="plain amount"):
        grant.grant(POLICY, 2, grant.Allowance(daily_budget=bad, max_single_purchase="10.00"))


def test_a_per_purchase_cap_above_the_daily_budget_is_refused() -> None:
    """It could never bind, so it is almost certainly a slip."""
    with pytest.raises(grant.GrantError, match="could never take effect"):
        grant.grant(POLICY, 2, grant.Allowance(daily_budget="20.00", max_single_purchase="50.00"))


def test_no_limits_are_invented() -> None:
    """A default daily budget chosen by a program is a decision about somebody's
    money that nobody made."""
    import inspect

    signature = inspect.signature(grant.Allowance)
    assert all(
        parameter.default is inspect.Parameter.empty for parameter in signature.parameters.values()
    )


# --- on disk -----------------------------------------------------------------


def test_granting_in_a_file(policy_file: Path) -> None:
    name = grant.grant_in_file(policy_file, 2, ALLOWANCE)

    assert name == "steward:person_2"
    assert "steward:person_2" in grant.read_agents(policy_file.read_text())


def test_a_missing_policy_file_says_which_one(tmp_path: Path) -> None:
    with pytest.raises(grant.GrantError, match="could not read the policy"):
        grant.grant_in_file(tmp_path / "nope.yaml", 2, ALLOWANCE)


def test_a_refused_grant_leaves_the_file_untouched(policy_file: Path) -> None:
    before = policy_file.read_text()

    with pytest.raises(grant.GrantError):
        grant.grant_in_file(policy_file, 9, ALLOWANCE)

    assert policy_file.read_text() == before


# --- the CLI -----------------------------------------------------------------


def test_granting_from_the_cli(
    db: str, policy_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sponsor = store.insert_person(name="Rae", role=Role.SPONSOR, db_path=db)
    spender = store.insert_person(name="Ana", role=Role.SPENDER, sponsor_id=sponsor, db_path=db)

    exit_code = cli.main(
        [
            "--db",
            db,
            "spend",
            "grant",
            "--person",
            str(spender),
            "--policy",
            str(policy_file),
            "--daily",
            "50.00",
            "--per-purchase",
            "20.00",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"steward:person_{spender}" in out
    assert "source of truth" in out
    assert f"steward:person_{spender}" in grant.read_agents(policy_file.read_text())


def test_the_cli_refuses_without_a_policy_path(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAY_WARDEN_POLICY", "")
    store.insert_person(name="Ana", role=Role.SPENDER, db_path=db)

    with pytest.raises(SystemExit, match="PAY_WARDEN_POLICY"):
        cli.main(["--db", db, "spend", "grant", "--daily", "50.00", "--per-purchase", "20.00"])
