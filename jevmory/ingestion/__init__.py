"""Ingestion sub-domain (docs/DOMAIN.md): session transcript parsing
(Claude Code, Codex), the immutable Event log, and deterministic
Statement/Candidate extraction.

Parsers are built from real transcripts observed on this machine
(2026-09-19); observed schemas are recorded in ``.ddd/notes/jevmory.md``.
Statements are raw/verbatim from parsers; redaction happens at the
storage boundary (``EventLog.append``, DOMAIN #6). DDL lives in
``jevmory.memory.schema`` (review R7), never here.
"""

from jevmory.ingestion.claude import parse_claude_transcript
from jevmory.ingestion.codex import parse_codex_transcript
from jevmory.ingestion.eventlog import (
    AppendResult,
    EventLog,
    event_id,
    normalize_content,
    project_slug,
    store_path,
)
from jevmory.ingestion.extract import (
    MAX_CANDIDATE_CHARS,
    MIN_CANDIDATE_CHARS,
    Candidate,
    candidates_from_statements,
    chunk_text,
    extract_candidates,
)
from jevmory.ingestion.models import (
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
from jevmory.ingestion.redact import (
    REDACTED_PREFIX,
    redact,
    redactions_in,
)
from jevmory.thresholds import CONTEXT_MAX_CHARS

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
