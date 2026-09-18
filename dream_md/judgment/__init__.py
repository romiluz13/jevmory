"""Judgment sub-domain (M2): the Jev transport layer.

Public surface:

- Questions/state: ``Question``, ``phase_a_state``, ``phase_a_questions``,
  ``phase_b_state``, ``phase_b_pair_questions``, ``phase_b_questions``.
- Estimation/batching: ``estimate_tokens``, ``Batch``, ``plan_phase_a``.
- Transport: ``JevClient`` (real, injectable), ``FakeJev`` (offline),
  ``JevResponse``, ``Usage``, and the ``JevError`` hierarchy.
- Answers: ``NoulAnswer``, ``ChoiceAnswer``, ``ScoreAnswer``,
  ``parse_answer``.
"""

from dream_md.judgment.answers import (
    CHOICE,
    NOUL,
    SCORE,
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
    parse_answer,
)
from dream_md.judgment.batch import Batch, BatchError, plan_phase_a
from dream_md.judgment.client import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    JevClient,
    JevResponse,
    Usage,
)
from dream_md.judgment.errors import (
    JevAuthError,
    JevError,
    JevProtocolError,
    JevRetryExhausted,
    JevTransportError,
    JevValidationError,
)
from dream_md.judgment.fake import FakeJev
from dream_md.judgment.questions import (
    CATEGORY_OPTIONS,
    SIGNIFICANCE_LEVELS,
    VERDICT_OPTIONS,
    Question,
    candidate_entry,
    phase_a_questions,
    phase_a_state,
    phase_a_triple,
    phase_b_pair_questions,
    phase_b_questions,
    phase_b_state,
)
from dream_md.judgment.tokens import estimate_tokens

__all__ = [
    "Batch",
    "BatchError",
    "CATEGORY_OPTIONS",
    "CHOICE",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "FakeJev",
    "JevAuthError",
    "JevClient",
    "JevError",
    "JevProtocolError",
    "JevResponse",
    "JevRetryExhausted",
    "JevTransportError",
    "JevValidationError",
    "NOUL",
    "NoulAnswer",
    "Question",
    "SCORE",
    "SIGNIFICANCE_LEVELS",
    "ScoreAnswer",
    "Usage",
    "VERDICT_OPTIONS",
    "candidate_entry",
    "estimate_tokens",
    "parse_answer",
    "phase_a_questions",
    "phase_a_state",
    "phase_a_triple",
    "phase_b_pair_questions",
    "phase_b_questions",
    "phase_b_state",
    "plan_phase_a",
]
