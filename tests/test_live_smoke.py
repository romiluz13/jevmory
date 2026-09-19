"""Live-smoke script pins (PART 7): the one network path, tested offline.

``scripts/live_smoke.py`` is lead-run against the real Jev API; these
tests pin everything around that: the budget cap fails closed as a
JevError (run row closed, events queued), the probe drives the exact
request -> envelope -> strict-parse path against a scripted transport,
the probe corpus is synthetic and bounded, main() refuses cleanly
without a key, and the --dream stage checks the opt-in marker BEFORE
any request or store trace.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "live_smoke", ROOT / "scripts" / "live_smoke.py"
)
live_smoke = importlib.util.module_from_spec(_spec)
sys.modules["live_smoke"] = live_smoke  # @dataclass needs the module registered
_spec.loader.exec_module(live_smoke)

from jev_md.judgment.answers import ChoiceAnswer, NoulAnswer, ScoreAnswer
from jev_md.judgment.errors import JevError


class ScriptedTransport:
    """Offline stand-in for the HTTP layer: builds a valid envelope.

    Reads the question set out of the request body and answers every
    question on-spec, so the client's strict parser runs for real —
    the same discipline FakeJev's scripted mode applies.
    """

    def __init__(self) -> None:
        self.seen_bodies: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes,
                 timeout: float) -> bytes:
        payload = json.loads(body.decode("utf-8"))
        self.seen_bodies.append(payload)
        answers = {
            qid: _raw_answer(question)
            for qid, question in payload["questions"].items()
        }
        envelope = {
            "answers": answers,
            "usage": {"input_tokens": 1234, "output_tokens": 210},
            "model": "jev-latest",
        }
        return json.dumps(envelope).encode("utf-8")


def _raw_answer(question: dict[str, Any]) -> dict[str, Any]:
    kind = question["type"]
    if kind == "noul":
        return {"type": "noul", "noul": 0.9}
    if kind == "choice":
        options = list(question["criteria"])
        target = "tooling" if "tooling" in options else options[0]
        peak = 0.7
        rest = (1.0 - peak) / (len(options) - 1)
        return {
            "type": "choice",
            "choice": target,
            "probabilities": {
                option: peak if option == target else rest
                for option in options
            },
            "confidence": 0.8,
        }
    levels = list(question["criteria"])
    base = {"0": 0.1, "1": 0.6, "2": 0.2, "3": 0.1}
    probabilities = {k: base[k] for k in list(base)[: len(levels)]}
    # rebalance to sum exactly 1.0 for any level count
    total = sum(probabilities.values())
    probabilities = {k: v / total for k, v in probabilities.items()}
    score = sum(int(k) * v for k, v in probabilities.items())
    return {
        "type": "score",
        "score": score,
        "legend": {str(i): level for i, level in enumerate(levels)},
        "probabilities": probabilities,
        "confidence": 0.8,
    }


class CountingTransportTest(unittest.TestCase):
    """The budget cap: every request counts, failure is closed."""

    def test_counts_calls_and_passes_under_cap(self):
        budget = live_smoke.CountingTransport(3, lambda *a: b"{}")
        self.assertEqual(budget("u", {}, b"{}", 1.0), b"{}")
        self.assertEqual(budget("u", {}, b"{}", 1.0), b"{}")
        self.assertEqual(budget.calls, 2)

    def test_raises_budget_exhausted_jeverror_at_cap(self):
        budget = live_smoke.CountingTransport(2, lambda *a: b"{}")
        budget("u", {}, b"{}", 1.0)
        budget("u", {}, b"{}", 1.0)
        with self.assertRaises(live_smoke.BudgetExhausted) as caught:
            budget("u", {}, b"{}", 1.0)
        # a JevError so the engine closes the run row and re-raises —
        # events stay queued, the smoke never overspends
        self.assertIsInstance(caught.exception, JevError)
        self.assertEqual(budget.calls, 2)  # the capped call never fired

    def test_rejects_non_positive_cap(self):
        with self.assertRaises(ValueError):
            live_smoke.CountingTransport(0, lambda *a: b"{}")


class ProbeTest(unittest.TestCase):
    """The 3-candidate probe: synthetic, bounded, strictly parsed."""

    def test_probe_state_synthetic_and_bounded(self):
        state = live_smoke.probe_state(3)
        self.assertEqual(len(state["candidates"]), 3)
        self.assertTrue(state["project_context"]["synthetic"])
        for candidate in state["candidates"]:
            self.assertEqual(
                set(candidate), {"id", "role", "text", "context"}
            )
        with self.assertRaises(ValueError):
            live_smoke.probe_state(0)
        with self.assertRaises(ValueError):
            live_smoke.probe_state(4)

    def test_probe_end_to_end_against_scripted_transport(self):
        transport = ScriptedTransport()
        response, budget = live_smoke.run_probe(
            "test-key", cap=5, count=3, transport=transport
        )
        # one request, nine questions (the Phase A triple x 3)
        self.assertEqual(budget.calls, 1)
        self.assertEqual(len(response.answers), 9)
        self.assertIsInstance(response.answers["c0_durable"], NoulAnswer)
        self.assertIsInstance(response.answers["c0_category"], ChoiceAnswer)
        self.assertIsInstance(
            response.answers["c0_significance"], ScoreAnswer
        )
        self.assertEqual(response.answers["c0_category"].choice, "tooling")
        # usage accounting came from the envelope
        self.assertEqual(response.usage.input_tokens, 1234)
        self.assertEqual(response.usage.output_tokens, 210)
        # the request the API would have seen: synthetic state + the
        # triple question set, Bearer auth
        body = transport.seen_bodies[0]
        self.assertEqual(len(body["questions"]), 9)
        self.assertTrue(body["state"]["project_context"]["synthetic"])


class DreamSmokeGatesTest(unittest.TestCase):
    """--dream: marker gate BEFORE any request or store trace."""

    def setUp(self):
        self._old_home = os.environ.get("JEV_MD_HOME")
        self._tmp = TemporaryDirectory()
        os.environ["JEV_MD_HOME"] = self._tmp.name
        self.project = self._tmp.name + "/proj"
        os.makedirs(self.project)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("JEV_MD_HOME", None)
        else:
            os.environ["JEV_MD_HOME"] = self._old_home
        self._tmp.cleanup()

    def test_marker_gate_fires_before_any_egress_or_store(self):
        fired = []

        def trap(*args):
            fired.append(args)
            raise AssertionError("network must not be touched pre-marker")

        with self.assertRaises(live_smoke.GradingNotEnabledError):
            live_smoke.run_dream_smoke(
                self.project, "k", cap=5, transport=trap
            )
        self.assertEqual(fired, [])  # no request attempted
        # and no store created either — zero trace
        self.assertFalse(
            live_smoke.store_path(self.project).exists()
        )


class MainTest(unittest.TestCase):
    """Entry behavior: clean refusal without a key."""

    def setUp(self):
        self._old = os.environ.get("TYPESAFE_API_KEY")
        os.environ.pop("TYPESAFE_API_KEY", None)

    def tearDown(self):
        if self._old is not None:
            os.environ["TYPESAFE_API_KEY"] = self._old
        else:
            os.environ.pop("TYPESAFE_API_KEY", None)

    def test_refuses_cleanly_without_key(self):
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = live_smoke.main(["--probe"])
        self.assertEqual(code, 1)
        self.assertIn("TYPESAFE_API_KEY", err.getvalue())

    def test_rejects_non_positive_cap_cleanly(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        err = io.StringIO()
        out = io.StringIO()
        try:
            with redirect_stderr(err), redirect_stdout(out):
                code = live_smoke.main(["--probe", "--cap", "0"])
        finally:
            os.environ.pop("TYPESAFE_API_KEY", None)
        self.assertEqual(code, 1)
        self.assertIn("--cap", err.getvalue())

    def test_dream_stage_exposes_the_force_flag(self):
        # T7: --force used to be unreachable from the CLI; it now parses
        # and plumbs into run_dream_smoke (here: clean marker refusal —
        # no request, no store; the gate fires first as always)
        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("JEV_MD_HOME")
            os.environ["JEV_MD_HOME"] = tmp
            os.environ["TYPESAFE_API_KEY"] = "test-key"
            err, out = io.StringIO(), io.StringIO()
            try:
                with redirect_stderr(err), redirect_stdout(out):
                    code = live_smoke.main(["--dream", "--force"])
            finally:
                os.environ.pop("TYPESAFE_API_KEY", None)
                if old_home is None:
                    os.environ.pop("JEV_MD_HOME", None)
                else:
                    os.environ["JEV_MD_HOME"] = old_home
        self.assertEqual(code, 1)
        self.assertIn("grading is not enabled", err.getvalue())


if __name__ == "__main__":
    unittest.main()
