"""Audit report renderer tests (M4): deterministic shapes for the
terminal screenshot, the md table, and the json report."""

from __future__ import annotations

import json
import unittest

from dream_md.audit.memfile import MemoryLine
from dream_md.audit.report import (
    AuditReport,
    build_report,
    render_json,
    render_md,
    render_terminal,
)
from dream_md.audit.rules import REVIEW, LineVerdict


def keep(number, text="holds up fine", section=None):
    return LineVerdict(
        line=MemoryLine(number=number, raw=f"- {text}", text=text,
                        section=section),
        disposition="keep", decisive=True, confidence=0.9,
        supported=0.9, contradicted=0.08,
    )


def problem(number, disposition, confidence=0.8, text="a claim that aged",
            section=None, supported=0.4, contradicted=0.5):
    return LineVerdict(
        line=MemoryLine(number=number, raw=f"- {text}", text=text,
                        section=section),
        disposition=disposition, decisive=confidence >= 0.6,
        confidence=confidence, supported=supported, contradicted=contradicted,
    )


def report(verdicts, **kwargs):
    return build_report(
        memory_file="MEMORY.md",
        verdicts=verdicts,
        run_id=7,
        api_calls=2,
        usage_tokens=480,
        evidence_facts=12,
        evidence_statements=40,
        **kwargs,
    )


class SummaryLineTest(unittest.TestCase):
    def test_all_keep(self):
        self.assertEqual(
            report([keep(4), keep(5), keep(6)]).summary_line(),
            "all 3 lines hold up — nothing stale, nothing wrong",
        )

    def test_zero_lines(self):
        self.assertEqual(
            report([]).summary_line(),
            "all 0 lines hold up — nothing stale, nothing wrong",
        )

    def test_mixed_counts_in_demo_voice(self):
        verdicts = [
            keep(4), keep(5), keep(6),
            problem(7, "stale"), problem(8, "stale"),
            problem(9, "wrong"),
            problem(10, "unsupported"),
            problem(11, "keep", confidence=0.4),
        ]
        self.assertEqual(
            report(verdicts).summary_line(),
            "your memory has 2 stale lines, 1 wrong line, "
            "1 unsupported line, 1 line needing review; 3 keep",
        )

    def test_singular_nouns(self):
        verdicts = [problem(7, "stale"), keep(4)]
        self.assertEqual(
            report(verdicts).summary_line(),
            "your memory has 1 stale line; 1 keep",
        )


class ProblemLinesTest(unittest.TestCase):
    def test_non_keep_in_file_order(self):
        verdicts = [
            keep(4),
            problem(9, "wrong"),
            problem(6, "stale"),
            problem(7, "keep", confidence=0.3),
        ]
        numbers = [v.line.number for v in report(verdicts).problem_lines()]
        self.assertEqual(numbers, [6, 7, 9])


class RenderTerminalTest(unittest.TestCase):
    def test_header_and_aligned_problem_rows(self):
        verdicts = [
            keep(4, text="fine claim"),
            problem(7, "stale", text="we use Postgres in staging"),
            problem(8, "wrong", confidence=0.7, text="the port is 8000"),
        ]
        out = render_terminal(report(verdicts))
        lines = out.splitlines()
        self.assertEqual(lines[0], "dream-md audit — MEMORY.md")
        self.assertTrue(lines[3].startswith("LINE"))
        # pinned layout: number 0:4, verdict 6:17, conf 19:23,
        # supp 25:29, contra 31:37, claim 39:
        rows = lines[4:6]
        for row, (number, keyword, conf) in zip(
            rows, [(7, "STALE", "0.80"), (8, "WRONG", "0.70")]
        ):
            self.assertEqual(row[:4].strip(), str(number))
            self.assertEqual(row[6:17].strip(), keyword)
            self.assertEqual(row[19:23].strip(), conf)
        self.assertNotIn("fine claim", out)  # keep lines stay out

    def test_all_keep_no_table(self):
        out = render_terminal(report([keep(4), keep(5)]))
        self.assertNotIn("LINE", out)
        self.assertIn("all 2 lines hold up", out)
        self.assertIn("receipts: run 7", out)

    def test_receipts_footer(self):
        out = render_terminal(report([keep(4)]))
        self.assertIn(
            "receipts: run 7 · 2 api calls · 480 tokens · "
            "evidence: 12 facts, 40 statements",
            out,
        )

    def test_no_run_footer(self):
        out = render_terminal(
            AuditReport(memory_file="MEMORY.md", counts={"KEEP": 0})
        )
        self.assertIn("receipts: no run (nothing graded) · 0 api calls", out)

    def test_long_claim_clipped_deterministically(self):
        long_text = "x" * 60
        verdicts = [problem(7, "stale", text=long_text)]
        out = render_terminal(report(verdicts))
        row = [l for l in out.splitlines() if l.startswith("   7")][0]
        self.assertEqual(row[39:], "x" * 43 + "…")


class RenderMdTest(unittest.TestCase):
    def test_every_line_in_table_including_keeps(self):
        verdicts = [
            keep(4, text="fine claim", section="Tooling"),
            problem(7, "stale", text="we use Postgres in staging"),
        ]
        out = render_md(report(verdicts))
        self.assertIn("# dream-md audit — MEMORY.md", out)
        self.assertIn(
            "| line | section | verdict | confidence | supported "
            "| contradicted | claim |",
            out,
        )
        self.assertIn("| 4 | Tooling | KEEP | 0.90 | 0.90 | 0.08 | fine claim |", out)
        self.assertIn(
            "| 7 |  | STALE | 0.80 | 0.40 | 0.50 "
            "| we use Postgres in staging |",
            out,
        )

    def test_pipes_in_claims_escaped(self):
        verdicts = [keep(4, text="a | b")]
        out = render_md(report(verdicts))
        self.assertIn("a \\| b", out)


class RenderJsonTest(unittest.TestCase):
    def test_roundtrip_shape(self):
        verdicts = [
            keep(4, section="Tooling"),
            problem(7, "stale"),
            problem(8, "keep", confidence=0.4),
        ]
        payload = json.loads(render_json(report(verdicts)))
        self.assertEqual(payload["memory_file"], "MEMORY.md")
        self.assertEqual(payload["run_id"], 7)
        self.assertEqual(payload["api_calls"], 2)
        self.assertEqual(payload["usage_tokens"], 480)
        self.assertEqual(payload["evidence"], {"facts": 12, "statements": 40})
        self.assertEqual(
            payload["counts"],
            {"KEEP": 1, "STALE": 1, "WRONG": 0, "UNSUPPORTED": 0, REVIEW: 1},
        )
        self.assertEqual(
            [entry["id"] for entry in payload["lines"]],
            ["line:4", "line:7", "line:8"],
        )
        stale = payload["lines"][1]
        self.assertEqual(
            stale,
            {
                "line": 7,
                "id": "line:7",
                "section": None,
                "claim": "a claim that aged",
                "disposition": "stale",
                "decisive": True,
                "keyword": "STALE",
                "confidence": 0.8,
                "supported": 0.4,
                "contradicted": 0.5,
            },
        )
        self.assertIn("your memory has", payload["summary"])

    def test_counts_match_lines(self):
        verdicts = [keep(4), problem(7, "stale"), problem(8, "wrong")]
        payload = json.loads(render_json(report(verdicts)))
        keywords = [entry["keyword"] for entry in payload["lines"]]
        for keyword, count in payload["counts"].items():
            self.assertEqual(keywords.count(keyword), count)


class BuildReportTest(unittest.TestCase):
    def test_counts_derived_never_hand_passed(self):
        built = build_report(
            memory_file="MEMORY.md",
            verdicts=[keep(4), problem(7, "stale")],
        )
        self.assertEqual(built.counts["KEEP"], 1)
        self.assertEqual(built.counts["STALE"], 1)
        self.assertEqual(built.run_id, None)
        self.assertEqual(built.api_calls, 0)

    def test_lines_is_a_tuple_in_file_order(self):
        verdicts = [keep(4), problem(5, "stale")]
        built = build_report(memory_file="MEMORY.md", verdicts=verdicts)
        self.assertIsInstance(built.lines, tuple)
        self.assertEqual([v.line.number for v in built.lines], [4, 5])


if __name__ == "__main__":
    unittest.main()
