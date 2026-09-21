"""Schema tests: DDL centralization (R7), schema_version migration, WAL."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jevmory.memory.schema import (
    SCHEMA_VERSION,
    SchemaVersionError,
    connect,
    create_all,
    current_version,
    migrate,
    table_exists,
)

# The v1 events DDL as committed in 607e3b5 (nullable text, no version row).
V1_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  source TEXT NOT NULL,
  session_id TEXT,
  ts TEXT,
  role TEXT,
  text TEXT,
  created_at TEXT NOT NULL,
  graded_at TEXT
)
"""

# The v2 facts DDL: everything v3 has MINUS the verified_at vintage
# marker — a faithful v2-era store for the forward-migration test.
V2_FACTS_DDL = """
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  project TEXT NOT NULL,
  claim TEXT NOT NULL,
  context TEXT,
  category TEXT NOT NULL,
  significance REAL NOT NULL,
  confidence REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  source_event_ids TEXT NOT NULL,
  support_count INTEGER NOT NULL DEFAULT 1,
  ask_seen_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_supported_at TEXT
)
"""

REQUIRED_TABLES = {
    "schema_version",
    "events",
    "facts",
    "fact_links",
    "judgments",
    "runs",
    "facts_fts",
}


class FreshStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "store.db"

    def test_migrate_creates_every_v2_table_and_stamps_version(self):
        conn = connect(self.db)
        self.addCleanup(conn.close)
        version = migrate(conn)
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(current_version(conn), SCHEMA_VERSION)
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue(REQUIRED_TABLES <= names, names)
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        self.assertIn("idx_judgments_subject", indexes)

    def test_migrate_is_idempotent(self):
        conn = connect(self.db)
        self.addCleanup(conn.close)
        migrate(conn)
        migrate(conn)
        rows = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        self.assertEqual(rows, 1)  # exactly one stamp, never duplicated

    def test_events_text_not_null_enforced_on_fresh_stores(self):
        conn = connect(self.db)
        self.addCleanup(conn.close)
        migrate(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (id, project, source, created_at) "
                "VALUES ('x', 'p', 'claude', 'now')"
            )

    def test_connect_sets_wal_and_busy_timeout(self):
        conn = connect(self.db)
        self.addCleanup(conn.close)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(mode, "wal")
        self.assertEqual(timeout, 5000)

    def test_fts_external_content_wiring_works(self):
        conn = connect(self.db)
        self.addCleanup(conn.close)
        migrate(conn)
        conn.execute(
            "INSERT INTO facts (project, claim, category, significance, "
            "confidence, source_event_ids, created_at, updated_at) "
            "VALUES ('p', 'always use uv run in this repo', 'tooling', 2, 0.9, '[]', 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO facts_fts (rowid, claim) VALUES (1, 'always use uv run in this repo')"
        )
        hits = conn.execute(
            "SELECT rowid FROM facts_fts WHERE facts_fts MATCH 'uv'"
        ).fetchall()
        self.assertEqual(hits, [(1,)])


class LegacyV1StoreTest(unittest.TestCase):
    """R7 branch: 'events table exists, no version row' from v1-era DBs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "v1.db"

    def _make_v1_store(self):
        conn = sqlite3.connect(self.db)
        conn.execute(V1_EVENTS_DDL)
        conn.execute(
            "INSERT INTO events (id, project, source, session_id, ts, role, "
            "text, created_at) VALUES ('id1', 'p', 'claude', 's1', 't', 'user', "
            "'always use uv run', 'now')"
        )
        conn.commit()
        conn.close()

    def test_v1_store_migrates_without_data_loss(self):
        self._make_v1_store()
        conn = connect(self.db)
        self.addCleanup(conn.close)
        self.assertIsNone(current_version(conn))  # no version row: v1-era
        version = migrate(conn)
        self.assertEqual(version, SCHEMA_VERSION)
        rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(rows, 1)  # data preserved
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue(REQUIRED_TABLES <= names)  # v2 tables created around it

    def test_v1_events_table_kept_as_is_documented_tolerance(self):
        # The v1 events table stays untouched (SQLite cannot ALTER the
        # nullability); NULL text stays insertable in a migrated v1 store.
        # Documented tolerance: all jevmory.md writes always supply text.
        self._make_v1_store()
        conn = connect(self.db)
        self.addCleanup(conn.close)
        migrate(conn)
        conn.execute(
            "INSERT INTO events (id, project, source, created_at) "
            "VALUES ('id2', 'p', 'claude', 'now')"
        )
        rows = conn.execute("SELECT COUNT(*) FROM events WHERE text IS NULL").fetchone()[0]
        self.assertEqual(rows, 1)


class LegacyV2StoreTest(unittest.TestCase):
    """v2 -> v3 (2026-09-21): facts.verified_at vintage marker added."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "v2.db"

    def _make_v2_store(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL)"
        )
        conn.execute(V2_FACTS_DDL)
        conn.execute(
            "INSERT INTO facts (project, claim, category, significance, "
            "confidence, source_event_ids, created_at, updated_at) "
            "VALUES ('p', 'always use uv run in this repo', 'tooling', "
            "2, 0.9, '[]', 'now', 'now')"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.commit()
        conn.close()

    def test_v2_store_migrates_adding_verified_at(self):
        self._make_v2_store()
        conn = connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(current_version(conn), 2)
        version = migrate(conn)
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(current_version(conn), SCHEMA_VERSION)
        # the column exists, and pre-v3 data is untouched: NULL verified_at
        # means "never audited" — never a guessed date, never corruption
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(facts)")
        }
        self.assertIn("verified_at", columns)
        claim, verified = conn.execute(
            "SELECT claim, verified_at FROM facts WHERE id = 1"
        ).fetchone()
        self.assertEqual(claim, "always use uv run in this repo")
        self.assertIsNone(verified)

    def test_v2_migration_is_idempotent_after_partial_alter(self):
        # a store that already gained the column (crash between ALTER
        # and stamp) must migrate cleanly — _column_exists guards the
        # duplicate ALTER
        self._make_v2_store()
        conn = connect(self.db)
        self.addCleanup(conn.close)
        conn.execute("ALTER TABLE facts ADD COLUMN verified_at TEXT")
        conn.commit()
        version = migrate(conn)
        self.assertEqual(version, SCHEMA_VERSION)


class FutureStoreTest(unittest.TestCase):
    def test_newer_version_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "future.db"
            conn = connect(db)
            create_all(conn)
            conn.execute("INSERT INTO schema_version (version) VALUES (99)")
            conn.commit()
            with self.assertRaises(SchemaVersionError):
                migrate(conn)
            conn.close()


class DdlCentralizationTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_eventlog_owns_no_ddl(self):
        # R7: every CREATE TABLE / CREATE VIRTUAL TABLE / CREATE INDEX for
        # the store lives in jevmory.memory.schema — nowhere else.
        source = (self.ROOT / "jevmory" / "ingestion" / "eventlog.py").read_text()
        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("CREATE VIRTUAL TABLE", source)
        self.assertNotIn("CREATE INDEX", source)

    def test_schema_module_is_the_ddl_home(self):
        source = (self.ROOT / "jevmory" / "memory" / "schema.py").read_text()
        for table in REQUIRED_TABLES - {"facts_fts"}:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} ", source)
        self.assertIn("CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts", source)


if __name__ == "__main__":
    unittest.main()
