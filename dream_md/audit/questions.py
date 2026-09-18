"""Phase C — audit questions over an external memory file (PLAN M4).

State is the parsed memory lines plus the evidence pool (recent active
facts + recent statements, redacted at rest). Per line ``i`` (position
in the request's ``memory_lines`` array):

- ``l{i}_supported``    (Noul) — does the evidence back the line?
- ``l{i}_contradicted`` (Noul) — does the evidence contradict the line?
- ``l{i}_disposition``  (Choice: keep | stale | wrong | unsupported)

Same conventions as Phase A/B (module ``dream_md.judgment.questions``):
question ids are code-only keys never shown to the model; instructions
are complete standalone questions pointing at ``state`` paths in
backticks; all three are always asked (speculative fan-out) and code
filters — the disposition is decisive only above
``AUDIT_DISPOSITION_GATE``, otherwise the line lands in the review band.

Receipt ids: judgments rows use ``subject_kind='line'``,
``subject_id='line:<file line number>'``, and the STABLE semantic
question keys (``supported`` / ``contradicted`` / ``disposition``) —
position-based ``l{i}_`` ids are request-local and would be ambiguous
across batches.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dream_md.audit.memfile import MemoryLine
from dream_md.judgment.questions import Question

# --- vocabulary (pinned by PLAN; tests assert exactly) ------------------------

DISPOSITION_OPTIONS = ("keep", "stale", "wrong", "unsupported")


def line_entry(line: MemoryLine) -> dict[str, Any]:
    """The state entry for one memory line (shared by state builder and
    the batch estimator so both serialize the exact same object)."""
    return {
        "id": line.id,
        "number": line.number,
        "section": line.section,
        "text": line.text,
    }


def audit_state(
    project_context: Mapping[str, Any] | None,
    lines: Sequence[MemoryLine],
    facts: Sequence[Mapping[str, Any]],
    statements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Phase C state: memory lines + the evidence pool they are judged
    against.

    ``facts``/``statements`` are fact/statement-shaped mappings (at
    least ``text``; typically ``id`` too) — assembled redacted from the
    store by the engine.
    """
    return {
        "project_context": dict(project_context or {}),
        "memory_lines": [line_entry(line) for line in lines],
        "evidence": {
            "facts": [dict(fact) for fact in facts],
            "statements": [dict(statement) for statement in statements],
        },
    }


def phase_c_questions(count: int) -> dict[str, Question]:
    """The three speculative questions per memory-line position."""
    if count < 0:
        raise ValueError("count must be >= 0")
    questions: dict[str, Question] = {}
    for i in range(count):
        questions.update(phase_c_triple(i))
    return questions


def phase_c_triple(i: int) -> dict[str, Question]:
    """The three Phase C questions for line position ``i`` alone."""
    return {
        f"l{i}_supported": Question(
            id=f"l{i}_supported",
            type="noul",
            instructions=(
                f"Looking at the `evidence` in this state — the recorded "
                "facts and recent statements about this project — is "
                f"`memory_lines[{i}].text` still a true description of how "
                "this project works?"
            ),
            criteria={
                "true": "The evidence is consistent with the line: nothing "
                "observed goes against it.",
                "false": "The evidence does not back the line: nothing "
                "observed supports it (or the line is about something the "
                "project has never done).",
            },
        ),
        f"l{i}_contradicted": Question(
            id=f"l{i}_contradicted",
            type="noul",
            instructions=(
                f"Does the `evidence` in this state contradict "
                f"`memory_lines[{i}].text` — is there a recorded fact or "
                "recent statement that says this project works differently "
                "than the line claims?"
            ),
            criteria={
                "true": "The evidence conflicts with the line: following "
                "the line would go against something recorded.",
                "false": "No conflict: nothing in the evidence says the "
                "project works differently.",
            },
        ),
        f"l{i}_disposition": Question(
            id=f"l{i}_disposition",
            type="choice",
            instructions=(
                f"Given the `evidence` in this state, what should happen "
                f"to `memory_lines[{i}].text` in this project's memory "
                "file?"
            ),
            criteria={
                "keep": "The line is backed by evidence and still "
                "describes this project.",
                "stale": "The line used to be true but the evidence shows "
                "the project has moved on.",
                "wrong": "The evidence contradicts the line: following it "
                "would be a mistake.",
                "unsupported": "Nothing in the evidence either backs or "
                "contradicts the line — it cannot be verified.",
            },
        ),
    }
