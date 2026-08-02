# Onboarding runbook — one copy per pair

Print or copy this checklist per pair. Every box gets ticked, in order, or the step is
escalated — no steps from memory. Steps marked ⏱ get a timestamp so we can price the
manual new-chat workaround (plan §5). **[verify against repo: replace the placeholder
dashboard steps in Part C with the actual sequence.]**

Pair ID: ______  Sponsor ID: ______  Spender ID: ______  Operator: ______  Date: ______

## Part A — Before scheduling anything

- [ ] Screening done: both parties 18+, capacity to consent, willing to do the calls.
- [ ] Spender's number confirmed as iMessage-capable: test message sent **from the
      dashboard** to the spender's number and delivery confirmed (blue-bubble check).
      ⏱ sent: ______ confirmed: ______
      *If this fails, stop here — do not schedule the onboarding call.*
- [ ] Consent forms and privacy notice sent to both parties, at least 48h before calls.

## Part B — Consent calls

- [ ] Spender consent conversation held **without the sponsor present**.
- [ ] Spender comprehension check passed (see `comprehension-check.md`); record filed.
- [ ] Spender consent signed and stored.
- [ ] Sponsor consent conversation held; comprehension check passed; record filed.
- [ ] Sponsor consent signed and stored.
      *Both must pass before Part C. If either does not, stop.*

## Part C — Live setup (on the onboarding call, sponsor call)

- [ ] Sponsor account created; pair ID assigned; contact-to-ID mapping stored in the
      separate contact store (never in the event log).
- [ ] Sponsor's initial spending rules entered together on the call, and the sponsor
      shown exactly what their decision view looks like (screenshot walkthrough).
- [ ] Sandbox funding confirmed; verify NO real payment method is attached anywhere.
- [ ] ⏱ start of manual chat-opening: ______
- [ ] Dashboard: open new Linq conversation to spender's number. **[verify against
      repo — enumerate actual steps 1..n here]**
- [ ] Scripted first message sent (must contain the bot disclosure — see below).
- [ ] Spender confirms receipt on their handset (text or call confirmation).
- [ ] ⏱ end of manual chat-opening: ______  (log duration in the setup-time sheet)
- [ ] Spender sends one test request; steward replies; decision appears on the
      sponsor's dashboard. Full loop verified end-to-end.
- [ ] STOP tested: spender sends "STOP", agent confirms and goes silent; operator
      reactivates. Both parties shown that this works.
- [ ] Message caps confirmed active for this pair (plan gate item 5).

## Scripted first message (do not improvise)

> Hi [SPENDER FIRST NAME] — I'm steward, an automated AI assistant taking part in a
> pilot you signed up for with [OPERATOR NAME]. I'm not a person. I can check spending
> requests against rules [SPONSOR FIRST NAME] has set — using pretend money for now,
> so nothing real gets bought. Text me what you need, or text STOP at any time to
> switch me off. What would you like to try first?

## Part D — Close-out

- [ ] Both parties have the operator's direct contact saved.
- [ ] Calendar: mid-point check-ins (~week 3, each party separately) and exit
      interviews scheduled.
- [ ] Pair marked active in the pilot tracker; canary pairs flagged as such.
- [ ] This checklist filed.

## If anything in Part C fails

Do not improvise around a failed step. Pause, note what failed and where, and either
resolve it on the call or reschedule. A failed chat-opening on a fresh number is
exactly the data the setup-time log exists to capture — record it, don't hide it.
