"""Phase C batch planner: greedy fill by token estimate, per-line
fallback (PLAN M4 + the M2 batcher's rules).

Every audit request carries the SAME evidence pool (recent facts +
recent statements, redacted) — evidence is constant per-request
overhead, unlike Phase A where only project context is shared. Lines
pack in original order while the estimate (state + all question
objects, chars/4) fits ``BATCH_TOKEN_BUDGET``; when adding the next
line would overflow, the batch closes. The floor is one line per
request; if even that overflows (a huge evidence pool), ``BatchError``
— never send an over-budget request.

Same two-layer discipline as ``plan_phase_a``: the greedy decision
uses a compositional char count, then every emitted batch is
re-verified with the real estimator so the two can never drift
silently.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dream_md.audit.memfile import MemoryLine
from dream_md.audit.questions import audit_state, line_entry, phase_c_questions, phase_c_triple
from dream_md.judgment.batch import Batch, BatchError
from dream_md.judgment.questions import Question
from dream_md.judgment.tokens import estimate_tokens, json_chars
from dream_md.thresholds import BATCH_TOKEN_BUDGET, CHARS_PER_TOKEN


def plan_phase_c(
    lines: Sequence[MemoryLine],
    facts: Sequence[Mapping[str, Any]],
    statements: Sequence[Mapping[str, Any]],
    project_context: Mapping[str, Any] | None = None,
    *,
    budget_tokens: int = BATCH_TOKEN_BUDGET,
) -> list[Batch]:
    """Plan Phase C requests for ``lines`` within ``budget_tokens``.

    Order is preserved (line ids map back by position); empty input
    plans zero requests (zero spend); coverage is exact — every line
    appears in exactly one batch. ``Batch.candidate_ids`` carries the
    line subject ids (``line:<n>``) — the same id appears in each
    state entry, so answers map back unambiguously.
    """
    lines = list(lines)
    if not lines:
        return []
    if budget_tokens <= 0:
        raise ValueError("budget_tokens must be positive")

    context = dict(project_context or {})

    # Composable char costs, exactly matching the real serialization.
    skeleton = json_chars(
        {
            "memory_lines": [],
            "project_context": context,
            "evidence": {"facts": [dict(f) for f in facts],
                          "statements": [dict(s) for s in statements]},
        }
    )
    questions_base = json_chars({"questions": {}})
    entry_chars = [json_chars(line_entry(line)) for line in lines]
    triple_chars = [
        sum(
            len(f'"{qid}":') + json_chars(question.to_wire())
            for qid, question in phase_c_triple(position).items()
        )
        + 2  # commas between the triple's questions
        for position in range(len(lines))
    ]

    def tokens(chars: int) -> int:
        return -(-chars // CHARS_PER_TOKEN)

    groups: list[list[int]] = []
    current: list[int] = []
    current_chars = skeleton + questions_base
    for index, chars in enumerate(entry_chars):
        position = len(current)
        # commas: one in the memory_lines array, one between question triples
        commas = 2 if position > 0 else 0
        attempt_chars = current_chars + chars + triple_chars[position] + commas
        if tokens(attempt_chars) <= budget_tokens:
            current.append(index)
            current_chars = attempt_chars
        elif not current:
            # per-line fallback floor: this line alone + evidence overflows
            raise BatchError(
                f"memory line {lines[index].id} plus the evidence pool "
                f"needs ~{tokens(attempt_chars)} tokens, over the "
                f"{budget_tokens} budget; shrink the evidence pool or "
                "raise the budget"
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
        group_lines = [lines[i] for i in group]
        batch = Batch(
            state=audit_state(
                context, group_lines, facts, statements
            ),
            questions=phase_c_questions(len(group_lines)),
            candidate_ids=tuple(line.id for line in group_lines),
        )
        estimate = batch.token_estimate()
        if estimate > budget_tokens:  # compositional math drifted: bug
            raise BatchError(
                f"internal estimate mismatch: batch measures {estimate} "
                f"tokens > budget {budget_tokens}"
            )
        batches.append(batch)
    return batches
