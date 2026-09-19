"""SQLite Event log: the events table (DDL owned by ``dream_md.memory.schema``).

The events table is the immutable statement log and the offline queue:
``graded_at`` stays NULL until a Dream run judges the event.

Event id (PLAN v2, review R2): ``sha256(normalized_content + session_id)``
— never path/line based. Consequences, all deliberate:

- Idempotent under transcript rewrites WITHIN a session: compaction or
  reformatting moves lines around, but a statement's normalized content
  is unchanged -> same id -> ``INSERT OR IGNORE`` dedupes.
- Cross-session verbatim repeats SURVIVE as their own events (distinct
  session_id -> distinct id), so Phase B can bump support_count and
  "seen in N sessions" receipts remain possible.
- Within one session, verbatim repeats collapse to a single event (one
  support source per session). Claude sidechains carry the PARENT
  session id, so sidechain/main duplicates collapse too.
- The id hashes the REDACTED, whitespace-normalized text — the identity
  of what is actually stored — joined as ``"<session_id>\\x1f<text>"``
  (the separator keeps content and session unambiguous).

Redaction at rest (DOMAIN #6) happens at this storage boundary: parsers
stay raw and verbatim; the store never holds unredacted text.

Paths that feed identities (project slug / store path) use
``os.path.realpath`` (review R5): symlink spellings (/tmp vs
/private/tmp) must not create duplicate stores.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dream_md.ingestion.models import ParsedTranscript
from dream_md.ingestion.redact import redact
from dream_md.memory.schema import connect, migrate

# No DDL here (review R7): every CREATE lives in dream_md.memory.schema.

_INSERT_SQL = (
    "INSERT OR IGNORE INTO events "
    "(id, project, source, session_id, ts, role, text, created_at, graded_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_content(text: str) -> str:
    """Id normalization: collapse whitespace runs to single spaces, strip.

    Survives transcript rewrites that reflow whitespace within a line;
    the stored text itself stays the outer-stripped verbatim quote
    (DOMAIN "Statement": verbatim modulo outer whitespace).
    """
    return " ".join(text.split())


def event_id(text: str, session_id: str | None) -> str:
    """Stable id for one statement: sha256 of '<session_id>\\x1f<normalized>'.

    ``text`` is the REDACTED statement text (the identity of what is
    stored); see the module docstring for the full scheme rationale.
    """
    key = f"{session_id or ''}\x1f{normalize_content(text)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def default_home() -> Path:
    """Root for dream-md state. ``$DREAM_MD_HOME`` wins over ``Path.home()``.

    One override for every state path (stores, opt-in markers, hook
    logs, scan roots) — tests and sandboxes redirect everything with a
    single env var; agent-owned config files (``~/.claude/settings.json``,
    ``~/.codex/config.toml``) are NOT dream-md state and always use the
    real home.
    """

    override = os.environ.get("DREAM_MD_HOME")
    return Path(override) if override else Path.home()


def project_slug(project_dir: str | os.PathLike[str]) -> str:
    """Per-project store slug: sha1 of the REAL project path, short.

    realpath (review R5): a project reached through a symlink spelling
    must map to the same store, not a duplicate.
    """
    real = os.path.realpath(os.path.expanduser(os.fspath(project_dir)))
    return hashlib.sha1(real.encode("utf-8")).hexdigest()[:12]


def store_path(
    project_dir: str | os.PathLike[str], home: str | os.PathLike[str] | None = None
) -> Path:
    """SQLite store path for a project (PLAN: ~/.dream-md/projects/<slug>.db)."""
    base = Path(home) if home is not None else default_home()
    return base / ".dream-md" / "projects" / f"{project_slug(project_dir)}.db"


def optin_path(
    project_dir: str | os.PathLike[str], home: str | os.PathLike[str] | None = None
) -> Path:
    """Grading opt-in marker path (~/.dream-md/projects/<slug>.optin).

    Privacy by architecture (PLAN #4): hooks only ever ingest locally;
    grading — anything that calls the Jev API — requires this marker,
    created by ``dream-md init --enable-grading``. No marker -> the
    dream engine refuses to grade and candidates stay queued.
    """
    base = Path(home) if home is not None else default_home()
    return base / ".dream-md" / "projects" / f"{project_slug(project_dir)}.optin"


@dataclass(frozen=True)
class AppendResult:
    inserted: int
    ignored: int  # already present (idempotent re-ingest or same-session repeat)
    skipped_lines: tuple[int, ...]  # malformed transcript lines


class EventLog:
    """Writer for the events table of one project's store."""

    def __init__(self, db_path: str | os.PathLike[str], *, project: str):
        self.db_path = str(db_path)
        self.project = project
        # connect() sets WAL + busy_timeout=5000 on every connection and
        # migrate() stamps schema_version (v1-era stores branch inside).
        self._conn = connect(self.db_path)
        migrate(self._conn)

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
        """Append all statements of a parsed transcript, idempotently.

        Redaction happens HERE (storage boundary): the parser's raw
        verbatim text is scrubbed before the INSERT; the store never
        holds unredacted text (DOMAIN #6).
        """
        created_at = _utc_now()
        before = self._conn.total_changes
        with self._conn:
            for statement in parsed.statements:
                session_id = (
                    statement.session_id
                    if statement.session_id is not None
                    else parsed.session_id
                )
                text = redact(statement.text)
                self._conn.execute(
                    _INSERT_SQL,
                    (
                        event_id(text, session_id),
                        self.project,
                        parsed.source,
                        session_id,
                        statement.ts,
                        statement.role,
                        text,
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
