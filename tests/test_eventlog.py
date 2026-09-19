"""Event log tests: content-hash ids (R2), redaction at rest, store paths."""

from __future__ import annotations

import hashlib
import re
import tempfile
import threading
import unittest
from pathlib import Path

from jevmory.ingestion.claude import parse_claude_transcript
from jevmory.ingestion.codex import parse_codex_transcript
from jevmory.ingestion.droid import parse_droid_transcript
from jevmory.ingestion.eventlog import (
    EventLog,
    event_id,
    normalize_content,
    project_slug,
    store_path,
)
from jevmory.ingestion.models import ParsedTranscript, Statement
from jevmory.ingestion.redact import redact

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLAUDE_SID = "00000000-0000-4000-8000-0000000000c1"
CODEX_SID = "11111111-0000-4000-8000-0000000000d1"
UTC_NOW_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def make_transcript(
    statements, session_id="s1", source="claude", path="t.jsonl"
) -> ParsedTranscript:
    return ParsedTranscript(
        path=path,
        source=source,
        session_id=session_id,
        cwd=None,
        statements=tuple(statements),
    )


def user_statement(text, session_id="s1", line_no=1, index=0, ts=None) -> Statement:
    return Statement(
        line_no=line_no, index=index, ts=ts, role="user", text=text,
        session_id=session_id,
    )


class EventIdTest(unittest.TestCase):
    def test_id_is_sha256_of_session_separator_normalized_content(self):
        # R2: the exact scheme, separator included — content and session
        # can never bleed into each other.
        expected = hashlib.sha256("s1\x1fhello world".encode("utf-8")).hexdigest()
        self.assertEqual(event_id("hello world", "s1"), expected)

    def test_stable_for_same_content_and_session(self):
        self.assertEqual(event_id("t", "s1"), event_id("t", "s1"))

    def test_differs_across_sessions(self):
        # Cross-session verbatim repeats survive as distinct events.
        self.assertNotEqual(event_id("t", "s1"), event_id("t", "s2"))

    def test_differs_across_content(self):
        self.assertNotEqual(event_id("t1", "s1"), event_id("t2", "s1"))

    def test_whitespace_normalization_folds_ids(self):
        # Compaction/reformatting that reflows whitespace keeps the id.
        self.assertEqual(event_id("a  b\t c\n", "s"), event_id("a b c", "s"))

    def test_none_session_equals_empty_session(self):
        # Documented normalization: no session is the empty session.
        self.assertEqual(event_id("t", None), event_id("t", ""))


class NormalizeContentTest(unittest.TestCase):
    def test_collapses_whitespace_runs_and_strips(self):
        self.assertEqual(normalize_content("  a  b\t\nc  "), "a b c")


class EventLogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "events.db"
        self.log = EventLog(self.db_path, project="slug12345678")
        self.addCleanup(self.log.close)
        self.claude = parse_claude_transcript(FIXTURES / "claude_session.jsonl")
        self.codex = parse_codex_transcript(FIXTURES / "codex_session.jsonl")
        self.droid = parse_droid_transcript(FIXTURES / "droid_session.jsonl")

    def _rows(self) -> dict:
        return {
            row[0]: row
            for row in self.log._conn.execute(
                "SELECT id, project, source, session_id, ts, role, text, "
                "created_at, graded_at FROM events"
            )
        }

    def test_append_inserts_every_statement(self):
        result = self.log.append(self.claude)
        self.assertEqual(result.inserted, len(self.claude.statements))
        self.assertEqual(result.ignored, 0)
        self.assertEqual(result.skipped_lines, self.claude.skipped_lines)
        self.assertEqual(self.log.count_all(), 5)
        self.assertEqual(self.log.count_pending(), 5)

    def test_row_shape_matches_plan_schema(self):
        self.log.append(self.claude)
        statement = self.claude.statements[0]  # line 3, typed uv-run prompt
        rows = self._rows()
        key = event_id(redact(statement.text), CLAUDE_SID)
        self.assertIn(key, rows)
        (
            row_id, project, source, session_id, ts, role, text,
            created_at, graded_at,
        ) = rows[key]
        self.assertEqual(row_id, key)
        self.assertEqual(project, "slug12345678")
        self.assertEqual(source, "claude")
        self.assertEqual(session_id, CLAUDE_SID)
        self.assertEqual(ts, "2026-09-01T19:41:02.101Z")
        self.assertEqual(role, "user")
        self.assertEqual(
            text,
            "Always use uv run in this repo; plain python breaks the "
            "lockfile and the tests hang on asyncio teardown.",
        )
        self.assertRegex(created_at, UTC_NOW_RE)
        self.assertIsNone(graded_at)  # offline queue: not yet graded

    def test_redaction_at_rest(self):
        # DOMAIN #6: redaction happens at THIS boundary — parsers stay
        # raw; the store never holds unredacted text.
        self.log.append(self.claude)
        statement = self.claude.statements[4]  # line 11, secrets statement
        rows = self._rows()
        key = event_id(redact(statement.text), CLAUDE_SID)
        self.assertIn(key, rows)
        text = rows[key][6]
        self.assertEqual(
            text,
            "One more thing: the deploy password=[redacted:credential] "
            "and the uploads rotate weekly; never log any of them.",
        )
        # and nothing raw anywhere in the store
        raw = self.log._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        leaks = self.log._conn.execute(
            "SELECT COUNT(*) FROM events WHERE text LIKE '%changeme12345%'"
        ).fetchone()[0]
        self.assertEqual(leaks, 0)
        self.assertEqual(raw, 5)

    def test_reingest_is_idempotent(self):
        first = self.log.append(self.claude)
        second = self.log.append(self.claude)
        self.assertEqual(first.inserted, 5)
        self.assertEqual(second.inserted, 0)
        self.assertEqual(second.ignored, 5)
        self.assertEqual(self.log.count_all(), 5)

    def test_same_session_verbatim_repeat_collapses_to_one_event(self):
        # One support source per session: INSERT OR IGNORE dedupes it.
        text = "Always run the linter before pushing."
        parsed = make_transcript(
            [user_statement(text, "s1"), user_statement(text, "s1")]
        )
        result = self.log.append(parsed)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.ignored, 1)
        self.assertEqual(self.log.count_all(), 1)

    def test_cross_session_repeats_survive_as_distinct_events(self):
        # R2 downstream contract: repeats across sessions must land as
        # separate events so M3 can bump support_count / "seen in N
        # sessions" instead of swallowing the repeat.
        text = "Always run the linter before pushing."
        first = self.log.append(make_transcript([user_statement(text, "s1")]))
        second = self.log.append(make_transcript([user_statement(text, "s2")]))
        self.assertEqual(first.inserted, 1)
        self.assertEqual(second.inserted, 1)
        self.assertEqual(self.log.count_all(), 2)
        self.assertEqual(len(self._rows()), 2)

    def test_mixed_sources_coexist(self):
        self.log.append(self.claude)
        self.log.append(self.codex)
        self.log.append(self.droid)
        # claude fixture: 5 statements; codex fixture: 6 (dogfood round 1
        # added machine-injected lines — excluded — and human survivors);
        # droid fixture: 7 (dogfood round 1, commit 2)
        self.assertEqual(self.log.count_all(), 5 + 6 + 7)
        sources = {
            row[0]
            for row in self.log._conn.execute("SELECT source FROM events")
        }
        self.assertEqual(sources, {"claude", "codex", "droid"})

    def test_two_threads_ingest_concurrently(self):
        # WAL + busy_timeout=5000 must make concurrent appends just work
        # (the "nightly dream" fan-out ingests many transcripts).
        errors: list[Exception] = []

        def worker(parsed):
            try:
                with EventLog(self.db_path, project="slug12345678") as log:
                    log.append(parsed)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(self.claude,)),
            threading.Thread(target=worker, args=(self.codex,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.log.count_all(), 5 + 6)  # claude + codex fixtures


class StorePathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = self._tmp.name

    def test_slug_is_short_sha1_of_real_path(self):
        slug = project_slug("/Users/x/proj")
        self.assertEqual(len(slug), 12)
        self.assertRegex(slug, r"^[0-9a-f]{12}$")
        self.assertEqual(slug, project_slug("/Users/x/proj"))  # deterministic
        self.assertNotEqual(slug, project_slug("/Users/x/proj2"))
        # trailing separator does not change the slug
        self.assertEqual(project_slug("/Users/x/proj/"), slug)

    def test_slug_uses_realpath_so_symlinks_collapse(self):
        # R5: /tmp vs /private/tmp spellings must map to one store.
        real = Path(self._tmp.name) / "real_project"
        real.mkdir()
        link = Path(self._tmp.name) / "link_to_project"
        link.symlink_to(real)
        self.assertEqual(project_slug(real), project_slug(link))

    def test_store_path_under_home(self):
        path = store_path("/Users/x/proj", home=self.home)
        self.assertEqual(
            path,
            Path(self.home)
            / ".jevmory"
            / "projects"
            / f"{project_slug('/Users/x/proj')}.db",
        )

    def test_for_project_creates_store_and_uses_slug(self):
        parsed = parse_claude_transcript(FIXTURES / "claude_session.jsonl")
        with EventLog.for_project("/Users/x/proj", home=self.home) as log:
            result = log.append(parsed)
            self.assertEqual(result.inserted, 5)
            self.assertEqual(log.project, project_slug("/Users/x/proj"))
            self.assertTrue(store_path("/Users/x/proj", home=self.home).exists())
            row = log._conn.execute("SELECT project FROM events LIMIT 1").fetchone()
            self.assertEqual(row[0], project_slug("/Users/x/proj"))


if __name__ == "__main__":
    unittest.main()
