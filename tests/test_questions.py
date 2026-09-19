"""Question-set + state-builder tests (PLAN "Jev question set").

Pins: exact ids, exact types, exact option vocabularies, instructions
that are complete standalone questions pointing at backticked state
paths, and the guarantee that question IDS never leak into the
instructions (they are code-only per the API reference).
"""

from __future__ import annotations

import unittest

from jevmory.ingestion.extract import Candidate
from jevmory.judgment.questions import (
    CATEGORY_OPTIONS,
    SIGNIFICANCE_LEVELS,
    VERDICT_OPTIONS,
    phase_a_questions,
    phase_a_state,
    phase_a_triple,
    phase_b_pair_questions,
    phase_b_questions,
    phase_b_state,
)


def candidate(i=0, role="user", text="Always run `make lint` before pushing."):
    return Candidate(
        event_id=f"ev{i:04d}", role=role, text=text,
        context="[assistant] Understood.", ts=None, session_id="s1",
    )


class PhaseATest(unittest.TestCase):
    def test_three_questions_per_candidate(self):
        questions = phase_a_questions(3)
        self.assertEqual(len(questions), 9)
        for i in range(3):
            self.assertEqual(
                sorted(
                    qid for qid in questions if qid.startswith(f"c{i}_")
                ),
                [f"c{i}_category", f"c{i}_durable", f"c{i}_significance"],
            )

    def test_types_match_the_plan(self):
        questions = phase_a_questions(2)
        self.assertEqual(questions["c0_durable"].type, "noul")
        self.assertEqual(questions["c0_category"].type, "choice")
        self.assertEqual(questions["c0_significance"].type, "score")

    def test_instructions_point_at_state_paths(self):
        for i in (0, 2, 11):
            triple = phase_a_triple(i)
            for question in triple.values():
                self.assertIn(f"`candidates[{i}].text`", question.instructions)

    def test_question_ids_never_appear_in_instructions(self):
        # ids are for our code only — the model must see complete
        # standalone questions, not id references.
        for qid, question in phase_a_questions(4).items():
            self.assertNotIn(qid, question.instructions)

    def test_category_options_exact(self):
        question = phase_a_triple(0)["c0_category"]
        self.assertEqual(tuple(question.criteria), CATEGORY_OPTIONS)
        self.assertEqual(
            set(question.criteria),
            {"preference", "tooling", "architecture", "pitfall",
             "convention", "none"},
        )
        # every option carries a rubric
        self.assertTrue(all(rubric for rubric in question.criteria.values()))

    def test_significance_levels_exact(self):
        question = phase_a_triple(0)["c0_significance"]
        self.assertIsInstance(question.criteria, list)
        # each rubric leads with its level name from SIGNIFICANCE_LEVELS
        self.assertEqual(
            [rubric.split(" — ")[0] for rubric in question.criteria],
            list(SIGNIFICANCE_LEVELS),
        )
        self.assertEqual(len(question.criteria), 4)

    def test_deterministic(self):
        self.assertEqual(phase_a_questions(3), phase_a_questions(3))
        self.assertEqual(phase_a_triple(2), phase_a_triple(2))

    def test_triple_is_subset_of_full_set(self):
        full = phase_a_questions(3)
        for qid, question in phase_a_triple(1).items():
            self.assertEqual(full[qid], question)

    def test_negative_count_rejected(self):
        with self.assertRaises(ValueError):
            phase_a_questions(-1)

    def test_wire_shape(self):
        wire = phase_a_triple(0)["c0_durable"].to_wire()
        self.assertEqual(
            sorted(wire), ["criteria", "instructions", "type"]
        )
        self.assertEqual(wire["criteria"]["true"][:6], "A fact")


class PhaseAStateTest(unittest.TestCase):
    def test_shape_matches_plan(self):
        state = phase_a_state({"slug": "demo", "path": "/x"}, [candidate(0), candidate(1)])
        self.assertEqual(sorted(state), ["candidates", "project_context"])
        self.assertEqual(state["project_context"], {"slug": "demo", "path": "/x"})
        first, second = state["candidates"]
        self.assertEqual(first, {
            "id": "ev0000", "role": "user",
            "text": "Always run `make lint` before pushing.",
            "context": "[assistant] Understood.",
        })
        self.assertEqual(second["id"], "ev0001")

    def test_default_project_context_is_empty(self):
        state = phase_a_state(None, [candidate()])
        self.assertEqual(state["project_context"], {})


class PhaseBTest(unittest.TestCase):
    def test_pair_ids_exact(self):
        questions = phase_b_pair_questions(2, 5)
        self.assertEqual(
            sorted(questions),
            ["p2_5_contradicts", "p2_5_same_claim", "p2_5_verdict"],
        )

    def test_pair_types(self):
        questions = phase_b_pair_questions(0, 1)
        self.assertEqual(questions["p0_1_same_claim"].type, "noul")
        self.assertEqual(questions["p0_1_contradicts"].type, "noul")
        self.assertEqual(questions["p0_1_verdict"].type, "choice")

    def test_pair_instructions_reference_both_parties(self):
        questions = phase_b_pair_questions(3, 7)
        for question in questions.values():
            self.assertIn("`candidates[3].text`", question.instructions)
            self.assertIn("`facts[7].text`", question.instructions)

    def test_verdict_options_exact(self):
        question = phase_b_pair_questions(0, 0)["p0_0_verdict"]
        self.assertEqual(tuple(question.criteria), VERDICT_OPTIONS)
        self.assertEqual(
            set(question.criteria),
            {"new_overrides", "old_stands", "unclear"},
        )

    def test_merged_pairs(self):
        questions = phase_b_questions([(0, 1), (0, 2), (1, 0)])
        self.assertEqual(len(questions), 9)
        self.assertIn("p0_2_same_claim", questions)
        self.assertIn("p1_0_verdict", questions)

    def test_state_shape(self):
        state = phase_b_state(
            {"slug": "demo"},
            [candidate(0)],
            [{"id": "f7", "text": "Use bun, not npm.", "status": "active"}],
        )
        self.assertEqual(sorted(state), ["candidates", "facts", "project_context"])
        self.assertEqual(state["facts"][0]["id"], "f7")
        self.assertEqual(state["facts"][0]["text"], "Use bun, not npm.")


if __name__ == "__main__":
    unittest.main()
