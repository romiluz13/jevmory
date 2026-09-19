"""Token-estimator tests.

The estimator is the budgeting input for the batcher; these tests pin
its ceil(chars/4) semantics, its determinism, and the compositional
identity the greedy planner relies on (a container's compact
``sort_keys`` JSON length is exactly built from its elements'
serializations).
"""

from __future__ import annotations

import unittest

from jevmory.judgment.questions import Question, phase_a_questions
from jevmory.judgment.tokens import estimate_tokens, json_chars, questions_wire
from jevmory.thresholds import CHARS_PER_TOKEN


def q(qid="c0", **overrides):
    base = dict(id=qid, type="noul", instructions="Answer `x`.", criteria={})
    base.update(overrides)
    return Question(**base)


class JsonCharsTest(unittest.TestCase):
    def test_empty_containers(self):
        self.assertEqual(json_chars({}), 2)
        self.assertEqual(json_chars([]), 2)
        self.assertEqual(json_chars("hello"), 7)

    def test_sort_keys_deterministic(self):
        a = {"state": 1, "questions": 2}
        b = {"questions": 2, "state": 1}
        self.assertEqual(json_chars(a), json_chars(b))

    def test_compact_separators(self):
        # no spaces after ':' or ','
        self.assertEqual(json_chars({"a": [1, 2]}), len('{"a":[1,2]}'))

    def test_compositional_identity(self):
        # the identity the batch planner relies on: a mapping's chars =
        # braces + per-entry ('"key":' + value chars) + inter-entry commas
        entries = {
            "candidates": [{"id": "ev1", "text": "hi"}, {"id": "ev2", "text": "yo"}],
            "project_context": {"slug": "demo"},
        }
        expected = (
            2  # braces
            + sum(
                len(f'"{key}":') + json_chars(value)
                for key, value in entries.items()
            )
            + (len(entries) - 1)  # commas
        )
        self.assertEqual(json_chars(entries), expected)

    def test_list_compositional_identity(self):
        items = ["a", {"k": 1}, None]
        expected = (
            2
            + sum(json_chars(item) for item in items)
            + (len(items) - 1)
        )
        self.assertEqual(json_chars(items), expected)


class EstimateTokensTest(unittest.TestCase):
    def test_empty_request(self):
        # {} (2 chars) + {"questions":{}} (16 chars) = 18 -> ceil(18/4) = 5
        self.assertEqual(json_chars({"questions": {}}), 16)
        self.assertEqual(estimate_tokens({}, {}), 5)

    def test_ceil_semantics(self):
        state = {"k": "x" * 400}  # exactly 408 chars of JSON
        base = json_chars({"questions": {}})
        exact = 408 + base
        self.assertEqual(exact % CHARS_PER_TOKEN, 0)  # sanity: no rounding
        self.assertEqual(
            estimate_tokens(state, {}),
            exact // CHARS_PER_TOKEN,
        )
        # one more char tips it over the boundary
        bigger = {"k": "x" * 401}
        self.assertEqual(
            estimate_tokens(bigger, {}),
            (json_chars(bigger) + base + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN,
        )

    def test_question_ids_consume_budget(self):
        state = {"k": "v"}
        short = {"c0_durable": q()}
        long = {"c0_durable_and_a_much_longer_id": q()}
        self.assertGreater(
            estimate_tokens(state, long), estimate_tokens(state, short)
        )

    def test_instructions_consume_budget(self):
        terse = {"c0": q(instructions="Answer.")}
        verbose = {"c0": q(instructions="Answer " + "carefully " * 50)}
        self.assertGreater(
            estimate_tokens({}, verbose), estimate_tokens({}, terse)
        )

    def test_questions_wire_uses_to_wire(self):
        questions = phase_a_questions(1)
        wire = questions_wire(questions)
        self.assertEqual(
            wire["c0_durable"], questions["c0_durable"].to_wire()
        )
        # the wire form is what the estimator counts
        self.assertEqual(
            estimate_tokens({}, questions),
            -(
                -(
                    json_chars({})
                    + json_chars({"questions": wire})
                )
                // CHARS_PER_TOKEN
            ),
        )


if __name__ == "__main__":
    unittest.main()
