"""Distill verdict rule tests (M5): Phase A gates by role, Phase B pair
actions, near-miss band, stable receipt keys."""

from __future__ import annotations

import unittest

from jevmory.distill.rules import (
    ACTION_ASK,
    ACTION_DUPLICATE,
    ACTION_NONE,
    ACTION_SUPERSEDE,
    DROP_LOW_DURABLE,
    DROP_LOW_SIGNIFICANCE,
    Q_CATEGORY,
    Q_CONTRADICTS,
    Q_DURABLE,
    Q_SAME_CLAIM,
    Q_SIGNIFICANCE,
    Q_VERDICT,
    CandidateVerdict,
    pair_action,
    phase_a_verdict,
)
from jevmory.ingestion.extract import Candidate
from jevmory.ingestion.models import ROLE_ASSISTANT, ROLE_USER
from jevmory.judgment.answers import ChoiceAnswer, NoulAnswer, ScoreAnswer
from jevmory.thresholds import (
    CONTRADICTION_GATE,
    DURABLE_GATE_ASSISTANT,
    DURABLE_GATE_USER,
    NEAR_MISS_LOW,
    SAME_CLAIM_GATE,
    SIGNIFICANCE_GATE,
    SUPERSEDE_CONFIDENCE,
)


def candidate(role=ROLE_USER):
    return Candidate(
        event_id="ev0001", role=role, text="we deploy on fridays",
        context=None, ts="2026-09-01T00:00:00Z", session_id="s1",
    )


def noul(value):
    return NoulAnswer(noul=value)


def choice(pick, confidence=0.9, options=("new_overrides", "old_stands", "unclear")):
    probabilities = {
        option: (0.8 if option == pick else 0.2 / (len(options) - 1))
        for option in options
    }
    return ChoiceAnswer(choice=pick, probabilities=probabilities,
                        confidence=confidence)


def score(value, confidence=0.9):
    return ScoreAnswer(
        score=value,
        legend={"0": "trivial", "1": "useful", "2": "important",
                "3": "critical"},
        probabilities={"0": 0.05, "1": 0.05, "2": 0.8, "3": 0.1},
        confidence=confidence,
    )


class PhaseAVerdictTest(unittest.TestCase):
    def test_user_statement_passes_at_user_gate(self):
        verdict = phase_a_verdict(
            candidate(ROLE_USER), noul(DURABLE_GATE_USER), choice("convention"), score(2.0),
        )
        self.assertTrue(verdict.kept)
        self.assertFalse(verdict.near_miss)
        self.assertIsNone(verdict.dropped_reason)

    def test_user_statement_below_user_gate_drops(self):
        verdict = phase_a_verdict(
            candidate(ROLE_USER), noul(DURABLE_GATE_USER - 0.01), choice("convention"), score(2.0),
        )
        self.assertFalse(verdict.kept)
        self.assertEqual(verdict.dropped_reason, DROP_LOW_DURABLE)

    def test_assistant_gate_is_higher(self):
        # 0.75 passes the user gate but not the assistant gate
        between = (DURABLE_GATE_USER + DURABLE_GATE_ASSISTANT) / 2
        self.assertGreater(between, DURABLE_GATE_USER)
        self.assertLess(between, DURABLE_GATE_ASSISTANT)
        verdict = phase_a_verdict(
            candidate(ROLE_ASSISTANT), noul(between), choice("convention"), score(2.0),
        )
        self.assertFalse(verdict.kept)
        self.assertEqual(verdict.dropped_reason, DROP_LOW_DURABLE)

    def test_assistant_statement_passes_at_assistant_gate(self):
        verdict = phase_a_verdict(
            candidate(ROLE_ASSISTANT), noul(DURABLE_GATE_ASSISTANT), choice("tooling"), score(1.0),
        )
        self.assertTrue(verdict.kept)

    def test_significance_gate(self):
        verdict = phase_a_verdict(
            candidate(), noul(0.9), choice("convention"),
            score(SIGNIFICANCE_GATE - 0.01),
        )
        self.assertFalse(verdict.kept)
        self.assertEqual(verdict.dropped_reason, DROP_LOW_SIGNIFICANCE)

    def test_durable_drop_wins_over_significance_drop(self):
        verdict = phase_a_verdict(
            candidate(), noul(0.1), choice("convention"), score(0.0),
        )
        self.assertEqual(verdict.dropped_reason, DROP_LOW_DURABLE)

    def test_near_miss_band_is_surfaced_never_silent(self):
        # NEAR_MISS_LOW <= durable < gate: dropped AND flagged
        verdict = phase_a_verdict(
            candidate(), noul(NEAR_MISS_LOW), choice("convention"), score(2.0),
        )
        self.assertFalse(verdict.kept)
        self.assertTrue(verdict.near_miss)
        self.assertEqual(verdict.dropped_reason, DROP_LOW_DURABLE)
        # just below the band: dropped, not a near miss
        below = phase_a_verdict(
            candidate(), noul(NEAR_MISS_LOW - 0.01), choice("convention"), score(2.0),
        )
        self.assertFalse(below.near_miss)

    def test_answers_kept_verbatim(self):
        durable, category, significance = noul(0.77), choice("pitfall"), score(1.4)
        verdict = phase_a_verdict(candidate(), durable, category, significance)
        self.assertIs(verdict.durable, durable)
        self.assertIs(verdict.category, category)
        self.assertIs(verdict.significance, significance)
        self.assertEqual(verdict.candidate.role, ROLE_USER)


class PairActionTest(unittest.TestCase):
    def test_same_claim_above_gate_is_duplicate(self):
        self.assertEqual(
            pair_action(noul(SAME_CLAIM_GATE), noul(0.0), choice("old_stands")),
            ACTION_DUPLICATE,
        )

    def test_duplicate_wins_even_with_contradiction(self):
        # same_claim dominates: a pair cannot be both a duplicate and a conflict
        self.assertEqual(
            pair_action(noul(0.95), noul(0.9), choice("new_overrides", 0.95)),
            ACTION_DUPLICATE,
        )

    def test_decisive_override_is_supersede(self):
        self.assertEqual(
            pair_action(noul(0.1), noul(CONTRADICTION_GATE),
                        choice("new_overrides", SUPERSEDE_CONFIDENCE)),
            ACTION_SUPERSEDE,
        )

    def test_override_below_confidence_is_ask(self):
        self.assertEqual(
            pair_action(noul(0.1), noul(0.9),
                        choice("new_overrides", SUPERSEDE_CONFIDENCE - 0.01)),
            ACTION_ASK,
        )

    def test_verdict_old_stands_is_ask(self):
        self.assertEqual(
            pair_action(noul(0.1), noul(0.9), choice("old_stands", 0.95)),
            ACTION_ASK,
        )

    def test_verdict_unclear_is_ask(self):
        self.assertEqual(
            pair_action(noul(0.1), noul(0.8), choice("unclear", 0.9)),
            ACTION_ASK,
        )

    def test_unrelated_pair_is_none(self):
        self.assertEqual(
            pair_action(noul(0.1), noul(CONTRADICTION_GATE - 0.01),
                        choice("unclear", 0.9)),
            ACTION_NONE,
        )

    def test_contradiction_gate_boundary_is_inclusive(self):
        self.assertEqual(
            pair_action(noul(0.1), noul(CONTRADICTION_GATE),
                        choice("old_stands", 0.9)),
            ACTION_ASK,
        )


class ReceiptKeysTest(unittest.TestCase):
    def test_keys_are_stable_and_disjoint(self):
        keys = (Q_DURABLE, Q_CATEGORY, Q_SIGNIFICANCE,
                Q_SAME_CLAIM, Q_CONTRADICTS, Q_VERDICT)
        self.assertEqual(
            keys,
            ("durable", "category", "significance",
             "same_claim", "contradicts", "verdict"),
        )
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
