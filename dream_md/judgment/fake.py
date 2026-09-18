"""FakeJev — an in-memory Jev for offline tests (no network, no key).

Duck-type compatible with ``JevClient.ask``: same arguments, same
``JevResponse`` return. Answers go through the SAME strict parser as
real envelopes, so scripted fixtures cannot quietly drift off-spec.

Modes:

- ``normal`` — deterministic, decisive answers (durable 0.9, duplicates
  0.9, contradictions 0.08, peaked choice/score distributions). For
  happy-path pipeline tests.
- ``adversarial`` — flat distributions, exact 0.5 nouls, zero
  confidence. Every gate in thresholds.py lands in its uncertain band
  (near-miss durable, no duplicate, no conflict), which is exactly what
  exercises ask/queue/dropped-low-durable logic downstream.
- ``scripted`` — explicit raw answers per question id; missing ids are
  a protocol error (fixtures must be complete).

``errors`` injects transport-style failures by 1-based call number
(``{2: JevRetryExhausted(...)}``) or ``{"always": ...}`` so retry and
queue-on-failure paths are testable without any HTTP.

Every request is recorded (``requests``, ``call_count``) for precise
assertions about what left the machine.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from dream_md.judgment.answers import parse_answer
from dream_md.judgment.client import DEFAULT_MODEL, JevResponse, Usage
from dream_md.judgment.errors import JevProtocolError
from dream_md.judgment.questions import Question

MODE_NORMAL = "normal"
MODE_ADVERSARIAL = "adversarial"
MODE_SCRIPTED = "scripted"
MODES = (MODE_NORMAL, MODE_ADVERSARIAL, MODE_SCRIPTED)

# Normal-mode decisive values (deterministic, never random).
_NORMAL_NOUL = {"durable": 0.9, "same_claim": 0.9, "supported": 0.9}
_NORMAL_NOUL_LOW = {"contradicts": 0.08, "contradicted": 0.08}
_NORMAL_CONFIDENCE = 0.8
_NORMAL_PEAK = 0.7
_ADVERSARIAL_NOUL = 0.5


class FakeJev:
    """In-memory Jev; construct with a mode, assert with ``requests``."""

    def __init__(
        self,
        *,
        mode: str = MODE_NORMAL,
        answers: Mapping[str, Mapping[str, Any]] | None = None,
        errors: Mapping[Any, BaseException] | None = None,
        usage: Usage | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        if mode == MODE_SCRIPTED and not answers:
            raise ValueError("scripted mode requires an answers mapping")
        self.mode = mode
        self._scripted = dict(answers or {})
        self._errors = dict(errors or {})
        self._usage = usage or Usage(input_tokens=312, output_tokens=48)
        self.requests: list[tuple[Any, dict[str, Question]]] = []
        self.call_count = 0

    def ask(
        self, state: Any, questions: Mapping[str, Question]
    ) -> JevResponse:
        """Same contract as ``JevClient.ask``."""
        self.call_count += 1
        error = self._error_for(self.call_count)
        if error is not None:
            raise error
        self.requests.append((state, dict(questions)))

        raw: dict[str, Any]
        if self.mode == MODE_SCRIPTED:
            raw = {}
            for qid in questions:
                if qid not in self._scripted:
                    raise JevProtocolError(
                        f"no scripted answer for question {qid!r}"
                    )
                raw[qid] = self._scripted[qid]
        elif self.mode == MODE_ADVERSARIAL:
            raw = {
                qid: self._adversarial(question)
                for qid, question in questions.items()
            }
        else:
            raw = {
                qid: self._normal(qid, question)
                for qid, question in questions.items()
            }

        parsed = {
            qid: parse_answer(questions[qid].type, raw[qid])
            for qid in questions
        }
        return JevResponse(answers=parsed, usage=self._usage, model=DEFAULT_MODEL)

    def _error_for(self, call: int) -> BaseException | None:
        if "always" in self._errors:
            return self._errors["always"]
        return self._errors.get(call)

    # --- answer synthesis ----------------------------------------------------

    def _normal(self, qid: str, question: Question) -> dict[str, Any]:
        suffix = qid.rsplit("_", 1)[-1]
        if question.type == "noul":
            value = _NORMAL_NOUL_LOW.get(suffix, _NORMAL_NOUL.get(suffix, 0.9))
            return {"type": "noul", "noul": value}
        if question.type == "choice":
            options = list(question.criteria)
            target = _NORMAL_CHOICE_TARGET.get(suffix)
            if target not in options:
                target = options[0]
            return {
                "type": "choice",
                "choice": target,
                "probabilities": _peaked(options, target),
                "confidence": _NORMAL_CONFIDENCE,
            }
        levels = list(question.criteria)
        peaked_index = min(1, len(levels) - 1)  # "useful" on the 4-level scale
        probabilities = _peaked_levels(len(levels), peaked_index)
        return {
            "type": "score",
            "score": _weighted_score(probabilities),
            "legend": {str(i): level for i, level in enumerate(levels)},
            "probabilities": probabilities,
            "confidence": _NORMAL_CONFIDENCE,
        }

    def _adversarial(self, question: Question) -> dict[str, Any]:
        if question.type == "noul":
            return {"type": "noul", "noul": _ADVERSARIAL_NOUL}
        if question.type == "choice":
            options = list(question.criteria)
            share = 1.0 / len(options)
            return {
                "type": "choice",
                "choice": options[0],
                "probabilities": {option: share for option in options},
                "confidence": 0.0,
            }
        levels = list(question.criteria)
        share = 1.0 / len(levels)
        probabilities = {str(i): share for i in range(len(levels))}
        return {
            "type": "score",
            "score": _weighted_score(probabilities),
            "legend": {str(i): level for i, level in enumerate(levels)},
            "probabilities": probabilities,
            "confidence": 0.0,
        }


_NORMAL_CHOICE_TARGET = {
    "category": "convention",
    "verdict": "new_overrides",
    "disposition": "keep",
}


def _peaked(options: list[str], target: str) -> dict[str, float]:
    """One option at the peak, the rest sharing the remainder (sums to 1)."""
    rest = (1.0 - _NORMAL_PEAK) / (len(options) - 1)
    return {option: (_NORMAL_PEAK if option == target else rest) for option in options}


def _peaked_levels(count: int, peaked_index: int) -> dict[str, float]:
    """Level distribution peaked at ``peaked_index``, summing to 1.

    Peak gets 0.6, immediate neighbours share 0.3, remaining levels
    share 0.1; when only the peak and its neighbour(s) exist the
    neighbour share absorbs the remainder.
    """
    if count == 1:
        return {"0": 1.0}
    neighbours = [i for i in range(count) if abs(i - peaked_index) == 1]
    rest = [i for i in range(count) if i != peaked_index and i not in neighbours]
    probabilities = {str(i): 0.0 for i in range(count)}
    probabilities[str(peaked_index)] = 0.6
    neighbour_share = 0.4 if not rest else 0.3
    for i in neighbours:
        probabilities[str(i)] = neighbour_share / len(neighbours)
    for i in rest:
        probabilities[str(i)] = 0.1 / len(rest)
    return probabilities


def _weighted_score(probabilities: Mapping[str, float]) -> float:
    """Probability-weighted level position (may land between levels)."""
    return sum(int(level) * weight for level, weight in probabilities.items())
