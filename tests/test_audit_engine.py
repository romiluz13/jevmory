"""Audit engine tests (M4): end-to-end runs against a real store with
FakeJev — verdicts, receipts, run stats, evidence pool, error path."""

from __future__ import annotations

import json
import tempfile
import unittest

from jevmory.audit.engine import run_audit
from jevmory.audit.questions import DISPOSITION_OPTIONS
from jevmory.judgment.errors import JevRetryExhausted
from jevmory.judgment.fake import FakeJev
from jevmory.memory.facts import add_fact
from jevmory.memory.schema import connect, migrate
from jevmory.thresholds import (
    AUDIT_EVIDENCE_FACTS,
    AUDIT_EVIDENCE_STATEMENTS,
)

NOW = "2026-09-19T02:00:00Z"

MEMORY = """# jevmory.md

## Tooling
- claim one about tooling
- claim two about tooling
- claim three about tooling
"""


class AuditEngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(f"{self._tmp.name}/store.db")
        migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def add_facts(self, count, project="p", claim="recorded fact {i}",
                  now=None):
        for i in range(count):
            add_fact(
                self.conn, project=project,
                claim=claim.format(i=i), category="convention",
                significance=1.2, durable_noul=0.9,
                now=now or f"2026-09-19T00:{i:02d}:00Z",
            )

    def add_events(self, count, project="p", start=0):
        for i in range(start, start + count):
            self.conn.execute(
                "INSERT INTO events (id, project, source, session_id, ts, "
                "role, text, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"ev{i:04d}", project, "test", "sess",
                 f"2026-09-01T00:{i % 60:02d}:00Z", "user",
                 f"statement number {i}", "2026-09-19T00:00:00Z"),
            )
        self.conn.commit()

    def run_engine(self, client, text=MEMORY, project="p"):
        return run_audit(
            text, self.conn, project=project, client=client,
            memory_file="MEMORY.md", now=NOW,
        )

    def run_row(self):
        return self.conn.execute(
            "SELECT kind, started_at, finished_at, stats, error "
            "FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def judgments(self):
        return self.conn.execute(
            "SELECT subject_id, question_id, type, answer_json, created_at "
            "FROM judgments ORDER BY id"
        ).fetchall()

    # --- happy path ------------------------------------------------------

    def test_normal_mode_all_keep(self):
        self.add_facts(2)
        client = FakeJev()
        report = self.run_engine(client)

        self.assertEqual(
            report.counts,
            {"KEEP": 3, "STALE": 0, "WRONG": 0, "UNSUPPORTED": 0,
             "REVIEW": 0, "VERIFIED": 0},
        )
        self.assertEqual(report.api_calls, 1)
        self.assertEqual(report.usage_tokens, 360)  # (312+48) per call
        self.assertEqual(report.evidence_facts, 2)
        self.assertEqual(report.evidence_statements, 0)
        self.assertEqual(
            [v.line.number for v in report.lines], [4, 5, 6]
        )
        self.assertEqual(report.run_id, 1)

        kind, started, finished, stats, error = self.run_row()
        self.assertEqual(kind, "audit")
        self.assertEqual(started, NOW)
        self.assertEqual(finished, NOW)
        self.assertIsNone(error)
        stats = json.loads(stats)
        self.assertEqual(stats["api_calls"], 1)
        self.assertEqual(stats["usage"], 360)
        self.assertEqual(stats["lines_audited"], 3)
        self.assertEqual(stats["lines_keep"], 3)
        self.assertEqual(stats["lines_review"], 0)

    def test_state_reaches_client_with_default_context(self):
        self.add_facts(1)
        client = FakeJev()
        self.run_engine(client)
        state, questions = client.requests[0]
        self.assertEqual(state["project_context"], {"name": "p"})
        self.assertEqual(
            [entry["id"] for entry in state["memory_lines"]],
            ["line:4", "line:5", "line:6"],
        )
        self.assertEqual(len(questions), 9)

    def test_receipts_three_per_line_with_wire_shape(self):
        client = FakeJev()
        self.run_engine(client)
        rows = self.judgments()
        self.assertEqual(len(rows), 9)
        by_subject = {}
        for subject_id, question_id, answer_type, answer_json, created in rows:
            self.assertEqual(created, NOW)
            answer = json.loads(answer_json)
            self.assertEqual(answer["type"], answer_type)  # wire shape kept
            by_subject.setdefault(subject_id, {})[question_id] = answer
        self.assertEqual(
            sorted(by_subject), ["line:4", "line:5", "line:6"]
        )
        for answers in by_subject.values():
            self.assertEqual(
                sorted(answers), ["contradicted", "disposition", "supported"]
            )
            self.assertEqual(answers["supported"]["noul"], 0.9)
            self.assertEqual(answers["disposition"]["choice"], "keep")

    # --- stage 1: deterministic anchors (v0.2) ---------------------------

    def test_anchored_line_is_verified_with_zero_api_spend(self):
        # two of three lines exist verbatim in the store as facts
        self.add_facts(1, claim="claim one about tooling")
        self.add_facts(1, claim="claim three about tooling", now="2026-09-19T00:05:00Z")
        client = FakeJev()
        report = self.run_engine(client)

        self.assertEqual(
            report.counts,
            {"KEEP": 1, "STALE": 0, "WRONG": 0, "UNSUPPORTED": 0,
             "REVIEW": 0, "VERIFIED": 2},
        )
        # only the unanchored line went to the model
        self.assertEqual(report.api_calls, 1)
        self.assertEqual(client.call_count, 1)
        # one question triple per line in the request: 3 for the ONE line
        state, questions = client.requests[0]
        self.assertEqual(
            [entry["id"] for entry in state["memory_lines"]], ["line:5"]
        )
        # file order preserved although stage 1 emitted lines 4 and 6 first
        self.assertEqual(
            [v.line.number for v in report.lines], [4, 5, 6]
        )
        keywords = [v.keyword for v in report.lines]
        self.assertEqual(keywords, ["VERIFIED", "KEEP", "VERIFIED"])
        anchored = [v for v in report.lines if v.anchored]
        self.assertEqual([v.anchor_fact_id for v in anchored], [1, 2])
        # receipts: the anchored lines never produced judgment rows
        rows = self.judgments()
        self.assertEqual(len(rows), 3)  # line:5 only
        self.assertEqual(
            {row[0] for row in rows}, {"line:5"}
        )
        stats = json.loads(self.run_row()[3])
        self.assertEqual(stats["api_calls"], 1)
        self.assertEqual(stats["lines_verified"], 2)
        self.assertIn("2 verified verbatim", report.summary_line())

    def test_anchor_write_back_stamps_verified_at_vintage(self):
        self.add_facts(1, claim="claim one about tooling")
        self.run_engine(FakeJev())
        stamped = self.conn.execute(
            "SELECT verified_at FROM facts WHERE id = 1"
        ).fetchone()[0]
        self.assertEqual(stamped, NOW)  # the run's now, not wall clock
        # facts that were not matched keep NULL (never audited)
        self.add_facts(1, claim="unrelated fact", now="2026-09-19T00:09:00Z")
        self.run_engine(FakeJev())
        unrelated = self.conn.execute(
            "SELECT verified_at FROM facts WHERE claim = 'unrelated fact'"
        ).fetchone()[0]
        self.assertIsNone(unrelated)

    def test_all_lines_anchored_is_zero_spend_but_still_a_run(self):
        for text in ("claim one about tooling", "claim two about tooling",
                     "claim three about tooling"):
            self.add_facts(1, claim=text)
        client = FakeJev()
        report = self.run_engine(client)

        self.assertEqual(report.api_calls, 0)
        self.assertEqual(client.call_count, 0)
        self.assertEqual(len(self.judgments()), 0)
        self.assertIsNotNone(report.run_id)  # provenance row, zero spend
        self.assertEqual(report.counts["VERIFIED"], 3)
        self.assertEqual(report.evidence_facts, 0)  # no evidence sent
        self.assertIn("3 verified verbatim (no api call)", report.summary_line())

    def test_anchoring_is_project_scoped(self):
        # the same claim in ANOTHER project's store never anchors
        self.add_facts(1, project="other", claim="claim one about tooling")
        client = FakeJev()
        report = self.run_engine(client)
        self.assertEqual(report.counts["VERIFIED"], 0)
        self.assertEqual(report.api_calls, 1)  # stage 2 graded it

    def test_retired_facts_never_anchor(self):
        self.add_facts(1, claim="claim one about tooling")
        self.conn.execute("UPDATE facts SET status='retired' WHERE id = 1")
        self.conn.commit()
        report = self.run_engine(FakeJev())
        self.assertEqual(report.counts["VERIFIED"], 0)

    # --- mode coverage ---------------------------------------------------

    def test_adversarial_mode_lands_in_review_band(self):
        client = FakeJev(mode="adversarial")
        report = self.run_engine(client)
        self.assertEqual(report.counts["REVIEW"], 3)
        self.assertEqual(report.counts["KEEP"], 0)
        for verdict in report.lines:
            self.assertFalse(verdict.decisive)
            self.assertEqual(verdict.disposition, "keep")  # raw kept
            self.assertEqual(verdict.confidence, 0.0)

    def test_scripted_mode_mixed_dispositions(self):
        scripted = {}
        plan = [  # position -> (disposition, confidence); 0.8 = gate edge
            ("keep", 0.95), ("stale", 0.9), ("wrong", 0.85),
            ("unsupported", 0.8), ("keep", 0.3),
        ]
        for i, (disposition, confidence) in enumerate(plan):
            scripted[f"l{i}_supported"] = {"type": "noul", "noul": 0.9}
            scripted[f"l{i}_contradicted"] = {"type": "noul", "noul": 0.1}
            scripted[f"l{i}_disposition"] = {
                "type": "choice", "choice": disposition,
                "probabilities": {
                    option: (0.7 if option == disposition else 0.1)
                    for option in DISPOSITION_OPTIONS
                },
                "confidence": confidence,
            }
        memory = "# jevmory.md\n" + "\n".join(
            f"- scripted claim {i}" for i in range(5)
        )
        report = self.run_engine(FakeJev(mode="scripted", answers=scripted),
                                 text=memory)
        keywords = [v.keyword for v in report.lines]
        self.assertEqual(
            keywords, ["KEEP", "STALE", "WRONG", "UNSUPPORTED", "REVIEW"]
        )
        self.assertEqual(
            report.summary_line(),
            "your memory has 1 stale line, 1 wrong line, "
            "1 unsupported line, 1 line needing review; 1 keep",
        )

    # --- zero spend ------------------------------------------------------

    def test_structural_only_memory_file_is_zero_spend(self):
        client = FakeJev()
        report = self.run_engine(
            client, text="# jevmory.md\n\n## Tooling\n\n| a | b |\n---\n"
        )
        self.assertEqual(report.lines, ())
        self.assertEqual(report.api_calls, 0)
        self.assertIsNone(report.run_id)
        self.assertEqual(
            report.summary_line(),
            "all 0 lines hold up — nothing stale, nothing wrong",
        )
        self.assertEqual(client.call_count, 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0
        )
        self.assertEqual(len(self.judgments()), 0)

    # --- evidence pool ---------------------------------------------------

    def test_evidence_pool_caps_recency_and_project_scope(self):
        self.add_facts(AUDIT_EVIDENCE_FACTS + 5)          # p: ids 1..35
        self.add_facts(2, project="other", claim="other {i}")  # excluded
        self.conn.execute("UPDATE facts SET status='retired' WHERE id=35")
        self.conn.commit()
        self.add_events(AUDIT_EVIDENCE_STATEMENTS + 5)     # p: ev0..ev44
        self.add_events(3, project="other", start=100)     # excluded

        client = FakeJev()
        report = self.run_engine(client)
        self.assertEqual(report.evidence_facts, AUDIT_EVIDENCE_FACTS)
        self.assertEqual(report.evidence_statements, AUDIT_EVIDENCE_STATEMENTS)

        state, _ = client.requests[0]
        facts = state["evidence"]["facts"]
        statements = state["evidence"]["statements"]
        # most recent 30 active p facts (35 retired), oldest first
        self.assertEqual([f["id"] for f in facts], list(range(5, 35)))
        # most recent 40 p events, oldest first
        self.assertEqual(
            [s["text"] for s in statements],
            [f"statement number {i}" for i in range(5, 45)],
        )
        self.assertNotIn("other 0", json.dumps(state))

    # --- multi-batch mapping ----------------------------------------------

    def test_positions_reset_per_batch_across_batches(self):
        # long claims force ~2 lines per batch at the default budget
        text = "# jevmory.md\n" + "\n".join(
            "- " + "y" * 44000 for _ in range(10)
        )
        scripted = {}
        for i in range(10):
            target = "stale" if i == 0 else "keep"
            scripted[f"l{i}_supported"] = {"type": "noul", "noul": 0.9}
            scripted[f"l{i}_contradicted"] = {"type": "noul", "noul": 0.1}
            scripted[f"l{i}_disposition"] = {
                "type": "choice", "choice": target,
                "probabilities": {
                    option: (0.7 if option == target else 0.1)
                    for option in DISPOSITION_OPTIONS
                },
                "confidence": 0.9,
            }
        client = FakeJev(mode="scripted", answers=scripted)
        report = self.run_engine(client, text=text)

        self.assertGreater(client.call_count, 1)  # really multi-batch
        self.assertEqual(report.api_calls, client.call_count)
        self.assertEqual([v.line.number for v in report.lines],
                         list(range(2, 12)))  # file order preserved
        # position 0 of EVERY batch was scripted stale: one stale line
        # per request, everything else keep — ids reset per batch
        self.assertEqual(report.counts["STALE"], client.call_count)
        self.assertEqual(report.counts["KEEP"], 10 - client.call_count)
        for verdict in report.lines:
            self.assertIn(verdict.disposition, ("stale", "keep"))
        stats = json.loads(self.run_row()[3])
        self.assertEqual(stats["lines_audited"], 10)
        self.assertEqual(len(self.judgments()), 30)

    # --- error path --------------------------------------------------------

    def test_client_error_finishes_run_and_reraises(self):
        client = FakeJev(
            errors={"always": JevRetryExhausted("transport dead")}
        )
        with self.assertRaises(JevRetryExhausted):
            self.run_engine(client)
        kind, started, finished, stats, error = self.run_row()
        self.assertEqual(kind, "audit")
        self.assertEqual(error, "JevRetryExhausted")
        self.assertEqual(finished, NOW)
        self.assertIsNone(stats)  # no stats on a failed run
        self.assertEqual(len(self.judgments()), 0)


if __name__ == "__main__":
    unittest.main()
