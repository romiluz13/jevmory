"""Parser for Claude Code session transcripts.

Built from REAL files observed on 2026-09-19 under
``~/.claude/projects/<project-slug>/<sessionId>.jsonl`` on this machine
(15 files: 372 ``user``, 526 ``assistant``, 18 ``system`` lines). Field
shapes relied on (recorded in ``.ddd/notes/jev-md.md``):

- One JSON object per line. Lines with ``type`` == "user" or "assistant"
  are message lines; all other observed line types (``mode``,
  ``attachment``, ``last-prompt``, ``permission-mode``, ``atis-latch``,
  ``file-history-snapshot``, ``file-history-delta``, ``ai-title``,
  ``queue-operation``, ``cost-state``, ``system``) are skipped.
- Common message-line fields: ``uuid``, ``parentUuid``, ``sessionId``,
  ``timestamp`` (ISO-8601 UTC, e.g. "2026-09-01T19:40:44.329Z"), ``cwd``,
  ``gitBranch``, ``version``, ``isSidechain`` (assistant lines also carry
  a duplicate ``session_id`` key). Sidechain transcripts
  (``agent-*.jsonl``) carry the PARENT session id in ``sessionId``; the
  field is authoritative, the file name is not.
- ``user`` lines: ``message`` == {"role": "user", "content": ...}.
  ``content`` is either a plain string (a typed prompt or an injected
  notification) or a list of blocks; in the observed corpus user blocks
  were exclusively ``tool_result`` (tool output, never a statement).
  Genuine human prompts carry ``promptSource`` == "typed" with
  ``origin`` == {"kind": "human"}; machine injections carry
  ``promptSource`` == "system" with ``origin`` ==
  {"kind": "task-notification"}, or ``isMeta`` == true. Text blocks
  inside user lists were never observed here but are tolerated.
- ``assistant`` lines: ``message`` == {"role": "assistant", "content":
  [blocks], ...}; observed blocks: {"type": "text", "text"},
  {"type": "thinking", ...}, {"type": "tool_use", ...}. Only ``text``
  blocks are statements (thinking and tool use are not quotes).
- Malformed JSON lines are recorded in ``skipped_lines`` and never
  raise (invariant #4: ingestion must not crash a session).
"""

from __future__ import annotations

import json
import os

from jevmory.ingestion.models import (
    ParsedTranscript,
    Statement,
    SOURCE_CLAUDE,
)


def parse_claude_transcript(path: str | os.PathLike[str]) -> ParsedTranscript:
    """Parse one Claude Code ``.jsonl`` transcript into statements."""
    path = os.fspath(path)
    statements: list[Statement] = []
    skipped: list[int] = []
    session_id: str | None = None
    cwd: str | None = None

    with open(path, encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                skipped.append(line_no)
                continue
            if not isinstance(record, dict):
                skipped.append(line_no)
                continue

            line_sid = record.get("sessionId")
            if session_id is None and isinstance(line_sid, str):
                session_id = line_sid
            if cwd is None and isinstance(record.get("cwd"), str):
                cwd = record["cwd"]

            record_type = record.get("type")
            if record_type not in ("user", "assistant"):
                continue

            ts = record.get("timestamp")
            ts = ts if isinstance(ts, str) else None
            sid = line_sid if isinstance(line_sid, str) else None

            message = record.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            role = role if isinstance(role, str) else record_type

            for index, text in enumerate(_statement_texts(record, message)):
                statements.append(
                    Statement(
                        line_no=line_no,
                        index=index,
                        ts=ts,
                        role=role,
                        text=text,
                        session_id=sid,
                    )
                )

    return ParsedTranscript(
        path=path,
        source=SOURCE_CLAUDE,
        session_id=session_id,
        cwd=cwd,
        statements=tuple(statements),
        skipped_lines=tuple(skipped),
    )


def _statement_texts(record: dict, message: dict) -> list[str]:
    """Extract verbatim statement texts from one Claude message line.

    User lines keep string/text-block content unless it is machine
    injected (``isMeta``, ``promptSource`` "system", or a
    task-notification origin, all observed in real transcripts).
    Assistant lines keep only ``text`` blocks.
    """
    content = message.get("content")
    if record.get("type") == "user":
        if record.get("isMeta") is True:
            return []
        if record.get("promptSource") == "system":
            return []
        origin = record.get("origin")
        if isinstance(origin, dict) and origin.get("kind") == "task-notification":
            return []
        if isinstance(content, str):
            return _clean([content])
        if isinstance(content, list):
            return _clean(
                block.get("text")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return []
    # assistant
    if isinstance(content, str):  # not observed, tolerated
        return _clean([content])
    if isinstance(content, list):
        return _clean(
            block.get("text")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return []


def _clean(texts) -> list[str]:
    out: list[str] = []
    for text in texts:
        if isinstance(text, str):
            text = text.strip()
            if text:
                out.append(text)
    return out
