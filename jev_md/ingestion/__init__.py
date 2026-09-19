"""Ingestion sub-domain (docs/DOMAIN.md): session transcript parsing
(Claude Code, Codex), the immutable Event log, and deterministic
Statement/Candidate extraction.

Parsers are built from real transcripts observed on this machine
(2026-09-19); observed schemas are recorded in ``.ddd/notes/jev-md.md``.
Statements are raw/verbatim from parsers; redaction happens at the
storage boundary (``EventLog.append``, DOMAIN #6). DDL lives in
``jev_md.memory.schema`` (review R7), never here.
"""

from jev_md.ingestion.claude import parse_claude_transcript
from jev_md.ingestion.codex import parse_codex_transcript
from jev_md.ingestion.eventlog import (
    AppendResult,
    EventLog,
    event_id,
    normalize_content,
    project_slug,
    store_path,
)
from jev_md.ingestion.extract import (
    MAX_CANDIDATE_CHARS,
    MIN_CANDIDATE_CHARS,
    Candidate,
    candidates_from_statements,
    chunk_text,
    extract_candidates,
)
from jev_md.ingestion.models import (
    ParsedTranscript,
    Statement,
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    SOURCE_CLAUDE,
    SOURCE_CODEX,
    SOURCE_MANUAL,
)
from jev_md.ingestion.redact import (
    REDACTED_PREFIX,
    redact,
    redactions_in,
)
from jev_md.thresholds import CONTEXT_MAX_CHARS

__all__ = [
    "AppendResult",
    "Candidate",
    "CONTEXT_MAX_CHARS",
    "EventLog",
    "MAX_CANDIDATE_CHARS",
    "MIN_CANDIDATE_CHARS",
    "ParsedTranscript",
    "REDACTED_PREFIX",
    "Statement",
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_USER",
    "SOURCE_CLAUDE",
    "SOURCE_CODEX",
    "SOURCE_MANUAL",
    "candidates_from_statements",
    "chunk_text",
    "event_id",
    "extract_candidates",
    "normalize_content",
    "parse_claude_transcript",
    "parse_codex_transcript",
    "project_slug",
    "redact",
    "redactions_in",
    "store_path",
]
