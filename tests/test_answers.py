"""Typed-answer parsing tests (docs/reference/typesafe-api.md shapes).

Every example below is the reference's own documented shape; violations
must raise JevProtocolError because every number in dream.md has to be
reproducible from stored receipts — off-spec answers are surfaced,
never guessed at.
"""

from __future__ import annotations

import unittest

from dream_md.judgment.answers import (
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
    parse_answer,
)
from dream_md.judgment.errors import JevProtocolError


class ParseNoulTest(unittest.TestCase):
    def test_reference_example(self):
        answer = parse_answer("noul", {"type": "noul", "noul": 0.92})
        self.assertEqual(answer, NoulAnswer(noul=0.92))

    def test_bounds_are_valid(self):
        self.assertEqual(parse_answer("noul", {"type": "noul", "noul": 0.0}).noul, 0.0)
        self.assertEqual(parse_answer("noul", {"type": "noul", "noul": 1.0}).noul, 1.0)
        self.assertEqual(parse_answer("noul", {"type": "noul", "noul": 1}).noul, 1.0)

    def test_extra_fields_ignored(self):
        # Noul has no confidence field by spec; a future envelope adding
        # one must not break parsing.
        answer = parse_answer(
            "noul", {"type": "noul", "noul": 0.5, "confidence": 0.9}
        )
        self.assertEqual(answer.noul, 0.5)
        self.assertNotIsInstance(answer, ChoiceAnswer)

    def test_missing_noul_field(self):
        with self.assertRaises(JevProtocolError):
            parse_answer("noul", {"type": "noul"})

    def test_non_number_noul(self):
        with self.assertRaises(JevProtocolError):
            parse_answer("noul", {"type": "noul", "noul": "yes"})

    def test_bool_noul_rejected(self):
        with self.assertRaises(JevProtocolError):
            parse_answer("noul", {"type": "noul", "noul": True})

    def test_out_of_range_noul(self):
        for bad in (-0.1, 1.5):
            with self.assertRaises(JevProtocolError):
                parse_answer("noul", {"type": "noul", "noul": bad})


class ParseChoiceTest(unittest.TestCase):
    def test_reference_example(self):
        answer = parse_answer(
            "choice",
            {
                "type": "choice",
                "choice": "technical",
                "probabilities": {
                    "billing": 0.08, "technical": 0.85, "sales": 0.07
                },
                "confidence": 0.82,
            },
        )
        self.assertEqual(answer, ChoiceAnswer(choice="technical",
                        probabilities={"billing": 0.08, "technical": 0.85,
                                       "sales": 0.07}, confidence=0.82))
        self.assertAlmostEqual(sum(answer.probabilities.values()), 1.0)

    def test_probabilities_sum_tolerance(self):
        # 0.999 sums within the 0.01 tolerance
        parse_answer(
            "choice",
            {"type": "choice", "choice": "a",
             "probabilities": {"a": 0.999, "b": 0.001}, "confidence": 0.5},
        )
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "choice",
                {"type": "choice", "choice": "a",
                 "probabilities": {"a": 0.9, "b": 0.1 - 0.05},
                 "confidence": 0.5},
            )

    def test_choice_not_among_options(self):
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "choice",
                {"type": "choice", "choice": "ghost",
                 "probabilities": {"a": 0.5, "b": 0.5}, "confidence": 0.5},
            )

    def test_missing_confidence(self):
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "choice",
                {"type": "choice", "choice": "a",
                 "probabilities": {"a": 1.0}},
            )

    def test_confidence_out_of_range(self):
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "choice",
                {"type": "choice", "choice": "a",
                 "probabilities": {"a": 1.0}, "confidence": 1.2},
            )

    def test_empty_probabilities(self):
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "choice",
                {"type": "choice", "choice": "a", "probabilities": {},
                 "confidence": 0.5},
            )


class ParseScoreTest(unittest.TestCase):
    def test_reference_example(self):
        answer = parse_answer(
            "score",
            {
                "type": "score", "score": 1.6,
                "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
                "probabilities": {"0": 0.05, "1": 0.3, "2": 0.65},
                "confidence": 0.78,
            },
        )
        self.assertIsInstance(answer, ScoreAnswer)
        self.assertEqual(answer.score, 1.6)
        self.assertEqual(answer.legend["1"], "Frustrated")
        self.assertEqual(answer.confidence, 0.78)

    def test_score_between_levels_allowed(self):
        answer = parse_answer(
            "score",
            {"type": "score", "score": 1.5,
             "legend": {"0": "a", "1": "b", "2": "c"},
             "probabilities": {"0": 0.2, "1": 0.3, "2": 0.5},
             "confidence": 0.6},
        )
        self.assertEqual(answer.score, 1.5)

    def test_score_above_level_range(self):
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "score",
                {"type": "score", "score": 2.5,
                 "legend": {"0": "a", "1": "b", "2": "c"},
                 "probabilities": {"0": 0.2, "1": 0.3, "2": 0.5},
                 "confidence": 0.6},
            )

    def test_negative_score(self):
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "score",
                {"type": "score", "score": -0.1,
                 "legend": {"0": "a", "1": "b"},
                 "probabilities": {"0": 0.5, "1": 0.5}, "confidence": 0.6},
            )

    def test_no_levels_defined(self):
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "score",
                {"type": "score", "score": 1.0, "confidence": 0.6},
            )

    def test_missing_score(self):
        with self.assertRaises(JevProtocolError):
            parse_answer(
                "score",
                {"type": "score", "legend": {"0": "a"}, "confidence": 0.6},
            )


class ParseContractTest(unittest.TestCase):
    def test_type_mismatch_raises_protocol_error(self):
        with self.assertRaises(JevProtocolError):
            parse_answer("noul", {"type": "choice", "choice": "a",
                                  "probabilities": {"a": 1.0},
                                  "confidence": 0.5})

    def test_non_object_answer(self):
        for expected in ("noul", "choice", "score"):
            with self.assertRaises(JevProtocolError):
                parse_answer(expected, "yes")

    def test_unknown_expected_type_is_programming_error(self):
        with self.assertRaises(ValueError):
            parse_answer("essay", {"type": "essay"})


if __name__ == "__main__":
    unittest.main()
