"""Phase C batch planner tests (M4): same discipline as Phase A —
greedy fill, order preserved, coverage exact, evidence shared."""

from __future__ import annotations

import unittest

from jevmory.audit.batch import plan_phase_c
from jevmory.audit.memfile import MemoryLine
from jevmory.judgment.batch import BatchError
from jevmory.thresholds import BATCH_TOKEN_BUDGET


def make_lines(count, text="claim about how this project works"):
    return [
        MemoryLine(number=i + 1, raw=f"- {text} {i}", text=f"{text} {i}",
                   section=None)
        for i in range(count)
    ]


FACTS = [{"id": i, "text": f"recorded fact number {i}"} for i in range(5)]
STATEMENTS = [
    {"id": f"ev{i}", "role": "user", "text": f"statement text {i}", "ts": None}
    for i in range(5)
]


class PlanPhaseCTest(unittest.TestCase):
    def test_empty_lines_plan_zero_requests(self):
        self.assertEqual(plan_phase_c([], FACTS, STATEMENTS), [])

    def test_order_preserved_and_coverage_exact(self):
        lines = make_lines(25)
        batches = plan_phase_c(lines, FACTS, STATEMENTS)
        ids = [lid for batch in batches for lid in batch.candidate_ids]
        self.assertEqual(ids, [line.id for line in lines])
        # every batch's state carries its own lines in order
        for batch in batches:
            state_ids = [entry["id"] for entry in batch.state["memory_lines"]]
            self.assertEqual(state_ids, list(batch.candidate_ids))

    def test_every_batch_within_default_budget(self):
        batches = plan_phase_c(make_lines(120), FACTS, STATEMENTS)
        self.assertGreater(len(batches), 1)
        for batch in batches:
            self.assertLessEqual(batch.token_estimate(), BATCH_TOKEN_BUDGET)

    def test_evidence_is_shared_overhead(self):
        batches = plan_phase_c(make_lines(80), FACTS, STATEMENTS)
        self.assertGreater(len(batches), 1)
        for batch in batches:
            self.assertEqual(batch.state["evidence"]["facts"], FACTS)
            self.assertEqual(batch.state["evidence"]["statements"], STATEMENTS)

    def test_questions_match_line_positions(self):
        batches = plan_phase_c(make_lines(9), FACTS, STATEMENTS)
        for batch in batches:
            count = len(batch.state["memory_lines"])
            self.assertEqual(len(batch.questions), 3 * count)
            for i in range(count):
                self.assertIn(f"l{i}_disposition", batch.questions)

    def test_tight_budget_one_line_per_request(self):
        lines = make_lines(3)
        one = plan_phase_c(lines[:1], FACTS, STATEMENTS,
                           budget_tokens=10**9)[0].token_estimate()
        two = plan_phase_c(lines[:2], FACTS, STATEMENTS,
                           budget_tokens=10**9)[0].token_estimate()
        # budget fits exactly one line: every line lands alone
        batches = plan_phase_c(lines, FACTS, STATEMENTS, budget_tokens=one)
        self.assertEqual(len(batches), 3)
        self.assertLess(one, two)  # sanity: two lines really need more

    def test_single_line_overflow_raises_batch_error(self):
        lines = make_lines(1)
        one = plan_phase_c(lines, FACTS, STATEMENTS,
                           budget_tokens=10**9)[0].token_estimate()
        with self.assertRaises(BatchError):
            plan_phase_c(lines, FACTS, STATEMENTS, budget_tokens=one - 1)

    def test_huge_evidence_pool_overflow_raises_batch_error(self):
        huge_facts = [
            {"id": i, "text": "evidence fact with a long claim " * 20}
            for i in range(400)
        ]
        with self.assertRaises(BatchError):
            plan_phase_c(make_lines(1), huge_facts, [], budget_tokens=1000)

    def test_synthetic_load_300_lines(self):
        lines = make_lines(300)
        batches = plan_phase_c(lines, FACTS, STATEMENTS)
        ids = [lid for batch in batches for lid in batch.candidate_ids]
        self.assertEqual(ids, [line.id for line in lines])
        for batch in batches:
            self.assertLessEqual(batch.token_estimate(), BATCH_TOKEN_BUDGET)

    def test_nonpositive_budget_raises_value_error(self):
        with self.assertRaises(ValueError):
            plan_phase_c(make_lines(1), FACTS, STATEMENTS, budget_tokens=0)


if __name__ == "__main__":
    unittest.main()
