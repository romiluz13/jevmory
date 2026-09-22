"""Parser for Codex session transcripts.

Built from REAL files observed on 2026-09-19 under
``~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`` on this
machine (1,077 files). Field shapes relied on:

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
  human or agent). Original six wrapper flavors (2026-09-19 sample of
  200 files): ``<environment_context>``, ``<subagent_notification>``,
  ``<skill>``, ``<heartbeat>``, ``<turn_aborted>``, ``# AGENTS.md
  instructions``. Dogfood round 1 census (2026-09-20, all 1,077
  rollout files under ``~/.codex/sessions``: 6,009 user-role texts,
  4,361 machine-injected) added: codex wrappers — ``# Files mentioned
  by the user:`` (24), ``<recommended_plugins`` (265),
  ``<codex_internal_context`` (72), ``<image name=`` / ``</image>``
  (23 + 23), ``<user_shell_command>`` (6), old-format ``>>>``
  transcript markers; herdr agent prompts — ``You are running ``
  benchmark dispatch (431), ``You are a skill `` selector/expert (82),
  reviewer/sidecar/read-only lanes, ``You are the <named role>``,
  ``You are implementing/reviewing/helping on`` dispatch, ``We are in
  /Users`` lanes, ``PLEASE IMPLEMENT THIS PLAN:`` (50); and herdr lane
  headers — ``Repo: ``, ``cwd: ``, ``Read-only `` / ``READ-ONLY ``
  (112), spec/code-quality review, research/docs-proof/validate task
  opens. Judgment rule: a machine prompt is excluded even when it
  mentions project paths, but no prefix may be broad enough to catch
  normal speech — bare ``You are `` is NOT excluded ("You are right"),
  and ``In /Users/...`` opens stay (human-plausible path-prefaced
  notes). Two fused cases deliberately KEPT: ``# In app browser:`` and
  ``<in-app-browser-context`` blocks carry the machine ambient-UI
  wrapper and the human's actual request ("## My request for Codex:
  ...") in ONE text — excluding them would discard human speech.
  Borderline families inspected in full and kept as human:
  "Your previous full answer is stuck..." and "My earlier consultation
  prompt ... Here are the four questions again" (first-person recovery
  instructions), "Round 2 — implementation review..." (first-person
  review instructions). All machine families skipped via
  ``EXCLUDED_USER_PREFIXES``.
- Older rollout formats replay the prior conversation inside the USER
  role as numbered lines (``[N] user:``, ``[N] assistant:``, ``[N]
  tool exec call:`` / ``[N] tool exec result:``; 83 lines observed);
  the numeric prefix varies per line, so these are skipped via
  ``EXCLUDED_USER_PATTERNS``.
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
import re

from jevmory.ingestion.models import (
    ParsedTranscript,
    Statement,
    SOURCE_CODEX,
)

# Machine-injected wrappers and task-dispatch prompts observed in real
# user-role messages (original six from the 2026-09-19 sample; the rest
# from the dogfood round 1 census, 2026-09-20). Texts starting with any
# of these are context or instructions pushed by Codex/herdr itself,
# never a statement said by a human or an agent. Judgment rule: machine
# prompts are excluded even when they mention project paths, but no
# prefix may be broad enough to catch normal speech — bare "You are "
# is not excluded; "In /Users/..." opens stay.
EXCLUDED_USER_PREFIXES: tuple[str, ...] = (
    # codex system/context wrappers
    "<environment_context>",
    "<subagent_notification>",
    "<skill>",
    "<heartbeat>",
    "<turn_aborted>",
    "# AGENTS.md instructions",
    "# Files mentioned by the user:",
    "<recommended_plugins",
    "<codex_internal_context",
    "<image name=",
    "</image>",
    "<user_shell_command>",
    ">>> TRANSCRIPT START",
    ">>> TRANSCRIPT END",
    ">>> APPROVAL REQUEST START",
    # herdr agent prompts (machine-composed task dispatch)
    "You are running ",
    "You are a skill ",
    "You are the skill ",
    "You are a Senior Code Reviewer",
    "You are a sidecar reviewer",
    "You are sidecar ",
    "You are a focused read-only",
    "You are a read-only docs/code",
    "You are doing code quality review",
    "You are doing the Spec axis",
    "You are doing the Standards axis",
    "You are helping on ",
    "You are implementing ",
    "You are reviewing ",
    "You are analyzing /Users",
    "You are in /Users",
    "We are in /Users",
    "You are the Benchmark Claims Auditor",
    "You are the Cloudflare Deploy Agent",
    "You are the Competitor Tone Researcher",
    "You are the Release Gate Agent",
    "You are the Repo Cartographer",
    "You are the Spec reviewer",
    "You are the Standards reviewer",
    "You are the Task 5 SPEC COMPLIANCE reviewer",
    "You are the npm Release Agent",
    "PLEASE IMPLEMENT THIS PLAN:",
    # herdr lane headers (machine-composed task text)
    "Repo: ",
    "cwd: ",
    "Read-only ",
    "READ-ONLY ",
    "Spec compliance review",
    "Code quality review",
    "Validate external ",
    "Research task:",
    "Research-only task",
    "Docs proof task:",
    "ZoomInfo docs/code",
)

# Older rollout formats replay the prior conversation inside the USER
# role as numbered lines; the numeric prefix varies per line, so these
# need patterns, not prefixes.
EXCLUDED_USER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\[\d+\] (?:user|assistant|tool exec call|tool exec result):"),
)


def _machine_injected(text: str) -> bool:
    """True when a user-role text is machine-pushed, not spoken."""
    if text.startswith(EXCLUDED_USER_PREFIXES):
        return True
    return any(pattern.match(text) for pattern in EXCLUDED_USER_PATTERNS)


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
        if role == "user" and _machine_injected(text):
            continue
        out.append(text)
    return out
