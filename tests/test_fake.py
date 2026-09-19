"""FakeJev tests: three modes, error injection, request recording.

The fake exists so the whole pipeline (and every judgment gate) is
testable offline. Its answers pass through the SAME strict parser as
real envelopes — asserted here by feeding it an off-spec scripted
answer and watching it reject.
"""

from __future__ import annotations

import unittest

from jevmory.judgment.client import Usage
from jevmory.judgment.errors import JevAuthError, JevProtocolError, JevRetryExhausted
from jevmory.judgment.fake import FakeJev
from jevmory.judgment.questions import (
    phase_a_questions,
    phase_a_state,
    phase_b_pair_questions,
)
from jevmory.ingestion.extract import Candidate
from jevmory.thresholds import (
    CONTRADICTION_GATE,
    DURABLE_GATE_USER,
    SAME_CLAIM_GATE,
)


def candidate(i=0):
    return Candidate(
        event_id=f"ev{i:04d}", role="user",
        text="Always run `make lint` before pushing.",
        context="[assistant] Understood.", ts=None, session_id="s1",
    )


STATE = phase_a_state({"slug": "demo"}, [candidate()])

# single-question subsets for scripted-mode tests
DURABLE = {"c0_durable": phase_a_questions(1)["c0_durable"]}


class NormalModeTest(unittest.TestCase):
    def test_decisive_speculative_answers(self):
        response = FakeJev().ask(STATE, phase_a_questions(1))
        self.assertEqual(response.answers["c0_durable"].noul, 0.9)
        category = response.answers["c0_category"]
        self.assertEqual(category.choice, "convention")
        self.assertEqual(category.confidence, 0.8)
        self.assertAlmostEqual(
            sum(category.probabilities.values()), 1.0, places=6
        )
        self.assertEqual(category.probabilities["convention"], 0.7)
        significance = response.answers["c0_significance"]
        self.assertGreater(significance.score, 1.0)  # clears the >=1 gate
        self.assertLess(significance.score, 2.0)
        self.assertEqual(significance.confidence, 0.8)
        self.assertAlmostEqual(
            sum(significance.probabilities.values()), 1.0, places=6
        )

    def test_decisive_pair_answers(self):
        questions = phase_b_pair_questions(0, 1)
        response = FakeJev().ask(STATE, questions)
        self.assertEqual(response.answers["p0_1_same_claim"].noul, 0.9)
        self.assertEqual(response.answers["p0_1_contradicts"].noul, 0.08)
        verdict = response.answers["p0_1_verdict"]
        self.assertEqual(verdict.choice, "new_overrides")
        self.assertEqual(verdict.confidence, 0.8)

    def test_normal_clears_gates(self):
        # durable 0.9 comfortably above the durable gate; same_claim 0.9
        # above the duplicate gate; contradicts 0.08 well below the
        # conflict gate — the normal fake feeds the happy path.
        self.assertGreater(0.9, DURABLE_GATE_USER)
        self.assertGreater(0.9, SAME_CLAIM_GATE)
        self.assertLess(0.08, CONTRADICTION_GATE)

    def test_usage_and_model_echoed(self):
        usage = Usage(input_tokens=5, output_tokens=6)
        response = FakeJev(usage=usage).ask(STATE, phase_a_questions(1))
        self.assertEqual(response.usage, usage)
        self.assertEqual(response.model, "jev-latest")


class AdversarialModeTest(unittest.TestCase):
    def test_flat_distributions_zero_confidence(self):
        response = FakeJev(mode="adversarial").ask(STATE, phase_a_questions(1))
        self.assertEqual(response.answers["c0_durable"].noul, 0.5)
        category = response.answers["c0_category"]
        self.assertAlmostEqual(
            sum(category.probabilities.values()), 1.0, places=6
        )
        self.assertEqual(
            len(set(category.probabilities.values())), 1  # perfectly flat
        )
        self.assertEqual(category.confidence, 0.0)
        significance = response.answers["c0_significance"]
        self.assertEqual(significance.confidence, 0.0)
        self.assertEqual(significance.score, 1.5)  # dead midpoint of 0..3
        self.assertAlmostEqual(
            sum(significance.probabilities.values()), 1.0, places=6
        )

    def test_every_noul_is_exact_half(self):
        questions = {**phase_a_questions(1), **phase_b_pair_questions(0, 0)}
        response = FakeJev(mode="adversarial").ask(STATE, questions)
        for answer in response.answers.values():
            if type(answer).__name__ == "NoulAnswer":
                self.assertEqual(answer.noul, 0.5)

    def test_adversarial_lands_in_uncertain_bands(self):
        # 0.5 sits below every gate: no durable verdict, no duplicate,
        # no conflict — exercises queue/ask-later logic downstream.
        self.assertLess(0.5, DURABLE_GATE_USER)
        self.assertLess(0.5, SAME_CLAIM_GATE)
        self.assertLess(0.5, CONTRADICTION_GATE)


class ScriptedModeTest(unittest.TestCase):
    def test_scripted_answers_used(self):
        fake = FakeJev(
            mode="scripted",
            answers={"c0_durable": {"type": "noul", "noul": 0.123}},
        )
        response = fake.ask(STATE, DURABLE)
        self.assertEqual(response.answers["c0_durable"].noul, 0.123)

    def test_missing_scripted_qid_is_protocol_error(self):
        fake = FakeJev(mode="scripted", answers={"c0_category": {
            "type": "choice", "choice": "none",
            "probabilities": {"none": 1.0}, "confidence": 0.5,
        }})
        with self.assertRaises(JevProtocolError):
            fake.ask(STATE, phase_a_questions(1))

    def test_off_spec_scripted_answer_rejected_by_real_parser(self):
        fake = FakeJev(
            mode="scripted",
            answers={"c0_durable": {"type": "noul", "noul": 1.5}},
        )
        with self.assertRaises(JevProtocolError):
            fake.ask(STATE, DURABLE)


class ErrorInjectionTest(unittest.TestCase):
    def test_error_on_nth_call(self):
        fake = FakeJev(errors={2: JevRetryExhausted("overloaded")})
        first = fake.ask(STATE, phase_a_questions(1))  # call 1: fine
        self.assertEqual(first.answers["c0_durable"].noul, 0.9)
        with self.assertRaises(JevRetryExhausted):
            fake.ask(STATE, phase_a_questions(1))  # call 2: raises
        third = fake.ask(STATE, phase_a_questions(1))  # call 3: fine
        self.assertEqual(third.answers["c0_durable"].noul, 0.9)

    def test_error_always(self):
        fake = FakeJev(errors={"always": JevAuthError("bad key")})
        for _ in range(2):
            with self.assertRaises(JevAuthError):
                fake.ask(STATE, phase_a_questions(1))

    def test_errors_are_not_recorded_as_requests(self):
        # error keys are 1-based call numbers (ints)
        fake = FakeJev(errors={1: JevRetryExhausted("overloaded")})
        with self.assertRaises(JevRetryExhausted):
            fake.ask(STATE, phase_a_questions(1))
        self.assertEqual(fake.requests, [])
        self.assertEqual(fake.call_count, 1)


class RecordingTest(unittest.TestCase):
    def test_requests_recorded_in_order(self):
        fake = FakeJev()
        q1 = phase_a_questions(1)
        q2 = phase_b_pair_questions(0, 1)
        fake.ask(STATE, q1)
        fake.ask({"other": "state"}, q2)
        self.assertEqual(fake.call_count, 2)
        self.assertEqual(len(fake.requests), 2)
        recorded_state, recorded_questions = fake.requests[0]
        self.assertEqual(recorded_state, STATE)
        self.assertEqual(recorded_questions, dict(q1))
        self.assertEqual(fake.requests[1][0], {"other": "state"})
        self.assertEqual(fake.requests[1][1], dict(q2))


class ConstructionTest(unittest.TestCase):
    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            FakeJev(mode="chaos")

    def test_scripted_requires_answers(self):
        with self.assertRaises(ValueError):
            FakeJev(mode="scripted")


if __name__ == "__main__":
    unittest.main()
