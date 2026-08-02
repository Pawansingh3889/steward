# steward

An agent that spends someone else's money well.

Most agentic-commerce demos are "an agent buys me socks" — but I can buy my own
socks, so the agent is a novelty. steward starts from an asymmetry that makes the
agent necessary: **the person spending and the person paying are different
people, and the payer cannot be in the loop for every purchase.** A student on an
allowance, a parent whose child manages their money, anyone with delegated funds.

The spender texts steward about what they need. steward remembers them, finds
options, and puts every spend through a policy engine the sponsor authored. The
sponsor sees decisions, the ledger and escalations — never the conversation,
unless the spender chooses to share a turn.

## Status

**All 8 phases built.** 535 tests + 19 opt-in, ruff clean.

```
$ steward evaluate

  strip every advantage → arms identical: passes

  by household  (forgetfulness 0.5)          without     with   verdict
    comfortable                                12.17     0.00   with_agent
    tight                                      12.17     0.00   with_agent
    overreaching                               12.17    44.47   without_agent
    precarious                                 12.17     0.00   with_agent
```

Three of four households reach zero stockout-days. The fourth is worse with the
agent, and the write-up says why rather than averaging it away —
**[docs/EVALUATION.md](docs/EVALUATION.md)**, including what it does not show.

```
$ steward plan propose --name Lisbon --kind trip \
    --target-cents 60000 --finish 2026-11-02 --per-period-cents 5000

     1  Lisbon  [draft]  short £450.00 GBP
        £50.00 GBP a month × 3 → £150.00 GBP of £600.00 GBP by 2026-11-02

  three ways to close the gap  (your call, not mine)
        take_longer      £600.00 GBP at £50.00 GBP by 2027-08-02
                         9 more months at the same amount
        smaller_goal     £150.00 GBP at £50.00 GBP by 2026-11-02
                         what this schedule actually reaches by then
        more_each_time   £600.00 GBP at £200.00 GBP by 2026-11-02
                         keep the goal and the date, and put aside more each time

it does nothing until you start it:  steward plan activate --id 1
```

Two humans still transact by text — phase 5's flow is unchanged.

```
Ana texts her line  +447700900002
    I'm out of soap, can I get some?

  → Ana Whitfield
    Asked Rae about that one — I'll let you know.

  → Rae Whitfield                                    (her own line)
    Ana Whitfield wants hand soap, 2 x 500ml — £25.00 GBP from Everyday Goods.
    Policy: 25 GBP exceeds auto-approval threshold 20.00 GBP; a human must release it
    Reply YES or NO (#1).

Rae replies  +447700900001
    yes

  → Rae Whitfield        Approved — hand soap, 2 x 500ml.
  → Ana Whitfield        …is approved. Finish it here: https://sandbox.collect…
```

Rae never sees the conversation — only what she was asked to decide.

```
$ steward shop soap

options for 'soap'  [FIXTURE catalogue]

  cornershop:cs-soap-1   Hand Soap, 500ml               £4.20 GBP
                         Corner Shop Express · arrives tomorrow
  everyday:ev-soap-2     Hand Soap Refill, 2 × 500ml    £3.80 GBP
                         Everyday Goods · arrives in about 2 days
  bulkline:bl-soap-2     Hand Soap, 2 × 500ml           £3.20 GBP
                         Bulkline Direct · arrives in about 3 days
```

```
$ steward memory list --person 2

what steward knows about Ana Whitfield

facts  (these drive decisions)
     1  supply     soap               out since Tuesday
        stated · since 2026-08-02T11:56:29+00:00

things you've said  (context only, never grounds to spend)
     1  I'm completely out of soap and shampoo

forget any of it:  steward memory forget --fact ID   (or --episode ID)
```

## The privacy boundary

Raw email, calendar and location never leave the device. Extraction happens
locally — deterministic parsers for the formulaic majority, a local model for
free-form text — and only *facts* cross to the model: not "here is the email",
but "a delivery arrives Tuesday".

That boundary is enforced in one place. `agent/llm.py` serializes the outgoing
request body, scans it against a denylist built fresh from the database and the
environment, and raises **before** the request is sent. A redaction bug upstream
becomes a loud error, not a person's life in a third party's logs. Names, phone
numbers, email addresses, card-like digit runs and coordinate pairs are all
covered; other people always appear to the model as `person_3`.

Memory is listable and deletable. "Forget that" tombstones the item so it stops
influencing every future decision, and a delete that matched nothing raises
rather than reporting a success it did not achieve.

Crucially, **deletion does not route through the model.** `steward memory list`
and `steward memory forget` read and write memory directly and work with
`OPENAI_API_KEY` unset. If the only way to delete something were to ask the
agent nicely, deletion would be a request rather than a guarantee — and this
product is asking someone to hand over their errands, their schedule and their
moods on the strength of that guarantee.

## A sponsor's dashboard, whose subject is what it will not show

```bash
make seed                 # a household to look at, offline and instant
make serve PERSON=1       # http://127.0.0.1:8787
```

The boundary above is the product, and in a terminal it is invisible: a command
nobody can run leaves nothing on screen. So there is one read-only page per
sponsor, and the panel it is built around draws the absence — the conversation
withheld on one side, and beside it the turns the spender chose to share.

**It has no login, and that is safe for two structural reasons rather than one
convenient one.** Nothing on it writes: approving is still `steward approvals
approve --id N`, because a release button on an unauthenticated page would make
the policy engine's escalation a formality. And the household is bound at
process start, so no route takes a person id — there is no authorisation check
to delete, because no handler is ever given anything to check. Editing the URL
reaches a 404 that says so. `serve --person` refuses a *spender*, which is the
one way the scope can be wrong while still looking right.

`store.shared_turns()` is the only turn reader the whole package may call — not
a filter over a wider read, a different function — and a test walks the syntax
tree of every file in `web/` to prove none of the others appear. The panel also
never says *how much* it is withholding: "14 messages you cannot see" is itself
conversation metadata, and volume and timing are most of what a message log
tells you about somebody.

`/ledger` reads pay-warden's own audit database live, one spender at a time,
because that database is shared by every agent it has ever answered for. It is
the only route that spawns a process, it degrades to a panel carrying the real
error when pay-warden is unreachable, and it renders no payment link: the link
goes to the spender, not to the sponsor who approved it, so showing it here
would contradict the thing the page exists to demonstrate.

Rendered from Python with `html.escape` and no template engine. A test walks
every f-string in `web/` that contains a `<` and fails on any interpolation
that did not go through an escaper — the naming convention `*_html` is what
makes "this is markup, not data" checkable rather than remembered.

## Nothing a model guessed becomes a belief on its own

Deterministic parsers handle the formulaic majority — bank alerts and `.ics`
files are written by machines to a specification, so a parser reads them
exactly, offline, identically every time. Free-form text goes to a **local**
model (Ollama), and what it produces lands *pending*: visible to you, invisible
to `recall_facts` and therefore to the frontier model, until you confirm it.

```
$ steward memory list

waiting for you  (a model read these; nothing sees them until you say so)
     2  identity   delivery_address   42 Wharf Lane, Salford M5 3EX
     1  schedule   boiler             boiler service Thursday
        steward memory confirm --fact ID   ·   or forget --fact ID
```

That address is why. A street address written in prose has no reliable syntax,
so no redaction pattern will ever catch one — the only defence that works is
that nobody guessed it into memory unattended. There is deliberately **no tool**
for confirming: an agent confirming its own guesses would make the whole
mechanism a formality.

Confirming promotes a proposal to `stated`, because you have now asserted it.
A confirmation timestamp is kept so "you typed this" and "a machine read it and
you agreed" stay distinguishable.

The local model is also the one component that sees unredacted text, so
`OLLAMA_BASE` is checked: a non-loopback host is refused unless you opt in
explicitly, and the error says exactly what you would be agreeing to.

## Steward never decides whether a purchase is allowed

Permission belongs to [pay-warden](../pay-warden): a separate process, reached
over MCP stdio, evaluating a YAML policy the sponsor wrote. It holds the Prava
credentials, so a bug in steward's reasoning cannot reach them, and it is pinned
to `mcp` 1.x while this is on 2.x — the protocol is the contract between them
rather than a shared import. *(Verified against the real subprocess, not a
stub: `tests/test_warden_integration.py`.)*

Three outcomes, and the middle one is why this product exists:

```
$ steward spend preview --description "hand soap" --amount-cents 450 …
  allowed  [pass]
  All policy rules passed

$ steward spend preview --description "winter coat" --amount-cents 2500 …
  needs_approval  [human-approval]
  25 GBP exceeds auto-approval threshold 20.00 GBP; a human must release it
```

A parked purchase writes an escalation for the sponsor, who sees it with
`steward approvals list` and releases it with `approve`. The spender was not
refused; they are waiting. A denial is relayed **verbatim with the rule that
fired** — an agent that editorialises "over your limit" into "I couldn't find
that" teaches people the system is broken rather than that a limit exists.

The model can request. It cannot approve: there is no approval tool, only a CLI
command the sponsor runs. An agent able to release its own escalations would
make the policy a suggestion.

Two things fail closed. An unrecognised verdict is never read as permission,
and a policy engine that cannot be reached is not permission either. Relatedly,
**enrolling someone in steward is not enough to let them spend** — they must
also appear in pay-warden's policy as `steward:person_<id>`, or every request is
denied with `unknown-agent`. Surprising the first time; correct.

## The agent finds the options; the person picks

Autonomy lives in enforcement and payment, not in selection. `find_options`
returns **every** match with price and delivery — never a shortlist of one — and
the tool that spends money takes an `offer_id`, so buying is a separate act from
searching. An agent that quietly bought the cheapest would be making a values
judgement on someone's behalf: cheapest is not best when you have run out today.

Every option is on the Pareto frontier — cheaper, or faster, or both. A test
enforces it, and it caught the first draft of the catalogue, where one supplier
happened to be cheapest *and* fastest for soap and there was no decision to make.

**The model does not set the price.** `buy_offer` re-reads the price from the
catalogue and refuses if it differs from what the person was shown — canibuy's
`pricestage.py` idea, for the same reason: the number reaching the policy engine
must be the number the merchant will charge. A misremembered £4.05 fails loudly
instead of quietly becoming what someone pays.

Delivery is modelled from the supplier's location and the person's, locally.
Coordinates live in two columns read by one module; what the model is told is
"arrives in about 2 days". With no location set, offers say *delivery time
unknown* rather than guessing — and that answer sorts last, not first.

The catalogue is modelled and labelled `FIXTURE` on every surface. That is the
honest form of a real finding: canibuy graded the open web for agent-readiness
and the best merchant scored **C**, most scored **F**, and none sell household
essentials. Phase 7 swaps live fetching in for any merchant that grades well.

## Two lines, and what crosses between them

The spender texts about what they need. The sponsor's line carries approvals
and policy and **nothing else** — that separation is the product, because it is
what lets a sponsor stay out of the day-to-day without losing the say they care
about. The approval link goes to the spender, not the sponsor: they said yes,
but it is still the spender's errand and their passkey.

Three routing rules are security properties, not conveniences:

- **An unknown number gets silence.** Not an error, not "who is this?" — any
  reply confirms to a stranger that this number moves money.
- **A sponsor can only decide their own household's escalations**, so a number
  cannot approve someone else's spending by guessing an id.
- **A bare "yes" with two things pending asks which.** Guessing would be
  guessing with somebody's money. And only the *first* word of a reply counts:
  scanning for a yes-word anywhere would approve a purchase on the strength of
  "fine" in the middle of a sentence.

Nothing is shared by default. The spender says "share this" or "keep this
private", and it applies to what is said from then on — turning sharing on does
not retroactively expose what came before, which is not what anyone means by
sharing a conversation. There is no tool for it: an agent that could open
someone's conversation to their sponsor would make the setting decorative.

Messaging is an adapter. `RecordingChannel` is a first-class implementation, so
the whole two-line flow rehearses on one machine with no provider at all — which
is what keeps Linq's sandbox (expiring 9 August 2026) from being able to stop
anyone managing their money. **The Linq adapter is dry-run unless
`STEWARD_LINQ_LIVE=1`.** A text reaches a real person and cannot be recalled; an
integration that went live merely because a token was present is how a test run
becomes a message to somebody's parent.

**Verified live on 2026-08-02** — one message delivered to a real handset over
iMessage. What that bought was discovering the adapter written from a documented
guess was wrong in *every particular*:

```
guessed   POST /messages   {"to": …, "from": …, "body": …}
actual    POST /chats/{chat_id}/messages
                           {"message": {"parts": [{"type": "text", "value": …}]}}
```

Messages address a **chat**, not a number, and the chat carries the protocol —
the sandbox conversation negotiated iMessage, so it arrived as a blue bubble.
Opening a *new* chat is still unverified: `POST /chats` rejects every body shape
probed against it, so `send` refuses clearly rather than guessing.

## Letting someone spend

Enrolling a person is not enough — pay-warden denies any agent its policy has
never heard of. `steward spend grant` registers them:

```
$ steward spend grant --person 2 --daily 50.00 --per-purchase 30.00
registered Ana Whitfield in household.yaml as steward:person_2
the file is the source of truth — read it, and edit it by hand any time.
```

It edits the YAML in place with the stdlib, preserving comments and ordering —
`yaml.safe_dump` would strip every note the sponsor wrote, which for a policy
file is most of its value. It invents no limits (a default budget chosen by a
program is a decision about someone's money that nobody made) and refuses to
re-grant rather than overwriting limits that may have been hand-edited since.

## Plans: shaped by the person, advisory to steward

A savings schedule is three numbers and a rhythm — **how much**, **by when**,
**how much each time**. Fix any two and the third follows; that is the whole
interaction. When it does not add up, it says so and offers three options with
identical shapes and no recommendation, because which of *later*, *less* or
*more each time* is right depends on what the goal is for and how tight the
money is — neither of which steward knows.

A draft does nothing. **Only a person activates a plan** — by CLI, or by texting
"start that plan" — and the model has no tool for it, exactly as it has none for
confirming a fact or approving a purchase. It has none for abandoning one
either: releasing a commitment is the same authority as making it. The start
date is not the model's to choose, because a backdated plan reports progress on
money nobody put aside.

**An active plan warns; it never blocks.** steward has no bank access, so a plan
is a promise about money it does not hold. When a purchase would set a goal
back, the result carries what it costs — "1 month, November becomes December" —
and pay-warden still makes the actual decision. Nothing edits a sponsor's policy
to protect a spender's goal.

Flights and places to stay always need a person to book. That flag is computed
in `store.py` from the item kind, so no layer above can clear it, and **nothing
in `plan/` imports `spend/`** — there is no code path from a plan item to a
payment at all, which a test enforces by reading the imports.

## Real accounts, and how little of them is kept

`integrations/` fetches and hands straight to `extract/`. Nothing there
interprets, nothing stores raw material, and **`agent/` imports none of it** — a
test reads the imports — so there is no path from somebody's inbox to a model
prompt. Read-only scopes and read-only code: no send, no delete, no calendar
write, asserted by a test that greps the module's own names.

A Google Calendar event becomes the *same* `Event` type a `.ics` file produces
and goes through the *same* `candidates_from`, which is the one place deciding
what a calendar may contribute. So a second calendar source cannot arrive with a
second, laxer policy about locations and attendees — a test proves both sources
yield an identical fact from the same trip.

Then the connective bit, and phase 7's point:

```
$ steward trip --target-cents 60000

from your calendar: Family holiday in Lisbon departs 2026-09-12
     1  Family holiday in Lisbon  [draft]
        £150.00 GBP a month × 4 → £600.00 GBP by 2026-09-12
```

The calendar supplies the deadline; the person supplies what it is worth, and
still starts it. Reading a holiday off a diary and committing money against it
would be deciding, from a calendar entry, that somebody is definitely going.

## Live prices, and what canibuy's grades actually predict

`steward price <url>` reads **structured data only** — JSON-LD, then microdata.
It never scrapes a number out of visible text, because a number nobody promised
ends up in a policy decision and then on somebody's card. When a page does not
publish one, the answer is *no price*, and the merchant stays modelled.

Run against merchants canibuy graded (`STEWARD_LIVE_PRICES=1`, 2026-08-02):

```
adafruit.com          C    $35.00 via json-ld, InStock
sparkfun.com          C     $7.50 via json-ld
bluebottlecoffee.com  F    loads, no structured price → stays modelled
```

The grades still describe reality, checked from the other direction. That is
kept as an opt-in test, so if it ever flips the argument for a modelled
catalogue gets revisited rather than repeated.

Reaching the network from the test suite requires `@pytest.mark.live` — the only
way past the no-network guard, written on the test itself, so grepping for it
lists every test that can.

## Asking for money back

`steward refund request` records a claim in the person's **own words, verbatim**
— a model must not summarise a complaint, because the paraphrase is a different
complaint and this is the text a merchant might read. It is anchored to a
pay-warden attempt id, so it refers to something that actually happened.

steward does **not** contact the merchant, open a dispute, or speak to a bank,
and it says so on every request. Somebody who thinks a claim has been filed will
not chase it themselves. There is no refund tool for the model, for the same
reason there is none for approving a purchase.

## Two kinds of memory

**Facts drive decisions; episodes are colour.** A fact is structured, keyed and
singular — `schedule/hours = 9-5 weekdays` — and the agent may act on one. An
episode is a sentence someone said, retrieved by resemblance, and it exists so
the agent sounds like it was listening. Nothing that spends money reads from
episodic memory, and the tool description tells the model so.

Embedding is local and dependency-free: a hashed bag of words, `blake2b` so
vectors survive a restart. It is a *lexical* matcher — "out of soap" will not
match "need to restock hand wash" — which is written down rather than glossed,
because the alternative is sending every conversational turn to an embeddings
API and trading the privacy argument for better recall on the feature that
matters least. The `Embedder` protocol is the seam for pointing this at the
same local Ollama the extractor uses, which would understand that soap and hand
wash are the same errand — still without anything leaving the device.

## Layout

```
src/steward/
  config.py        the only module that reads os.environ
  store.py         every SQL statement; people, facts, episodes, turns, audit
  models.py        shared vocabularies (roles, fact kinds, speakers, triggers)
  cli.py           the direct interface to your own memory
  extract/
    ics.py         calendars, minus the fields that would hurt
    bank.py        alerts the person already received
    local.py       Ollama, and the check that keeps it local
    eta.py         delivery days from coordinates that never leave
    pipeline.py    parsers first, the model for what is left
  memory/
    embed.py       local, dependency-free vectors
    episodic.py    what was said, searchable by resemblance
    recall.py      "what do you know about me?", answered once for all surfaces
  catalogue/
    fixtures.py    the modelled storefront, and why it is modelled
    search.py      offers, ordering, and price integrity at purchase
  evaluation/
    world.py       two arms, three separable advantages, one shared world
    metrics.py     the primary metric, fixed before the first run
    report.py      a curve and a per-household table, not a headline
  integrations/
    google.py      read-only calendar and mail; fetch, never interpret
    prices.py      structured data only; None rather than a guess
    sync.py        pull into extract/, and a trip plan from a diary
  plan/
    schedule.py    the arithmetic; honest when it does not add up
    goals.py       drafts, activation, and what a purchase costs a goal
  spend/
    warden.py      the MCP client to pay-warden; the only path to money
    purchase.py    blocked / escalated / approved / paid
    grant.py       registering a spender in the sponsor's policy
  surface/
    base.py        channels, and reading a one-thumb reply
    router.py      who sent this, and what happens next
    linq.py        real phones, dry-run unless told otherwise
  agent/
    llm.py         the one place this system talks to OpenAI
    privacy.py     pseudonyms + denylist, rebuilt per run
    tools.py       what the agent can do, scoped to one person
    loop.py        one question in, one audited answer out
  web/
    scope.py       one household, bound before the server listens
    panels.py      every read the dashboard makes, in one file
    render.py      the only exit from data into markup
    style.py       the design system; no webfont, for the obvious reason
    app.py         four routes, none of which names a person
```

`store.py` owns all SQL. `agent/` never imports sqlite3 and never opens a socket
except through `llm.py`. Policy decisions belong to
[pay-warden](../pay-warden), called over MCP as a separate process.

## See it run

```bash
export OPENAI_API_KEY=$(grep -m1 ^OPENAI_API_KEY= ~/projects/payoptimize/.env | cut -d= -f2-)
export PAY_WARDEN_COMMAND=uv
export PAY_WARDEN_ARGS="run --project ../pay-warden python -m pay_warden.server"
export PAY_WARDEN_CWD=$HOME/projects/pay-warden

uv run python scripts/demo.py
```

Two humans, two lines, a real language model, and a real policy engine in
another process. Only the phone network is stubbed — messages print to the
terminal; `--linq` sends them to real handsets.

**It spends nothing by default.** pay-warden mints a Prava session the moment it
*allows* a purchase, and the sandbox has a finite number of those — so the demo
policy parks every purchase for the sponsor, which costs nothing and is the
interesting path anyway. `--release` goes further and mints one real session.

`--keep` holds on to the database it built and prints the command that points
the dashboard at it, so the page can be read against a run a real model and a
real policy engine actually produced rather than against a fixture:

```bash
uv run python scripts/demo.py --keep
STEWARD_DB=/tmp/steward-demo-XXXX/demo.sqlite3 \
  PAY_WARDEN_POLICY=/tmp/steward-demo-XXXX/household.yaml \
  uv run python -m steward serve --person 1
```

## Development

```bash
make setup     # uv sync, and a .env from .env.example
make test      # pytest
make check     # ruff + pytest — green before every commit
```

The suite never touches the network: every model call goes through
`httpx.MockTransport`, and a guard fixture makes both real httpx transports raise.
An un-mocked call fails immediately instead of quietly spending credits.

The agent is optional. With `OPENAI_API_KEY` unset everything that is not the
model still works, and the agent surfaces say why they cannot answer.

## Credits

Ports three pieces of prior work rather than rewriting them, because a verified
privacy boundary that gets rewritten quietly stops being verified:
the LLM wrapper, redactor and tool loop from **payoptimize**; the policy engine
and Prava rail from **pay-warden**; merchant agent-readiness grading and the
fixture storefront from **canibuy**.
