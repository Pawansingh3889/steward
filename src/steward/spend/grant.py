"""Registering a spender in the sponsor's policy.

Phase 3 surfaced this the hard way: pay-warden denies any agent its policy has
never heard of, so enrolling somebody in steward does not let them spend a
penny. Something has to write `steward:person_<id>` into the YAML, and this is
that something.

**The YAML stays the source of truth, and stays reviewable.** This edits it
rather than replacing it, preserves every key it does not understand, and never
invents limits — the caller passes the numbers, because a default daily budget
chosen by a program is a decision about somebody's money that nobody made. What
comes back is a diff-able file a sponsor can read, which is the whole reason
policy lives in a file and not in this database.

Written with the stdlib only. A YAML library would be a reasonable dependency,
but the shape being edited is a handful of nested scalars under one key, and
round-tripping it by hand keeps the file's comments and ordering intact —
`yaml.safe_dump` would silently strip every explanatory comment the sponsor
wrote, which for a policy file is most of its value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .warden import agent_name


class GrantError(RuntimeError):
    """The policy file could not be read, understood, or safely edited."""


@dataclass(frozen=True)
class Allowance:
    daily_budget: str
    max_single_purchase: str

    def validate(self) -> None:
        for label, value in (
            ("daily_budget", self.daily_budget),
            ("max_single_purchase", self.max_single_purchase),
        ):
            if not re.fullmatch(r"\d+(\.\d{1,2})?", value):
                raise GrantError(f"{label} must be a plain amount like '50.00', not {value!r}")
        if float(self.max_single_purchase) > float(self.daily_budget):
            # Not fatal to pay-warden, but almost certainly a slip: a single
            # purchase cap above the daily budget can never bind.
            raise GrantError(
                f"max_single_purchase {self.max_single_purchase} is above the daily budget"
                f" {self.daily_budget}, so it could never take effect"
            )


# A mapping key with no value on the same line. Two forms, because both occur:
# pay-warden's own policy names agents like `demo-shopper:`, while steward's
# contain a colon (`steward:person_2`) and so must be quoted. A single pattern
# that excluded colons — the obvious first attempt — silently found no steward
# agents at all, which made `grant` cheerfully register the same person twice.
_QUOTED_KEY = re.compile(r'^\s*"([^"]+)"\s*:\s*$')
_BARE_KEY = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*:\s*$")


def _key_on(line: str) -> str | None:
    for pattern in (_QUOTED_KEY, _BARE_KEY):
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def read_agents(policy_text: str) -> list[str]:
    """Agent names the policy already registers."""
    names: list[str] = []
    inside = False
    base_indent = 0
    for line in policy_text.splitlines():
        stripped = line.strip()
        if not inside:
            if stripped == "agents:":
                inside = True
                base_indent = _indent_of(line)
            continue
        if stripped and _indent_of(line) <= base_indent and not stripped.startswith("#"):
            break  # a sibling key: the agents block is over
        if stripped.startswith("#"):
            continue
        # Only keys one level in are agents; anything deeper is their settings.
        if _indent_of(line) != base_indent + 2:
            continue
        key = _key_on(line)
        if key:
            names.append(key)
    return names


def grant(policy_text: str, person_id: int, allowance: Allowance) -> str:
    """Return the policy with this spender registered.

    Idempotent by refusal rather than by overwrite: re-granting raises instead
    of silently resetting limits a sponsor may have hand-edited since. Changing
    an existing allowance is a different, deliberate act.
    """
    allowance.validate()
    name = agent_name(person_id)
    if name in read_agents(policy_text):
        raise GrantError(
            f"{name} is already in this policy. Edit the file directly to change their limits —"
            " overwriting them from here would discard whatever was set by hand."
        )

    lines = policy_text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "agents:":
            continue
        indent = " " * (_indent_of(line) + 2)
        block = [
            f'{indent}"{name}":',
            f'{indent}  daily_budget: "{allowance.daily_budget}"',
            f'{indent}  max_single_purchase: "{allowance.max_single_purchase}"',
        ]
        return "\n".join([*lines[: index + 1], *block, *lines[index + 1 :]]) + (
            "\n" if policy_text.endswith("\n") else ""
        )

    raise GrantError(
        "this policy has no `agents:` section, so there is nowhere to register a spender"
    )


def grant_in_file(path: str | Path, person_id: int, allowance: Allowance) -> str:
    """Edit the policy on disk. Returns the agent name that was registered."""
    policy = Path(path)
    try:
        text = policy.read_text()
    except OSError as exc:
        raise GrantError(f"could not read the policy at {policy}: {exc}") from exc
    updated = grant(text, person_id, allowance)
    try:
        policy.write_text(updated)
    except OSError as exc:
        raise GrantError(f"could not write the policy at {policy}: {exc}") from exc
    return agent_name(person_id)
