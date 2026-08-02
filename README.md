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

Phase 0 of 8. The agent answers from a fact store; nothing spends money yet.

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

Memory is listable and deletable. "Forget that" tombstones the fact so it stops
influencing every future decision, and a delete that matched nothing raises
rather than reporting a success it did not achieve.

## Layout

```
src/steward/
  config.py        the only module that reads os.environ
  store.py         every SQL statement; people, facts, turns, audit
  models.py        shared vocabularies (roles, fact kinds, triggers)
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
