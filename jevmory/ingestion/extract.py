"""Deterministic candidate extraction (PLAN v2 ingestion spec).

Turns parsed Statements into Candidates:

- **Sentence-boundary chunking (280-600 chars).** A chunk always ends at
  a sentence end; a sentence is never split to reach the 280 aim (tail
  chunks may be shorter). One sentence longer than the 600 cap is
  hard-split at whitespace — the cap is hard because candidate quotes
  that leave the machine are <=600 chars (privacy statement).
- **Anaphoric-start drop.** Chunks whose first word is
  that/this/it/they/"the same"/those are dropped: without the referent
  they cannot carry a standalone claim. Applied to ALL chunks including
  a statement's first — deliberate, simple, deterministic (Phase A
  would grade them ephemeral anyway; dropping saves grading budget).
  Pinned by test; if the lead wants first-chunk exemption, it is a
  one-line change.
- **Verbatim context per candidate**: the 1-2 preceding exchange turns
  of the SAME session, rendered ``[role] text`` joined by blank lines,
  redacted (events are redacted at rest and ``redact`` is idempotent),
  truncated to 800 chars at a whitespace boundary.
- **No content dedupe (v2 change, review R2).** Every occurrence becomes
  a candidate with its own source event id — cross-session repeats must
  survive so memory-level dedupe (M3: hash + Jaccard) can bump
  support_count instead of swallowing the repeat. (Grouping identical
  candidate texts before calling Jev is M5's optimization, not
  extraction's.)

Chunks are exact substrings of the statement's REDACTED text (the stored
form; redaction is deterministic so this stays verbatim-tracking).
Joining all chunks of a statement with a single space reproduces its
whitespace-normalized text. Nothing is ever added, elided, or
paraphrased (invariant #1). Sub-12-char chunks are noise and dropped
(observed real prompts: "ok", "keep going").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jevmory.ingestion.eventlog import event_id
from jevmory.ingestion.models import (
    ParsedTranscript,
    Statement,
    ROLE_ASSISTANT,
    ROLE_USER,
)
from jevmory.ingestion.redact import redact
from jevmory.thresholds import (
    CHUNK_MAX_CHARS,
    CHUNK_MIN_CHARS,
    CONTEXT_MAX_CHARS,
    MIN_CANDIDATE_CHARS,
)

# Back-compat aliases (the constants live in thresholds.py now).
MAX_CANDIDATE_CHARS = CHUNK_MAX_CHARS
MIN_CANDIDATE_CHARS = MIN_CANDIDATE_CHARS

# A sentence boundary: a whitespace run whose last preceding non-space
# character closed a sentence (. ! ? and common closers ) ] " ').
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?'\"\)\]])\s+")

# Chunks starting with an anaphoric token cannot stand alone.
_ANAPHORIC_START_RE = re.compile(r"^(?:that|this|it|they|the\s+same|those)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    """A Statement chunk that will be graded by Jev (verbatim, redacted)."""

    event_id: str  # provenance: source event id of THIS occurrence
    role: str  # user | assistant
    text: str  # verbatim chunk, <= CHUNK_MAX_CHARS
    context: str  # 1-2 preceding turns, redacted, <= CONTEXT_MAX_CHARS
    ts: str | None  # statement timestamp from the transcript
    session_id: str | None


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split text into verbatim sentence-boundary chunks of at most ``max_chars``.

    Greedy packing: consecutive sentences accumulate while the chunk fits
    the cap (so chunks aim for the 280 target whenever material allows).
    Chunks are exact substrings of ``text.strip()``.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    text = text.strip()
    if not text:
        return []

    pieces: list[tuple[int, int]] = []  # (start, end) spans of hard pieces
    for start, end in _sentence_spans(text):
        if end - start <= max_chars:
            pieces.append((start, end))
        else:  # one sentence longer than the cap: hard-split at whitespace
            pieces.extend(_hard_split_span(text, start, end, max_chars))

    chunks: list[str] = []
    cur_start: int | None = None
    cur_end = 0
    for start, end in pieces:
        if cur_start is None:
            cur_start, cur_end = start, end
        elif end - cur_start <= max_chars:  # extend: keeps interior whitespace
            cur_end = end
        else:
            chunks.append(text[cur_start:cur_end])
            cur_start, cur_end = start, end
    if cur_start is not None:
        chunks.append(text[cur_start:cur_end])
    return [chunk for chunk in (c.strip() for c in chunks) if chunk]


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of sentences; boundary whitespace is not in any span."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        if match.start() > start:
            spans.append((start, match.start()))
        start = match.end()
    if len(text) > start:
        spans.append((start, len(text)))
    return spans


def _hard_split_span(
    text: str, start: int, end: int, max_chars: int
) -> list[tuple[int, int]]:
    """Whitespace-boundary splits inside text[start:end], hard cut fallback."""
    spans: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > max_chars:
        window = text[cursor : cursor + max_chars]
        cut = max(window.rfind(" "), window.rfind("\n"))
        if cut <= 0:  # no whitespace in the window: hard cut
            cut = max_chars
        spans.append((cursor, cursor + cut))
        cursor += cut
        while cursor < end and text[cursor].isspace():
            cursor += 1
    if end > cursor:
        spans.append((cursor, end))
    return spans


def _context_for(preceding: list[Statement]) -> str:
    """Verbatim context from the 1-2 preceding exchange turns (redacted)."""
    turns = preceding[-2:]
    if not turns:
        return ""
    context = "\n\n".join(f"[{turn.role}] {turn.text}" for turn in turns)
    context = redact(context)
    return _truncate_context(context)


def _truncate_context(context: str) -> str:
    if len(context) <= CONTEXT_MAX_CHARS:
        return context
    window = context[:CONTEXT_MAX_CHARS]
    cut = max(window.rfind(" "), window.rfind("\n"))
    if cut <= 0:  # no whitespace at all: hard cut
        cut = CONTEXT_MAX_CHARS
    return window[:cut].rstrip()


def _candidates(
    statements: tuple[Statement, ...] | list[Statement],
    default_session_id: str | None = None,
) -> list[Candidate]:
    """Candidates from an ordered statement list, grouped by session.

    Context never crosses a session boundary; within a session it is
    the 1-2 preceding user/assistant turns in list order.
    """
    groups: dict[str, list[Statement]] = {}
    order: list[str] = []
    for statement in statements:
        sid = (
            statement.session_id
            if statement.session_id is not None
            else default_session_id
        )
        key = sid or ""
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(statement)

    candidates: list[Candidate] = []
    for key in order:
        group = groups[key]
        session_id = key or None
        for position, statement in enumerate(group):
            if statement.role not in (ROLE_USER, ROLE_ASSISTANT):
                continue
            preceding = [
                other
                for other in group[:position]
                if other.role in (ROLE_USER, ROLE_ASSISTANT)
            ]
            context = _context_for(preceding)
            safe_text = redact(statement.text)
            for chunk in chunk_text(safe_text):
                if len(chunk) < MIN_CANDIDATE_CHARS:
                    continue
                if _ANAPHORIC_START_RE.match(chunk):
                    continue
                candidates.append(
                    Candidate(
                        event_id=event_id(safe_text, session_id),
                        role=statement.role,
                        text=chunk,
                        context=context,
                        ts=statement.ts,
                        session_id=session_id,
                    )
                )
    return candidates


def extract_candidates(*transcripts: ParsedTranscript) -> list[Candidate]:
    """Deterministic candidates from user + assistant statements (no dedupe)."""
    candidates: list[Candidate] = []
    for parsed in transcripts:
        candidates.extend(
            _candidates(parsed.statements, parsed.session_id)
        )
    return candidates


def candidates_from_statements(
    statements: tuple[Statement, ...] | list[Statement],
) -> list[Candidate]:
    """Dream-time variant: candidates from stored (already redacted) event rows.

    Accepts any Statement-shaped sequence; groups by session_id so
    context is built per session even across a multi-session event list.
    """
    return _candidates(statements)
