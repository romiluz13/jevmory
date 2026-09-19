"""Parser for Codex session transcripts.

Built from REAL files observed on 2026-09-19 under
``~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`` on this
machine (1,077 files). Field shapes relied on (recorded in
``.ddd/notes/jevmory.md``):

- One JSON object per line: {"timestamp": ISO-8601 UTC, "ordinal": int,
  "type": "session_meta" | "response_item" | "event_msg" |
  "turn_context" | "compacted", "payload": {...}}.
- Line 1 is always ``session_meta`` (present in all 1,077 observed
  files; a few files repeat it after compaction with the same id). Its
  payload carries ``session_id``, ``id`` (== session_id), ``timestamp``,
  ``cwd``, ``originator``, ``cli_version``, ``source``, and
  ``model_provider``. The filename uuid matches ``session_id`` in only
  620/1,077 files (subagent threads differ), so the id always comes
  from the payload, never the file name.
- ``response_item`` with payload.type == "message": {"role": "user" |
  "assistant" | "developer", "content": [blocks]}. Observed blocks:
  {"type": "input_text", "text"} (user, developer),
  {"type": "output_text", "text"} (assistant), and
  {"type": "input_image", ...} (user). Only text blocks are statements.
  Role ``developer`` is machine-injected instruction text (e.g.
  "<permissions instructions>"), not a statement by anyone: skipped.
- Observed machine injections into the USER role (never statements by a
  human or agent; counts across 200 sampled files): texts beginning
  with ``<environment_context>`` (378), ``<subagent_notification>``
  (312), ``# AGENTS.md instructions`` (214), ``<heartbeat>`` (136),
  ``<skill>`` (41), ``<turn_aborted>`` (30). Skipped via
  ``EXCLUDED_USER_PREFIXES``.
- Other observed payload types (``reasoning``, ``function_call``,
  ``function_call_output``, ``custom_tool_call``(+output),
  ``web_search_call``, ``tool_search_call``/``output``) and all
  ``event_msg`` / ``turn_context`` / ``compacted`` lines carry no
  statements. Notably ``event_msg`` payload ``task_complete`` has a
  ``last_agent_message`` field that duplicates the assistant
  ``output_text``: skipped to avoid double-counting.
- Malformed JSON lines are recorded in ``skipped_lines`` and never
  raise (invariant #4: ingestion must not crash a session).
"""

from __future__ import annotations

import json
import os

from jevmory.ingestion.models import (
    ParsedTranscript,
    Statement,
    SOURCE_CODEX,
)

# Machine-injected wrappers observed in real user-role messages.
# Texts starting with any of these are context pushed by Codex itself,
# never a statement said by a human or an agent.
EXCLUDED_USER_PREFIXES: tuple[str, ...] = (
    "<environment_context>",
    "<subagent_notification>",
    "<skill>",
    "<heartbeat>",
    "<turn_aborted>",
    "# AGENTS.md instructions",
)


def parse_codex_transcript(path: str | os.PathLike[str]) -> ParsedTranscript:
    """Parse one Codex ``.jsonl`` rollout transcript into statements."""
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

            record_type = record.get("type")
            payload = record.get("payload")

            if record_type == "session_meta" and isinstance(payload, dict):
                if session_id is None and isinstance(payload.get("session_id"), str):
                    session_id = payload["session_id"]
                if cwd is None and isinstance(payload.get("cwd"), str):
                    cwd = payload["cwd"]
                continue

            if record_type != "response_item" or not isinstance(payload, dict):
                continue
            if payload.get("type") != "message":
                continue

            role = payload.get("role")
            if role not in ("user", "assistant"):  # 'developer' == machine text
                continue

            ts = record.get("timestamp")
            ts = ts if isinstance(ts, str) else None

            for index, text in enumerate(_statement_texts(role, payload.get("content"))):
                statements.append(
                    Statement(
                        line_no=line_no,
                        index=index,
                        ts=ts,
                        role=role,
                        text=text,
                        session_id=session_id,
                    )
                )

    return ParsedTranscript(
        path=path,
        source=SOURCE_CODEX,
        session_id=session_id,
        cwd=cwd,
        statements=tuple(statements),
        skipped_lines=tuple(skipped),
    )


def _statement_texts(role: str, content) -> list[str]:
    """Verbatim texts from one Codex message payload's content blocks."""
    if not isinstance(content, list):
        return []
    out: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in ("input_text", "output_text"):
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not text:
            continue
        if role == "user" and text.startswith(EXCLUDED_USER_PREFIXES):
            continue
        out.append(text)
    return out
