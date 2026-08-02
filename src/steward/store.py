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

SCHEMA = """
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
  deleted_ts TEXT NOT NULL DEFAULT ''
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
  ts TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS agent_transcripts (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES agent_runs(id),
  messages TEXT NOT NULL,
  ts TEXT NOT NULL
);
-- One live fact per (person, kind, key); tombstoned rows are exempt, so
-- re-stating something you deleted is an insert rather than a constraint
-- violation. A plain UNIQUE would make deletion permanent in the worst way:
-- you could never say it again.
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_live
  ON facts(person_id, kind, key) WHERE deleted_ts = '';
CREATE INDEX IF NOT EXISTS idx_facts_person ON facts(person_id, kind);
CREATE INDEX IF NOT EXISTS idx_turns_person ON turns(person_id, ts);
CREATE INDEX IF NOT EXISTS idx_agent_runs_ts ON agent_runs(ts);
CREATE INDEX IF NOT EXISTS idx_people_phone ON people(phone);
"""

_initialized: set[str] = set()
_init_lock = threading.Lock()


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
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = ()


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, spec in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


def _ensure_schema(conn: sqlite3.Connection, path: str) -> None:
    """Run the schema once per DB path per process."""
    if path in _initialized:
        return
    with _init_lock:
        if path in _initialized:
            return
        conn.executescript(SCHEMA)
        _migrate(conn)
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
    db_path: str | None = None,
) -> int:
    """Set a fact, superseding any live fact with the same (person, kind, key).

    Restating something is not a second fact — "I work 9-5" said twice is one
    truth, and a memory that accumulated duplicates would show the person a
    list that grows every time they repeat themselves. The superseded row is
    tombstoned rather than overwritten, so the history of what we believed and
    when stays intact.
    """
    now = utc_now_iso()
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE facts SET deleted_ts = ? WHERE person_id = ? AND kind = ?"
            " AND key = ? AND deleted_ts = ''",
            (now, person_id, kind, key),
        )
        cur = conn.execute(
            "INSERT INTO facts (person_id, kind, key, value, source, created_ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (person_id, kind, key, value, source, now),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def list_facts(
    person_id: int,
    *,
    kind: str = "",
    include_deleted: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM facts WHERE person_id = ?"
    params: list[Any] = [person_id]
    if not include_deleted:
        sql += " AND deleted_ts = ''"
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY kind, key"
    with transaction(db_path) as conn:
        return _rows(conn.execute(sql, tuple(params)))


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


# --- turns -------------------------------------------------------------------


def insert_turn(
    *,
    person_id: int,
    speaker: str,
    text: str,
    shared_with_sponsor: bool = False,
    db_path: str | None = None,
) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO turns (person_id, speaker, text, shared_with_sponsor, ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (person_id, speaker, text, int(shared_with_sponsor), utc_now_iso()),
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


def shared_turns(person_id: int, db_path: str | None = None) -> list[dict[str, Any]]:
    """What the sponsor is allowed to read of this person's conversation."""
    with transaction(db_path) as conn:
        return _rows(
            conn.execute(
                "SELECT * FROM turns WHERE person_id = ? AND shared_with_sponsor = 1 ORDER BY id",
                (person_id,),
            )
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


def insert_agent_transcript(run_id: int, messages: str, db_path: str | None = None) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO agent_transcripts (run_id, messages, ts) VALUES (?, ?, ?)",
            (run_id, messages, utc_now_iso()),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)
