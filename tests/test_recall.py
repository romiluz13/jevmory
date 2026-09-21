"""SessionStart recall tests: rendering, ordering, and the never-die stance.

All timestamps pinned; every store lives under a fresh $JEVMORY_HOME
temp dir so tests never touch the real ~/.jevmory.
"""

from __future__ import annotations

import io
import json
import os
import contextlib
import tempfile
import unittest
from pathlib import Path

from jevmory.ingestion.eventlog import project_slug, store_path
from jevmory.memory.facts import add_fact
from jevmory.memory.schema import connect, migrate
from jevmory.recall import render_recall, run_recall

T0 = "2026-09-19T00:00:00Z"
T1 = "2026-09-19T00:01:00Z"
T2 = "2026-09-19T00:02:00Z"
T3 = "2026-09-19T00:03:00Z"


class RecallTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._env = os.environ.get("JEVMORY_HOME")
        os.environ["JEVMORY_HOME"] = str(self.home)
        self.project = self.home / "proj"
        self.project.mkdir()

    def tearDown(self):
        if self._env is None:
            os.environ.pop("JEVMORY_HOME", None)
        else:
            os.environ["JEVMORY_HOME"] = self._env
        self._tmp.cleanup()

    def seed(self, claim, *, category="convention", significance=1.2,
             durable=0.9, now=T0, event_ids=("ev1",)):
        conn = connect(store_path(str(self.project), home=self.home))
        try:
            migrate(conn)
            return add_fact(
                conn, project=project_slug(str(self.project)), claim=claim,
                category=category, significance=significance,
                durable_noul=durable, source_event_ids=event_ids, now=now,
            )
        finally:
            conn.close()


class RenderRecallTest(RecallTestCase):
    def test_no_store_is_silent(self):
        self.assertIsNone(render_recall(str(self.project), home=self.home))

    def test_store_without_facts_is_silent(self):
        store = store_path(str(self.project), home=self.home)
        store.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(store)
        migrate(conn)
        conn.close()
        self.assertIsNone(render_recall(str(self.project), home=self.home))

    def test_renders_facts_confidence_ordered_with_footer(self):
        self.seed("We use uv, never pip.", now=T0)
        self.seed("The API prefix is /api/v3.", now=T1)
        text = render_recall(str(self.project), home=self.home)
        assert text is not None
        lines = text.splitlines()
        self.assertIn("remembered facts for proj", lines[0])
        self.assertIn("We use uv, never pip.", lines[1])
        self.assertIn("conf", lines[1])
        self.assertIn("said 2026-09-19", lines[1])
        self.assertIn("jevmory.md", lines[-1])

    def test_limit_caps_fact_lines(self):
        for i in range(5):
            self.seed(f"Fact number {i} about testing.", now=T0)
        text = render_recall(str(self.project), limit=3, home=self.home)
        assert text is not None
        fact_lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(fact_lines), 3)

    def test_long_claim_is_truncated_with_ellipsis(self):
        self.seed("x" * 400, now=T0)
        text = render_recall(str(self.project), home=self.home)
        assert text is not None
        fact_line = [ln for ln in text.splitlines() if ln.startswith("- ")][0]
        self.assertIn("…", fact_line)
        self.assertLess(len(fact_line), 300)

    def test_only_active_facts_render(self):
        self.seed("Retired convention.", now=T0)
        conn = connect(store_path(str(self.project), home=self.home))
        try:
            from jevmory.memory.facts import retire

            retire(conn, 1, now=T1)
        finally:
            conn.close()
        self.assertIsNone(render_recall(str(self.project), home=self.home))

    def test_corrupt_store_is_silent_not_fatal(self):
        store = store_path(str(self.project), home=self.home)
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_bytes(b"this is not sqlite")
        self.assertIsNone(render_recall(str(self.project), home=self.home))


class RunRecallTest(RecallTestCase):
    def _stdout(self, argv, stdin_text):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run_recall(argv, stdin_text, home=self.home)
        return rc, buf.getvalue()

    def test_payload_cwd_locates_project(self):
        self.seed("We use uv, never pip.", now=T0)
        rc, out = self._stdout([], json.dumps({"cwd": str(self.project)}))
        self.assertEqual(rc, 0)
        self.assertIn("We use uv, never pip.", out)

    def test_project_flag_overrides_payload(self):
        self.seed("Flag wins over payload.", now=T0)
        other = self.home / "other"
        other.mkdir()
        rc, out = self._stdout(
            ["--project", str(self.project)],
            json.dumps({"cwd": str(other)}),
        )
        self.assertEqual(rc, 0)
        self.assertIn("Flag wins over payload.", out)

    def test_no_store_prints_nothing(self):
        rc, out = self._stdout([], json.dumps({"cwd": str(self.project)}))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_garbage_payload_is_silent_zero(self):
        rc, out = self._stdout([], "not json at all")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
