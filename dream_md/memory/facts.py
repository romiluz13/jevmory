"""Fact store operations (PLAN v2 M3): lifecycle, dedupe, FTS retrieval.

Everything here works on one project's store connection (``connect`` +
``migrate`` from ``dream_md.memory.schema``); DDL itself stays in
schema.py (review R7). The operations are the storage primitives the
dream engine (M5) composes into verdicts:

- **add** an active fact (confidence = the one formula, receipts land
  in ``judgments`` via ``record_judgment``);
- **code-level dedupe** (zero Jev spend): normalized-claim hash equal,
  or Jaccard ≥ ``JACCARD_GATE`` against an existing ACTIVE fact;
- **retrieve** similar facts: FTS5 fetch ``FTS_RETRIEVE_K`` = 10, then
  re-rank by token overlap and keep ``FTS_KEEP_TOP`` = 5 — these become
  the Phase B pair partners;
- **lifecycle**: duplicate → ``bump_support``; destructive verdicts →
  ``supersede`` / ``retire`` (row KEPT for provenance, removed from
  FTS); low-confidence conflict → ``mark_ask`` (+ ``bump_ask`` counting
  consecutive unresolved dreams; expiry disposition is M5's call).

FTS maintenance rule (PLAN "FTS maintenance"): only ACTIVE facts are
ever indexed in ``facts_fts`` — retire/supersede delete the FTS row via
the external-content 'delete' command (which must carry the EXACT
indexed claim) while the facts row survives for provenance.

Cost note: ``find_code_duplicate`` scans all active facts in Python —
O(facts) per candidate. Deliberate: exact normalized-hash equality must
hold even for claims with no FTS-indexable tokens, and the local store
(hundreds of facts) makes the scan trivial; revisit only if real
corpora prove it slow.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from dream_md.thresholds import (
    FTS_KEEP_TOP,
    FTS_RETRIEVE_K,
    JACCARD_GATE,
    fact_confidence,
)

# Fact statuses (PLAN "Verdict": keep / supersede / retire / ask).
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_RETIRED = "retired"
STATUS_ASK = "ask"

_FACT_COLUMNS = (
    "id, project, claim, context, category, significance, confidence, "
    "status, source_event_ids, support_count, ask_seen_count, "
    "created_at, updated_at, last_supported_at"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Fact:
    """One row of the facts table (source_event_ids decoded to a tuple)."""

    id: int
    project: str
    claim: str
    context: str | None
    category: str
    significance: float
    confidence: float
    status: str
    source_event_ids: tuple[str, ...]
    support_count: int
    ask_seen_count: int
    created_at: str
    updated_at: str
    last_supported_at: str | None


def _row_to_fact(row: sqlite3.Row | tuple) -> Fact:
    return Fact(
        id=row[0],
        project=row[1],
        claim=row[2],
        context=row[3],
        category=row[4],
        significance=row[5],
        confidence=row[6],
        status=row[7],
        source_event_ids=tuple(json.loads(row[8])),
        support_count=row[9],
        ask_seen_count=row[10],
        created_at=row[11],
        updated_at=row[12],
        last_supported_at=row[13],
    )


# --- reads --------------------------------------------------------------------


def get_fact(conn: sqlite3.Connection, fact_id: int) -> Fact | None:
    row = conn.execute(
        f"SELECT {_FACT_COLUMNS} FROM facts WHERE id = ?", (fact_id,)
    ).fetchone()
    return _row_to_fact(row) if row else None


def active_facts(conn: sqlite3.Connection, project: str) -> list[Fact]:
    """Active facts of a project, insertion order (Phase B state / dream.md)."""
    rows = conn.execute(
        f"SELECT {_FACT_COLUMNS} FROM facts "
        "WHERE project = ? AND status = ? ORDER BY id",
        (project, STATUS_ACTIVE),
    ).fetchall()
    return [_row_to_fact(row) for row in rows]


def ask_facts(conn: sqlite3.Connection, project: str) -> list[Fact]:
    """Ask-status facts of a project, id order (expiry pass / rendering)."""
    rows = conn.execute(
        f"SELECT {_FACT_COLUMNS} FROM facts "
        "WHERE project = ? AND status = ? ORDER BY id",
        (project, STATUS_ASK),
    ).fetchall()
    return [_row_to_fact(row) for row in rows]


def contradicts_partners(
    conn: sqlite3.Connection, fact_id: int
) -> list[Fact]:
    """All facts this one contradicts (``contradicts`` links, oldest first).

    A fact can carry several such links: the dream engine keeps the
    contradicts edge for every over-gate pair (review S2), not just the
    pair whose action won. ``resolve`` acts on ALL of them.
    """
    rows = conn.execute(
        "SELECT related_id FROM fact_links "
        "WHERE fact_id = ? AND relation = 'contradicts' "
        "ORDER BY rowid",
        (fact_id,),
    ).fetchall()
    return [get_fact(conn, row[0]) for row in rows]


def contradicts_partner(conn: sqlite3.Connection, fact_id: int) -> Fact | None:
    """The oldest ``contradicts`` partner, or None (display helper).

    The writer's "new vs old" question line shows this one; resolution
    semantics use ``contradicts_partners`` (all of them).
    """
    partners = contradicts_partners(conn, fact_id)
    return partners[0] if partners else None


# --- receipts (judgments + runs) ----------------------------------------------


def record_judgment(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    subject_kind: str,
    subject_id: str,
    question_id: str,
    type: str,
    answer: Any,
    now: str | None = None,
) -> int:
    """Store one verbatim answer (F7.1: every number reproducible from here)."""
    answer_json = answer if isinstance(answer, str) else json.dumps(answer)
    with conn:
        cursor = conn.execute(
            "INSERT INTO judgments (run_id, subject_kind, subject_id, "
            "question_id, type, answer_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                subject_kind,
                subject_id,
                question_id,
                type,
                answer_json,
                now or _utc_now(),
            ),
        )
    return cursor.lastrowid


def start_run(
    conn: sqlite3.Connection, *, project: str, kind: str, now: str | None = None
) -> int:
    """Open a run row (kind: dream | audit); returns its id."""
    with conn:
        cursor = conn.execute(
            "INSERT INTO runs (project, kind, started_at) VALUES (?, ?, ?)",
            (project, kind, now or _utc_now()),
        )
    return cursor.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    stats: dict[str, Any] | None = None,
    error: str | None = None,
    now: str | None = None,
) -> None:
    """Close a run row: stats JSON (api_calls, usage, dropped_low_durable…)."""
    with conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, stats = ?, error = ? WHERE id = ?",
            (
                now or _utc_now(),
                json.dumps(stats) if stats is not None else None,
                error,
                run_id,
            ),
        )


# --- fact lifecycle -----------------------------------------------------------


def add_fact(
    conn: sqlite3.Connection,
    *,
    project: str,
    claim: str,
    category: str,
    significance: float,
    durable_noul: float,
    context: str | None = None,
    source_event_ids: Sequence[str] = (),
    now: str | None = None,
) -> Fact:
    """Insert an ACTIVE fact and index it in FTS (confidence = the one formula).

    ``durable_noul`` is the verbatim Phase A answer; the derived
    confidence is stored, the raw answer goes to ``judgments`` via
    ``record_judgment`` (the receipt), by the engine.
    """
    timestamp = now or _utc_now()
    confidence = fact_confidence(durable_noul)
    with conn:
        cursor = conn.execute(
            "INSERT INTO facts (project, claim, context, category, "
            "significance, confidence, status, source_event_ids, "
            "support_count, ask_seen_count, created_at, updated_at, "
            "last_supported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)",
            (
                project,
                claim,
                context,
                category,
                significance,
                confidence,
                STATUS_ACTIVE,
                json.dumps(list(source_event_ids)),
                timestamp,
                timestamp,
                timestamp,  # first sighting counts as "last seen"
            ),
        )
        fact_id = cursor.lastrowid
        # external-content FTS5: index by explicit rowid insert
        conn.execute(
            "INSERT INTO facts_fts (rowid, claim) VALUES (?, ?)",
            (fact_id, claim),
        )
    fact = get_fact(conn, fact_id)
    assert fact is not None  # inserted in the transaction above
    return fact


def bump_support(
    conn: sqlite3.Connection,
    fact_id: int,
    *,
    source_event_id: str,
    now: str | None = None,
) -> Fact:
    """Duplicate handling (PLAN): bump support_count, add source event id.

    Idempotent per event id: re-grading the same event must not
    double-count support (mirrors the events-level "one support source
    per session" stance).
    """
    fact = get_fact(conn, fact_id)
    if fact is None:
        raise ValueError(f"no fact {fact_id}")
    timestamp = now or _utc_now()
    if source_event_id in fact.source_event_ids:
        return fact  # already supported by this event — no double count
    with conn:
        conn.execute(
            "UPDATE facts SET support_count = ?, source_event_ids = ?, "
            "last_supported_at = ?, updated_at = ? WHERE id = ?",
            (
                fact.support_count + 1,
                json.dumps([*fact.source_event_ids, source_event_id]),
                timestamp,
                timestamp,
                fact_id,
            ),
        )
    updated = get_fact(conn, fact_id)
    assert updated is not None
    return updated


def add_link(
    conn: sqlite3.Connection, fact_id: int, related_id: int, relation: str
) -> None:
    """Record a fact_links edge (duplicate_of | supersedes | contradicts)."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO fact_links (fact_id, related_id, relation) "
            "VALUES (?, ?, ?)",
            (fact_id, related_id, relation),
        )


def _remove_from_fts(conn: sqlite3.Connection, fact: Fact) -> None:
    """External-content FTS5 delete — must carry the EXACT indexed claim."""
    conn.execute(
        "INSERT INTO facts_fts (facts_fts, rowid, claim) "
        "VALUES ('delete', ?, ?)",
        (fact.id, fact.claim),
    )


def _set_status(
    conn: sqlite3.Connection, fact: Fact, status: str, now: str | None = None
) -> Fact:
    timestamp = now or _utc_now()
    with conn:
        conn.execute(
            "UPDATE facts SET status = ?, updated_at = ? WHERE id = ?",
            (status, timestamp, fact.id),
        )
        if status != STATUS_ACTIVE:
            # FTS maintenance: non-active facts leave the similarity index
            # (row kept for provenance; PLAN "FTS maintenance").
            _remove_from_fts(conn, fact)
    updated = get_fact(conn, fact.id)
    assert updated is not None
    return updated


def supersede(
    conn: sqlite3.Connection,
    fact_id: int,
    *,
    by_fact_id: int,
    now: str | None = None,
) -> Fact:
    """Destructive verdict: old fact status superseded, out of FTS, linked."""
    fact = get_fact(conn, fact_id)
    if fact is None:
        raise ValueError(f"no fact {fact_id}")
    add_link(conn, by_fact_id, fact_id, "supersedes")
    return _set_status(conn, fact, STATUS_SUPERSEDED, now)


def retire(
    conn: sqlite3.Connection, fact_id: int, now: str | None = None
) -> Fact:
    """Destructive verdict: status retired, out of FTS, row kept."""
    fact = get_fact(conn, fact_id)
    if fact is None:
        raise ValueError(f"no fact {fact_id}")
    return _set_status(conn, fact, STATUS_RETIRED, now)


def mark_ask(
    conn: sqlite3.Connection, fact_id: int, now: str | None = None
) -> Fact:
    """Low-confidence conflict verdict: fact enters the ask state (fresh count).

    The FTS row is deliberately left in place: ``mark_ask`` never
    touches the index (M3 stance, pinned by tests) — retrieval and
    code dedupe both filter on ``status = 'active'`` anyway, and
    ``reactivate`` re-checks index presence rather than assuming.
    """
    fact = get_fact(conn, fact_id)
    if fact is None:
        raise ValueError(f"no fact {fact_id}")
    timestamp = now or _utc_now()
    with conn:
        conn.execute(
            "UPDATE facts SET status = ?, ask_seen_count = 0, updated_at = ? "
            "WHERE id = ?",
            (STATUS_ASK, timestamp, fact_id),
        )
    updated = get_fact(conn, fact_id)
    assert updated is not None
    return updated


def reactivate(
    conn: sqlite3.Connection, fact_id: int, now: str | None = None
) -> Fact:
    """Set a fact back to ACTIVE and make sure its FTS row exists.

    The ``keep-new`` resolution of an ask: the challenger becomes the
    active truth. Index presence is checked, not assumed — ``mark_ask``
    leaves the FTS row in place, so a fact that went ask->reactivate
    may still be indexed (insert would collide) or not (if it passed
    through retire on another path).
    """
    fact = get_fact(conn, fact_id)
    if fact is None:
        raise ValueError(f"no fact {fact_id}")
    timestamp = now or _utc_now()
    with conn:
        conn.execute(
            "UPDATE facts SET status = ?, ask_seen_count = 0, updated_at = ? "
            "WHERE id = ?",
            (STATUS_ACTIVE, timestamp, fact_id),
        )
        indexed = conn.execute(
            "SELECT 1 FROM facts_fts WHERE rowid = ?", (fact_id,)
        ).fetchone()
        if not indexed:
            conn.execute(
                "INSERT INTO facts_fts (rowid, claim) VALUES (?, ?)",
                (fact_id, fact.claim),
            )
    updated = get_fact(conn, fact_id)
    assert updated is not None
    return updated


def bump_ask(
    conn: sqlite3.Connection, fact_id: int, now: str | None = None
) -> int:
    """One more dream seen with this ask unresolved; returns the new count.

    Only ask-status facts accumulate; other statuses are a no-op
    (returns their current count). Expiry disposition is the engine's
    (M5) — this only counts.
    """
    fact = get_fact(conn, fact_id)
    if fact is None:
        raise ValueError(f"no fact {fact_id}")
    if fact.status != STATUS_ASK:
        return fact.ask_seen_count
    timestamp = now or _utc_now()
    with conn:
        conn.execute(
            "UPDATE facts SET ask_seen_count = ?, updated_at = ? WHERE id = ?",
            (fact.ask_seen_count + 1, timestamp, fact_id),
        )
    return fact.ask_seen_count + 1


# --- code-level dedupe (zero Jev spend) ---------------------------------------


def normalize_claim(claim: str) -> str:
    """Dedup normalization: lowercase + collapse whitespace runs."""
    return " ".join(claim.lower().split())


def claim_hash(claim: str) -> str:
    """sha256 of the normalized claim (PLAN 'normalized-text hash')."""
    return hashlib.sha256(normalize_claim(claim).encode("utf-8")).hexdigest()


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def claim_tokens(claim: str) -> list[str]:
    """Lowercased alphanumeric tokens (len >= 2: 'a', 'I' are noise)."""
    return [token for token in _TOKEN_RE.findall(claim.lower()) if len(token) >= 2]


def jaccard(tokens_a: Sequence[str], tokens_b: Sequence[str]) -> float:
    """Token-set Jaccard overlap; empty sets never match (0.0)."""
    if not tokens_a or not tokens_b:
        return 0.0
    set_a, set_b = set(tokens_a), set(tokens_b)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def find_code_duplicate(
    conn: sqlite3.Connection,
    claim: str,
    *,
    jaccard_gate: float = JACCARD_GATE,
) -> tuple[Fact, float] | None:
    """Code-level duplicate of ``claim`` among ACTIVE facts, or None.

    Hash-equal (normalized) scores 1.0; otherwise the best Jaccard at or
    above the gate wins. Only active facts participate (same rule as
    FTS). The store is one db per project, so scanning all active rows
    IS the project scope.
    """
    normalized = normalize_claim(claim)
    tokens = claim_tokens(claim)
    best: tuple[Fact, float] | None = None
    for row in conn.execute(
        f"SELECT {_FACT_COLUMNS} FROM facts WHERE status = ? ORDER BY id",
        (STATUS_ACTIVE,),
    ):
        fact = _row_to_fact(row)
        if normalize_claim(fact.claim) == normalized:
            return fact, 1.0
        score = jaccard(tokens, claim_tokens(fact.claim))
        if score >= jaccard_gate:
            if best is None or score > best[1]:
                best = (fact, score)
    return best


# --- FTS retrieval ------------------------------------------------------------


def retrieve_similar(
    conn: sqlite3.Connection,
    claim: str,
    *,
    k: int = FTS_RETRIEVE_K,
    keep: int = FTS_KEEP_TOP,
) -> list[Fact]:
    """Similar ACTIVE facts for a candidate claim (Phase B partners).

    FTS5 fetches the top ``k`` by bm25 rank, then re-rank by token
    overlap (Jaccard) and keep ``keep`` — order: overlap desc, then
    oldest first. Deterministic.
    """
    tokens = claim_tokens(claim)
    if not tokens or k <= 0 or keep <= 0:
        return []
    match = " OR ".join(f'"{token}"' for token in tokens)
    rows = conn.execute(
        "SELECT rowid FROM facts_fts WHERE facts_fts MATCH ? "
        "ORDER BY rank LIMIT ?",
        (match, k),
    ).fetchall()
    if not rows:
        return []
    ids = [row[0] for row in rows]
    placeholders = ",".join("?" * len(ids))
    fact_rows = conn.execute(
        f"SELECT {_FACT_COLUMNS} FROM facts "
        f"WHERE id IN ({placeholders}) AND status = ?",
        (*ids, STATUS_ACTIVE),
    ).fetchall()
    facts = [_row_to_fact(row) for row in fact_rows]
    scored = sorted(
        ((jaccard(tokens, claim_tokens(fact.claim)), fact) for fact in facts),
        key=lambda pair: (-pair[0], pair[1].id),
    )
    return [fact for _, fact in scored[:keep]]
