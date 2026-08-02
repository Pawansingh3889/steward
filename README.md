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

Phase 2 of 8. The agent answers from memory, you can correct it, and it can
read a calendar or a bank alert without any of it leaving the machine. Nothing
spends money yet.

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
  agent/
    llm.py         the one place this system talks to OpenAI
    privacy.py     pseudonyms + denylist, rebuilt per run
    tools.py       what the agent can do, scoped to one person
    loop.py        one question in, one audited answer out
```

`store.py` owns all SQL. `agent/` never imports sqlite3 and never opens a socket
except through `llm.py`. Policy decisions belong to
[pay-warden](../pay-warden), called over MCP as a separate process.

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
