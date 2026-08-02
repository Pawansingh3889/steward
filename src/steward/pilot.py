"""The event stream a pilot is analysed from.

One function, `events`, returning every recorded thing about one pair in time
order, each row carrying a **pair id** and — where there was one — the **agent
run** that caused it. That is the whole point: without the run id, joining "what
they said" to "what was decided" is a guess about timestamps, and the pilot's
central question is which message led to which decision.

**Pseudonymous by construction.** A pair id is `pair_<sponsor>_<spender>` and no
name, phone number or email appears in any row. What a pilot needs to analyse is
sequence and outcome, not who anybody is, so the export cannot leak an identity
even if it is mailed to a supervisor by accident.

**Message text is excluded by default.** `include_text=True` is available for a
participant exercising their right to see what is held, but a analysis export
that carried the conversation would be a transcript of somebody's private
messages sitting in a spreadsheet. Counts and kinds answer the pilot's
questions; the words do not.

Corrections are first-class here rather than reconstructed. "This system held
something wrong about me and I fixed it" is what the pilot is trying to observe,
and a tombstone alone cannot tell that apart from a fact that was superseded.
"""

from __future__ import annotations

from typing import Any

from . import store


def pair_id(spender: dict[str, Any]) -> str:
    """Stable, pseudonymous, and derivable without a lookup table."""
    sponsor = spender["sponsor_id"] or 0
    return f"pair_{sponsor}_{spender['id']}"


def _row(pair: str, kind: str, at: str, run_id: int = 0, **detail: Any) -> dict[str, Any]:
    return {"pair": pair, "kind": kind, "at": at, "run_id": run_id, **detail}


def events(
    spender_id: int, *, include_text: bool = False, db_path: str | None = None
) -> list[dict[str, Any]]:
    """Everything recorded about one pair, in time order."""
    spender = store.get_person(spender_id, db_path=db_path)
    if spender is None:
        raise store.NotFoundError(f"no person {spender_id}")
    pair = pair_id(spender)
    out: list[dict[str, Any]] = []

    for turn in store.recent_turns(spender_id, limit=10_000, db_path=db_path):
        detail: dict[str, Any] = {
            "speaker": str(turn["speaker"]),
            "chars": len(str(turn["text"])),
            "shared_with_sponsor": bool(turn["shared_with_sponsor"]),
        }
        if include_text:
            detail["text"] = str(turn["text"])
        out.append(_row(pair, "message", str(turn["ts"]), int(turn["run_id"]), **detail))

    for run in store.recent_agent_runs(limit=10_000, db_path=db_path):
        if int(run["person_id"]) != spender_id:
            continue
        out.append(
            _row(
                pair,
                "agent_run",
                str(run["ts"]),
                int(run["id"]),
                trigger=str(run["trigger_kind"]),
                tools_used=str(run["tools_used"]),
                latency_ms=int(run["latency_ms"]),
                # Whether it failed at all, without the reason, which can carry
                # whatever the exception happened to say.
                failed=str(run["answer"]).startswith("(agent run failed)"),
            )
        )

    for escalation in store.list_escalations(spender_id=spender_id, db_path=db_path):
        out.append(
            _row(
                pair,
                "escalation_raised",
                str(escalation["created_ts"]),
                int(escalation["run_id"]),
                amount_cents=int(escalation["amount_cents"]),
                currency=str(escalation["currency"]),
                rule_id=str(escalation["rule_id"]),
            )
        )
        if escalation["decided_ts"]:
            out.append(
                _row(
                    pair,
                    "escalation_decided",
                    str(escalation["decided_ts"]),
                    int(escalation["run_id"]),
                    status=str(escalation["status"]),
                    # How long the sponsor took. The pilot's trust question in
                    # one number.
                    waited_from=str(escalation["created_ts"]),
                )
            )

    for correction in store.list_corrections(spender_id, db_path=db_path):
        out.append(
            _row(
                pair,
                "correction",
                str(correction["created_ts"]),
                int(correction["run_id"]),
                correction_kind=str(correction["kind"]),
                subject=str(correction["subject"]),
            )
        )

    for plan in store.list_plans(spender_id, db_path=db_path):
        out.append(
            _row(
                pair,
                "plan_proposed",
                str(plan["created_ts"]),
                target_cents=int(plan["target_cents"]),
                kind=str(plan["kind"]),
            )
        )
        if plan["activated_ts"]:
            out.append(_row(pair, "plan_started", str(plan["activated_ts"])))

    for refund in store.list_refunds(spender_id, db_path=db_path):
        out.append(
            _row(
                pair,
                "refund_requested",
                str(refund["created_ts"]),
                amount_cents=int(refund["amount_cents"]),
            )
        )

    out.sort(key=lambda row: (row["at"], row["kind"]))
    return out


def summary(spender_id: int, *, db_path: str | None = None) -> dict[str, Any]:
    """The counts a weekly pilot check-in actually reads.

    Deliberately not a score. These are the raw numbers the pilot plan says to
    collect; what they mean is a judgement for the person running it, and a
    single composite figure would hide the one that mattered.
    """
    stream = events(spender_id, db_path=db_path)
    counted: dict[str, int] = {}
    for row in stream:
        counted[row["kind"]] = counted.get(row["kind"], 0) + 1

    decided = [row for row in stream if row["kind"] == "escalation_decided"]
    return {
        "pair": stream[0]["pair"] if stream else "",
        "events": len(stream),
        "counts": counted,
        # The pilot's three questions, as close as counts can get to them.
        "corrections": counted.get("correction", 0),
        "escalations_raised": counted.get("escalation_raised", 0),
        "escalations_decided": len(decided),
        "escalations_undecided": counted.get("escalation_raised", 0) - len(decided),
        "agent_failures": sum(
            1 for row in stream if row["kind"] == "agent_run" and row.get("failed")
        ),
    }
