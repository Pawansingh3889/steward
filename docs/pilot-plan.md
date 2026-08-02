# Steward pilot plan

A plan for the first pilot of steward (`Pawansingh3889/steward`, `feat/phase-0-scaffold`)
with real sponsor–spender pairs. Written against the project state as of 2026-08-02:
8 phases built, 470 tests; live-verified pay-warden policy decisions over MCP, Linq
iMessage delivery to a real handset, and structured-data price reading; **not** verified:
the agent loop against a real gpt-5 (all tests drive a scripted stub) and opening a new
Linq chat programmatically.

> This plan was drafted without read access to the steward repo, so anything that names a
> **The markers below are now answered** in `docs/steward-pilot/verified-against-repo.md`,
> written with repository access. Where that file and this one disagree, it wins.
>
> specific file, event type, or dashboard step is marked **[verify against repo]**. The
> shape of the plan doesn't depend on those details.

## 1. What this pilot is, and is not

Pilots run on sandbox money. Nothing real is purchased, so the pilot **cannot** measure
whether anyone is financially better off — those outcome claims stay with the simulation
in `docs/EVALUATION.md` and must never be blended with pilot findings.

What a pilot with real people *can* honestly measure:

1. **Comprehension** — can both parties accurately state what the agent can do, what the
   money is, and — the load-bearing one — *who sees what*. Steward shows the sponsor
   decisions but not conversations. That is a designed position on the spender's
   autonomy, and it only holds if both people actually understand it, not just if the
   software enforces it.
2. **Trust** — do people keep using it after the novelty fades, and does their stated
   trust match their behavior (usage, escalations, going silent).
3. **Correctability** — when the system does something a sponsor or spender disagrees
   with, can they notice it, understand why it happened, and change the policy so it
   doesn't happen again — and does the change visibly take effect.

Everything below is organized around measuring those three things safely.

## 2. Gate list — before any real person touches it

These are ordered by risk. Items 1–5 are hard blockers; 6–10 are required but small.

1. **Live-model burn-in.** The agent has never run against a real gpt-5; every one of the
   470 tests drives a scripted stub. That is the single biggest unknown, and it cannot be
   discharged by more stub tests. Before any participant:
   - Replay the existing scripted scenarios against the live model and diff behavior
     against the stub's expectations. Log every malformed tool call, every message the
     stub tests never anticipated.
   - Then a one-week two-operator role-play: you and one other person play sponsor and
     spender daily over the real Linq channel, including adversarial turns — asking the
     agent to bend policy, feeding it ambiguous requests, asking "are you a bot", trying
     to get it to speculate about the other party.
   - Written pass criteria before starting, e.g.: N sessions with zero attempts to act
     outside a warden approval, zero messages you would be uncomfortable showing a
     participant's parent, malformed-tool-call rate below a threshold. If it fails, fix
     and repeat; the pilot start date moves, not the bar.
2. **Warden as the last line, verified.** The pay-warden is live-verified for decisions;
   verify the stronger property that **no money-moving action path exists that bypasses
   it** — that the agent process literally cannot execute a payment without a warden
   approval token, regardless of what the model says. This matters doubly because the
   agent reads structured price data from external sources: a hostile or malformed page
   is a prompt-injection vector, and the defense you can actually rely on is the action
   gate, not model behavior. Add one test that tries to drive a payment from injected
   page content and confirms the warden blocks it. **[verify against repo]**
3. **Adversarial loop validation of the approval layer.** Automate the pressure that
   items 1–2 apply by hand: an adversarial driver (scripted mutations, optionally a
   second LLM playing attacker) runs the real agent against the real warden through high
   volumes of hostile scenarios — policy edge cases, injection payloads embedded in
   price pages, malformed tool-call sequences, retry storms. After every iteration the
   harness asserts the invariant: **zero action-layer executions without a warden
   approval token, and a warden decision logged for every attempt.** One violation fails
   the gate. Run the same loop against live gpt-5 to cheaply accumulate
   malformed-tool-call and policy-probe rates, which become the quantitative pass
   criteria for the burn-in in item 1 — the human role-play week then only has to cover
   what needs judgment (message tone and appropriateness). Hygiene: stub the messaging
   transport (never loop over real Linq/iMessage — rate limits and spam flags would
   burn the verified channel), tag loop traffic with synthetic pair IDs so it can't
   pollute pilot analytics, and have the loop assert on the **same safety counters the
   pilot instruments (§7)**, so "zero action-layer bypasses" is one continuous metric
   from pre-pilot testing through the live pilot.
4. **Kill switch, tested.** One operator action that silences the agent for a single pair,
   and one that halts everything. Both parties must also be able to stop it themselves:
   texting `STOP` (or plain "stop") ends the agent's participation for that pair within
   one message. Test all three paths before day one.
5. **Runaway limits.** Sandbox money makes overspend harmless, but messages are real: a
   loop that texts a participant 200 times is a real harm. Cap outbound messages per pair
   per day, cap warden consultations per request, and page the operator instead of
   retrying past the cap.
6. **Bot disclosure.** The agent identifies itself as an AI in its first message and
   whenever asked. Scripted, not left to the model.
7. **New-chat workaround runbook, rehearsed.** See §5. Test the full manual flow on a
   fresh phone number you've never messaged before — not the handset that already works.
8. **Deletion drill.** Run one fake participant end-to-end through the withdrawal path
   (§6) and verify the event log, Linq conversation, and any backups actually end up in
   the state the privacy notice promises. Do this before the notice is shown to anyone.
9. **Consent and safeguarding materials** (§4): consent forms ×2, privacy notice,
   comprehension-check script, incident protocol with a named safeguarding lead.
10. **Event-log gaps** (§7): per-pair pseudonymous IDs, a way to join message events to
   decision events, and (ideally) a correction event. Small engineering, big analytical
   payoff. **[verify against repo — some of this may exist]**

## 3. Recruiting: 5–10 pairs

- **Unit of recruitment is the pair.** Both people consent independently or the pair
  doesn't enter. Recruit ~12 pairs expecting attrition to land in the 5–10 range.
- **Eligibility, first pilot:** both parties 18+, both with capacity to consent, spender
  has an iPhone with working iMessage (blue-bubble check during screening — send a test
  message from the dashboard *before* scheduling onboarding, since the new-chat flow is
  the fragile step). Both willing to do an onboarding call, a mid-point check-in, and an
  exit interview.
- **Explicitly excluded from this pilot: spenders with reduced capacity.** That is a core
  intended population, and precisely why they should not be in the first pilot: piloting
  on people who may not be able to consent to an experimental system requires a dedicated
  protocol, capacity assessment, and independent ethics input that a 5–10 pair
  feasibility pilot doesn't have. Say this in the writeup as a scoping decision, and
  treat "design the reduced-capacity protocol" as a named follow-on, not a footnote.
- **Mix:** mostly parent/young-adult pairs (the primary dynamic), plus 1–2 partner or
  friend pairs as contrast — if the trust findings differ sharply by relationship type,
  that's worth knowing before building further.
- **Source:** one hop out from your network (friends-of-friends, a local parents' group)
  rather than close friends — close friends are too polite to give you the distrust data
  the pilot exists to collect.
- **Compensation:** pay both parties for their time (flat amount per person, paid
  regardless of how much they use the system). Be explicit that sandbox money is not
  compensation and nothing the agent "buys" is real — set this expectation in the
  recruiting message, not just the consent form, or you will recruit people who want the
  goods.

## 4. Consent and safeguarding

Delegated spending between a parent and a young adult is a safeguarding question before
it is a product question. The system's visibility design — sponsor sees decisions, not
conversations — is a stance on the spender's privacy, and the pilot itself can become an
instrument of surveillance or control if run carelessly. Concretely:

- **Two consents, two conversations.** The spender's consent conversation happens without
  the sponsor present (a separate call or a private segment of the onboarding call). A
  spender saying yes in front of the parent who controls the money is not consent you can
  rely on. Either party can withdraw at any time without giving a reason; either
  withdrawal ends the pair's participation, and the agent never tells the other party
  more than "the pilot has ended for you both."
- **Consent content, in plain words:** what the agent can and cannot do; the money is
  sandbox and nothing real arrives; who sees what — stated symmetrically to both parties
  ("your sponsor sees each decision and its reason; they do not see your messages") —
  the system is AI and will make mistakes; how to stop it instantly; what data is held,
  where it goes (Linq, Apple, OpenAI), and how to withdraw it; who to contact.
- **Comprehension check as a gate and a baseline.** Before activation, each party
  answers, in their own words: (1) who can see your/their messages? (2) is the money
  real? (3) how do you stop it? (4) who do you contact if something feels wrong? Wrong
  answers get corrected and re-asked; a pair activates only when both pass. Since the
  pilot's headline measure *is* comprehension, this doubles as the baseline measurement.
- **Private mid-point check-in with each party separately** (15 min, ~week 3). The
  spender's check-in asks directly: is anything about this uncomfortable? What do you
  think your sponsor can see? Has the system been used to pressure you? The gap between
  what the spender believes the sponsor sees and what the sponsor actually sees is one
  of the pilot's most important results.
- **Coercion / distress protocol.** If a spender reports pressure, distress, or the
  check-in raises concern: pause the pair (kill switch), talk to the spender first,
  don't relay anything to the sponsor without the spender's agreement. Named
  safeguarding lead (you), response within 24h.
- **Incident categories and halt criteria, written down in advance:**
  - *Halt the pair:* agent misstates a decision or policy to either party; participant
    reports distress; any use of the system in a pair conflict.
  - *Halt the pilot:* agent attempts an action without warden approval; agent sends
    content that is harassing, deceptive beyond a simple mistake, or discloses one
    party's information to the other outside the designed surface; any safeguarding
    incident involving coercion.
  - Every incident gets a written note (what happened, event-log refs, action taken).
- **The agent's own conduct rules:** it reports decision facts to the sponsor and
  nothing editorial about the spender ("request denied under rule X", never "they seem
  to be asking for a lot lately"). Verify the prompts and sponsor-notification templates
  enforce this. **[verify against repo]**

## 5. Onboarding around the new-chat gap

A spender can currently only be reached if a Linq conversation already exists, opened
manually from the dashboard. Plan assumes this is **not** fixed:

- **Fold the manual steps into the onboarding call.** Each pair gets a 30-minute
  scheduled call that you need anyway for consent and the comprehension check. During
  it, the operator opens the Linq conversation from the dashboard live, sends the
  scripted first message (which carries the bot disclosure), and confirms receipt on the
  spender's handset before the call ends. Ten manual steps × 10 pairs is an hour of
  total operator time — acceptable at this scale, dangerous only if done from memory.
- **Write it as a per-pair checklist runbook** — every step checked off per pair, so a
  missed step is visible instead of silent. **[verify against repo — enumerate the
  actual dashboard steps]**
- **Screen for the failure mode early.** iMessage deregistration, Android, or a number
  that won't resolve should surface at screening (the blue-bubble test message), not on
  the onboarding call.
- **Log setup time per pair.** That number is itself pilot data: it prices the new-chat
  gap and tells you whether fixing it is a real priority or a vanity fix.
- **Stagger activation.** Onboard 2 pairs in week 1 and let them run for a week before
  the rest. They are the canaries for the live-gpt-5 risk that the burn-in (§2.1) can
  reduce but not eliminate. Only proceed to the remaining pairs if week 1 produces zero
  pilot-halt incidents.

## 6. Data: what is held, where it goes, how someone leaves

**Inventory** (disclose all of it in the privacy notice):

| Data | Where it lives | Notes |
|---|---|---|
| Spender↔agent messages | Linq, Apple iMessage, steward DB, OpenAI API | OpenAI processes content; state their retention terms |
| Decisions, events | steward event log | Pseudonymized per-pair ID |
| Policies set by sponsor | steward DB | Sponsor-authored content |
| Prices / structured data fetched | steward DB / logs | External page content |
| Names, phone numbers | Separate mapping store | Never in the event log |
| Interview notes, check-in notes, recordings | Operator's storage | Recordings only with explicit consent |

- **Minimization:** the event log carries pseudonymous pair/party IDs only; the mapping
  of IDs to phone numbers and names lives separately. No real payment credentials exist
  anywhere (sandbox only) — keep it that way for the pilot's whole life.
- **Retention:** raw message content deleted 30 days after pilot end; pseudonymized
  event log and analysis retained. Interview recordings deleted after transcription.
- **Withdrawal** (either party, any time, no reason needed): text STOP to the agent or
  message the operator. Within 24h: agent stops messaging, pair deactivated. Withdrawal
  from participation is offered separately from **deletion of already-collected data** —
  a participant can leave and let existing data stand, or leave and have message content
  and identifiers deleted. Be honest about the limits: you cannot delete iMessage
  history from participants' own phones, and Linq/OpenAI retention follows their
  policies — say so rather than over-promising.
- Run the deletion drill (§2.8) before the first participant is onboarded.

## 7. Instrumentation

The event log already exists; the pilot needs it to answer "did people understand,
trust, and correct this," so instrument the correction loop, not just the request loop.

**Per request (likely mostly present — [verify against repo]):** request received →
price read(s) → warden consult → decision (approve / deny / refer-to-sponsor) →
notifications sent → outcome, all timestamped with pair ID and a thread/session ID that
joins message events to decision events.

**Corrections — the key additions:**
- Sponsor policy edits: what changed, and how long after which decision. A policy edit
  within hours of a denial or approval is the behavioral signature of correctability —
  if the linkage can't be captured in the log, capture it in interviews, but the log is
  cheaper and unbiased.
- Sponsor overrides of individual decisions, if the product has them.
- Spender retries: a rephrased request shortly after a denial. This is either learning
  the policy or probing it — both are findings, and interviews disambiguate.
- Explanation events: spender asks "why was that denied" / "what am I allowed"; whether
  an explanation is followed by a successful reformulated request.

**Trust proxies:** requests per pair per week (does usage survive novelty), time to
first unprompted request, silence (a pair that stops using it has told you something —
follow up in the exit interview, don't just average them away), escalations to the
operator.

**Safety counters:** warden denials of agent-initiated actions (should be > 0 at the
decision layer and **exactly 0** at the action layer), malformed tool calls from the
live model, message-send failures, per-pair message-cap hits, human interventions.

**Qualitative instruments** (the log cannot measure understanding directly):
- Baseline: the onboarding comprehension check (§4), scored.
- Mid-point: separate check-ins; the "what do you think the other person can see?"
  question asked to both sides independently.
- Exit interview per person (separate, 30 min): re-run the comprehension questions
  (did understanding improve or decay?), three scenario probes ("if it bought the wrong
  thing tomorrow, what would you do?" — the answer reveals whether correctability is
  understood), and the same 4–5 item trust scale used at baseline.
- Weekly prompted tasks: because the money is fake there is no organic demand, so seed
  it — e.g. week 2: "spender, request something you expect to be denied"; week 3:
  "sponsor, change one policy and don't tell the spender — we'll see if they notice."
  Prompted use is honest as long as the writeup says which behavior was prompted.

## 8. Timeline

| Week | What |
|---|---|
| 0 (–1?) | Gate list §2: burn-in, warden bypass check, kill switch, runbook rehearsal, deletion drill, materials. Takes as long as it takes — the start date moves, not the bar. |
| 1 | Onboard 2 canary pairs. Daily log review. |
| 2 | Zero halt-incidents → onboard remaining pairs. Weekly prompted task begins. |
| 3 | Mid-point separate check-ins. Policy-change-without-telling task. |
| 4 | Last week of use; exit interviews in the final days. |
| +1 | Analysis, writeup, data deletion per retention promises. |

Four weeks of participant exposure: long enough for novelty to wear off (the trust
signal is week-3-and-4 usage, not week-1), short enough that sandbox-money artificiality
doesn't fully rot engagement. Operator load: daily event-log review (15 min), plus
onboarding/check-in/exit calls — roughly 25 calls total across the pilot.

## 9. What can honestly be claimed at the end

**Claimable, if the data supports it:**
- Comprehension: "N of M pairs could accurately state, at onboarding and again at exit,
  what the agent does, that the money was not real, and who sees what" — including
  where the two parties' beliefs about visibility diverged.
- Correctability: "sponsors changed policy in response to specific decisions X times;
  changes took effect; spenders noticed unannounced changes in K of L pairs."
- Operational: "the live agent handled X requests over 4 weeks with Y operator
  interventions and zero action-layer warden bypasses."
- Qualitative: how the decisions-visible / conversations-private asymmetry felt from
  each side of a real relationship.

**Not claimable, and the writeup should say so explicitly:**
- Any financial benefit or improved outcomes. Outcome claims remain simulation-only
  (`docs/EVALUATION.md`); pilot behavior and simulated outcomes are separate evidence
  classes and never merge into one sentence.
- Real-stakes trust. Willingness to delegate sandbox money is weak evidence about real
  money; the exit interview can *ask* "would you connect a real card?" but the answer
  is a stated preference, not a measurement.
- Anything about spenders with reduced capacity — excluded population, dedicated
  follow-on protocol required.
- Generalization: 5–10 self-selected pairs from one social graph, several behaviors
  prompted. This is a feasibility-and-comprehension pilot, not an efficacy study, and
  its title should say so.

**The most valuable possible outputs**, worth designing the interviews toward: the
divergence between what spenders believe sponsors can see and reality; the first real
correction loop observed end-to-end; and a priced answer to whether the new-chat gap
needs engineering before pilot #2.
