"""Neutral data structures for the ingestion sub-domain.

The parsers (``claude.py``, ``codex.py``) own every format-specific
decision; this module only defines the format-independent records they
both produce, plus the role/source vocabularies of the events table
(docs/PLAN.md, schema v2). Statements are RAW here (verbatim, unredacted
by design — parsers never rewrite); redaction happens at the storage
boundary in ``eventlog.EventLog.append`` (DOMAIN #6).
"""

from __future__ import annotations

from dataclasses import dataclass

# Roles stored in the events table. Parsers currently emit only
# ROLE_USER and ROLE_ASSISTANT (observed machine/system lines are never
# statements); the full vocabulary is kept for schema compatibility.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"
ROLE_TOOL = "tool"

SOURCE_CLAUDE = "claude"
SOURCE_CODEX = "codex"
SOURCE_MANUAL = "manual"


@dataclass(frozen=True)
class Statement:
    """One verbatim text quote extracted from a single transcript line.

    A line may yield several Statements (e.g. a Claude assistant message
    with multiple text blocks); ``index`` distinguishes them within the
    line. Never rewritten. Event ids are content-based (sha256 of
    normalized content + session_id, review R2) — ``index`` and
    ``line_no`` are provenance metadata, not id inputs.
    """

    line_no: int  # 1-based line number in the transcript file
    index: int  # 0-based statement ordinal within the line
    ts: str | None  # transcript timestamp (ISO-8601), may be missing
    role: str  # user | assistant
    text: str  # verbatim statement text
    session_id: str | None


@dataclass(frozen=True)
class ParsedTranscript:
    """Result of parsing one transcript file."""

    path: str
    source: str  # 'claude' | 'codex'
    session_id: str | None
    cwd: str | None
    statements: tuple[Statement, ...]
    skipped_lines: tuple[int, ...] = ()  # 1-based lines with malformed JSON
