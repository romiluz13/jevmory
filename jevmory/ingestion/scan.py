"""Transcript discovery: find session logs for a project without reading them fully.

PLAN #2: `--scan` walks the agent transcript roots on disk and finds
transcripts whose recorded working directory (cwd) belongs to the project.
It never parses whole files; each candidate is sampled at the head
(first _HEAD_BYTES bytes) and matched by string containment of the
real project path, which keeps discovery cheap and side-effect free.

Scan is a read-only operation. Nothing is written, graded, or uploaded.

Roots (home resolves via JEVMORY_HOME when set, for tests/sandboxes):

* claude: <home>/.claude/projects/**/*.jsonl
* codex:  <home>/.codex/sessions/**/*.jsonl
* droid:  <home>/.factory/sessions/**/*.jsonl
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .eventlog import default_home

_HEAD_BYTES = 64 * 1024  # enough to cover session_start + first turns


@dataclass(frozen=True)
class DiscoveredTranscript:
    """One transcript on disk whose session ran inside the project."""

    path: str
    source: str  # "claude" | "codex" | "droid"
    mtime: float  # st_mtime, most recent first


def _roots(home: Path) -> list[tuple[Path, str]]:
    return [
        (home / ".claude" / "projects", "claude"),
        (home / ".codex" / "sessions", "codex"),
        (home / ".factory" / "sessions", "droid"),
    ]


def _head_text(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(_HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def discover_transcripts(
    project_dir: str | os.PathLike[str], *, home: Path | None = None
) -> list[DiscoveredTranscript]:
    """Return transcripts whose recorded cwd matches project_dir, newest first.

    Matching is by string containment of the realpath'd project directory
    inside the transcript head: agent logs embed the project path in
    session metadata (claude "cwd" / codex "payload.cwd" / droid
    session_start "cwd"). Containment
    is a cheap pre-filter, not a proof: a transcript that merely PRINTS
    another project's path inside its head can match. That is fine by
    design — the parser's own session cwd decides where the event log
    actually goes at ingest time, so a false positive costs one parse,
    never a wrong store.
    """

    real = os.path.realpath(os.path.expanduser(os.fspath(project_dir)))
    base = home if home is not None else default_home()
    found: list[DiscoveredTranscript] = []
    for root, source in _roots(base):
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if real in _head_text(path):
                found.append(DiscoveredTranscript(str(path), source, mtime))
    found.sort(key=lambda item: item.mtime, reverse=True)
    return found


def detect_source(path: str | os.PathLike[str]) -> str:
    """Guess whether a transcript came from claude, codex, or droid.

    Location hints win (a file under .claude/ is claude's), otherwise the
    first line is sniffed: codex sessions open with a session_meta record
    whose payload has an "id"; droid sessions open with a session_start
    record; claude sessions open with records carrying "sessionId".
    Raises ValueError when neither heuristic applies.
    """

    text = os.fspath(path)
    if "/.claude/" in text:
        return "claude"
    if "/.codex/" in text:
        return "codex"
    if "/.factory/" in text:
        return "droid"
    with open(text, "r", encoding="utf-8") as fh:
        first = fh.readline()
    if '"session_meta"' in first and '"payload"' in first:
        return "codex"
    if '"session_start"' in first:
        return "droid"
    if '"sessionId"' in first or '"type":"mode"' in first.replace(" ", ""):
        return "claude"
    raise ValueError(f"cannot tell claude vs codex vs droid for {path!r}; use --agent")
