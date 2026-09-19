"""Token estimator for Jev request budgeting (PLAN Phase A: state + all
question instructions, chars/4, <= ~28k tokens per request).

Counts EVERYTHING that goes over the wire and consumes budget: the full
state JSON and the full questions mapping (ids included — they are keys
of the request body). Deliberately a pessimistic chars/4 heuristic: no
tokenizer dependency (stdlib only), and overshooting the estimate is
safe (a smaller real request) while undershooting wastes a 422.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from jevmory.judgment.questions import Question
from jevmory.thresholds import CHARS_PER_TOKEN


def json_chars(value: Any) -> int:
    """Length of the compact deterministic JSON serialization.

    ``sort_keys=True`` + compact separators: element serializations are
    composable (the serialization of a container is built exactly from
    the serializations of its elements), which ``batch`` relies on to
    estimate a batch without re-serializing it per candidate.
    """
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")))


def questions_wire(questions: Mapping[str, Question]) -> dict[str, Any]:
    """The ``questions`` member of the request body."""
    return {qid: question.to_wire() for qid, question in questions.items()}


def estimate_tokens(
    state: Any, questions: Mapping[str, Question]
) -> int:
    """Ceil((state chars + question chars) / CHARS_PER_TOKEN)."""
    chars = json_chars(state) + json_chars({"questions": questions_wire(questions)})
    return -(-chars // CHARS_PER_TOKEN)
