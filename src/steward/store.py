"""Every SQL statement in steward.

No other module imports sqlite3: the agent stays pure decision logic, the
surfaces stay ignorant of persistence, and the schema has exactly one owner.
Rows leave as plain dicts, so no sqlite3 object escapes this file either.

Concurrency model and pragmas are carried over from payoptimize unchanged —
one process, WAL, a connection per call. Cheap under SQLite, and it keeps the
async surfaces from ever sharing a connection across threads.

The one thing this schema does that payoptimize's does not: **facts are
tombstoned, never dropped.** A person must be able to say "forget that I said
that" and have it stop influencing decisions — while the audit trail still
shows that a fact existed and when it stopped counting. A DELETE would satisfy
the first requirement and quietly destroy the second.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import config
from .models import utc_now_iso

# Tables, then columns added later, then indexes — in that order, because an
# index over a column this version introduced cannot be created until the
# migration has added it to databases that predate it.
TABLES = """
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  -- Who funds this person. NULL for a sponsor (nobody funds them here).
  -- Self-referential so that one human's two roles are two rows, not a flag:
  -- somebody can be spending a parent's money while sponsoring their own child.
  sponsor_id INTEGER REFERENCES people(id),
  phone TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  -- Roughly where they are, for estimating how long a delivery takes. Kept in
  -- its own columns rather than as facts on purpose: a coordinate is the one
  -- input the plan promises never crosses to the model, and it is easier to
  -- guarantee that about two columns read by one module than about a row in a
  -- table everything reads. NULL means unknown, which the catalogue reports as
  -- "delivery time unknown" rather than guessing.
  home_lat REAL,
  home_lon REAL,
  -- Whether this person's conversation is currently visible to their sponsor.
  -- Theirs to set and theirs to change; 'private' is the default because a
  -- default that leaks is not a default anyone chose.
  share_mode TEXT NOT NULL DEFAULT 'private',
  created_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  -- Where this came from: 'stated' (the person said it), 'parsed' (a
  -- deterministic extractor), 'inferred' (a local model). Displayed when the
  -- person asks what we know, because "you told me" and "I guessed" are not
  -- the same claim and must not look the same.
  source TEXT NOT NULL DEFAULT 'stated',
  created_ts TEXT NOT NULL,
  -- Tombstone. Empty string means live; see the module docstring.
  deleted_ts TEXT NOT NULL DEFAULT '',
  -- A proposal, not yet a belief. Anything a model inferred lands here and is
  -- invisible to `recall_facts` — and therefore to the frontier model —
  -- until the person confirms it. See `confirm_fact`.
  pending INTEGER NOT NULL DEFAULT 0,
  -- When a person confirmed a proposal. Its presence on a `stated` fact is how
  -- we can still tell "they typed this" from "a machine read it and they
  -- agreed", which `source` alone would lose at the moment of promotion.
  confirmed_ts TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  speaker TEXT NOT NULL,
  text TEXT NOT NULL,
  -- The dependent chooses what the sponsor sees, per conversation. Defaults to
  -- 0: the sponsor sees decisions, ledger and escalations, never the chat,
  -- unless the spender opts a turn in.
  shared_with_sponsor INTEGER NOT NULL DEFAULT 0,
  -- The agent run this turn belongs to. Without it, joining "what they said" to
  -- "what was decided" is a guess about timestamps, and the pilot's entire
  -- question is which message led to which decision. 0 for turns with no run
  -- behind them — a deterministic router reply, for one.
  run_id INTEGER NOT NULL DEFAULT 0,
  ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  text TEXT NOT NULL,
  -- float32 vector, packed. Opaque to SQL: similarity is computed in Python,
  -- see memory/episodic.py for why that is the right size of solution here.
  embedding BLOB NOT NULL,
  -- The turn this was distilled from, when there was one. Nullable because an
  -- episode can also come from an extractor with no conversation behind it.
  turn_id INTEGER REFERENCES turns(id),
  created_ts TEXT NOT NULL,
  -- Tombstoned like facts, and for the same promise.
  deleted_ts TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS escalations (
  id INTEGER PRIMARY KEY,
  spender_id INTEGER NOT NULL REFERENCES people(id),
  sponsor_id INTEGER NOT NULL REFERENCES people(id),
  -- pay-warden's own id for the parked attempt. This is the handle the sponsor's
  -- approval is replayed against, so it is the one field here that must survive
  -- exactly; everything else is a copy kept for display.
  attempt_id TEXT NOT NULL,
  description TEXT NOT NULL,
  merchant_name TEXT NOT NULL DEFAULT '',
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL,
  -- Why policy parked it, verbatim from pay-warden. Shown to the sponsor
  -- unedited: the rule that fired is the reason they are being asked at all.
  rule_id TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  payment_url TEXT NOT NULL DEFAULT '',
  -- The run that caused it, so a decision joins back to the conversation.
  run_id INTEGER NOT NULL DEFAULT 0,
  created_ts TEXT NOT NULL,
  decided_ts TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'goal',
  target_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'GBP',
  cadence TEXT NOT NULL DEFAULT 'monthly',
  per_period_cents INTEGER NOT NULL DEFAULT 0,
  start_date TEXT NOT NULL,
  finish_date TEXT NOT NULL,
  -- draft until a person activates it. A draft is arithmetic; an active plan is
  -- something steward will talk about and warn against spending into, which is
  -- why only a person can make one.
  status TEXT NOT NULL DEFAULT 'draft',
  -- What has actually been put aside, as the person has told us. steward has no
  -- bank access and cannot move money, so this is the only kind of progress it
  -- can honestly report — never a figure it inferred from the calendar.
  contributed_cents INTEGER NOT NULL DEFAULT 0,
  created_ts TEXT NOT NULL,
  activated_ts TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plan_items (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  description TEXT NOT NULL,
  amount_cents INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'other',
  -- Flights and accommodation always need a person, whatever policy would say.
  -- Set in plan/goals.py rather than by a caller, so the model cannot clear it.
  needs_human INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'planned',
  created_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  -- pay-warden's attempt id, so a refund is anchored to something that
  -- actually happened rather than to a description somebody typed.
  attempt_id TEXT NOT NULL,
  description TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL,
  -- In the person's own words. Never summarised by a model: this is the text a
  -- merchant may end up reading, and a paraphrase of a complaint is a different
  -- complaint.
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'requested',
  resolution TEXT NOT NULL DEFAULT '',
  created_ts TEXT NOT NULL,
  resolved_ts TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id),
  -- What the person was telling us: 'deleted_belief' (we held something wrong),
  -- 'rejected_proposal' (a model guessed and they said no). Recorded as its own
  -- row rather than inferred later from a tombstone, because "they corrected
  -- us" and "it expired" look identical in a deleted_ts and mean opposite
  -- things about whether this system is working.
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  subject_id INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT '',
  run_id INTEGER NOT NULL DEFAULT 0,
  created_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL DEFAULT 0,
  trigger_kind TEXT NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  answer TEXT NOT NULL DEFAULT '',
  tools_used TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  ts TEXT NOT NULL
);
-- What the model was actually sent, which is not what the person actually said.
-- `turns` holds their words; this holds the redacted view the model saw, with
-- other people as pseudonyms. Keeping both is the only way to answer "why did
-- it decide that" without keeping a second copy of anybody's raw conversation.
CREATE TABLE IF NOT EXISTS agent_transcripts (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES agent_runs(id),
  messages TEXT NOT NULL,
  ts TEXT NOT NULL,
  deleted_ts TEXT NOT NULL DEFAULT ''
);
"""

INDEXES = """
-- One live *believed* fact per (person, kind, key). Two exemptions, each
-- deliberate: tombstoned rows, so re-stating something you deleted is an
-- insert rather than a constraint violation; and pending rows, so a proposal
-- can sit alongside the belief it would replace without displacing it. A plain
-- UNIQUE would make deletion permanent in the worst way — you could never say
-- the thing again — and would let an unconfirmed guess evict a confirmed fact.
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_believed
  ON facts(person_id, kind, key) WHERE deleted_ts = '' AND pending = 0;
CREATE INDEX IF NOT EXISTS idx_facts_person ON facts(person_id, kind);
CREATE INDEX IF NOT EXISTS idx_facts_pending ON facts(person_id, pending, deleted_ts);
CREATE INDEX IF NOT EXISTS idx_episodes_person ON episodes(person_id, deleted_ts);
CREATE INDEX IF NOT EXISTS idx_turns_person ON turns(person_id, ts);
CREATE INDEX IF NOT EXISTS idx_agent_runs_ts ON agent_runs(ts);
CREATE INDEX IF NOT EXISTS idx_people_phone ON people(phone);
CREATE INDEX IF NOT EXISTS idx_escalations_sponsor ON escalations(sponsor_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_escalations_attempt ON escalations(attempt_id);
CREATE INDEX IF NOT EXISTS idx_plans_person ON plans(person_id, status);
CREATE INDEX IF NOT EXISTS idx_plan_items_plan ON plan_items(plan_id);
CREATE INDEX IF NOT EXISTS idx_refunds_person ON refunds(person_id, status);
CREATE INDEX IF NOT EXISTS idx_corrections_person ON corrections(person_id, created_ts);
CREATE INDEX IF NOT EXISTS idx_turns_run ON turns(run_id);
CREATE INDEX IF NOT EXISTS idx_escalations_run ON escalations(run_id);
"""

_initialized: set[str] = set()
_init_lock = threading.Lock()


SHARE_PRIVATE = "private"
SHARE_SHARED = "shared"
SHARE_MODES = (SHARE_PRIVATE, SHARE_SHARED)


class StoreError(RuntimeError):
    """A write did not land on the row it was aimed at."""


class NotFoundError(StoreError):
    """The row a write targeted does not exist."""


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")


# Columns added after a database was first created. `CREATE TABLE IF NOT EXISTS`
# does nothing to an existing table, so every column added later has to arrive
# this way or a deployed volume stays one schema behind and fails on the first
# query that names the new column. Additive only — SQLite cannot drop or retype
# a column without rebuilding the table.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("facts", "pending", "INTEGER NOT NULL DEFAULT 0"),
    ("facts", "confirmed_ts", "TEXT NOT NULL DEFAULT ''"),
    ("people", "home_lat", "REAL"),
    ("people", "home_lon", "REAL"),
    ("people", "share_mode", "TEXT NOT NULL DEFAULT 'private'"),
    ("turns", "run_id", "INTEGER NOT NULL DEFAULT 0"),
    ("escalations", "run_id", "INTEGER NOT NULL DEFAULT 0"),
    ("agent_transcripts", "deleted_ts", "TEXT NOT NULL DEFAULT ''"),
)

# Indexes this version replaced. Dropped by name before the new ones are
# created, because `CREATE INDEX IF NOT EXISTS` under a name that already
# exists is a silent no-op — an old database would keep enforcing the old
# uniqueness rule and reject the very proposals the new one allows.
DROPPED_INDEXES: tuple[str, ...] = ("idx_facts_live",)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, spec in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
    for index in DROPPED_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {index}")


def _ensure_schema(conn: sqlite3.Connection, path: str) -> None:
    """Run the schema once per DB path per process.

    Tables, then migrations, then indexes. The order is load-bearing: an index
    over a column this version added cannot be created on an older database
    until the ALTER TABLE that adds it has run.
    """
    if path in _initialized:
        return
    with _init_lock:
        if path in _initialized:
            return
        conn.executescript(TABLES)
        _migrate(conn)
        conn.executescript(INDEXES)
        conn.commit()
        # ":memory:" is a brand-new database on every connection, so caching it
        # as initialized would hand the next caller an empty schema.
        if path != ":memory:":
            _initialized.add(path)


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or config.db_path()
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    _ensure_schema(conn, path)
    return conn


@contextmanager
def transaction(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Commit on clean exit, roll back on anything else, always close."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Create the schema if it is not there. Called once at boot."""
    connect(db_path).close()


def _row(cursor: sqlite3.Cursor) -> dict[str, Any] | None:
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


# --- people ------------------------------------------------------------------


def insert_person(
    *,
    name: str,
    role: str,
    sponsor_id: int | None = None,
    phone: str = "",
    email: str = "",
    db_path: str | None = None,
) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO people (name, role, sponsor_id, phone, email, created_ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, role, sponsor_id, phone, email, utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def get_person(person_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)))


def person_by_phone(phone: str, db_path: str | None = None) -> dict[str, Any] | None:
    """How an inbound text finds its sender."""
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM people WHERE phone = ?", (phone,)))


def set_home_location(
    person_id: int, latitude: float | None, longitude: float | None, db_path: str | None = None
) -> None:
    """Set or clear roughly where someone is. Passing None for both forgets it,
    which has to be as easy as setting it."""
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE people SET home_lat = ?, home_lon = ? WHERE id = ?",
            (latitude, longitude, person_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no person {person_id}")


def set_share_mode(person_id: int, mode: str, db_path: str | None = None) -> None:
    """Set whether this person's conversation is visible to their sponsor.

    Only ever called on the spender's own instruction. There is no tool for it:
    an agent that could open someone's conversation to their sponsor would make
    the setting decorative.
    """
    if mode not in SHARE_MODES:
        raise StoreError(f"unknown share mode {mode!r}; use one of: {', '.join(SHARE_MODES)}")
    with transaction(db_path) as conn:
        cur = conn.execute("UPDATE people SET share_mode = ? WHERE id = ?", (mode, person_id))
        if cur.rowcount == 0:
            raise NotFoundError(f"no person {person_id}")


def list_people(db_path: str | None = None) -> list[dict[str, Any]]:
    with transaction(db_path) as conn:
        return _rows(conn.execute("SELECT * FROM people ORDER BY id"))


# --- facts -------------------------------------------------------------------


def upsert_fact(
    *,
    person_id: int,
    kind: str,
    key: str,
    value: str,
    source: str = "stated",
    pending: bool = False,
    db_path: str | None = None,
) -> int:
    """Set a fact, superseding any live fact with the same (person, kind, key).

    Restating something is not a second fact — "I work 9-5" said twice is one
    truth, and a memory that accumulated duplicates would show the person a
    list that grows every time they repeat themselves. The superseded row is
    tombstoned rather than overwritten, so the history of what we believed and
    when stays intact.

    A **pending** write supersedes only other pending proposals. It must never
    displace a believed fact: a model that guessed wrong would otherwise
    silently evict something the person actually told us, replacing it with a
    proposal they cannot even see until they go looking. Conversely a believed
    write clears any pending proposal for the same key, because the person has
    now said what is true and there is nothing left to ask them about.
    """
    now = utc_now_iso()
    with transaction(db_path) as conn:
        if pending:
            supersede = "AND pending = 1"
        else:
            supersede = ""  # clears the belief *and* any proposal about it
        conn.execute(
            "UPDATE facts SET deleted_ts = ? WHERE person_id = ? AND kind = ?"
            f" AND key = ? AND deleted_ts = '' {supersede}",
            (now, person_id, kind, key),
        )
        cur = conn.execute(
            "INSERT INTO facts (person_id, kind, key, value, source, created_ts, pending)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (person_id, kind, key, value, source, now, int(pending)),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def list_facts(
    person_id: int,
    *,
    kind: str = "",
    pending: bool = False,
    include_deleted: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Believed facts by default; pass `pending=True` for the proposals.

    The default is the safe one on purpose. Every caller that feeds the model —
    `agent/tools.py` above all — gets believed facts without having to remember
    to ask for them, and a new caller that forgets the distinction entirely
    still cannot leak an unconfirmed guess.
    """
    sql = "SELECT * FROM facts WHERE person_id = ? AND pending = ?"
    params: list[Any] = [person_id, int(pending)]
    if not include_deleted:
        sql += " AND deleted_ts = ''"
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY kind, key"
    with transaction(db_path) as conn:
        return _rows(conn.execute(sql, tuple(params)))


def confirm_fact(fact_id: int, db_path: str | None = None) -> None:
    """Promote a proposal to a belief.

    The person has read it and said yes, so it now carries their authority and
    `source` becomes `stated`. `confirmed_ts` is what preserves the provenance
    that promotion would otherwise erase: a `stated` fact with a confirmation
    time was read by a machine and agreed to, which is not quite the same thing
    as one the person typed themselves.
    """
    now = utc_now_iso()
    with transaction(db_path) as conn:
        row = _row(conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)))
        if row is None or row["deleted_ts"] or not row["pending"]:
            raise NotFoundError(f"no pending fact {fact_id} to confirm")
        # Confirming makes this the belief, so whatever it replaces goes now.
        conn.execute(
            "UPDATE facts SET deleted_ts = ? WHERE person_id = ? AND kind = ? AND key = ?"
            " AND deleted_ts = '' AND pending = 0",
            (now, row["person_id"], row["kind"], row["key"]),
        )
        conn.execute(
            "UPDATE facts SET pending = 0, source = 'stated', confirmed_ts = ? WHERE id = ?",
            (now, fact_id),
        )


def get_fact(fact_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)))


def delete_fact(fact_id: int, db_path: str | None = None) -> None:
    """Tombstone a fact. Raises rather than silently no-op'ing on an unknown or
    already-deleted id: "forget that" reporting success without doing anything
    is the one failure mode this feature cannot have."""
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE facts SET deleted_ts = ? WHERE id = ? AND deleted_ts = ''",
            (utc_now_iso(), fact_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no live fact {fact_id} to delete")


# --- episodes ----------------------------------------------------------------


def insert_episode(
    *,
    person_id: int,
    text: str,
    embedding: bytes,
    turn_id: int | None = None,
    db_path: str | None = None,
) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO episodes (person_id, text, embedding, turn_id, created_ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (person_id, text, embedding, turn_id, utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def list_episodes(
    person_id: int,
    *,
    include_deleted: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Every live episode for a person, embeddings included.

    Loading them all is deliberate: search ranks in Python, so it needs the
    whole candidate set. See memory/episodic.py for why that is affordable.
    """
    sql = "SELECT * FROM episodes WHERE person_id = ?"
    if not include_deleted:
        sql += " AND deleted_ts = ''"
    sql += " ORDER BY id"
    with transaction(db_path) as conn:
        return _rows(conn.execute(sql, (person_id,)))


def get_episode(episode_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)))


def delete_episode(episode_id: int, db_path: str | None = None) -> None:
    """Tombstone an episode. Raises on an unknown or already-deleted id, for the
    same reason delete_fact does."""
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE episodes SET deleted_ts = ? WHERE id = ? AND deleted_ts = ''",
            (utc_now_iso(), episode_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no live episode {episode_id} to delete")


# --- turns -------------------------------------------------------------------


def insert_turn(
    *,
    person_id: int,
    speaker: str,
    text: str,
    shared_with_sponsor: bool = False,
    run_id: int = 0,
    db_path: str | None = None,
) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO turns (person_id, speaker, text, shared_with_sponsor, run_id, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (person_id, speaker, text, int(shared_with_sponsor), run_id, utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def recent_turns(
    person_id: int, *, limit: int = 20, db_path: str | None = None
) -> list[dict[str, Any]]:
    """Newest-first from SQL, oldest-first on the way out — the caller wants a
    conversation that reads forwards, but LIMIT has to take from the recent end."""
    with transaction(db_path) as conn:
        rows = _rows(
            conn.execute(
                "SELECT * FROM turns WHERE person_id = ? ORDER BY id DESC LIMIT ?",
                (person_id, limit),
            )
        )
    return list(reversed(rows))


def get_turn(turn_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)))


def shared_turns(person_id: int, db_path: str | None = None) -> list[dict[str, Any]]:
    """What the sponsor is allowed to read of this person's conversation."""
    with transaction(db_path) as conn:
        return _rows(
            conn.execute(
                "SELECT * FROM turns WHERE person_id = ? AND shared_with_sponsor = 1 ORDER BY id",
                (person_id,),
            )
        )


# --- escalations ---------------------------------------------------------------


def insert_escalation(
    *,
    spender_id: int,
    sponsor_id: int,
    attempt_id: str,
    description: str,
    amount_cents: int,
    currency: str,
    merchant_name: str = "",
    rule_id: str = "",
    reason: str = "",
    run_id: int = 0,
    db_path: str | None = None,
) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO escalations (spender_id, sponsor_id, attempt_id, description,"
            " merchant_name, amount_cents, currency, rule_id, reason, run_id, created_ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spender_id,
                sponsor_id,
                attempt_id,
                description,
                merchant_name,
                amount_cents,
                currency,
                rule_id,
                reason,
                run_id,
                utc_now_iso(),
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def get_escalation(escalation_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM escalations WHERE id = ?", (escalation_id,)))


def escalation_by_attempt(attempt_id: str, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM escalations WHERE attempt_id = ?", (attempt_id,)))


def list_escalations(
    *,
    sponsor_id: int | None = None,
    spender_id: int | None = None,
    status: str = "",
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM escalations WHERE 1=1"
    params: list[Any] = []
    if sponsor_id is not None:
        sql += " AND sponsor_id = ?"
        params.append(sponsor_id)
    if spender_id is not None:
        sql += " AND spender_id = ?"
        params.append(spender_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"
    with transaction(db_path) as conn:
        return _rows(conn.execute(sql, tuple(params)))


def decide_escalation(
    escalation_id: int,
    *,
    status: str,
    payment_url: str = "",
    db_path: str | None = None,
) -> None:
    """Move a pending escalation to approved or declined, exactly once.

    The `status = 'pending'` in the WHERE clause is the whole point: two sponsors
    tapping approve at the same moment, or one tapping twice, must not mint two
    payment sessions for one request. Whoever loses the race gets a
    NotFoundError, which the caller turns into "already decided" rather than a
    second charge.
    """
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE escalations SET status = ?, payment_url = ?, decided_ts = ?"
            " WHERE id = ? AND status = 'pending'",
            (status, payment_url, utc_now_iso(), escalation_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no pending escalation {escalation_id} to decide")


def reopen_escalation(escalation_id: int, db_path: str | None = None) -> None:
    """Put a decision back, when the action it authorised did not happen.

    Only ever called after an approval that failed to release. A sponsor whose
    approval silently bought nothing is worse off than one who has to tap twice.
    """
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE escalations SET status = 'pending', decided_ts = '' WHERE id = ?",
            (escalation_id,),
        )


def set_escalation_payment_url(
    escalation_id: int, payment_url: str, db_path: str | None = None
) -> None:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE escalations SET payment_url = ? WHERE id = ?", (payment_url, escalation_id)
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no escalation {escalation_id}")


# --- plans ---------------------------------------------------------------------

# Item kinds nobody buys unattended. Kept here, beside the SQL that writes the
# flag, so `needs_human` is computed at the point of insert and no caller —
# tool, CLI or future executor — can pass it in as False.
PLAN_ITEM_ALWAYS_HUMAN = frozenset({"flight", "accommodation"})

PLAN_DRAFT = "draft"
PLAN_ACTIVE = "active"
PLAN_DONE = "done"
PLAN_ABANDONED = "abandoned"
PLAN_STATUSES = (PLAN_DRAFT, PLAN_ACTIVE, PLAN_DONE, PLAN_ABANDONED)


def insert_plan(
    *,
    person_id: int,
    name: str,
    kind: str,
    target_cents: int,
    currency: str,
    cadence: str,
    per_period_cents: int,
    start_date: str,
    finish_date: str,
    db_path: str | None = None,
) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO plans (person_id, name, kind, target_cents, currency, cadence,"
            " per_period_cents, start_date, finish_date, created_ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                person_id,
                name,
                kind,
                target_cents,
                currency,
                cadence,
                per_period_cents,
                start_date,
                finish_date,
                utc_now_iso(),
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def get_plan(plan_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)))


def list_plans(
    person_id: int, *, status: str = "", db_path: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM plans WHERE person_id = ?"
    params: list[Any] = [person_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"
    with transaction(db_path) as conn:
        return _rows(conn.execute(sql, tuple(params)))


def update_plan_schedule(
    plan_id: int,
    *,
    person_id: int,
    target_cents: int,
    per_period_cents: int,
    cadence: str,
    finish_date: str,
    db_path: str | None = None,
) -> None:
    """Reshape a plan. Only a draft, and only the owner's.

    Both conditions live in the WHERE clause rather than in a caller's `if`.
    Draft-only, because changing the numbers under a plan somebody activated
    makes the thing they agreed to and the thing steward tracks two different
    plans. Owner-only, because `plan_id` is a handle the model can guess, and a
    scope check that lives above this line is a scope check that can be
    forgotten by the next caller.
    """
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE plans SET target_cents = ?, per_period_cents = ?, cadence = ?,"
            " finish_date = ? WHERE id = ? AND person_id = ? AND status = 'draft'",
            (target_cents, per_period_cents, cadence, finish_date, plan_id, person_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no draft plan {plan_id} of yours to reshape")


def set_plan_status(
    plan_id: int, *, person_id: int, status: str, expect: str, db_path: str | None = None
) -> None:
    """Move a plan between statuses, exactly once, and only the owner's.

    `expect` in the WHERE clause is the same guard `decide_escalation` uses: two
    activations of one plan, or an activation racing an abandonment, must not
    both succeed. `person_id` is there for the same reason it is in
    `update_plan_schedule` — a plan id is guessable.
    """
    if status not in PLAN_STATUSES:
        raise StoreError(f"unknown plan status {status!r}")
    activated = utc_now_iso() if status == PLAN_ACTIVE else ""
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE plans SET status = ?, activated_ts = CASE WHEN ? = '' THEN activated_ts"
            " ELSE ? END WHERE id = ? AND person_id = ? AND status = ?",
            (status, activated, activated, plan_id, person_id, expect),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no plan {plan_id} of yours in status {expect!r}")


def add_contribution(
    plan_id: int, amount_cents: int, *, person_id: int, db_path: str | None = None
) -> None:
    """Record what the person says they have put aside."""
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE plans SET contributed_cents = contributed_cents + ?"
            " WHERE id = ? AND person_id = ?",
            (amount_cents, plan_id, person_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no plan {plan_id} of yours")


def insert_plan_item(
    *,
    plan_id: int,
    description: str,
    amount_cents: int,
    kind: str,
    db_path: str | None = None,
) -> int:
    """Add something a plan has to pay for.

    `needs_human` is **computed here from the kind** and is not a parameter. A
    flight or a hotel is confirmed by a person however small it is and whatever
    policy would allow; making it an argument would make it negotiable by
    whoever calls next.

    The parent is looked up rather than left to the foreign key. `PRAGMA
    foreign_keys=ON` would raise sqlite3.IntegrityError, which is not a
    StoreError and would escape the agent's dispatch, the loop and the router
    uncaught — a guessed plan id would crash a conversation instead of being
    answered.
    """
    with transaction(db_path) as conn:
        if _row(conn.execute("SELECT id FROM plans WHERE id = ?", (plan_id,))) is None:
            raise NotFoundError(f"no plan {plan_id}")
        cur = conn.execute(
            "INSERT INTO plan_items (plan_id, description, amount_cents, kind, needs_human,"
            " created_ts) VALUES (?, ?, ?, ?, ?, ?)",
            (
                plan_id,
                description,
                amount_cents,
                kind,
                int(kind in PLAN_ITEM_ALWAYS_HUMAN),
                utc_now_iso(),
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def list_plan_items(plan_id: int, db_path: str | None = None) -> list[dict[str, Any]]:
    with transaction(db_path) as conn:
        return _rows(
            conn.execute("SELECT * FROM plan_items WHERE plan_id = ? ORDER BY id", (plan_id,))
        )


# --- refunds -------------------------------------------------------------------

REFUND_REQUESTED = "requested"
REFUND_REFUNDED = "refunded"
REFUND_REFUSED = "refused"
REFUND_STATUSES = (REFUND_REQUESTED, REFUND_REFUNDED, REFUND_REFUSED)


def insert_refund(
    *,
    person_id: int,
    attempt_id: str,
    description: str,
    amount_cents: int,
    currency: str,
    reason: str,
    db_path: str | None = None,
) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO refunds (person_id, attempt_id, description, amount_cents, currency,"
            " reason, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (person_id, attempt_id, description, amount_cents, currency, reason, utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def get_refund(refund_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM refunds WHERE id = ?", (refund_id,)))


def list_refunds(
    person_id: int, *, status: str = "", db_path: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM refunds WHERE person_id = ?"
    params: list[Any] = [person_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"
    with transaction(db_path) as conn:
        return _rows(conn.execute(sql, tuple(params)))


def resolve_refund(
    refund_id: int,
    *,
    person_id: int,
    status: str,
    resolution: str = "",
    db_path: str | None = None,
) -> None:
    """Record how a refund ended, exactly once and only the owner's."""
    if status not in (REFUND_REFUNDED, REFUND_REFUSED):
        raise StoreError(f"a refund ends refunded or refused, not {status!r}")
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE refunds SET status = ?, resolution = ?, resolved_ts = ?"
            " WHERE id = ? AND person_id = ? AND status = 'requested'",
            (status, resolution, utc_now_iso(), refund_id, person_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no open refund {refund_id} of yours to resolve")


# --- corrections ---------------------------------------------------------------

DELETED_BELIEF = "deleted_belief"
REJECTED_PROPOSAL = "rejected_proposal"
CORRECTION_KINDS = (DELETED_BELIEF, REJECTED_PROPOSAL)


def insert_correction(
    *,
    person_id: int,
    kind: str,
    subject: str,
    subject_id: int = 0,
    detail: str = "",
    run_id: int = 0,
    db_path: str | None = None,
) -> int:
    """Record that a person told us we had something wrong.

    Its own row rather than something inferred later from a tombstone: "they
    corrected us" and "it was superseded" both leave a deleted_ts, and they mean
    opposite things about whether this system is working.
    """
    if kind not in CORRECTION_KINDS:
        raise StoreError(f"unknown correction kind {kind!r}")
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO corrections (person_id, kind, subject, subject_id, detail, run_id,"
            " created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (person_id, kind, subject, subject_id, detail, run_id, utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def list_corrections(person_id: int, db_path: str | None = None) -> list[dict[str, Any]]:
    with transaction(db_path) as conn:
        return _rows(
            conn.execute("SELECT * FROM corrections WHERE person_id = ? ORDER BY id", (person_id,))
        )


# --- agent audit -------------------------------------------------------------


def insert_agent_run(
    *,
    person_id: int,
    trigger_kind: str,
    question: str,
    model: str,
    db_path: str | None = None,
) -> int:
    """Open a run before the first model call, so a crash mid-run still leaves
    an audit row saying the agent was invoked."""
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO agent_runs (person_id, trigger_kind, question, model, ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (person_id, trigger_kind, question, model, utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def finish_agent_run(
    run_id: int,
    *,
    answer: str,
    tools_used: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    db_path: str | None = None,
) -> None:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE agent_runs SET answer = ?, tools_used = ?, tokens_in = ?,"
            " tokens_out = ?, latency_ms = ? WHERE id = ?",
            (answer, tools_used, tokens_in, tokens_out, latency_ms, run_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"no agent run {run_id} to finish")


def get_agent_run(run_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        return _row(conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)))


def recent_agent_runs(*, limit: int = 20, db_path: str | None = None) -> list[dict[str, Any]]:
    with transaction(db_path) as conn:
        return _rows(conn.execute("SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (limit,)))


def get_agent_transcript(run_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    """What the model saw on one run, unless it has been forgotten.

    Tombstoned rows are absent rather than marked, because every other reader in
    this module treats a tombstone as gone and one that did not would be the
    exception nobody remembers.
    """
    with transaction(db_path) as conn:
        return _row(
            conn.execute(
                "SELECT * FROM agent_transcripts WHERE run_id = ? AND deleted_ts = ''"
                " ORDER BY id DESC LIMIT 1",
                (run_id,),
            )
        )


def list_agent_transcripts(
    person_id: int, *, limit: int = 20, db_path: str | None = None
) -> list[dict[str, Any]]:
    """One person's transcripts, newest first.

    Joined through agent_runs because a transcript knows its run and a run knows
    its person; storing the person on both would be two places to get it wrong.
    """
    with transaction(db_path) as conn:
        return _rows(
            conn.execute(
                "SELECT t.* FROM agent_transcripts t JOIN agent_runs r ON r.id = t.run_id"
                " WHERE r.person_id = ? AND t.deleted_ts = '' ORDER BY t.id DESC LIMIT ?",
                (person_id, limit),
            )
        )


def delete_agent_transcripts_for_run(run_id: int, db_path: str | None = None) -> int:
    """Tombstone whatever the model saw on this run.

    Called when the person forgets the thing that caused the run. Without it,
    "forget that" would leave the sentence sitting in a transcript — deleted
    from memory, still on disk, and still readable by anything that reads
    transcripts. A deletion that only reaches some of the copies is not one.
    """
    with transaction(db_path) as conn:
        cur = conn.execute(
            "UPDATE agent_transcripts SET deleted_ts = ? WHERE run_id = ? AND deleted_ts = ''",
            (utc_now_iso(), run_id),
        )
        return int(cur.rowcount)


def insert_agent_transcript(run_id: int, messages: str, db_path: str | None = None) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO agent_transcripts (run_id, messages, ts) VALUES (?, ?, ?)",
            (run_id, messages, utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)
