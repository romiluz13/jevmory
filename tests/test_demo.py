"""Planted-error demo pins (PART 7): the screenshot is guaranteed.

``demo/run_demo.py`` must ALWAYS produce one stale line, one wrong
line, one unsupported line with receipts — offline, deterministic,
never touching the real ``~/.jev-md``. These tests pin that promise;
if someone edits the fixture or the demo asker and breaks it, this
file is the tripwire.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "run_demo", ROOT / "demo" / "run_demo.py"
)
run_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_demo)


class DemoReportTest(unittest.TestCase):
    """The rendered report: planted errors present, receipts honest."""

    @classmethod
    def setUpClass(cls):
        cls.report = run_demo.build_demo_report()

    def test_summary_counts_the_planted_errors(self):
        self.assertIn(
            "your memory has 1 stale line, 1 wrong line, "
            "1 unsupported line; 2 keep",
            self.report,
        )

    def test_problem_table_has_one_row_per_planted_error(self):
        # verdict column rows: a line number, then the keyword
        for keyword in ("STALE", "WRONG", "UNSUPPORTED"):
            self.assertRegex(
                self.report, re.compile(rf"^\s*\d+\s+{keyword}\b", re.M)
            )
        # nothing lands in the review band in the demo
        self.assertNotIn("REVIEW", self.report)

    def test_receipts_footer_is_honest_about_the_demo(self):
        self.assertIn("receipts: run ", self.report)
        self.assertIn(" 1 api calls", self.report)
        self.assertIn("evidence: 0 facts, 6 statements", self.report)

    def test_deterministic_two_runs_byte_identical(self):
        self.assertEqual(self.report, run_demo.build_demo_report())

    def test_store_lands_in_the_throwaway_home_only(self):
        home = Path(tempfile.mkdtemp(prefix="demo-test-"))
        try:
            run_demo.build_demo_report(home=home)
            store = run_demo.store_path(str(run_demo.DEMO_DIR), home=home)
            self.assertTrue(store.exists())
        finally:
            shutil.rmtree(home, ignore_errors=True)


class PlantedJevTest(unittest.TestCase):
    """The demo asker: strict parsing, substring matching, keep default."""

    def _ask(self, texts):
        state = {"memory_lines": [{"id": f"line:{n}", "number": n,
                                   "section": "", "text": t}
                                  for n, t in enumerate(texts, 1)]}
        questions = {}
        for i in range(len(texts)):
            questions.update(_phase_c_triple(i))
        return run_demo.PlantedJev().ask(state, questions)

    def test_planted_dispositions_by_substring(self):
        response = self._ask(
            [
                "The build runs on Bun; bun run build is the only command.",
                "The test suite runs with pytest.",
                "Failed API requests retry up to five times.",
                "State lives in a local SQLite store.",
            ]
        )
        choices = {
            qid: answer.choice
            for qid, answer in response.answers.items()
            if qid.endswith("_disposition")
        }
        self.assertEqual(
            choices,
            {
                "l0_disposition": "stale",
                "l1_disposition": "wrong",
                "l2_disposition": "unsupported",
                "l3_disposition": "keep",
            },
        )

    def test_noul_receipts_match_the_planted_values(self):
        response = self._ask(["The test suite runs with pytest."])
        self.assertAlmostEqual(
            response.answers["l0_supported"].noul, 0.07
        )
        self.assertAlmostEqual(
            response.answers["l0_contradicted"].noul, 0.93
        )

    def test_answers_go_through_the_strict_parser(self):
        # peaked probabilities must sum to 1 or parse_answer rejects —
        # proving the demo cannot quietly render off-spec answers
        response = self._ask(["The test suite runs with pytest."])
        disposition = response.answers["l0_disposition"]
        self.assertAlmostEqual(sum(disposition.probabilities.values()), 1.0)
        self.assertEqual(disposition.confidence, 0.93)

    def test_asker_records_requests_like_fakejev(self):
        asker = run_demo.PlantedJev()
        state = {"memory_lines": [{"id": "line:1", "number": 1,
                                   "section": "", "text": "anything"}]}
        asker.ask(state, _phase_c_triple(0))
        self.assertEqual(asker.call_count, 1)
        self.assertEqual(len(asker.requests), 1)
        recorded_state, recorded_questions = asker.requests[0]
        self.assertIs(recorded_state, state)
        self.assertEqual(set(recorded_questions), set(_phase_c_triple(0)))


def _phase_c_triple(i):
    from jev_md.audit.questions import phase_c_triple

    return phase_c_triple(i)


class DemoCommandTest(unittest.TestCase):
    """`python3 demo/run_demo.py` — the one-command story."""

    def test_main_runs_clean_via_subprocess(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "demo" / "run_demo.py")],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 stale line, 1 wrong line", result.stdout)
        # T7: the printed "live equivalent" must be the complete 3-step
        # sequence — copy-paste hits the opt-in gate without init
        self.assertIn("jev-md init --enable-grading", result.stdout)
        self.assertIn("receipts: run ", result.stdout)


if __name__ == "__main__":
    unittest.main()
