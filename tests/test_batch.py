"""Batch-planner tests: greedy packing, exact coverage, fallback floor.

Budgets in the split tests are derived from the planner's own
estimator (measure one candidate, use that as the budget), so the
tests stay robust to instruction-text edits while still pinning the
packing behavior: order preserved, every candidate in exactly one
batch, no batch over budget, per-batch question ids re-indexed from
``c0_``.
"""

from __future__ import annotations

import unittest

from dream_md.ingestion.extract import Candidate
from dream_md.judgment.batch import BatchError, plan_phase_a
from dream_md.judgment.questions import phase_a_questions, phase_a_state
from dream_md.judgment.tokens import estimate_tokens
from dream_md.thresholds import BATCH_TOKEN_BUDGET


def candidate(i, text=None, context=None):
    return Candidate(
        event_id=f"ev{i:04d}",
        role="user",
        text=text or f"Always run `make lint` before pushing branch {i}.",
        context=context or "[assistant] Understood, noted.",
        ts=None,
        session_id="s1",
    )


CTX = {"slug": "demo", "stack": ["python", "sqlite"]}


def one_candidate_tokens(c):
    return estimate_tokens(phase_a_state(CTX, [c]), phase_a_questions(1))


def flatten(batches):
    return [cid for batch in batches for cid in batch.candidate_ids]


def assert_valid_batches(testcase, batches, expected_ids, budget):
    testcase.assertEqual(flatten(batches), expected_ids)
    for batch in batches:
        testcase.assertLessEqual(batch.token_estimate(), budget)
        # question ids are re-indexed per batch from c0_
        positions = {
            int(qid.split("_")[0][1:]) for qid in batch.questions
        }
        testcase.assertEqual(
            positions, set(range(len(batch.candidate_ids)))
        )
        # every batch grades 3 questions per candidate
        testcase.assertEqual(
            len(batch.questions), 3 * len(batch.candidate_ids)
        )
        # state carries exactly this batch's candidates
        testcase.assertEqual(
            [entry["id"] for entry in batch.state["candidates"]],
            list(batch.candidate_ids),
        )
        testcase.assertEqual(batch.state["project_context"], CTX)


class PackingTest(unittest.TestCase):
    def test_empty_input_plans_no_requests(self):
        self.assertEqual(plan_phase_a([], CTX), [])

    def test_zero_budget_rejected(self):
        with self.assertRaises(ValueError):
            plan_phase_a([candidate(0)], CTX, budget_tokens=0)

    def test_single_candidate_single_batch(self):
        c = candidate(0)
        batches = plan_phase_a([c], CTX)
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertEqual(batch.candidate_ids, ("ev0000",))
        self.assertEqual(
            sorted(batch.questions), ["c0_category", "c0_durable", "c0_significance"]
        )
        self.assertEqual(
            batch.token_estimate(), one_candidate_tokens(c)
        )

    def test_default_budget_keeps_order_and_coverage(self):
        cands = [candidate(i) for i in range(7)]
        batches = plan_phase_a(cands, CTX)
        assert_valid_batches(
            self, batches, [c.event_id for c in cands], BATCH_TOKEN_BUDGET
        )

    def test_budget_boundary_splits_batches(self):
        # budget = exactly two candidates' request -> [2, 2, 1] packing
        cands = [candidate(i) for i in range(5)]
        two = estimate_tokens(
            phase_a_state(CTX, [cands[0], cands[1]]), phase_a_questions(2)
        )
        batches = plan_phase_a(cands, CTX, budget_tokens=two)
        self.assertEqual(
            [len(b.candidate_ids) for b in batches], [2, 2, 1]
        )
        assert_valid_batches(
            self, batches, [c.event_id for c in cands], two
        )

    def test_per_candidate_fallback_floor(self):
        # budget = exactly one candidate's request -> all singletons
        cands = [candidate(i) for i in range(4)]
        one = one_candidate_tokens(cands[0])
        batches = plan_phase_a(cands, CTX, budget_tokens=one)
        self.assertEqual(
            [len(b.candidate_ids) for b in batches], [1, 1, 1, 1]
        )
        assert_valid_batches(self, batches, [c.event_id for c in cands], one)

    def test_single_candidate_overflow_raises(self):
        c = candidate(0)
        one = one_candidate_tokens(c)
        with self.assertRaises(BatchError) as ctx:
            plan_phase_a([c], CTX, budget_tokens=one - 1)
        self.assertIn("ev0000", str(ctx.exception))

    def test_project_context_counts_toward_budget(self):
        # a fat project context splits candidates into singleton batches
        fat = {"notes": "n" * 12000}
        cands = [candidate(0), candidate(1)]
        one_fat = estimate_tokens(
            phase_a_state(fat, [cands[0]]), phase_a_questions(1)
        )
        two_fat = estimate_tokens(
            phase_a_state(fat, [cands[0], cands[1]]), phase_a_questions(2)
        )
        budget = one_fat  # fits one, not two
        batches = plan_phase_a(cands, fat, budget_tokens=budget)
        self.assertEqual([len(b.candidate_ids) for b in batches], [1, 1])
        with self.assertRaises(BatchError) as ctx:
            plan_phase_a(cands, fat, budget_tokens=one_fat - 200)
        self.assertIn("project_context", str(ctx.exception))
        self.assertGreater(two_fat, one_fat)  # sanity for the split above


class SyntheticLoadTest(unittest.TestCase):
    def _run_load(self, count):
        cands = [
            candidate(
                i,
                text=f"Fact {i}: " + "always verify the build locally " * (1 + i % 3),
                context="[user] hmm " * (1 + i % 5),
            )
            for i in range(count)
        ]
        expected = [c.event_id for c in cands]
        batches = plan_phase_a(cands, CTX)
        assert_valid_batches(self, batches, expected, BATCH_TOKEN_BUDGET)
        # at this scale packing must be multi-batch under the 28k budget
        self.assertGreaterEqual(len(batches), 2)
        self.assertLess(len(batches), count)  # and still packs many per batch
        return batches

    def test_load_300_candidates(self):
        batches = self._run_load(300)
        self.assertLess(len(batches), 30)

    def test_load_500_candidates(self):
        batches = self._run_load(500)
        self.assertLess(len(batches), 50)


if __name__ == "__main__":
    unittest.main()
