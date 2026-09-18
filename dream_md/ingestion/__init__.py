"""Ingestion sub-domain (docs/DOMAIN.md): session transcript parsing
(Claude Code, Codex), the immutable Event log, and deterministic
Statement/Candidate extraction.

Parsers are built from real transcripts observed on this machine
(2026-09-19); observed schemas are recorded in ``.ddd/notes/dream-md.md``.
"""

from dream_md.ingestion.claude import parse_claude_transcript
from dream_md.ingestion.codex import parse_codex_transcript
from dream_md.ingestion.eventlog import (
    AppendResult,
    EventLog,
    event_id,
    project_slug,
    store_path,
)
from dream_md.ingestion.extract import (
    MAX_CANDIDATE_CHARS,
    MIN_CANDIDATE_CHARS,
    Candidate,
    chunk_text,
    extract_candidates,
)
from dream_md.ingestion.models import (
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

__all__ = [
    "AppendResult",
    "Candidate",
    "EventLog",
    "MAX_CANDIDATE_CHARS",
    "MIN_CANDIDATE_CHARS",
    "ParsedTranscript",
    "Statement",
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_USER",
    "SOURCE_CLAUDE",
    "SOURCE_CODEX",
    "SOURCE_MANUAL",
    "chunk_text",
    "event_id",
    "extract_candidates",
    "parse_claude_transcript",
    "parse_codex_transcript",
    "project_slug",
    "store_path",
]
