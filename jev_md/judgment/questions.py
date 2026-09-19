"""Fan-out question set + state builders (PLAN "Jev question set").

Question ids are for OUR code only — the model never sees them (API
reference); instructions are complete standalone questions that point
at parts of the structured state with backticked dot-and-index paths
(e.g. ``Does `candidates[3].text` ...``).

Speculative fan-out: all three Phase A questions are always asked and
code filters the answers; all three pair questions are always asked and
the verdict answer is ignored unless ``contradicts >= 0.6``. No cost to
asking, no conditional branching in batch shapes (API reference,
constraint 3).

Phase A grades candidates; Phase B dedupes/conflicts candidates against
remembered facts. Phase C (audit) belongs to M4 with the memory-file
line parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from jev_md.ingestion.extract import Candidate

# --- vocabularies (pinned by PLAN; tests assert these exactly) ---------------

CATEGORY_OPTIONS = (
    "preference",
    "tooling",
    "architecture",
    "pitfall",
    "convention",
    "none",
)
SIGNIFICANCE_LEVELS = ("trivial", "useful", "important", "critical")
VERDICT_OPTIONS = ("new_overrides", "old_stands", "unclear")


@dataclass(frozen=True)
class Question:
    """One typed judgment question (wire form via ``to_wire``)."""

    id: str
    type: str  # "noul" | "choice" | "score"
    instructions: str
    criteria: Any  # dict for noul/choice, sequence of levels for score

    def to_wire(self) -> dict[str, Any]:
        """The question object sent to the API (id stays code-side)."""
        return {
            "type": self.type,
            "instructions": self.instructions,
            "criteria": self.criteria,
        }


# --- Phase A: grade candidates -----------------------------------------------


def phase_a_state(
    project_context: Mapping[str, Any] | None, candidates: Sequence[Candidate]
) -> dict[str, Any]:
    """Structured state: project context plus the candidate quotes to grade.

    Candidate ``id`` is the source event id (provenance back into the
    events table); ``context`` is the verbatim 1-2 preceding turns
    (redacted at ingest).
    """
    return {
        "project_context": dict(project_context or {}),
        "candidates": [candidate_entry(candidate) for candidate in candidates],
    }


def phase_a_questions(count: int) -> dict[str, Question]:
    """The three speculative questions per candidate position.

    ``count`` is the number of candidates in this request's state;
    positions are 0-based within the batch (ids and state paths use the
    same index, so answers map back to candidates by position).
    """
    if count < 0:
        raise ValueError("count must be >= 0")
    questions: dict[str, Question] = {}
    for i in range(count):
        questions.update(phase_a_triple(i))
    return questions


def phase_a_triple(i: int) -> dict[str, Question]:
    """The three Phase A questions for candidate position ``i`` alone."""
    return {
        f"c{i}_durable": Question(
            id=f"c{i}_durable",
            type="noul",
            instructions=(
                f"Does `candidates[{i}].text` state a durable fact about "
                "working in this project — a preference, tooling rule, "
                "convention, architectural decision, or pitfall that will "
                "still be true in a later session — rather than ephemeral "
                "chatter about this one session (a task in progress, a "
                "transient error, a greeting)? Use the optional "
                f"`candidates[{i}].context` only to resolve what the quoted "
                "text refers to, not as extra evidence."
            ),
            criteria={
                "true": "A fact worth remembering next week: it holds "
                "across sessions.",
                "false": "Ephemeral: it only mattered inside this session.",
            },
        ),
        f"c{i}_category": Question(
            id=f"c{i}_category",
            type="choice",
            instructions=(
                f"Which category best classifies the claim in "
                f"`candidates[{i}].text`?"
            ),
            criteria={
                "preference": "A stated taste or choice: how the author "
                "wants things done.",
                "tooling": "Which tool, command, or service to use, and how.",
                "architecture": "How the system is structured or why.",
                "pitfall": "A warning about something that breaks or bites.",
                "convention": "A rule the project follows: style, process, "
                "naming.",
                "none": "None of these categories fit the claim.",
            },
        ),
        f"c{i}_significance": Question(
            id=f"c{i}_significance",
            type="score",
            instructions=(
                f"How significant is the claim in `candidates[{i}].text` "
                "for working effectively in this project?"
            ),
            criteria=[
                "trivial — nice to know, changes nothing if forgotten",
                "useful — saves time or avoids small mistakes",
                "important — getting this wrong wastes real effort",
                "critical — getting this wrong breaks builds, data, or trust",
            ],
        ),
    }


def candidate_entry(candidate: Candidate) -> dict[str, Any]:
    """The state entry for one candidate (shared by state builders and
    the batch estimator so both serialize the exact same object)."""
    return {
        "id": candidate.event_id,
        "role": candidate.role,
        "text": candidate.text,
        "context": candidate.context,
    }


# --- Phase B: dedupe & conflict (candidate vs remembered fact) ----------------


def phase_b_state(
    project_context: Mapping[str, Any] | None,
    candidates: Sequence[Candidate],
    facts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """State for pair questions: the candidates and the facts they meet.

    ``facts`` are fact-shaped mappings (at least ``text``; typically
    ``id`` too). Phase B runs only on survivors, after the code-level
    dedupe pre-pass (M3).
    """
    return {
        "project_context": dict(project_context or {}),
        "candidates": [candidate_entry(candidate) for candidate in candidates],
        "facts": [dict(fact) for fact in facts],
    }


def phase_b_pair_questions(i: int, j: int) -> dict[str, Question]:
    """The three speculative pair questions for candidate ``i`` vs fact ``j``.

    ``i``/``j`` are positions in the request's ``candidates``/``facts``
    arrays. The verdict answer is ignored by code unless
    ``contradicts >= 0.6`` (CONTRADICTION_GATE).
    """
    return {
        f"p{i}_{j}_same_claim": Question(
            id=f"p{i}_{j}_same_claim",
            type="noul",
            instructions=(
                f"Do `candidates[{i}].text` and `facts[{j}].text` make the "
                "same claim (equivalent meaning, possibly different "
                "wording)?"
            ),
            criteria={
                "true": "The two statements assert the same thing about "
                "this project.",
                "false": "They differ in substance or are about different "
                "things.",
            },
        ),
        f"p{i}_{j}_contradicts": Question(
            id=f"p{i}_{j}_contradicts",
            type="noul",
            instructions=(
                f"Does `candidates[{i}].text` contradict `facts[{j}].text` "
                "for this project — can both be followed at once, or does "
                "the new statement conflict with the remembered fact?"
            ),
            criteria={
                "true": "They conflict: following the new statement means "
                "violating the remembered fact.",
                "false": "No conflict: both can hold at the same time (or "
                "they are unrelated).",
            },
        ),
        f"p{i}_{j}_verdict": Question(
            id=f"p{i}_{j}_verdict",
            type="choice",
            instructions=(
                f"If `candidates[{i}].text` and `facts[{j}].text` conflict, "
                "which should a developer working in this project follow?"
            ),
            criteria={
                "new_overrides": "The new statement is the current truth; "
                "the remembered fact is outdated.",
                "old_stands": "The remembered fact still holds; the new "
                "statement is the exception or noise.",
                "unclear": "Cannot tell which one applies.",
            },
        ),
    }


def phase_b_questions(
    pairs: Sequence[tuple[int, int]],
) -> dict[str, Question]:
    """All pair questions for ``(candidate_position, fact_position)`` pairs."""
    questions: dict[str, Question] = {}
    for i, j in pairs:
        questions.update(phase_b_pair_questions(i, j))
    return questions
