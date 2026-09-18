"""Disposition rule tests (M4): the gate, the REVIEW band, receipts
verbatim, count_dispositions. Rules are code, not Jev."""

from __future__ import annotations

import unittest

from dream_md.audit.memfile import MemoryLine
from dream_md.audit.questions import DISPOSITION_OPTIONS
from dream_md.audit.rules import (
    DISPOSITION_KEYWORDS,
    Q_CONTRADICTED,
    Q_DISPOSITION,
    Q_SUPPORTED,
    REVIEW,
    LineVerdict,
    count_dispositions,
    line_verdict,
)
from dream_md.judgment.answers import ChoiceAnswer, NoulAnswer
from dream_md.thresholds import AUDIT_DISPOSITION_GATE


def line(number=4):
    return MemoryLine(number=number, raw="- claim", text="claim", section=None)


def answers(disposition="keep", confidence=0.8, supported=0.9,
            contradicted=0.08):
    return {
        Q_SUPPORTED: NoulAnswer(supported),
        Q_CONTRADICTED: NoulAnswer(contradicted),
        Q_DISPOSITION: ChoiceAnswer(
            choice=disposition,
            probabilities={
                option: (0.7 if option == disposition else 0.1)
                for option in DISPOSITION_OPTIONS
            },
            confidence=confidence,
        ),
    }


class GateTest(unittest.TestCase):
    def test_decisive_above_gate_maps_every_option(self):
        for disposition, keyword in DISPOSITION_KEYWORDS.items():
            verdict = line_verdict(line(), answers(disposition=disposition))
            self.assertTrue(verdict.decisive)
            self.assertEqual(verdict.keyword, keyword)
            self.assertEqual(verdict.disposition, disposition)

    def test_below_gate_lands_in_review_band(self):
        verdict = line_verdict(line(), answers(confidence=0.5))
        self.assertFalse(verdict.decisive)
        self.assertEqual(verdict.keyword, REVIEW)
        # the raw disposition is preserved, never silently dropped
        self.assertEqual(verdict.disposition, "keep")
        self.assertEqual(verdict.confidence, 0.5)

    def test_gate_boundary_is_decisive(self):
        verdict = line_verdict(line(), answers(confidence=AUDIT_DISPOSITION_GATE))
        self.assertTrue(verdict.decisive)

    def test_custom_gate_respected(self):
        verdict = line_verdict(line(), answers(confidence=0.8), gate=0.9)
        self.assertFalse(verdict.decisive)
        self.assertEqual(verdict.keyword, REVIEW)


class ReceiptsTest(unittest.TestCase):
    def test_nouls_recorded_verbatim(self):
        verdict = line_verdict(line(), answers(supported=0.73, contradicted=0.21))
        self.assertEqual(verdict.supported, 0.73)
        self.assertEqual(verdict.contradicted, 0.21)
        self.assertEqual(verdict.confidence, 0.8)

    def test_line_carried_through(self):
        mem = line(17)
        verdict = line_verdict(mem, answers())
        self.assertIs(verdict.line, mem)
        self.assertIsInstance(verdict, LineVerdict)

    def test_stable_receipt_keys(self):
        self.assertEqual(Q_SUPPORTED, "supported")
        self.assertEqual(Q_CONTRADICTED, "contradicted")
        self.assertEqual(Q_DISPOSITION, "disposition")


class BadAnswersTest(unittest.TestCase):
    def test_wrong_answer_type_raises_type_error(self):
        bad = {
            Q_SUPPORTED: NoulAnswer(0.9),
            Q_CONTRADICTED: ChoiceAnswer(choice="keep"),
            Q_DISPOSITION: ChoiceAnswer(choice="keep"),
        }
        with self.assertRaises(TypeError):
            line_verdict(line(), bad)

    def test_out_of_vocabulary_disposition_raises_value_error(self):
        bad = dict(answers())
        bad[Q_DISPOSITION] = ChoiceAnswer(choice="maybe")
        with self.assertRaises(ValueError):
            line_verdict(line(), bad)

    def test_missing_answer_raises_key_error(self):
        with self.assertRaises(KeyError):
            line_verdict(line(), {Q_SUPPORTED: NoulAnswer(0.9)})


class CountDispositionsTest(unittest.TestCase):
    def test_all_keywords_present_even_when_zero(self):
        counts = count_dispositions([])
        self.assertEqual(
            counts,
            {"KEEP": 0, "STALE": 0, "WRONG": 0, "UNSUPPORTED": 0, REVIEW: 0},
        )

    def test_mixed_verdicts_counted_by_keyword(self):
        verdicts = [
            line_verdict(line(1), answers()),
            line_verdict(line(2), answers()),
            line_verdict(line(3), answers(disposition="stale")),
            line_verdict(line(4), answers(confidence=0.4)),
        ]
        counts = count_dispositions(verdicts)
        self.assertEqual(counts["KEEP"], 2)
        self.assertEqual(counts["STALE"], 1)
        self.assertEqual(counts[REVIEW], 1)
        self.assertEqual(counts["WRONG"], 0)


if __name__ == "__main__":
    unittest.main()
