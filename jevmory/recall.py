"""SessionStart recall hook: surface this project's top facts as context.

Claude Code adds a SessionStart hook's plain-text stdout to Claude's
context, so this module prints the project's highest-confidence active
facts (verbatim quotes, receipts inline) at every session start —
including resumes and post-compaction restarts, where context was lost
and memory matters most.

Contract (same never-die stance as ``jevmory.hook``):

- never exit nonzero, never raise — a broken recall must never break
  a session start;
- silent when there is nothing to say: no store, or no active facts,
  prints nothing (a fresh project stays unpolluted);
- fully local: reads only this project's store; never the network;
- factual tone, not instructions: Claude Code treats injected text
  framed as system commands as a possible prompt injection, so the
  output is written as project facts (which is all jevmory stores).

Usage (plugin hooks.json): ``python3 -m jevmory.recall`` — the payload
on stdin carries the session cwd (tolerant key matching, same table as
the ingest hook); ``--project DIR`` overrides; with neither, the
process cwd is the project.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .hook import extract_payload
from .ingestion.eventlog import project_slug, store_path
from .memory.facts import active_facts
from .memory.schema import connect, migrate

# Context economy: cap lines and claim length so the injection stays
# small even for projects with long histories.
RECALL_LIMIT = 12
CLAIM_WIDTH = 200

# Payload cwd spellings, most likely first (kept in sync with
# jevmory.hook, same stance as the cli module's copies).
_CWD_KEYS = ("cwd", "project_dir", "working_directory", "workspace", "workspace_root")


def _first(payload: dict, keys: tuple[str, ...]):
    for key in keys:
        if payload.get(key):
            return payload[key]
    return None


def render_recall(
    project_dir: str, *, limit: int = RECALL_LIMIT, home: Path | None = None
) -> str | None:
    """Top active facts of a project as context text, or None if silent.

    Confidence-ordered (desc), then oldest first — the same order the
    ``jevmory.md`` writer uses, so the agent sees the same leading
    facts the memory file leads with. Never raises on store problems:
    a missing/corrupt store means "nothing to say", not an error.
    """

    path = store_path(project_dir, home=home)
    if not path.exists():
        return None
    try:
        conn = connect(path)
    except Exception:  # noqa: BLE001 - never-die: silent, not fatal
        return None
    try:
        migrate(conn)
        facts = active_facts(conn, project=project_slug(project_dir))
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()
    if not facts:
        return None

    facts = sorted(facts, key=lambda f: (-f.confidence, f.id))[:limit]
    lines = [
        f"jevmory — remembered facts for {Path(project_dir).name} "
        f"(verbatim quotes, confidence-ordered):"
    ]
    for fact in facts:
        claim = fact.claim if len(fact.claim) <= CLAIM_WIDTH else (
            fact.claim[: CLAIM_WIDTH - 1].rstrip() + "…"
        )
        vintage = fact.verified_at or "unverified"
        lines.append(
            f"- {claim}  "
            f"[conf {fact.confidence:.2f} · said {fact.created_at[:10]} "
            f"· verified {vintage[:10] if fact.verified_at else vintage}]"
        )
    lines.append(
        "full memory: jevmory.md at the project root — "
        "`jevmory audit jevmory.md` gives receipts for every line"
    )
    return "\n".join(lines)


def _project_from_argv(argv: list[str]) -> str | None:
    """Explicit project override: ``--project DIR`` / ``--project=DIR``."""

    for index, arg in enumerate(argv):
        if arg == "--project" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--project="):
            return arg.split("=", 1)[1]
    return None


def run_recall(
    argv: list[str] | None = None,
    stdin_text: str | None = None,
    *,
    home: Path | None = None,
) -> int:
    """Hook body: print recall text or stay silent; always 0, never raises."""

    try:
        payload = extract_payload(argv or [], stdin_text) or {}
        cwd = _first(payload, _CWD_KEYS) if isinstance(payload, dict) else None
        override = _project_from_argv(argv or [])
        project = (
            override
            or (str(cwd) if cwd else None)
            or os.path.realpath(os.getcwd())
        )
        text = render_recall(project, home=home)
        if text:
            print(text)
    except Exception:  # noqa: BLE001 - contract: silent, never fatal
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdin_text: str | None = None
    try:
        if not sys.stdin.isatty():
            stdin_text = sys.stdin.read()
    except (OSError, ValueError):
        stdin_text = None
    return run_recall(argv, stdin_text)


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
