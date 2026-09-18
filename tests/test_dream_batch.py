"""Phase B batch planner tests (M5): shared facts array, pair
positions, greedy fill, order preserved, coverage exact."""

from __future__ import annotations

import unittest

from dream_md.dream.batch import PairPlan, plan_phase_b
from dream_md.ingestion.extract import Candidate
from dream_md.judgment.batch import BatchError
from dream_md.thresholds import BATCH_TOKEN_BUDGET


def make_candidate(i, text="candidate claim about deploying on fridays"):
    return Candidate(
        event_id=f"ev{i:04d}",
        role="user",
        text=f"{text} {i}",
        context=None,
        ts="2026-09-01T00:00:00Z",
        session_id="s1",
    )


def make_fact_entry(i, text="remembered fact about this project"):
    return {"id": i, "text": f"{text} {i}"}


def make_plans(count, facts_per_plan=5):
    """``count`` plans; plan k partners with facts k..k+facts_per_plan-1
    (overlapping ranges exercise the shared facts array)."""
    return [
        PairPlan(
            candidate=make_candidate(i),
            facts=tuple(
                make_fact_entry(j)
                for j in range(i, i + facts_per_plan)
            ),
        )
        for i in range(count)
    ]


class PlanPhaseBTest(unittest.TestCase):
    def test_empty_plans_plan_zero_requests(self):
        self.assertEqual(plan_phase_b([]), [])

    def test_order_preserved_and_coverage_exact(self):
        plans = make_plans(25)
        batches = plan_phase_b(plans)
        ids = [
            eid for batch in batches for eid in batch.candidate_ids
        ]
        self.assertEqual(ids, [p.candidate.event_id for p in plans])
        for batch in batches:
            state_ids = [entry["id"] for entry in batch.state["candidates"]]
            self.assertEqual(state_ids, list(batch.candidate_ids))

    def test_shared_facts_array_dedupes_entries(self):
        # plans 0..4 partner with fact ranges i..i+8 -> ids 0..12, each
        # appearing exactly once across the (shared) facts arrays
        plans = make_plans(5, facts_per_plan=9)
        batches = plan_phase_b(plans)
        fact_ids = [
            entry["id"]
            for batch in batches
            for entry in batch.state["facts"]
        ]
        self.assertEqual(sorted(fact_ids), list(range(13)))
        self.assertEqual(len(fact_ids), len(set(fact_ids)))
        # a fact needed by two plans of one batch is in that array once
        for batch in batches:
            ids = [entry["id"] for entry in batch.state["facts"]]
            self.assertEqual(len(ids), len(set(ids)))

    def test_pairs_reference_batch_positions(self):
        plans = make_plans(3, facts_per_plan=2)
        batch = plan_phase_b(plans)[0]
        # plan i pairs with facts i and i+1 -> positions in the shared array
        self.assertEqual(
            set(batch.pairs),
            {(0, 0), (0, 1), (1, 1), (1, 2), (2, 2), (2, 3)},
        )
        for i, j in batch.pairs:
            self.assertIn(f"p{i}_{j}_same_claim", batch.questions)
            self.assertIn(f"p{i}_{j}_contradicts", batch.questions)
            self.assertIn(f"p{i}_{j}_verdict", batch.questions)
        self.assertEqual(len(batch.questions), 3 * len(batch.pairs))

    def test_fact_ids_resolve_pair_positions(self):
        plans = make_plans(3, facts_per_plan=2)
        batch = plan_phase_b(plans)[0]
        for i, j in batch.pairs:
            plan = batch.pair_plans[i]
            fact_id = batch.fact_ids[j]
            self.assertIn(fact_id, [entry["id"] for entry in plan.facts])

    def test_every_batch_within_default_budget(self):
        batches = plan_phase_b(make_plans(120, facts_per_plan=5))
        self.assertGreater(len(batches), 1)
        for batch in batches:
            self.assertLessEqual(batch.token_estimate(), BATCH_TOKEN_BUDGET)

    def test_synthetic_load_300_candidates(self):
        plans = make_plans(300, facts_per_plan=5)
        batches = plan_phase_b(plans)
        ids = [eid for batch in batches for eid in batch.candidate_ids]
        self.assertEqual(ids, [p.candidate.event_id for p in plans])
        for batch in batches:
            self.assertLessEqual(batch.token_estimate(), BATCH_TOKEN_BUDGET)

    def test_positions_reset_across_group_boundary(self):
        # A candidate that overflows the current group is re-planned at
        # position 0 of the next group, its pair questions rebuilt.
        plans = make_plans(10, facts_per_plan=40)  # huge: ~4 plans per batch
        batches = plan_phase_b(plans)
        self.assertGreater(len(batches), 1)
        for batch in batches:
            for i in range(len(batch.pair_plans)):
                # every candidate position has its pair questions
                js = [j for k, j in batch.pairs if k == i]
                self.assertTrue(js)
                for j in js:
                    for suffix in ("same_claim", "contradicts", "verdict"):
                        self.assertIn(f"p{i}_{j}_{suffix}", batch.questions)

    def test_tight_budget_one_plan_per_request(self):
        plans = make_plans(3, facts_per_plan=3)
        one = plan_phase_b(plans[:1], budget_tokens=10**9)[0].token_estimate()
        two = plan_phase_b(
            plans[:2], budget_tokens=10**9
        )[0].token_estimate()
        batches = plan_phase_b(plans, budget_tokens=one)
        self.assertEqual(len(batches), 3)
        self.assertLess(one, two)  # sanity: two plans really need more

    def test_single_plan_overflow_raises_batch_error(self):
        plans = make_plans(1, facts_per_plan=5)
        one = plan_phase_b(plans, budget_tokens=10**9)[0].token_estimate()
        with self.assertRaises(BatchError):
            plan_phase_b(plans, budget_tokens=one - 1)

    def test_huge_context_overflow_raises_batch_error(self):
        plans = make_plans(1)
        with self.assertRaises(BatchError):
            plan_phase_b(
                plans,
                {"blob": "x" * 200_000},
                budget_tokens=1000,
            )

    def test_plan_without_facts_is_a_programming_error(self):
        plan = PairPlan(candidate=make_candidate(0), facts=())
        with self.assertRaises(ValueError):
            plan_phase_b([plan])

    def test_nonpositive_budget_raises_value_error(self):
        with self.assertRaises(ValueError):
            plan_phase_b(make_plans(1), budget_tokens=0)


if __name__ == "__main__":
    unittest.main()
