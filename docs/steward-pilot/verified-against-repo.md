# Answers to the plan's `[verify against repo]` markers

The pilot plan was written without repository access — it is careful to mark
every claim it could not check. This file resolves those markers against the
code at `feat/pilot-plan`, and it is deliberately separate from the plan so the
two can disagree visibly if the code moves.

Where an answer needed a test rather than a paragraph, the test exists and is
named.

---

## Gate item 2 — does any money-moving path bypass pay-warden?

**No, and it is now enforced structurally rather than by inspection.**
See `tests/test_no_bypass.py`.

- `warden.request()` and `warden.release()` are the only calls that can produce
  a payment session, and `spend/purchase.py` is the only module that calls
  either. Asserted by walking the source tree, so a third caller fails the suite.
- Nothing anywhere assigns a `payment_url` a string literal — every one is a
  field copied off a `Decision` pay-warden returned. Asserted from the AST, and
  the check is itself tested against a planted violation.
- An unrecognised verdict raises rather than defaulting to allowed.
- An unreachable warden returns `verdict: "unavailable"` and no payment URL.
- Every attempt leaves a decision behind: a parked purchase writes an escalation
  even though no money moved.

### The injection vector is narrower than the plan assumed

The plan reasonably worried that reading structured price data from external
pages is a prompt-injection route into a payment. **Today it is not, because the
edge does not exist:**

- `integrations/prices.py` is reachable only from `cli.py` (`steward price`).
- `catalogue/search.py` — which is what prices a purchase — imports `fixtures`
  and nothing else.
- `agent/` imports no module from `integrations/` at all.

So a hostile page cannot influence an amount, because no fetched page reaches
the pricing path. Two further facts make the eventual wiring safer:

- `prices.read()` returns a `LivePrice` carrying exactly four fields — price,
  currency, source, availability. Product `name` and `description` are never
  read, so injected prose is discarded at the parser, not filtered later.
- `buy_offer` takes its authoritative amount from `search.quote()`, which
  re-validates against the catalogue and refuses if it moved.

**This is a temporary structural fact, and the tests are written to notice when
it stops being true.** `test_the_catalogue_reads_no_live_page` fails the moment
live prices are wired into the catalogue. That failure is the point: the fix is
not to delete the test but to re-validate a fetched price on the way in, and
then rewrite the test to assert *that*.

---

## §4 — do the prompts stop the agent editorialising about the spender?

**Partly. One half is enforced; the other half is a prompt instruction only, and
the plan should treat it as unverified until the gpt-5 gate runs.**

Enforced in code:

- The sponsor notification is assembled in `surface/router.py::_escalation_text`
  from stored fields — description, amount, merchant, and the policy's own
  `reason` verbatim. The model does not write it and cannot add to it.
- The sponsor sees no conversation unless the spender turned sharing on, and
  sharing is decided when a turn is *written*, so enabling it never
  retroactively exposes earlier messages.
- There is no tool by which the agent could send the sponsor anything else.

Not enforced, prompt only:

- `agent/loop.py::SYSTEM_PROMPT` says "Do not moralise about their spending. You
  are not their parent." Nothing tests that it obeys. This belongs in the gpt-5
  behavioural gate as an explicit scenario.

---

## §7 — what the event log already has, and what the pilot must add

Present, per request:

| plan asks for | where it is |
|---|---|
| request received | `turns` (speaker, text, timestamp) |
| warden consult + decision | `escalations` (rule_id, reason, status) and pay-warden's own audit |
| notifications sent | `Handled.replies`, and `Delivery` per message |
| outcome | `escalations.status` + `decided_ts` |
| agent reasoning trail | `agent_runs` (question, answer, tools_used, tokens, latency) |

### All three gaps are now closed

They were flagged as cheap now and unrecoverable later, so they were done rather
than scheduled. See `src/steward/pilot.py` and `tests/test_pilot_export.py`.

- **Pair ID.** `pilot.pair_id()` gives a stable `pair_<sponsor>_<spender>`, and
  every exported row carries it.
- **Thread/session join.** `turns.run_id` and `escalations.run_id` now exist, so
  a message joins to the decision it caused. The agent run row is opened
  *before* the turn is written, which is what makes the id available to point
  at. A turn with no run behind it — a deterministic router reply — carries 0
  rather than a borrowed id, so an unrelated decision can never look like its
  consequence.
- **A correction event.** A `corrections` table, written where a correction
  actually happens rather than reconstructed afterwards. It distinguishes
  `deleted_belief` (we held something wrong) from `rejected_proposal` (a model
  guessed and they said no), and **a superseded fact is not counted** — restating
  something tombstones the old row, and treating that as a correction would
  inflate the pilot's headline number with ordinary use.

`pilot.events()` returns the joined stream in time order and `pilot.summary()`
the weekly counts. Two properties are tested rather than intended: **no name,
phone number or email appears in any exported row**, and **message text is
excluded unless asked for** — an analysis file that carried somebody's private
messages is a different object from one that carries counts. `include_text=True`
exists for a participant exercising their right to see what is held.

Verified against a database created before these columns existed: columns added,
`corrections` created, rows preserved, export working.

---

## §5 — the onboarding runbook's dashboard steps

Confirmed against the live Linq API and dashboard on 2026-08-02:

- Opening a new chat from the API is **not possible with what is documented**;
  `POST /chats` rejects every body shape probed. The manual dashboard step in
  the runbook is therefore load-bearing, not a convenience.
- Once a chat exists, sending works and is verified end to end — one message
  delivered to a real handset over iMessage.
- The account's sending line is a **US number** (+1 206). The verified test
  recipient is UK. Cross-border iMessage worked; SMS fallback for a non-iMessage
  handset is **untested** and should be screened for, exactly as the runbook says.
- `Delivery.detail` reports the negotiated protocol, so the runbook's "confirm
  the blue bubble" step can be checked from the log rather than by eye.

---

## What is still genuinely unverified

- **The agent against a real gpt-5.** Every test drives a scripted stub. All
  behavioural claims in the plan — shows every option, relays denials
  unsoftened, never claims to have started a plan — are untested.
- **Opening a Linq chat programmatically.**
- **Google Calendar and Gmail**, which have no live token.
- **SMS fallback** for a recipient without iMessage.
