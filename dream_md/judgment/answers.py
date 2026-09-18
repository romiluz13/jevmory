"""Typed Jev answers + strict parsing (docs/reference/typesafe-api.md).

Answer shapes, verbatim from the reference:

- Noul:   ``{"type": "noul", "noul": 0.92}`` — probability of yes.
  **No confidence field**: the noul value itself is the signal (distance
  from 0.5 is the certainty).
- Choice: ``{"type": "choice", "choice": "...", "probabilities": {...},
  "confidence": 0.82}`` — probabilities sum to 1 across the defined
  options.
- Score:  ``{"type": "score", "score": 1.6, "legend": {...},
  "probabilities": {...}, "confidence": 0.78}`` — probability-weighted,
  can land between levels.

Parsing is strict (``JevProtocolError`` on any violation): every number
in dream.md must be reproducible from stored receipts, so off-spec
answers are surfaced, never guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from dream_md.judgment.errors import JevProtocolError

NOUL = "noul"
CHOICE = "choice"
SCORE = "score"
QUESTION_TYPES = (NOUL, CHOICE, SCORE)

# Tolerance for "probabilities sum to 1" (floating-point envelopes).
_PROB_SUM_TOLERANCE = 0.01


@dataclass(frozen=True)
class NoulAnswer:
    """Yes/no probability. No confidence field by design (API spec)."""

    noul: float


@dataclass(frozen=True)
class ChoiceAnswer:
    """One picked option plus the full distribution and its confidence."""

    choice: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass(frozen=True)
class ScoreAnswer:
    """Weighted position on ordered levels (may land between levels)."""

    score: float
    legend: dict[str, Any] = field(default_factory=dict)
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


def parse_answer(expected_type: str, raw: Any) -> object:
    """Parse one raw answer dict against the question's declared type.

    Raises ``JevProtocolError`` on any shape/range violation; raises
    ``ValueError`` for an unknown ``expected_type`` (a programming
    error, not a protocol one).
    """
    if expected_type not in QUESTION_TYPES:
        raise ValueError(f"unknown question type: {expected_type!r}")
    if not isinstance(raw, Mapping):
        raise JevProtocolError(f"answer is not an object: {raw!r}")

    got = raw.get("type")
    if got != expected_type:
        raise JevProtocolError(
            f"answer type {got!r} does not match question type "
            f"{expected_type!r}"
        )

    if expected_type == NOUL:
        return _parse_noul(raw)
    if expected_type == CHOICE:
        return _parse_choice(raw)
    return _parse_score(raw)


def _parse_noul(raw: Mapping[str, Any]) -> NoulAnswer:
    if "noul" not in raw:
        raise JevProtocolError("noul answer missing 'noul' field")
    value = _probability(raw["noul"], "noul")
    return NoulAnswer(noul=value)


def _parse_choice(raw: Mapping[str, Any]) -> ChoiceAnswer:
    choice = raw.get("choice")
    if not isinstance(choice, str) or not choice:
        raise JevProtocolError(f"choice answer has invalid 'choice': {choice!r}")
    probabilities = _parse_probabilities(raw.get("probabilities"))
    if choice not in probabilities:
        raise JevProtocolError(
            f"picked choice {choice!r} is not among the defined options "
            f"{sorted(probabilities)}"
        )
    confidence = _confidence(raw.get("confidence"))
    return ChoiceAnswer(
        choice=choice, probabilities=probabilities, confidence=confidence
    )


def _parse_score(raw: Mapping[str, Any]) -> ScoreAnswer:
    if "score" not in raw:
        raise JevProtocolError("score answer missing 'score' field")
    score = raw["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise JevProtocolError(f"score is not a number: {score!r}")
    score = float(score)
    legend = raw.get("legend", {})
    probabilities = _parse_probabilities(raw.get("probabilities"))
    levels = _level_count(legend, probabilities)
    if not 0.0 <= score <= levels:
        raise JevProtocolError(
            f"score {score} outside level range [0, {levels}]"
        )
    confidence = _confidence(raw.get("confidence"))
    return ScoreAnswer(
        score=score,
        legend=dict(legend) if isinstance(legend, Mapping) else {},
        probabilities=probabilities,
        confidence=confidence,
    )


def _level_count(
    legend: Mapping[str, Any], probabilities: Mapping[str, float]
) -> int:
    """Highest defined level index (legend keys are string ints)."""
    if legend:
        try:
            return max(int(key) for key in legend)
        except (TypeError, ValueError):
            raise JevProtocolError(f"legend keys are not level indices: {legend!r}")
    if probabilities:
        try:
            return max(int(key) for key in probabilities)
        except (TypeError, ValueError):
            raise JevProtocolError(
                f"probability keys are not level indices: {list(probabilities)!r}"
            )
    raise JevProtocolError("score answer defines no levels (legend/probabilities)")


def _parse_probabilities(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping) or not raw:
        raise JevProtocolError(f"probabilities missing or empty: {raw!r}")
    probabilities = {
        key: _probability(value, f"probabilities[{key!r}]")
        for key, value in raw.items()
    }
    total = sum(probabilities.values())
    if abs(total - 1.0) > _PROB_SUM_TOLERANCE:
        raise JevProtocolError(f"probabilities sum to {total}, expected 1.0")
    return probabilities


def _probability(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevProtocolError(f"{field_name} is not a number: {value!r}")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise JevProtocolError(f"{field_name} out of range [0, 1]: {value}")
    return value


def _confidence(value: Any) -> float:
    if value is None:
        raise JevProtocolError("answer missing 'confidence'")
    return _probability(value, "confidence")
