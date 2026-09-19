"""Phase A batch planner: greedy fill by token estimate, per-candidate
fallback (PLAN Phase A + "Batcher" note).

Every request carries the shared state (project context + the batch's
candidates) and 3 speculative questions per candidate. The planner
packs candidates in original order while the estimate (state + all
question objects, chars/4) fits ``BATCH_TOKEN_BUDGET``; when adding the
next candidate would overflow, the batch is closed. The floor is one
candidate per request (the plan's per-candidate fallback); if even that
exceeds the budget, ``BatchError`` — never send an over-budget request.

The greedy decision uses a compositional char count (compact
``sort_keys`` JSON is built exactly from its elements' serializations),
then every emitted batch is re-verified with the real estimator — the
two can never drift silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from jevmory.ingestion.extract import Candidate
from jevmory.judgment.questions import (
    Question,
    candidate_entry,
    phase_a_questions,
    phase_a_state,
    phase_a_triple,
)
from jevmory.judgment.tokens import estimate_tokens, json_chars
from jevmory.thresholds import BATCH_TOKEN_BUDGET, CHARS_PER_TOKEN


class BatchError(Exception):
    """A single candidate + context cannot fit the token budget."""


@dataclass(frozen=True)
class Batch:
    """One Jev request: state, questions, and the candidates they grade."""

    state: dict[str, Any] = field(default_factory=dict)
    questions: dict[str, Question] = field(default_factory=dict)
    candidate_ids: tuple[str, ...] = ()

    def token_estimate(self) -> int:
        """The real estimator over this batch's state and questions."""
        return estimate_tokens(self.state, self.questions)


def plan_phase_a(
    candidates: Sequence[Candidate],
    project_context: Mapping[str, Any] | None = None,
    *,
    budget_tokens: int = BATCH_TOKEN_BUDGET,
) -> list[Batch]:
    """Plan Phase A requests for ``candidates`` within ``budget_tokens``.

    Order is preserved (candidate ids map back by position); empty input
    plans zero requests (zero spend); coverage is exact — every
    candidate appears in exactly one batch.
    """
    candidates = list(candidates)
    if not candidates:
        return []
    if budget_tokens <= 0:
        raise ValueError("budget_tokens must be positive")

    context = dict(project_context or {})

    # Composable char costs, exactly matching the real serialization.
    skeleton = json_chars({"candidates": [], "project_context": context})
    questions_base = json_chars({"questions": {}})
    entry_chars = [json_chars(candidate_entry(c)) for c in candidates]
    triple_chars = [
        sum(
            len(f'"{qid}":') + json_chars(question.to_wire())
            for qid, question in phase_a_triple(position).items()
        )
        + 2  # commas between the triple's questions
        for position in range(len(candidates))
    ]

    def tokens(chars: int) -> int:
        return -(-chars // CHARS_PER_TOKEN)

    groups: list[list[int]] = []
    current: list[int] = []
    current_chars = skeleton + questions_base
    for index, chars in enumerate(entry_chars):
        position = len(current)
        # commas: one in the candidates array, one between question triples
        commas = 2 if position > 0 else 0
        attempt_chars = current_chars + chars + triple_chars[position] + commas
        if tokens(attempt_chars) <= budget_tokens:
            current.append(index)
            current_chars = attempt_chars
        elif not current:
            # per-callback fallback floor: this candidate alone overflows
            raise BatchError(
                f"candidate {candidates[index].event_id} plus project "
                f"context needs ~{tokens(attempt_chars)} tokens, over the "
                f"{budget_tokens} budget; shrink project_context or raise "
                "the budget"
            )
        else:
            groups.append(current)
            current = [index]
            current_chars = (
                skeleton + questions_base + chars + triple_chars[0]
            )
    if current:
        groups.append(current)

    batches: list[Batch] = []
    for group in groups:
        group_candidates = [candidates[i] for i in group]
        batch = Batch(
            state=phase_a_state(context, group_candidates),
            questions=phase_a_questions(len(group_candidates)),
            candidate_ids=tuple(c.event_id for c in group_candidates),
        )
        estimate = batch.token_estimate()
        if estimate > budget_tokens:  # compositional math drifted: bug
            raise BatchError(
                f"internal estimate mismatch: batch measures {estimate} "
                f"tokens > budget {budget_tokens}"
            )
        batches.append(batch)
    return batches
