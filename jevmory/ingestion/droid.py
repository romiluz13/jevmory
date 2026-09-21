"""Parser for Factory Droid session transcripts.

Built from REAL files observed on 2026-09-20 under
``~/.factory/sessions/<project-dir-slug>/<uuid>.jsonl`` on this machine
(census pass over all 1,061 files, 206,743 message lines; recorded in
``.ddd/notes/jev-md.md``):

- One JSON object per line: {"type": "session_start" | "message" |
  "todo_state" | "agent_turn_outcome" | "compaction_state", ...}. The
  first line is always ``session_start`` (1,061/1,061 files). Its
  payload carries ``id`` (the session uuid — also the file name, but
  the payload is authoritative), ``cwd``, ``title``, ``owner``,
  ``version``, ``hostId``, and for subagent tasks
  ``callingSessionId``/``callingToolUseId`` (893 files) — subagent
  threads still carry their own ``id``, so it is always taken from
  the payload, never the file name.
- ``message`` lines: {"id", "parentId", "timestamp" (ISO-8601 UTC,
  present on every observed message line), "message": {"role":
  "user" | "assistant", "content": [...]}, optional
  "compactionSummaryId"}. Only the two roles were observed (111,208
  user, 95,535 assistant). ``content`` was ALWAYS a list of blocks;
  a plain string is tolerated anyway (claude-shaped tolerance).
  Observed blocks: user — {"type": "text", "text"} (16,191),
  {"type": "tool_result", ...} (148,863), {"type": "image", ...} (3);
  assistant — {"type": "text", "text"} (33,056), {"type": "thinking",
  ...} (52,471), {"type": "tool_use", ...} (148,869). Only ``text``
  blocks are statements; tool results and images are never quotes,
  thinking and tool use are not speech.
- Hook-generated user messages carry ``hookEventName`` (observed:
  PreToolUse 3,579, PostToolUse 3,480, SessionStart 1,703,
  UserPromptSubmit 91, Stop 83, Notification 22, PreCompact 2 — 8,960
  total, every one with EMPTY list content): skipped entirely, a hook
  message is machine by definition.
- Machine injections into the USER role (never statements by anyone).
  Census over all 16,191 user text blocks, 13,168 excluded via
  ``EXCLUDED_USER_PREFIXES``/``EXCLUDED_USER_PATTERNS``: harness
  context — ``<system-reminder`` (10,533), ``<system-
  notification`` (15); scheduler notifications — ``Background task
  `` (1,173), ``# Task Tool Invocation`` (874), ``Bounded r<N> ``
  monitoring checks (77), ``Monitor the active `` (16),
  ``OPERATIONAL health check`` (13); harness status/errors —
  ``Request interrupted`` (103), ``Request cancelled`` (100),
  ``Unable to reach `` (68), ``BYOK Error:`` (51), ``The AI model
  timed out`` (18), ``{"type":"bash_result`` (15), ``Interrupted``
  (7), ``The model repeatedly issued`` (4), ``Skill "<name>"
  activated`` (16); orchestrator templates — ``# Follow-up
  Instructions`` (71, herdr follow-up dispatch), ``Reply with
  exactly: `` (8, machine sentinel probe), ``You are implementing ``
  (6, herdr task dispatch — same family excluded in codex.py).
  Judgment rule (same as codex): a machine template is excluded even
  when it mentions project paths, but no prefix may be broad enough
  to catch normal speech. Ad hoc root-agent dispatches with VARIED
  phrasing are KEPT as authored speech — ``Read /Users/...
  workstreams/REVIEW-W2-CACHE.md. Begin your independent behavioral
  review now.`` (61), ``Continue as ... team lead from ... HANDOFF.md``
  (13), ``FREEZE CHECK ...``, ``Astra disposition ...`` — they are
  typed, not templated, and the distill grader downweights commands;
  ``keep going`` (36), ``go ahed`` (11), ``yes`` (7) obviously stay.
  Verification pass over the full tree: 0 crashes, 0 malformed lines,
  36,077 statements kept (3,021 user, 33,056 assistant).
- Other line types carry no statements: ``todo_state`` (todos +
  messageIndex), ``agent_turn_outcome`` (turnId, reason, resultKind),
  ``compaction_state`` (summaryText + anchors — the summary is a
  machine digest, not a quote).
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
    SOURCE_DROID,
)

# Machine-injected wrappers, notifications, and scheduler prompts
# observed in real user text blocks (census of 2026-09-20, counts in
# the module docstring). Texts starting with any of these are pushed by
# the Droid harness, its background scheduler, or the herdr orchestrator's
# fixed templates — never a statement said by a human or an agent.
# Judgment rule (same as codex.py): machine templates are excluded even
# when they mention project paths, but no prefix may be broad enough to
# catch normal speech — ad hoc root-agent dispatches ("Read /Users/...",
# "Continue as ...") are typed, not templated, and stay.
EXCLUDED_USER_PREFIXES: tuple[str, ...] = (
    # harness context / notifications
    "<system-reminder",
    "<system-notification",
    # scheduler notifications and monitor prompts
    "Background task ",
    "# Task Tool Invocation",
    "Monitor the active ",
    "OPERATIONAL health check",
    # harness status / errors
    "Request interrupted",
    "Request cancelled",
    "Interrupted",
    "Unable to reach ",
    "BYOK Error:",
    "The AI model timed out",
    "The model repeatedly issued",
    '{"type":"bash_result',
    # orchestrator fixed templates / probes
    "# Follow-up Instructions",
    "Reply with exactly: ",
    "You are implementing ",  # herdr task dispatch (excluded in codex too)
)

# Machine families whose shape varies per line need patterns, not
# prefixes: the scheduler's "Bounded r4/r5/... monitoring check" prompts
# and the harness's skill activation notices.
EXCLUDED_USER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Bounded r\d+ [^\n]* monitoring check"),
    re.compile(r'Skill "[^"]+" activated'),
)


def _machine_injected(text: str) -> bool:
    """True when a user-role text is machine-pushed, not spoken."""
    if text.startswith(EXCLUDED_USER_PREFIXES):
        return True
    return any(pattern.match(text) for pattern in EXCLUDED_USER_PATTERNS)


def parse_droid_transcript(path: str | os.PathLike[str]) -> ParsedTranscript:
    """Parse one Droid ``.jsonl`` session transcript into statements."""
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

            if record_type == "session_start":
                if session_id is None and isinstance(record.get("id"), str):
                    session_id = record["id"]
                if cwd is None and isinstance(record.get("cwd"), str):
                    cwd = record["cwd"]
                continue

            if record_type != "message":
                continue  # todo_state / agent_turn_outcome / compaction_state

            message = record.get("message")
            if not isinstance(message, dict):
                continue
            if message.get("hookEventName") is not None:
                continue  # hook-generated, machine by definition

            role = message.get("role")
            if role not in ("user", "assistant"):
                continue

            ts = record.get("timestamp")
            ts = ts if isinstance(ts, str) else None

            for index, text in enumerate(_statement_texts(role, message.get("content"))):
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
        source=SOURCE_DROID,
        session_id=session_id,
        cwd=cwd,
        statements=tuple(statements),
        skipped_lines=tuple(skipped),
    )


def _statement_texts(role: str, content) -> list[str]:
    """Verbatim texts from one Droid message's content blocks."""
    if isinstance(content, str):  # not observed, tolerated
        texts = [content]
    elif isinstance(content, list):
        texts = [
            block.get("text")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
    else:
        return []
    out: list[str] = []
    for text in texts:
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not text:
            continue
        if role == "user" and _machine_injected(text):
            continue
        out.append(text)
    return out
