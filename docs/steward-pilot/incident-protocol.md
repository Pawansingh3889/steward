# Incident protocol — Steward pilot

Safeguarding lead: **[OPERATOR NAME]**, [CONTACT] — response within 24 hours.
Deputy (if the lead is a subject of the concern): [NAME], [CONTACT].

The kill switches (per-pair, global, participant STOP) are tested before the pilot
(plan gate item 4) and are the first tool in every category below. When in doubt,
pause first, investigate second — a paused pair loses a few days of data; an unpaused
incident can harm a person.

## Categories and required responses

### Category A — halt the whole pilot (all pairs, immediately)

- The agent attempts, or completes, any action without a warden approval — even in
  sandbox. This is the core safety invariant; one breach invalidates the safety case
  for everyone.
- The agent discloses one party's information to the other outside the designed
  surface (e.g., quotes or characterizes the spender's messages to the sponsor).
- The agent sends content that is harassing, or deceptive beyond an ordinary mistake.
- Any safeguarding incident involving coercion (see below).

Response: global kill switch → written incident note within 24h → root-cause before
any restart → all participants told the pilot is paused (no blame, no detail about
other pairs) → restart only when the cause is fixed and the fix is tested.

### Category B — halt the pair

- The agent misstates a decision or a rule to either party.
- A participant reports distress connected to the pilot.
- Evidence the system is being used inside a pair conflict (e.g., decisions cited as
  ammunition, rules changed punitively).
- Repeated operational failure for that pair (missed messages, wrong decisions).

Response: per-pair kill switch → contact the affected party (for spender-side concerns,
the spender first) → decide with them whether to resume, adjust, or end their
participation → incident note within 24h.

### Category C — log and monitor (no halt)

- Isolated confusing-but-harmless agent message, self-corrected.
- Message-cap or rate-limit triggers that paged the operator and were resolved.
- Participant question or complaint resolved on the spot.

Response: note in the pilot log; review weekly for patterns. Three Category C events
of the same kind in one pair escalate to Category B.

## Coercion and distress — the safeguarding path

If a spender reports pressure, fear, or discomfort involving the sponsor, or the
mid-point check-in raises concern:

1. Pause the pair (kill switch) without announcing why to the sponsor.
2. Talk to the spender first, privately. Their account decides the next step.
3. **Nothing is relayed to the sponsor without the spender's explicit agreement.**
   If asked, the sponsor is told only that the pilot has ended or is paused for their
   pair.
4. The pilot is not a substitute for real safeguarding services: if the spender
   appears to be at risk of harm, help them reach appropriate local support —
   [LIST LOCAL SERVICES / HELPLINES BEFORE THE PILOT STARTS] — and follow up.
5. Incident note within 24h, stored with restricted access.

## Incident note template

- Date/time, category, pair ID (pseudonymous), reporter.
- What happened (facts only), with event-log references.
- Immediate action taken, and when.
- Root cause (or "under investigation" with an owner and a date).
- Follow-up: participant outcome, fix shipped, restart decision.

## Weekly review

Every week during the pilot, the lead reviews: all Category C notes, all cap/limit
pages, and the event log's safety counters (plan §7). Anything trending gets acted on
before it becomes a Category B.
