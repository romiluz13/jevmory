"""Agent hook entry point: ingest a just-finished session without dying.

Never exit nonzero (lead): a broken hook must never break the agent run.
`python3 -m jevmory.hook` reads one JSON payload from stdin (Claude
SessionEnd) or argv (Codex notify), extracts the project working
directory plus transcript path, ingests the transcript locally, and
always exits 0. Errors are written to a per-project hook log under
~/.jevmory/hooks/ so they are visible via `jevmory status` without
ever surfacing to the agent.

Payload keys are matched tolerantly: agents spell fields differently
across versions (cwd/project_dir/workspace, transcript_path/path/...),
so the first recognized spelling wins. A payload without a transcript
path is a no-op logged as "skipped" (many notify invocations carry no
transcript). A payload with neither cwd nor a parseable transcript
cannot be attributed and is logged globally.

Offline-first (PLAN #4): the hook only appends to the local SQLite
store. It never calls the network; grading happens later via
`jevmory dream`.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ingestion.claude import parse_claude_transcript
from .ingestion.codex import parse_codex_transcript
from .ingestion.eventlog import EventLog, default_home, project_slug

# Payload key spellings, most likely first.
_CWD_KEYS = ("cwd", "project_dir", "working_directory", "workspace", "workspace_root")
_PATH_KEYS = (
    "transcript_path",
    "transcript",
    "path",
    "session_file",
    "rollout_path",
    "file",
)
_SESSION_KEYS = ("session_id", "session", "sessionId")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_path(project_dir: str, *, home: Path | None = None) -> Path:
    """Per-project hook log: <home>/.jevmory/hooks/<slug>.jsonl."""

    base = home if home is not None else default_home()
    return base / ".jevmory" / "hooks" / f"{project_slug(project_dir)}.jsonl"


def global_log_path(home: Path | None = None) -> Path:
    """Hook log for events that carry no attributable project (payload-less
    invocations, unattributable errors)."""

    base = home if home is not None else default_home()
    return base / ".jevmory" / "hooks" / "global.jsonl"


def append_log(
    project_dir: str | None, entry: dict[str, Any], *, home: Path | None = None
) -> None:
    """Append one JSONL status line; never raises (best effort)."""

    try:
        path = (
            log_path(project_dir, home=home)
            if project_dir
            else global_log_path(home)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        pass


def extract_payload(argv: list[str], stdin_text: str | None) -> dict[str, Any] | None:
    """Best-effort payload extraction; returns None when nothing parses."""

    if stdin_text:
        try:
            data = json.loads(stdin_text)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
    for arg in argv or []:
        try:
            data = json.loads(arg)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if payload.get(key):
            return payload[key]
    return None


def _project_from_argv(argv: list[str]) -> str | None:
    """Explicit project override: ``--project DIR`` / ``--project=DIR``."""

    for index, arg in enumerate(argv):
        if arg == "--project" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--project="):
            return arg.split("=", 1)[1]
    return None


def run_hook(
    argv: list[str] | None = None,
    stdin_text: str | None = None,
    *,
    home: Path | None = None,
) -> int:
    """Hook body. Never raises; always returns 0 (never-die contract)."""

    try:
        _hook_once(argv or [], stdin_text, home=home)
    except Exception as exc:  # noqa: BLE001 - contract: log, do not die
        append_log(
            None,
            {"ts": _utc_now(), "event": "hook_error", "error": f"{type(exc).__name__}: {exc}"},
            home=home,
        )
    return 0


def _hook_once(
    argv: list[str], stdin_text: str | None, *, home: Path | None
) -> None:
    payload = extract_payload(argv, stdin_text)
    project_override = _project_from_argv(argv)
    if payload is None:
        append_log(
            project_override,
            {"ts": _utc_now(), "event": "skipped", "reason": "no JSON payload"},
            home=home,
        )
        return

    transcript = _first(payload, _PATH_KEYS)
    cwd = _first(payload, _CWD_KEYS)
    session = _first(payload, _SESSION_KEYS)
    # Attribution precedence: explicit --project > payload cwd > transcript
    # cwd (known only after parsing). Pre-parse logs use what is known.
    project_dir = project_override or (str(cwd) if cwd else None)

    def log(event: str, **fields: Any) -> None:
        append_log(project_dir, {"ts": _utc_now(), "event": event, **fields}, home=home)

    if not transcript:
        log("skipped", reason="no transcript path in payload", session_id=session)
        return

    # Source detection: location hint first, else sniff the first line.
    text = str(transcript)
    if "/.claude/" in text:
        source = "claude"
    elif "/.codex/" in text:
        source = "codex"
    else:
        try:
            with open(text, "r", encoding="utf-8") as fh:
                first = fh.readline()
        except OSError as exc:
            log("ingest_error", transcript=text, error=f"{type(exc).__name__}: {exc}")
            return
        if '"session_meta"' in first and '"payload"' in first:
            source = "codex"
        else:
            source = "claude"

    try:
        if source == "claude":
            parsed = parse_claude_transcript(text)
        else:
            parsed = parse_codex_transcript(text)
        if not project_dir:
            project_dir = parsed.cwd or None
        if not project_dir:
            log(
                "skipped",
                reason="no project dir in payload or transcript",
                transcript=text,
            )
            return
        event_log = EventLog.for_project(project_dir, home=home)
        result = event_log.append(parsed)
    except Exception as exc:  # noqa: BLE001 - never-die: log, return 0
        log("ingest_error", transcript=text, error=f"{type(exc).__name__}: {exc}")
        return

    log(
        "ingest",
        transcript=text,
        source=source,
        statements=len(parsed.statements),
        inserted=result.inserted,
        duplicates=result.ignored,
        session_id=parsed.session_id,
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdin_text: str | None = None
    try:
        if not sys.stdin.isatty():
            stdin_text = sys.stdin.read()
    except (OSError, ValueError):
        stdin_text = None
    return run_hook(argv, stdin_text)


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
