"""Phase B batch planner (PLAN M5): pairs of candidates vs remembered facts.

Same discipline as Phase A (``dream_md.judgment.batch``) and Phase C
(``dream_md.audit.batch``): greedy fill in original order while the
compositional char estimate fits ``BATCH_TOKEN_BUDGET``, floor of one
candidate + its facts per request (``BatchError`` below that), and
every emitted batch re-verified with the real estimator so the two can
never drift silently.

What makes Phase B different: a request carries a SHARED ``facts``
array — several candidates' partners merge into one array, and every
candidate pairs against each of its partners. Question ids encode pair
positions (``p{i}_{j}_same_claim``), so when the greedy planner closes
a batch mid-stream, positions reset and the pair questions for the
carried-over candidate are REBUILT against the new group's positions
(the attempt cost is always computed from the would-be positions, and
a committed cost is never reused across a group boundary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from dream_md.ingestion.extract import Candidate
from dream_md.judgment.batch import Batch, BatchError
from dream_md.judgment.questions import (
    candidate_entry,
    phase_b_pair_questions,
    phase_b_questions,
    phase_b_state,
)
from dream_md.judgment.tokens import estimate_tokens, json_chars
from dream_md.thresholds import BATCH_TOKEN_BUDGET, CHARS_PER_TOKEN


@dataclass(frozen=True)
class PairPlan:
    """One survivor candidate plus the fact entries it must meet.

    ``facts`` are the exact state entries (mappings with at least
    ``id`` and ``text``) so the planner's char math and
    ``phase_b_state`` serialize the very same objects — never two
    serializations of one fact.
    """

    candidate: Candidate
    facts: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class PhaseBBatch(Batch):
    """One Phase B request plus the pair map back to candidates and facts.

    ``pairs`` are ``(candidate_position, fact_position)`` in this
    batch's state arrays; ``pair_plans`` / ``fact_ids`` let the engine
    resolve those positions without parsing question ids.
    """

    pairs: tuple[tuple[int, int], ...] = ()
    pair_plans: tuple[PairPlan, ...] = ()
    fact_ids: tuple[int, ...] = ()


class _Group:
    """Mutable accumulator for one forming batch (never leaves the planner)."""

    def __init__(self, skeleton_chars: int) -> None:
        self.plans: list[PairPlan] = []
        self.fact_entries: list[Mapping[str, Any]] = []
        self.fact_positions: dict[int, int] = {}  # fact id -> position
        self.pairs: list[tuple[int, int]] = []
        self.chars = skeleton_chars

    def position_of(self, fact_id: int) -> int | None:
        return self.fact_positions.get(fact_id)


def plan_phase_b(
    pair_plans: Sequence[PairPlan],
    project_context: Mapping[str, Any] | None = None,
    *,
    budget_tokens: int = BATCH_TOKEN_BUDGET,
) -> list[PhaseBBatch]:
    """Plan Phase B requests for ``pair_plans`` within ``budget_tokens``.

    Every plan's candidates and facts keep first-seen order; a fact
    needed by two candidates appears once in the shared array. Empty
    input plans zero requests; a plan with no facts is a programming
    error (the engine adds such candidates directly, no pairing).
    """
    plans = list(pair_plans)
    if not plans:
        return []
    if budget_tokens <= 0:
        raise ValueError("budget_tokens must be positive")
    for plan in plans:
        if not plan.facts:
            raise ValueError(
                f"pair plan for candidate {plan.candidate.event_id} has no "
                "facts; add the fact directly instead of pairing"
            )

    context = dict(project_context or {})
    skeleton = json_chars(
        {"candidates": [], "facts": [], "project_context": context}
    )
    questions_base = json_chars({"questions": {}})
    entry_chars = [json_chars(candidate_entry(p.candidate)) for p in plans]

    def tokens(chars: int) -> int:
        return -(-chars // CHARS_PER_TOKEN)

    def triple_chars(i: int, j: int) -> int:
        return sum(
            len(f'"{qid}":') + json_chars(question.to_wire())
            for qid, question in phase_b_pair_questions(i, j).items()
        ) + 2  # commas between the triple's questions

    groups: list[_Group] = []
    current = _Group(skeleton + questions_base)

    for index, plan in enumerate(plans):
        while True:  # retried once when a group closes under this plan
            i = len(current.plans)
            j_base = len(current.fact_entries)
            new_facts = [
                entry
                for entry in plan.facts
                if entry["id"] not in current.fact_positions
            ]
            # would-be positions: existing facts stay, new ones append
            positions = dict(current.fact_positions)
            for offset, entry in enumerate(new_facts):
                positions[entry["id"]] = j_base + offset

            attempt = current.chars + entry_chars[index]
            if i > 0:
                attempt += 1  # comma in the candidates array
            for offset, entry in enumerate(new_facts):
                attempt += json_chars(entry)
                if j_base + offset > 0:
                    attempt += 1  # comma in the facts array
            for order, entry in enumerate(plan.facts):
                attempt += triple_chars(i, positions[entry["id"]])
                if current.pairs or order > 0:
                    attempt += 1  # comma separating question triples

            if tokens(attempt) <= budget_tokens:
                current.plans.append(plan)
                for entry in new_facts:
                    current.fact_entries.append(entry)
                    current.fact_positions[entry["id"]] = len(
                        current.fact_entries
                    ) - 1
                for entry in plan.facts:
                    pair = (i, positions[entry["id"]])
                    if pair not in current.pairs:  # one question set per pair
                        current.pairs.append(pair)
                current.chars = attempt
                break
            if not current.plans:
                raise BatchError(
                    f"candidate {plan.candidate.event_id} plus its facts "
                    f"needs ~{tokens(attempt)} tokens, over the "
                    f"{budget_tokens} budget; shrink project_context or "
                    "raise the budget"
                )
            groups.append(current)
            current = _Group(skeleton + questions_base)

    if current.plans:
        groups.append(current)

    batches: list[PhaseBBatch] = []
    for group in groups:
        batch = PhaseBBatch(
            state=phase_b_state(
                context, [p.candidate for p in group.plans], group.fact_entries
            ),
            questions=phase_b_questions(group.pairs),
            candidate_ids=tuple(p.candidate.event_id for p in group.plans),
            pairs=tuple(group.pairs),
            pair_plans=tuple(group.plans),
            fact_ids=tuple(
                entry["id"] for entry in group.fact_entries
            ),
        )
        estimate = batch.token_estimate()
        if estimate > budget_tokens:  # compositional math drifted: bug
            raise BatchError(
                f"internal estimate mismatch: batch measures {estimate} "
                f"tokens > budget {budget_tokens}"
            )
        batches.append(batch)
    return batches
