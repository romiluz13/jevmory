"""Deterministic candidate extraction (PLAN milestone M1).

Turns parsed Statements into Candidates: verbatim text, chunked to at
most ``MAX_CANDIDATE_CHARS`` (600), deduplicated by content hash
(sha256 of the chunk text) across the whole batch. Jev grades later;
this module only selects, never rewrites (invariant #1).

Chunking: chunks are exact substrings of the source statement. When a
statement exceeds the limit we break at the last whitespace inside the
window when one exists, else hard-cut at the limit; outer whitespace of
each chunk is trimmed so no chunk starts/ends mid-pad. Nothing is ever
added, elided, or paraphrased.

Statements shorter than ``MIN_CANDIDATE_CHARS`` (observed real prompts
like "keep going", 10 chars) are dropped: they cannot carry a durable
fact and only burn grading budget. Both bounds are named, documented,
tunable constants (PLAN's threshold policy).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from dream_md.ingestion.eventlog import event_id
from dream_md.ingestion.models import (
    ParsedTranscript,
    ROLE_ASSISTANT,
    ROLE_USER,
)

MAX_CANDIDATE_CHARS = 600
MIN_CANDIDATE_CHARS = 12


@dataclass(frozen=True)
class Candidate:
    """A Statement chunk that will be graded by Jev (verbatim)."""

    event_id: str  # provenance: source event of the first occurrence
    role: str  # user | assistant
    text: str  # verbatim, <= MAX_CANDIDATE_CHARS
    ts: str | None  # session timestamp from the transcript
    session_id: str | None
    content_hash: str  # sha256 hex of the text; dedupe key


def chunk_text(text: str, limit: int = MAX_CANDIDATE_CHARS) -> list[str]:
    """Split text into verbatim chunks of at most ``limit`` characters."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        break_at = max(window.rfind(" "), window.rfind("\n"))
        if break_at <= 0:  # no whitespace in the window: hard cut
            break_at = limit
        chunks.append(rest[:break_at].strip())
        rest = rest[break_at:].lstrip()
    tail = rest.strip()
    if tail:
        chunks.append(tail)
    return [chunk for chunk in chunks if chunk]


def extract_candidates(*transcripts: ParsedTranscript) -> list[Candidate]:
    """Deterministic candidates from user + assistant statements.

    Dedupe is by content hash across all given transcripts; the first
    occurrence (in argument and file order) wins and keeps its event id.
    """
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for parsed in transcripts:
        for statement in parsed.statements:
            if statement.role not in (ROLE_USER, ROLE_ASSISTANT):
                continue
            for chunk in chunk_text(statement.text):
                if len(chunk) < MIN_CANDIDATE_CHARS:
                    continue
                content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                if content_hash in seen:
                    continue
                seen.add(content_hash)
                candidates.append(
                    Candidate(
                        event_id=event_id(
                            parsed.path, statement.line_no, statement.index
                        ),
                        role=statement.role,
                        text=chunk,
                        ts=statement.ts,
                        session_id=statement.session_id,
                        content_hash=content_hash,
                    )
                )
    return candidates
