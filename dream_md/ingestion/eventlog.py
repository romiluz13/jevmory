"""SQLite Event log: the events table of PLAN schema v1.

The events table is the immutable raw statement log and the offline
queue: ``graded_at`` stays NULL until a Dream run judges the event.

Event id: PLAN says sha256(transcript_path + line_no). One transcript
line can yield SEVERAL statements (a Claude assistant message with
multiple text blocks), so the id input is
``"<abs transcript path>:<line_no>:<statement index>"`` — the same
stability guarantee, unique per statement. Idempotent by construction:
re-ingesting a transcript is a no-op (INSERT OR IGNORE on the same ids).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dream_md.ingestion.models import ParsedTranscript

EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  source TEXT NOT NULL,
  session_id TEXT,
  ts TEXT,
  role TEXT,
  text TEXT,
  created_at TEXT NOT NULL,
  graded_at TEXT
)
"""

_INSERT_SQL = (
    "INSERT OR IGNORE INTO events "
    "(id, project, source, session_id, ts, role, text, created_at, graded_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def project_slug(project_dir: str | os.PathLike[str]) -> str:
    """Per-project store slug: sha1 of the absolute project path, short."""
    absolute = os.path.abspath(os.fspath(project_dir))
    return hashlib.sha1(absolute.encode("utf-8")).hexdigest()[:12]


def store_path(
    project_dir: str | os.PathLike[str], home: str | os.PathLike[str] | None = None
) -> Path:
    """SQLite store path for a project (PLAN: ~/.dream-md/projects/<slug>.db)."""
    base = Path(home) if home is not None else Path.home()
    return base / ".dream-md" / "projects" / f"{project_slug(project_dir)}.db"


def event_id(
    transcript_path: str | os.PathLike[str], line_no: int, index: int
) -> str:
    """Stable id for one statement: sha256 of '<abs path>:<line>:<index>'."""
    key = f"{os.path.abspath(os.fspath(transcript_path))}:{line_no}:{index}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AppendResult:
    inserted: int
    ignored: int  # already present (idempotent re-ingest)
    skipped_lines: tuple[int, ...]  # malformed transcript lines


class EventLog:
    """Writer for the events table of one project's store."""

    def __init__(self, db_path: str | os.PathLike[str], *, project: str):
        self.db_path = str(db_path)
        self.project = project
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(EVENTS_TABLE_SQL)
        self._conn.commit()

    @classmethod
    def for_project(
        cls,
        project_dir: str | os.PathLike[str],
        home: str | os.PathLike[str] | None = None,
    ) -> "EventLog":
        """Open the store for a project directory (creates directories)."""
        return cls(
            store_path(project_dir, home=home), project=project_slug(project_dir)
        )

    def append(self, parsed: ParsedTranscript) -> AppendResult:
        """Append all statements of a parsed transcript, idempotently."""
        created_at = _utc_now()
        before = self._conn.total_changes
        with self._conn:
            for statement in parsed.statements:
                self._conn.execute(
                    _INSERT_SQL,
                    (
                        event_id(parsed.path, statement.line_no, statement.index),
                        self.project,
                        parsed.source,
                        statement.session_id,
                        statement.ts,
                        statement.role,
                        statement.text,
                        created_at,
                    ),
                )
        inserted = self._conn.total_changes - before
        return AppendResult(
            inserted=inserted,
            ignored=len(parsed.statements) - inserted,
            skipped_lines=parsed.skipped_lines,
        )

    def count_all(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def count_pending(self) -> int:
        """Events not yet graded — the offline queue depth."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM events WHERE graded_at IS NULL"
        ).fetchone()[0]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
