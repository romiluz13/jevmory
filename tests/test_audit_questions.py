"""Phase C question tests (M4): vocabulary pinned, ids code-only,
instructions standalone, state shape exact."""

from __future__ import annotations

import unittest

from jev_md.audit.memfile import MemoryLine
from jev_md.audit.questions import (
    DISPOSITION_OPTIONS,
    audit_state,
    line_entry,
    phase_c_questions,
    phase_c_triple,
)


def line(number, text, section=None):
    return MemoryLine(number=number, raw=f"- {text}", text=text, section=section)


class VocabularyTest(unittest.TestCase):
    def test_disposition_options_exact(self):
        self.assertEqual(
            DISPOSITION_OPTIONS, ("keep", "stale", "wrong", "unsupported")
        )


class PhaseCTripleTest(unittest.TestCase):
    def test_ids_and_types(self):
        triple = phase_c_triple(0)
        self.assertEqual(
            sorted(triple),
            ["l0_contradicted", "l0_disposition", "l0_supported"],
        )
        self.assertEqual(triple["l0_supported"].type, "noul")
        self.assertEqual(triple["l0_contradicted"].type, "noul")
        self.assertEqual(triple["l0_disposition"].type, "choice")

    def test_choice_criteria_are_the_disposition_options(self):
        triple = phase_c_triple(7)
        self.assertEqual(
            tuple(triple["l7_disposition"].criteria), DISPOSITION_OPTIONS
        )

    def test_ids_never_appear_in_instructions(self):
        for i in range(3):
            for question in phase_c_triple(i).values():
                self.assertNotIn(question.id, question.instructions)

    def test_instructions_point_at_state_paths(self):
        triple = phase_c_triple(2)
        for question in triple.values():
            self.assertIn("`memory_lines[2].text`", question.instructions)
            self.assertIn("`evidence`", question.instructions)

    def test_indexed_ids_for_every_position(self):
        questions = phase_c_questions(3)
        self.assertEqual(len(questions), 9)
        self.assertIn("l2_disposition", questions)
        self.assertNotIn("l3_supported", questions)

    def test_negative_count_raises(self):
        with self.assertRaises(ValueError):
            phase_c_questions(-1)

    def test_zero_count_is_empty(self):
        self.assertEqual(phase_c_questions(0), {})


class AuditStateTest(unittest.TestCase):
    def test_state_shape_exact(self):
        lines = [line(4, "first claim", "Tooling"), line(5, "second")]
        facts = [{"id": 1, "text": "fact text"}]
        statements = [{"id": "ev1", "role": "user", "text": "stmt", "ts": None}]
        state = audit_state({"name": "proj"}, lines, facts, statements)
        self.assertEqual(
            state,
            {
                "project_context": {"name": "proj"},
                "memory_lines": [
                    {"id": "line:4", "number": 4, "section": "Tooling",
                     "text": "first claim"},
                    {"id": "line:5", "number": 5, "section": None,
                     "text": "second"},
                ],
                "evidence": {
                    "facts": [{"id": 1, "text": "fact text"}],
                    "statements": [
                        {"id": "ev1", "role": "user", "text": "stmt", "ts": None}
                    ],
                },
            },
        )

    def test_line_entry_matches_state_entries(self):
        mem_line = line(9, "entry claim")
        self.assertEqual(
            line_entry(mem_line),
            {"id": "line:9", "number": 9, "section": None,
             "text": "entry claim"},
        )

    def test_empty_context_defaults_to_empty_mapping(self):
        state = audit_state(None, [], [], [])
        self.assertEqual(state["project_context"], {})
        self.assertEqual(state["memory_lines"], [])
        self.assertEqual(
            state["evidence"], {"facts": [], "statements": []}
        )


if __name__ == "__main__":
    unittest.main()
