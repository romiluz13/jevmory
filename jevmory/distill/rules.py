"""Verdict rules for the distill engine (PLAN "Decision rules", M5).

Code, not Jev, decides — Jev only answers. Every rule reads verbatim
typed answers and applies a named threshold from ``thresholds.py``;
the same answers are stored verbatim as receipts, so every status
change is reproducible from ``judgments``.

Phase A (per candidate):

- durable gate by role — user statements are decisions (>= 0.7),
  assistant statements are narration (>= 0.8);
- significance gate (>= 1.0);
- near-misses (0.5 <= durable < gate) are never silent: flagged so
  ``status`` can surface them and ``runs.stats`` counts them.

Phase B (per candidate x fact pair):

- ``same_claim >= 0.8`` -> duplicate (bump support, no new fact);
- else ``contradicts >= 0.6`` and the verdict picks ``new_overrides``
  with confidence >= 0.8 -> supersede (old fact out, new fact in);
- else a conflict that is not decisively overridden -> ask (both rows
  kept; the challenger is held in the ask state for a human or expiry);
- otherwise -> none (unrelated fact, plain add).
"""

from __future__ import annotations

from dataclasses import dataclass

from jevmory.ingestion.extract import Candidate
from jevmory.ingestion.models import ROLE_USER
from jevmory.judgment.answers import ChoiceAnswer, NoulAnswer, ScoreAnswer
from jevmory.judgment.questions import VERDICT_OPTIONS
from jevmory.thresholds import (
    CONTRADICTION_GATE,
    DURABLE_GATE_ASSISTANT,
    DURABLE_GATE_USER,
    NEAR_MISS_LOW,
    SAME_CLAIM_GATE,
    SIGNIFICANCE_GATE,
    SUPERSEDE_CONFIDENCE,
)

# Stable receipt question keys (code-side; wire ids are per-request).
Q_DURABLE = "durable"
Q_CATEGORY = "category"
Q_SIGNIFICANCE = "significance"
Q_SAME_CLAIM = "same_claim"
Q_CONTRADICTS = "contradicts"
Q_VERDICT = "verdict"

NEW_OVERRIDES = VERDICT_OPTIONS[0]  # pinned vocabulary, same string

DROP_LOW_DURABLE = "low_durable"
DROP_LOW_SIGNIFICANCE = "low_significance"

# Phase B pair actions (priority order when several pairs fire).
ACTION_DUPLICATE = "duplicate"
ACTION_SUPERSEDE = "supersede"
ACTION_ASK = "ask"
ACTION_NONE = "none"


@dataclass(frozen=True)
class CandidateVerdict:
    """The Phase A decision for one candidate, answers kept verbatim."""

    candidate: Candidate
    durable: NoulAnswer
    category: ChoiceAnswer
    significance: ScoreAnswer
    kept: bool  # passed both gates -> becomes (or meets) a fact
    near_miss: bool  # NEAR_MISS_LOW <= durable < gate — surfaced, never silent
    dropped_reason: str | None  # DROP_LOW_DURABLE | DROP_LOW_SIGNIFICANCE


def phase_a_verdict(
    candidate: Candidate,
    durable: NoulAnswer,
    category: ChoiceAnswer,
    significance: ScoreAnswer,
    *,
    user_gate: float = DURABLE_GATE_USER,
    assistant_gate: float = DURABLE_GATE_ASSISTANT,
    significance_gate: float = SIGNIFICANCE_GATE,
    near_miss_low: float = NEAR_MISS_LOW,
) -> CandidateVerdict:
    """Apply the Phase A gates to one candidate's verbatim answers."""
    gate = user_gate if candidate.role == ROLE_USER else assistant_gate
    durable_pass = durable.noul >= gate
    significance_pass = significance.score >= significance_gate
    kept = durable_pass and significance_pass
    if kept:
        dropped_reason = None
    elif not durable_pass:
        dropped_reason = DROP_LOW_DURABLE
    else:
        dropped_reason = DROP_LOW_SIGNIFICANCE
    return CandidateVerdict(
        candidate=candidate,
        durable=durable,
        category=category,
        significance=significance,
        kept=kept,
        near_miss=near_miss_low <= durable.noul < gate,
        dropped_reason=dropped_reason,
    )


@dataclass(frozen=True)
class PairVerdict:
    """The Phase B decision for one (candidate, fact) pair, verbatim answers."""

    candidate_event_id: str
    fact_id: int
    same_claim: NoulAnswer
    contradicts: NoulAnswer
    verdict: ChoiceAnswer
    action: str  # ACTION_* — what the engine does with this pair


def pair_action(
    same_claim: NoulAnswer,
    contradicts: NoulAnswer,
    verdict: ChoiceAnswer,
    *,
    same_claim_gate: float = SAME_CLAIM_GATE,
    contradiction_gate: float = CONTRADICTION_GATE,
    supersede_confidence: float = SUPERSEDE_CONFIDENCE,
) -> str:
    """Apply the Phase B rules to one pair's verbatim answers."""
    if same_claim.noul >= same_claim_gate:
        return ACTION_DUPLICATE
    if contradicts.noul >= contradiction_gate:
        if (
            verdict.choice == NEW_OVERRIDES
            and verdict.confidence >= supersede_confidence
        ):
            return ACTION_SUPERSEDE
        return ACTION_ASK
    return ACTION_NONE
