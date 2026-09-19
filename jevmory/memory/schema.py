"""SQLite schema (v2) for the per-project store — the ONLY home of DDL.

Every CREATE TABLE / CREATE VIRTUAL TABLE / CREATE INDEX statement for
the store lives here (review R7); ``jevmory.ingestion.eventlog`` writes
the events table but must not define it (asserted by
``tests/test_schema.py``). The shapes below are PLAN v2's "SQLite schema
(v2)" verbatim:

- ``events``    — immutable statement log + offline queue (ingestion writes it)
- ``facts``     — active graded statements with receipts (M3/M5)
- ``fact_links``— duplicate_of / supersedes / contradicts edges
- ``judgments`` — verbatim Jev answers, the actual receipts (F7.1)
- ``runs``      — one row per dream/audit run, stats JSON
- ``facts_fts`` — FTS5 index over active facts' claims (external content)

``schema_version`` is created by the FIRST migration, with an explicit
branch for v1-era databases ("events table exists, no version row",
review R7): the v1 events table is kept exactly as-is — its ``text``
column was nullable and SQLite cannot change that retroactively; all
jevmory.md writes always supply text, so the difference is tolerated and
documented — the v2 tables are created around it and the version row is
stamped.

Connections (``connect``): WAL + ``busy_timeout=5000`` on EVERY
connection (PLAN v2). WAL is the real gap fixed here (readers stop
blocking writers, crash-safety); busy_timeout mirrors the sqlite3
default 5s but is explicit and per-connection.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
)
"""

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,                -- sha256(normalized content + session_id), review R2
  project TEXT NOT NULL,
  source TEXT NOT NULL,
  session_id TEXT,
  ts TEXT,
  role TEXT,
  text TEXT NOT NULL,                 -- redacted at ingest (DOMAIN #6)
  created_at TEXT NOT NULL,
  graded_at TEXT
)
"""

FACTS_DDL = """
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  project TEXT NOT NULL,
  claim TEXT NOT NULL,                -- verbatim, redacted
  context TEXT,                       -- verbatim surrounding exchange, redacted, <=800 chars
  category TEXT NOT NULL,
  significance REAL NOT NULL,
  confidence REAL NOT NULL,           -- clamp01(2*|durable_noul - 0.5|), thresholds.py
  status TEXT NOT NULL DEFAULT 'active',
  source_event_ids TEXT NOT NULL,     -- JSON array
  support_count INTEGER NOT NULL DEFAULT 1,
  ask_seen_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_supported_at TEXT
)
"""

FACT_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS fact_links (
  fact_id INTEGER NOT NULL,
  related_id INTEGER NOT NULL,
  relation TEXT NOT NULL,             -- duplicate_of | supersedes | contradicts
  UNIQUE(fact_id, related_id, relation)
)
"""

JUDGMENTS_DDL = """
CREATE TABLE IF NOT EXISTS judgments (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL,
  subject_kind TEXT NOT NULL,         -- candidate | fact | pair | line
  subject_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  type TEXT NOT NULL,
  answer_json TEXT NOT NULL,          -- verbatim Jev answer
  created_at TEXT NOT NULL
)
"""

JUDGMENTS_SUBJECT_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_judgments_subject "
    "ON judgments(subject_kind, subject_id)"
)

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  project TEXT NOT NULL,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  stats TEXT,                         -- JSON incl. api_calls, usage tokens, dropped_low_durable
  error TEXT
)
"""

FACTS_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5("
    "claim, content='facts', content_rowid='id')"
)

_ALL_DDL: tuple[str, ...] = (
    SCHEMA_VERSION_DDL,
    EVENTS_DDL,
    FACTS_DDL,
    FACT_LINKS_DDL,
    JUDGMENTS_DDL,
    JUDGMENTS_SUBJECT_INDEX_DDL,
    RUNS_DDL,
    FACTS_FTS_DDL,
)


class SchemaVersionError(RuntimeError):
    """The store's schema version is newer than this code supports."""


def connect(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open a store connection with WAL + busy_timeout on every connection."""
    db_path = os.fspath(db_path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def current_version(conn: sqlite3.Connection) -> int | None:
    """Highest applied schema version, or None if never migrated."""
    if not table_exists(conn, "schema_version"):
        return None
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] if row and row[0] is not None else None


def create_all(conn: sqlite3.Connection) -> None:
    """Create every v2 table/index (IF NOT EXISTS — safe on any store)."""
    for ddl in _ALL_DDL:
        conn.execute(ddl)


def _stamp(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def migrate(conn: sqlite3.Connection) -> int:
    """Bring a store up to ``SCHEMA_VERSION``; returns the applied version.

    Branches:
    - fresh store (no events table, no version row) -> create all, stamp 2;
    - v1-era store (events table exists, no version row) -> keep the v1
      events table as-is (nullable text; see module docstring), create the
      v2 tables around it, stamp 2;
    - older stamped version -> forward migrations (none beyond 2 yet);
    - newer stamped version -> SchemaVersionError (refuse, never corrupt).
    """
    version = current_version(conn)
    if version is None:
        # First migration on this store (fresh or v1-era).
        create_all(conn)
        _stamp(conn, SCHEMA_VERSION)
    elif version > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"store schema version {version} is newer than supported "
            f"{SCHEMA_VERSION}; upgrade jevmory"
        )
    elif version < SCHEMA_VERSION:
        # Forward migrations hook: none beyond v2 yet.
        create_all(conn)
        _stamp(conn, SCHEMA_VERSION)
    conn.commit()
    return current_version(conn) or SCHEMA_VERSION
