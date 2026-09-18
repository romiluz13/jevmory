"""Phase C disposition rules — code, not Jev (PLAN "Decision rules").

The choice answer carries the disposition (keep | stale | wrong |
unsupported) and a calibrated confidence. One gate, applied by code:

- ``confidence >= AUDIT_DISPOSITION_GATE`` -> the disposition is
  DECISIVE and reported as a verdict keyword.
- below the gate -> the line lands in the REVIEW band — surfaced in the
  report and counted, never silent (the same stance as Phase A
  near-misses: an uncertain answer is information, not noise).

The two Noul answers (supported / contradicted) are RECEIPTS, not
gates: they are reported verbatim next to the disposition so a human
can check the reasoning behind every keyword. Choice confidence is
never blended into anything (DOMAIN "Confidence").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from dream_md.audit.memfile import MemoryLine
from dream_md.audit.questions import DISPOSITION_OPTIONS
from dream_md.judgment.answers import ChoiceAnswer, NoulAnswer
from dream_md.thresholds import AUDIT_DISPOSITION_GATE

# Report keyword per disposition (terminal report verdict column).
DISPOSITION_KEYWORDS = {
    "keep": "KEEP",
    "stale": "STALE",
    "wrong": "WRONG",
    "unsupported": "UNSUPPORTED",
}
REVIEW = "REVIEW"

# Stable receipt question keys (judgments.question_id for subject_kind
# 'line' — see audit/questions.py docstring for why not l{i}_ ids).
Q_SUPPORTED = "supported"
Q_CONTRADICTED = "contradicted"
Q_DISPOSITION = "disposition"


@dataclass(frozen=True)
class LineVerdict:
    """The audited outcome of one memory line, with its receipts."""

    line: MemoryLine
    disposition: str  # DISPOSITION_OPTIONS value, or REVIEW band marker
    decisive: bool  # False -> review band (confidence under the gate)
    confidence: float  # the disposition choice answer's confidence
    supported: float  # verbatim Noul receipt
    contradicted: float  # verbatim Noul receipt

    @property
    def keyword(self) -> str:
        """The verdict keyword for the terminal report."""
        if not self.decisive:
            return REVIEW
        return DISPOSITION_KEYWORDS[self.disposition]


def line_verdict(
    line: MemoryLine,
    answers: Mapping[str, object],
    *,
    gate: float = AUDIT_DISPOSITION_GATE,
) -> LineVerdict:
    """Apply the disposition rule to one line's parsed answers.

    ``answers`` maps the STABLE receipt keys (``supported`` /
    ``contradicted`` / ``disposition``) to parsed answer objects, as
    produced by the engine from a ``l{i}_``-indexed response.
    """
    supported = answers[Q_SUPPORTED]
    contradicted = answers[Q_CONTRADICTED]
    disposition = answers[Q_DISPOSITION]
    if not isinstance(supported, NoulAnswer) or not isinstance(
        contradicted, NoulAnswer
    ):
        raise TypeError("supported/contradicted answers must be NoulAnswer")
    if not isinstance(disposition, ChoiceAnswer):
        raise TypeError("disposition answer must be ChoiceAnswer")
    if disposition.choice not in DISPOSITION_OPTIONS:
        # parse_answer already confines choices to the question's
        # options; this guards a hand-built answer in tests/callers.
        raise ValueError(
            f"disposition {disposition.choice!r} not in "
            f"{DISPOSITION_OPTIONS}"
        )
    decisive = disposition.confidence >= gate
    return LineVerdict(
        line=line,
        disposition=disposition.choice,
        decisive=decisive,
        confidence=disposition.confidence,
        supported=supported.noul,
        contradicted=contradicted.noul,
    )


def count_dispositions(verdicts: Sequence[LineVerdict]) -> dict[str, int]:
    """Counts per verdict keyword (keep/stale/wrong/unsupported/review)."""
    counts = {
        keyword: 0 for keyword in (*DISPOSITION_KEYWORDS.values(), REVIEW)
    }
    for verdict in verdicts:
        counts[verdict.keyword] += 1
    return counts
