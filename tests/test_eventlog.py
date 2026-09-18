"""Event log (SQLite events table) tests: shape, idempotency, store paths."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from dream_md.ingestion.claude import parse_claude_transcript
from dream_md.ingestion.codex import parse_codex_transcript
from dream_md.ingestion.eventlog import (
    EventLog,
    event_id,
    project_slug,
    store_path,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UTC_NOW_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class EventLogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "events.db"
        self.log = EventLog(self.db_path, project="slug12345678")
        self.addCleanup(self.log.close)
        self.claude = parse_claude_transcript(FIXTURES / "claude_session.jsonl")
        self.codex = parse_codex_transcript(FIXTURES / "codex_session.jsonl")

    def _row(self, line_no=3):
        return self.log._conn.execute(
            "SELECT id, project, source, session_id, ts, role, text, "
            "created_at, graded_at FROM events WHERE id = ?",
            (event_id(self.claude.path, line_no, 0),),
        ).fetchone()

    def test_append_inserts_every_statement(self):
        result = self.log.append(self.claude)
        self.assertEqual(result.inserted, len(self.claude.statements))
        self.assertEqual(result.ignored, 0)
        self.assertEqual(result.skipped_lines, self.claude.skipped_lines)
        self.assertEqual(self.log.count_all(), 4)
        self.assertEqual(self.log.count_pending(), 4)

    def test_row_shape_matches_plan_schema(self):
        self.log.append(self.claude)
        row = self._row(line_no=3)
        self.assertIsNotNone(row)
        row_id, project, source, session_id, ts, role, text, created_at, graded_at = row
        self.assertEqual(row_id, event_id(self.claude.path, 3, 0))
        self.assertEqual(project, "slug12345678")
        self.assertEqual(source, "claude")
        self.assertEqual(session_id, "f617f58f-40f9-4a35-9d77-69bb271400b2")
        self.assertEqual(ts, "2026-09-01T19:41:02.101Z")
        self.assertEqual(role, "user")
        self.assertEqual(
            text,
            "Always use uv run in this repo; plain python breaks the "
            "lockfile and the tests hang on asyncio teardown.",
        )
        self.assertRegex(created_at, UTC_NOW_RE)
        self.assertIsNone(graded_at)  # offline queue: ungraded

    def test_reingest_is_idempotent(self):
        first = self.log.append(self.claude)
        second = self.log.append(self.claude)
        self.assertEqual(first.inserted, 4)
        self.assertEqual(second.inserted, 0)
        self.assertEqual(second.ignored, 4)
        self.assertEqual(self.log.count_all(), 4)

    def test_mixed_sources_coexist(self):
        self.log.append(self.claude)
        self.log.append(self.codex)
        self.assertEqual(self.log.count_all(), 4 + 3)
        sources = {
            row[0]
            for row in self.log._conn.execute("SELECT source FROM events")
        }
        self.assertEqual(sources, {"claude", "codex"})

    def test_event_id_stable_and_unique_per_statement(self):
        path = "/tmp/transcript.jsonl"
        self.assertEqual(event_id(path, 3, 0), event_id(path, 3, 0))
        self.assertNotEqual(event_id(path, 3, 0), event_id(path, 3, 1))
        self.assertNotEqual(event_id(path, 3, 0), event_id(path, 4, 0))
        # relative and absolute paths resolve to the same id
        self.assertEqual(
            event_id("relative/x.jsonl", 1, 0),
            event_id(Path.cwd() / "relative" / "x.jsonl", 1, 0),
        )


class StorePathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = self._tmp.name

    def test_slug_is_short_sha1_of_absolute_path(self):
        slug = project_slug("/Users/x/proj")
        self.assertEqual(len(slug), 12)
        self.assertRegex(slug, r"^[0-9a-f]{12}$")
        self.assertEqual(slug, project_slug("/Users/x/proj"))  # deterministic
        self.assertNotEqual(slug, project_slug("/Users/x/proj2"))
        # trailing separator does not change the slug
        self.assertEqual(project_slug("/Users/x/proj/"), slug)

    def test_store_path_under_home(self):
        path = store_path("/Users/x/proj", home=self.home)
        self.assertEqual(
            path,
            Path(self.home)
            / ".dream-md"
            / "projects"
            / f"{project_slug('/Users/x/proj')}.db",
        )

    def test_for_project_creates_store_and_uses_slug(self):
        parsed = parse_claude_transcript(FIXTURES / "claude_session.jsonl")
        with EventLog.for_project("/Users/x/proj", home=self.home) as log:
            result = log.append(parsed)
            self.assertEqual(result.inserted, 4)
            self.assertEqual(log.project, project_slug("/Users/x/proj"))
            self.assertTrue(store_path("/Users/x/proj", home=self.home).exists())
            row = log._conn.execute(
                "SELECT project FROM events LIMIT 1"
            ).fetchone()
            self.assertEqual(row[0], project_slug("/Users/x/proj"))


if __name__ == "__main__":
    unittest.main()
