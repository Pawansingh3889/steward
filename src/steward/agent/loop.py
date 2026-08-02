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
from ..memory import episodic
from ..models import Speaker, Trigger
from ..spend.warden import Warden
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

Buying things:
- When they need something, call find_options and show them EVERY option with \
its price and delivery time. Then ask which one. Do not choose for them, do not \
drop the expensive or the slow option to be helpful, and do not assume cheapest \
is best — someone who has run out today may want the one that arrives tomorrow.
- Once they choose, call buy_offer with that offer_id and the price you showed \
them. The catalogue is a modelled FIXTURE; tell them that rather than implying \
these are real shops.

Plans and goals:
- When they want to save for something, call propose_plan. If the schedule does \
not reach, say so and read out ALL THREE ways to close the gap without picking \
one — later, smaller, or more each time is their call, not yours.
- A draft does nothing. Tell them to say "start that plan" — you have no way to \
start one, and saying you have would be a lie about a commitment.
- If a purchase would set an active goal back, the result says so. Mention it in \
one sentence and ask if they still want it. Do not refuse, do not lecture, and \
do not decide for them that the goal matters more than the thing.
- Flights and hotels always need a person to book. Never imply you will.

What you must not do:
- Do not invent facts, amounts, dates or prices. If you do not know, say so and \
ask.
- Do not pick an option on their behalf, even if one is obviously better. \
Finding the options and handling the money is your job; deciding is theirs.
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
    record: bool = True,
    http: httpx.Client | None = None,
    warden: Warden | None = None,
) -> dict[str, Any]:
    """One full agent conversation. Returns the redacted answer, the evidence
    trail, any memory writes, and usage — everything already safe to display.

    `record=False` runs without writing to the conversation log or episodic
    memory, for callers that are asking on the person's behalf rather than
    relaying something they said.
    """
    started = time.monotonic()
    redactor = Redactor.build(db_path=db_path)
    model = config.agent_model()
    safe_question = redactor.redact(question)
    # The log and episodic memory take the question as the person actually said
    # it. Redaction happens on the way to the model, not on the way to disk:
    # this is their machine and their words, and a memory that reads back
    # [redacted] protects nothing from anyone who can already open the file.
    # The run row opens first — before the turn, before the model — so that
    # everything written afterwards can point at it. That ordering is what makes
    # "which message caused this decision" answerable rather than inferred from
    # timestamps.
    run_id = store.insert_agent_run(
        person_id=person_id,
        trigger_kind=trigger,
        question=safe_question,
        model=model,
        db_path=db_path,
    )
    turn_id = None
    # Whether this turn is visible to the sponsor is the spender's standing
    # choice, read at the moment the turn is written. Deciding it later, at read
    # time, would retroactively expose everything said before they turned
    # sharing on — which is not what anyone means by "share this conversation".
    person = store.get_person(person_id, db_path=db_path)
    shared = bool(person and person["share_mode"] == store.SHARE_SHARED)
    if record:
        turn_id = store.insert_turn(
            person_id=person_id,
            speaker=Speaker.PERSON,
            text=question,
            shared_with_sponsor=shared,
            run_id=run_id,
            db_path=db_path,
        )
        episodic.remember(person_id=person_id, text=question, turn_id=turn_id, db_path=db_path)
    box = ToolBox(
        person_id=person_id, redactor=redactor, db_path=db_path, run_id=run_id, warden=warden
    )
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
    # The model's own view of the run: system prompt, the redacted question,
    # every tool call and result, and what it said back. Deliberately the
    # *sent* messages rather than the person's words — `turns` already holds
    # those, unredacted, because it is their machine. This holds what the model
    # was given, which is the only record that can answer "why did it decide
    # that" and is already pseudonymised by the time it gets here.
    #
    # Behind `record` with everything else: a caller that asked not to be
    # remembered did not mean "except the transcript".
    if record:
        store.insert_agent_transcript(run_id, json.dumps(messages), db_path=db_path)

    # Logged after the run succeeds, and as a turn only — never an episode. See
    # models.Speaker: an agent that embeds its own output starts recalling its
    # own guesses as though the person had said them.
    if record:
        store.insert_turn(
            person_id=person_id,
            speaker=Speaker.STEWARD,
            text=safe_answer,
            shared_with_sponsor=shared,
            run_id=run_id,
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
