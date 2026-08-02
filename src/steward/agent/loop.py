"""The agent loop: one question in, one audited answer out.

Ported from payoptimize, minus its Engine — steward's loop needs only a database
path and a person, because everything it can touch it touches through its
ToolBox. Synchronous on purpose, like every model- or HTTP-touching module here;
async surfaces wrap `run` in a threadpool.

The loop holds no privileges of its own: it can only call the tools its ToolBox
exposes, and the ToolBox was built scoped to one person with this run's
redactor. The system prompt teaches manners; the boundary does not depend on it.

The audit is unconditional. The run row opens before the first model call and
finishes in a `finally`, so a crash mid-conversation still leaves a record of
what was asked, what was spent, and how far it got.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .. import config, store
from ..models import Trigger
from . import llm
from .privacy import Redactor
from .tools import ToolBox

MAX_TURNS = 10

CAP_ANSWER = (
    "I hit my tool-call budget before reaching a conclusion. What I gathered so far"
    " is recorded in this run's audit trail."
)

SYSTEM_PROMPT = """You are steward, an agent that helps someone manage money \
that is not entirely their own. The person you are talking to is the SPENDER: \
they decide what is needed. A SPONSOR funds them and sets the policy. That \
asymmetry is the whole job — the spender should not have to ask permission for \
every small thing, and the sponsor should not have to approve every small thing.

How you work:
- Memory is the point. Call recall_facts before answering anything about the \
person; an answer from memory beats a guess, and a guess presented as memory is \
the fastest way to lose their trust.
- Store what they tell you with remember_fact, so they only have to say it once. \
Store what they STATED, not what you concluded. If they say "I'm out of soap", \
that is a supply fact; if you decide they must be out of soap because it has \
been a while, that is not a fact and does not get stored.
- Never infer mood. If it matters, ask. If they tell you, store it — and like \
any fact, they can delete it.
- When they ask you to forget something, call forget_fact and confirm plainly. \
It stops influencing every future decision. Never argue about it.
- If they ask what you know about them, call recall_facts and tell them all of \
it, including where each item came from.

What you must not do:
- Do not invent facts, amounts, dates or prices. If you do not know, say so and \
ask.
- Do not moralise about their spending. You are not their parent, and the \
sponsor set the policy precisely so that you would not have to be.
- People other than the one you are speaking to appear as pseudonyms \
(person_3). Use them as-is; never guess who they are.

Be concise and plain. They are texting you, not reading a report."""


# Marks a run that never produced an answer. Kept in the answer column so the
# audit trail is complete without a schema change, and recognisable to any
# surface that must not render a failure as if it were a reply.
FAILED_PREFIX = "(agent run failed)"


def run(
    question: str,
    *,
    person_id: int,
    db_path: str | None = None,
    trigger: str = Trigger.ASK,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    """One full agent conversation. Returns the redacted answer, the evidence
    trail, any memory writes, and usage — everything already safe to display."""
    started = time.monotonic()
    redactor = Redactor.build(db_path=db_path)
    model = config.agent_model()
    safe_question = redactor.redact(question)
    run_id = store.insert_agent_run(
        person_id=person_id,
        trigger_kind=trigger,
        question=safe_question,
        model=model,
        db_path=db_path,
    )
    box = ToolBox(person_id=person_id, redactor=redactor, db_path=db_path, run_id=run_id)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": safe_question},
    ]
    tools_used: list[str] = []
    evidence: list[dict[str, Any]] = []
    tokens_in = tokens_out = 0
    answer = ""
    try:
        for _ in range(MAX_TURNS):
            reply = llm.complete(
                messages, box.specs(), denylist=redactor.denylist(), model=model, http=http
            )
            tokens_in += reply.tokens_in
            tokens_out += reply.tokens_out
            if not reply.tool_calls:
                answer = reply.content
                break
            messages.append(reply.raw_message)
            for call in reply.tool_calls:
                result = box.dispatch(call.name, call.arguments)
                tools_used.append(call.name)
                evidence.append({"tool": call.name, "arguments": call.arguments, "result": result})
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
                )
        else:
            answer = CAP_ANSWER
    except Exception as exc:
        # The run row already exists and the `finally` below writes it whatever
        # happens. Without this the row lands blank, and "the agent was invoked
        # and something went wrong" is not an audit trail. The reason goes
        # through the redactor like any other text.
        answer = f"{FAILED_PREFIX} {exc}"
        raise
    finally:
        # The answer is redacted a second time on the way to disk: it is model
        # output, and belt-and-braces is the house style at this boundary.
        safe_answer = redactor.redact(answer)
        store.finish_agent_run(
            run_id,
            answer=safe_answer,
            tools_used=",".join(tools_used),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000),
            db_path=db_path,
        )
    return {
        "run_id": run_id,
        "answer": safe_answer,
        # The person reading this is the person it is about, so their own name
        # goes back in. Everyone else stays a pseudonym.
        "display_answer": redactor.restore_for(safe_answer, person_id),
        "evidence": evidence,
        "writes": box.writes_log,
        "usage": {"model": model, "tokens_in": tokens_in, "tokens_out": tokens_out},
    }
