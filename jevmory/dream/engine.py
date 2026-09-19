"""Dream engine (PLAN M5): one ``dream`` run over the pending queue.

Composes the M1-M4 layers into a single deterministic pass:

1. **Privacy gate** — grading calls the Jev API, so it requires the
   per-project opt-in marker (``optin_path``); no marker means the
   engine refuses and the queue keeps waiting (PLAN #4).
2. **Queue drain** — pending events (``graded_at IS NULL``) are
   re-derived into candidates (``candidates_from_statements``) over
   the FULL statement history of the pending sessions — already-graded
   turns included, so context is the real conversation, not just the
   ungraded tail (review S4) — grouped by normalized text + role so a
   claim repeated across sessions is graded ONCE (support bumps apply
   per occurrence event id). A statement that chunks into multiple
   candidates shares one event id but occupies distinct groups; every
   verdict is routed by GROUP KEY ``(normalize_claim(text), role)``,
   never by event id alone (review S1: event-id keying silently
   discarded all but the last chunk's verdict).
3. **Cap + ordering** — events are selected user-role-first (user
   statements are decisions), rowid order within; the cap counts
   candidate groups (``MAX_CANDIDATES_PER_DREAM``); events past it
   stay pending (``events_deferred``). Events whose statements yield
   no candidates are graded too — an empty yield is deterministic and
   must not re-queue forever.
4. **Ask expiry** — every ask open when the dream begins is bumped;
   at ``ASK_EXPIRY_DREAMS`` it expires keep-old (``dream.resolve``).
   An ask created BY this dream is not counted for it.
5. **Phase A** — plan/ask/verdict per group; receipts under stable
   question keys, one per (candidate, question).
6. **Survivor routing** — code-level duplicate (zero Jev) bumps
   support; significance < ``PAIR_SIGNIFICANCE_GATE`` passes straight
   to a fact; otherwise Phase B pairs the candidate against retrieved
   active facts.
7. **Phase B** — pair batches against the merged facts array; one
   disposition per candidate, strongest action wins: duplicate >
   supersede > ask > plain add. A conflict that is not decisively
   overridden becomes an ask: both rows kept, the CHALLENGER held in
   the ask state (the incumbent stays active — a low-confidence
   challenger never displaces it, DOMAIN #3), linked ``contradicts``.
   Pairs over ``CONTRADICTION_GATE`` that did NOT win the action keep
   their ``contradicts`` edges anyway — the edge is evidence, not a
   decision (review S2).
8. **Close** — graded events stamped, run stats written, report
   loaded from the store (facts + open asks + per-fact distinct
   session counts for the writer).

On a ``JevError`` — or ANY exception — the run row is finished with
the error and the exception re-raises (review S3: a torn-down run row
must never stay open); events stay pending (grading incomplete), and
whatever was already written — receipts, facts — stands, exactly as
recorded. The CLI (M6) owns messaging and exit codes.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from jevmory.dream.batch import PairPlan, plan_phase_b
from jevmory.dream.resolve import bump_and_expire_asks
from jevmory.dream.rules import (
    ACTION_ASK,
    ACTION_DUPLICATE,
    ACTION_SUPERSEDE,
    DROP_LOW_DURABLE,
    DROP_LOW_SIGNIFICANCE,
    Q_CATEGORY,
    Q_CONTRADICTS,
    Q_DURABLE,
    Q_SAME_CLAIM,
    Q_SIGNIFICANCE,
    Q_VERDICT,
    CandidateVerdict,
    PairVerdict,
    pair_action,
    phase_a_verdict,
)
from jevmory.dream.writer import AskPair
from jevmory.ingestion.eventlog import optin_path
from jevmory.ingestion.extract import Candidate, candidates_from_statements
from jevmory.ingestion.models import ROLE_USER, Statement
from jevmory.judgment.batch import plan_phase_a
from jevmory.judgment.errors import JevError
from jevmory.memory.facts import (
    Fact,
    active_facts,
    add_fact,
    add_link,
    ask_facts,
    bump_support,
    contradicts_partner,
    find_code_duplicate,
    finish_run,
    mark_ask,
    normalize_claim,
    record_judgment,
    retrieve_similar,
    start_run,
    supersede,
)
from jevmory.thresholds import (
    CONTRADICTION_GATE,
    MAX_CANDIDATES_PER_DREAM,
    PAIR_SIGNIFICANCE_GATE,
)

# Anything shaped like JevClient / FakeJev: an ``ask(state, questions)``.
Asker = Callable[..., Any]


class GradingNotEnabledError(Exception):
    """No opt-in marker: the engine refuses to call the Jev API."""

    def __init__(self, project_dir: str, marker: str) -> None:
        super().__init__(
            f"grading is not enabled for {project_dir} (no opt-in marker at "
            f"{marker}); run `jevmory init --enable-grading`. Candidates "
            "stay queued — nothing left the machine."
        )
        self.project_dir = project_dir
        self.marker = marker


@dataclass(frozen=True)
class DreamReport:
    """What one dream did, plus the store state for the writer."""

    run_id: int | None
    api_calls: int
    usage_tokens: int
    events_graded: int
    events_deferred: int  # stayed pending: over the candidate cap
    candidates_graded: int  # groups (deduped by normalized text + role)
    occurrences: int  # candidate rows covered by those groups
    dropped_low_durable: int
    near_misses: int  # NEAR_MISS_LOW <= durable < gate — surfaced, never silent
    dropped_low_significance: int
    pair_skipped_low_significance: int  # below the pairing bar: plain add
    facts_added: tuple[int, ...]
    duplicates: int  # code-level + Jev same-claim bumps
    superseded: tuple[int, ...]  # old fact ids taken out by new facts
    asks: tuple[int, ...]  # new fact ids held in the ask state
    asks_expired: tuple[int, ...]
    facts: tuple[Fact, ...]  # active after the run — render input
    ask_pairs: tuple[AskPair, ...]  # open asks — render input
    # distinct observing sessions per fact id — the writer's "seen in
    # N sessions" is this count, NOT support_count (which counts events;
    # one session can observe a claim in several turns — review S6)
    sessions_by_fact: Mapping[int, int] = field(default_factory=dict)


def run_dream(
    conn: sqlite3.Connection,
    *,
    project: str,
    client: Asker,
    project_dir: str,
    project_context: Mapping[str, Any] | None = None,
    home: str | os.PathLike[str] | None = None,
    now: str | None = None,
    max_candidates: int | None = MAX_CANDIDATES_PER_DREAM,
    enforce_optin: bool = True,
) -> DreamReport:
    """Run one dream over the project's pending queue. See module docstring.

    ``enforce_optin=False`` is the CLI's ``--offline`` path: simulated
    grading (FakeJev) never calls the API, so the opt-in gate — which
    exists to guard API egress, not local computation — does not apply.
    """
    marker = optin_path(project_dir, home=home)
    if enforce_optin and not marker.exists():
        raise GradingNotEnabledError(project_dir, str(marker))
    if max_candidates is not None and max_candidates <= 0:
        raise ValueError("max_candidates must be positive or None")
    context = dict(project_context) if project_context is not None else {
        "name": project
    }
    stamp = now or _utc_now()  # one timestamp for the whole run

    events = _pending_events(conn, project)
    # Context source: the FULL statement history of the pending sessions,
    # already-graded turns included (review S4) — a graded decision the
    # session made earlier is exactly the context a new candidate needs.
    # Graded-turn candidates are filtered out: only pending groups grade.
    pending_ids = set(events.ids)
    candidates = [
        candidate
        for candidate in candidates_from_statements(
            _session_statements(conn, project)
        )
        if candidate.event_id in pending_ids
    ]
    keys = [_group_key(c) for c in candidates]

    selected, deferred = _select_events(events, candidates, keys, max_candidates)
    selected_set = set(selected)

    # groups to grade: candidates of selected events only, first-seen order
    grade_groups: dict[tuple[str, str], list[Candidate]] = {}
    for candidate, key in zip(candidates, keys):
        if candidate.event_id in selected_set:
            grade_groups.setdefault(key, []).append(candidate)
    representatives = [group[0] for group in grade_groups.values()]

    if not selected and not ask_facts(conn, project):
        # zero spend: no run row, no receipts, store state as-is
        facts = tuple(active_facts(conn, project))
        return DreamReport(
            run_id=None,
            api_calls=0,
            usage_tokens=0,
            events_graded=0,
            events_deferred=0,
            candidates_graded=0,
            occurrences=0,
            dropped_low_durable=0,
            near_misses=0,
            dropped_low_significance=0,
            pair_skipped_low_significance=0,
            facts_added=(),
            duplicates=0,
            superseded=(),
            asks=(),
            asks_expired=(),
            facts=facts,
            ask_pairs=_ask_pairs(conn, project),
            sessions_by_fact=_sessions_by_fact(conn, facts),
        )

    run_id = start_run(conn, project=project, kind="dream", now=stamp)
    api_calls = 0
    usage_tokens = 0
    facts_added: list[int] = []
    duplicates_code = 0
    duplicates_jev = 0
    superseded_ids: list[int] = []
    ask_ids: list[int] = []
    pair_skipped = 0
    try:
        expired = bump_and_expire_asks(
            conn, run_id, project=project, now=stamp
        )

        # --- Phase A: grade the representatives ---------------------------
        # Verdicts are keyed by GROUP KEY (normalized text + role), never
        # by event id: a statement that chunks into several candidates
        # shares one event id across distinct groups, and event-id keying
        # silently kept only the LAST chunk's verdict (review S1).
        # ``by_event`` holds the representatives per event id in order;
        # batch coverage is exact and order-preserving, so popping maps
        # each batch position to its own representative — including
        # same-event chunks.
        verdicts: dict[tuple[str, str], CandidateVerdict] = {}
        by_event: dict[str, list[Candidate]] = {}
        for representative in representatives:
            by_event.setdefault(representative.event_id, []).append(
                representative
            )
        if representatives:
            for batch in plan_phase_a(representatives, context):
                response = client.ask(batch.state, batch.questions)
                api_calls += 1
                usage_tokens += (
                    response.usage.input_tokens
                    + response.usage.output_tokens
                )
                for position, event_id in enumerate(batch.candidate_ids):
                    representative = by_event[event_id].pop(0)
                    durable = response.answers[f"c{position}_durable"]
                    category = response.answers[f"c{position}_category"]
                    significance = response.answers[
                        f"c{position}_significance"
                    ]
                    # the receipt subject stays the event id: provenance
                    # (which transcript turn was graded), not routing
                    _receipt_candidate(
                        conn, run_id, event_id,
                        durable, category, significance, now=stamp,
                    )
                    verdicts[_group_key(representative)] = phase_a_verdict(
                        representative,
                        durable,
                        category,
                        significance,
                    )

        dropped_low_durable = sum(
            1 for v in verdicts.values()
            if v.dropped_reason == DROP_LOW_DURABLE
        )
        near_misses = sum(1 for v in verdicts.values() if v.near_miss)
        dropped_low_significance = sum(
            1 for v in verdicts.values()
            if v.dropped_reason == DROP_LOW_SIGNIFICANCE
        )

        # --- survivor routing: dedupe, pass-through, or pairing -----------
        pair_plans: list[PairPlan] = []
        pair_verdicts: dict[tuple[str, str], list[PairVerdict]] = {}
        for verdict in (v for v in verdicts.values() if v.kept):
            occurrences = tuple(
                c.event_id for c in grade_groups[_group_key(verdict.candidate)]
            )
            duplicate = find_code_duplicate(conn, verdict.candidate.text)
            if duplicate is not None:
                fact, _score = duplicate
                for event_id in occurrences:
                    bump_support(
                        conn, fact.id, source_event_id=event_id, now=stamp
                    )
                duplicates_code += 1
                continue
            if verdict.significance.score < PAIR_SIGNIFICANCE_GATE:
                facts_added.append(
                    _add_fact(
                        conn, project, verdict, occurrences, now=stamp
                    ).id
                )
                pair_skipped += 1
                continue
            partners = retrieve_similar(conn, verdict.candidate.text)
            if not partners:
                facts_added.append(
                    _add_fact(
                        conn, project, verdict, occurrences, now=stamp
                    ).id
                )
                continue
            pair_plans.append(
                PairPlan(
                    candidate=verdict.candidate,
                    facts=tuple(
                        {"id": fact.id, "text": fact.claim}
                        for fact in partners
                    ),
                )
            )

        # --- Phase B: pair the survivors against remembered facts ---------
        if pair_plans:
            for batch in plan_phase_b(pair_plans, context):
                response = client.ask(batch.state, batch.questions)
                api_calls += 1
                usage_tokens += (
                    response.usage.input_tokens
                    + response.usage.output_tokens
                )
                for i, j in batch.pairs:
                    plan = batch.pair_plans[i]
                    fact_id = batch.fact_ids[j]
                    same = response.answers[f"p{i}_{j}_same_claim"]
                    contradicts = response.answers[f"p{i}_{j}_contradicts"]
                    verdict = response.answers[f"p{i}_{j}_verdict"]
                    _receipt_pair(
                        conn, run_id, plan.candidate.event_id, fact_id,
                        same, contradicts, verdict, now=stamp,
                    )
                    pair_verdicts.setdefault(
                        _group_key(plan.candidate), []
                    ).append(
                        PairVerdict(
                            candidate_event_id=plan.candidate.event_id,
                            fact_id=fact_id,
                            same_claim=same,
                            contradicts=contradicts,
                            verdict=verdict,
                            action=pair_action(same, contradicts, verdict),
                        )
                    )

        # --- apply Phase B verdicts (representative order) ----------------
        for plan in pair_plans:
            key = _group_key(plan.candidate)
            verdict = verdicts[key]
            occurrences = tuple(
                c.event_id for c in grade_groups[_group_key(verdict.candidate)]
            )
            of_candidate = pair_verdicts.get(key, [])
            duplicates = [p for p in of_candidate
                          if p.action == ACTION_DUPLICATE]
            supersedes = [p for p in of_candidate
                          if p.action == ACTION_SUPERSEDE]
            conflicts = [p for p in of_candidate if p.action == ACTION_ASK]
            if duplicates:
                best = max(
                    duplicates, key=lambda p: (p.same_claim.noul, -p.fact_id)
                )
                for occurrence in occurrences:
                    bump_support(
                        conn, best.fact_id,
                        source_event_id=occurrence, now=stamp,
                    )
                duplicates_jev += 1
                # the merged claim keeps its contradicts evidence against
                # every over-gate partner it did NOT merge into (S2)
                for pair in _conflict_evidence(of_candidate, best):
                    add_link(conn, best.fact_id, pair.fact_id, "contradicts")
            elif supersedes:
                best = max(
                    supersedes,
                    key=lambda p: (p.verdict.confidence, -p.fact_id),
                )
                new_fact = _add_fact(
                    conn, project, verdict, occurrences, now=stamp
                )
                facts_added.append(new_fact.id)
                supersede(
                    conn, best.fact_id, by_fact_id=new_fact.id, now=stamp
                )
                superseded_ids.append(best.fact_id)
                # conflicts against facts it did not supersede are kept
                # as contradicts edges — evidence, not a decision (S2)
                for pair in _conflict_evidence(of_candidate, best):
                    add_link(conn, new_fact.id, pair.fact_id, "contradicts")
            elif conflicts:
                new_fact = _add_fact(
                    conn, project, verdict, occurrences, now=stamp
                )
                facts_added.append(new_fact.id)
                mark_ask(conn, new_fact.id, now=stamp)
                for pair in conflicts:
                    add_link(conn, new_fact.id, pair.fact_id, "contradicts")
                ask_ids.append(new_fact.id)
            else:
                facts_added.append(
                    _add_fact(
                        conn, project, verdict, occurrences, now=stamp
                    ).id
                )

        # --- close: graded events + stats + report ------------------------
        if selected:
            with conn:
                conn.executemany(
                    "UPDATE events SET graded_at = ? WHERE id = ?",
                    ((stamp, event_id) for event_id in selected),
                )
    except JevError as error:
        finish_run(conn, run_id, error=type(error).__name__, now=stamp)
        raise
    except BaseException as error:
        # KeyboardInterrupt, MemoryError, store corruption — anything at
        # all: the run row closes with the error, then the exception
        # propagates unchanged (review S3: no open run rows, ever)
        finish_run(conn, run_id, error=type(error).__name__, now=stamp)
        raise

    occurrences_total = sum(len(g) for g in grade_groups.values())
    finish_run(
        conn,
        run_id,
        stats={
            "api_calls": api_calls,
            "usage": usage_tokens,
            "events_graded": len(selected),
            "events_deferred": len(deferred),
            "candidates_graded": len(representatives),
            "occurrences": occurrences_total,
            "dropped_low_durable": dropped_low_durable,
            "near_misses": near_misses,
            "dropped_low_significance": dropped_low_significance,
            "pair_skipped_low_significance": pair_skipped,
            "facts_added": facts_added,
            "duplicates_code": duplicates_code,
            "duplicates_jev": duplicates_jev,
            "superseded": superseded_ids,
            "asks": ask_ids,
            "asks_expired": list(expired),
        },
        now=stamp,
    )
    final_facts = tuple(active_facts(conn, project))
    return DreamReport(
        run_id=run_id,
        api_calls=api_calls,
        usage_tokens=usage_tokens,
        events_graded=len(selected),
        events_deferred=len(deferred),
        candidates_graded=len(representatives),
        occurrences=occurrences_total,
        dropped_low_durable=dropped_low_durable,
        near_misses=near_misses,
        dropped_low_significance=dropped_low_significance,
        pair_skipped_low_significance=pair_skipped,
        facts_added=tuple(facts_added),
        duplicates=duplicates_code + duplicates_jev,
        superseded=tuple(superseded_ids),
        asks=tuple(ask_ids),
        asks_expired=expired,
        facts=final_facts,
        ask_pairs=_ask_pairs(conn, project),
        sessions_by_fact=_sessions_by_fact(conn, final_facts),
    )


# --- helpers -------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _group_key(candidate: Candidate) -> tuple[str, str]:
    """Group identity: normalized claim + role.

    Chunks of one statement share an event id but differ in text, so
    an event id can NEVER identify a group (review S1) — only this key
    can, and it is exactly the grouping key of ``grade_groups``.
    """
    return (normalize_claim(candidate.text), candidate.role)


class _PendingEvents:
    """Pending event rows in rowid order: ids + roles for selection."""

    def __init__(self, rows: Sequence[sqlite3.Row | tuple]) -> None:
        self.ids: list[str] = [row[0] for row in rows]
        self.roles: list[str] = [row[3] for row in rows]


def _pending_events(conn: sqlite3.Connection, project: str) -> _PendingEvents:
    rows = conn.execute(
        "SELECT id, session_id, ts, role, text FROM events "
        "WHERE project = ? AND graded_at IS NULL ORDER BY rowid",
        (project,),
    ).fetchall()
    return _PendingEvents(rows)


def _session_statements(
    conn: sqlite3.Connection, project: str
) -> list[Statement]:
    """Statements of the pending sessions, rowid order (review S4).

    Pending turns of the project (whatever their session id, NULL
    included) PLUS the already-graded turns of those sessions — one
    query. The graded turns yield candidates too; the caller filters
    them out and keeps only their context: a decision the session made
    in an earlier, already-graded turn is exactly the context a new
    candidate needs.
    """
    rows = conn.execute(
        "SELECT id, session_id, ts, role, text FROM events "
        "WHERE project = ? AND ("
        "    graded_at IS NULL"
        "    OR session_id IN ("
        "        SELECT DISTINCT session_id FROM events"
        "        WHERE project = ? AND graded_at IS NULL"
        "          AND session_id IS NOT NULL"
        "    )"
        ") ORDER BY rowid",
        (project, project),
    ).fetchall()
    return [
        Statement(
            line_no=0,
            index=0,
            ts=row[2],
            role=row[3],
            text=row[4],
            session_id=row[1],
        )
        for row in rows
    ]


def _sessions_by_fact(
    conn: sqlite3.Connection, facts: Sequence[Fact]
) -> dict[int, int]:
    """Distinct observing sessions per fact id (review S6).

    ``support_count`` counts source EVENTS; one session can observe a
    claim across several turns. The writer's "seen in N sessions"
    renders this count, not support_count. Source id lists are chunked
    to stay under SQLite's bound-variable limit; a fact observed only
    under NULL sessions still claims one session — it was observed.
    """
    counts: dict[int, int] = {}
    for fact in facts:
        sessions: set[str] = set()
        ids = list(fact.source_event_ids)
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT DISTINCT session_id FROM events "
                f"WHERE id IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            sessions.update(row[0] for row in rows if row[0] is not None)
        counts[fact.id] = max(len(sessions), 1)
    return counts


def _conflict_evidence(
    pairs: Sequence[PairVerdict], best: PairVerdict
) -> list[PairVerdict]:
    """Over-gate pairs that did not win the action (review S2).

    Their ``contradicts`` answers are evidence that the runner-up
    facts conflict with the surviving claim; the edge is recorded, the
    decision is not — one disposition per candidate, all evidence on
    the graph.
    """
    return [
        pair
        for pair in pairs
        if pair is not best and pair.contradicts.noul >= CONTRADICTION_GATE
    ]


def _select_events(
    events: _PendingEvents,
    candidates: Sequence[Candidate],
    keys: Sequence[tuple[str, str]],
    max_candidates: int | None,
) -> tuple[list[str], list[str]]:
    """User-role-first event selection under the candidate-group cap.

    Returns (selected ids, deferred ids). Selection stops at the first
    event whose new groups would not fit; everything from there defers
    (user decisions grade first — review R10). Events yielding no
    candidates are always selected: an empty yield is deterministic and
    must not re-queue forever.
    """
    event_groups: dict[str, list[tuple[str, str]]] = {}
    for candidate, key in zip(candidates, keys):
        event_groups.setdefault(candidate.event_id, []).append(key)

    order = sorted(
        range(len(events.ids)),
        key=lambda index: 0 if events.roles[index] == ROLE_USER else 1,
    )  # stable: rowid order preserved within each role
    selected: list[str] = []
    covered: set[tuple[str, str]] = set()
    for index in order:
        event_id = events.ids[index]
        new = [
            key
            for key in event_groups.get(event_id, ())
            if key not in covered
        ]
        if max_candidates is None or len(covered) + len(new) <= max_candidates:
            covered.update(new)
            selected.append(event_id)
        else:
            break
    selected_set = set(selected)
    deferred = [
        event_id for event_id in events.ids if event_id not in selected_set
    ]
    return selected, deferred


def _add_fact(
    conn: sqlite3.Connection,
    project: str,
    verdict: CandidateVerdict,
    occurrences: Sequence[str],
    *,
    now: str,
) -> Fact:
    """Add the fact for one kept candidate; support accrues per occurrence.

    ``add_fact`` seeds support_count at 1 with the first source event id;
    each further occurrence bumps it (idempotent per event id), so
    support_count always equals the distinct source events — the
    "seen in N sessions" receipt.
    """
    fact = add_fact(
        conn,
        project=project,
        claim=verdict.candidate.text,
        context=verdict.candidate.context or None,
        category=verdict.category.choice,
        significance=verdict.significance.score,
        durable_noul=verdict.durable.noul,
        source_event_ids=occurrences[:1],
        now=now,
    )
    for event_id in occurrences[1:]:
        bump_support(conn, fact.id, source_event_id=event_id, now=now)
    return fact


def _ask_pairs(conn: sqlite3.Connection, project: str) -> tuple[AskPair, ...]:
    """Open asks with their oldest contradicts partner, for the writer.

    An ask can carry several contradicts links now (S2 keeps every
    over-gate edge); the writer's question line shows the oldest
    partner, while ``resolve`` acts on all of them.
    """
    pairs = []
    for fact in ask_facts(conn, project):
        partner = contradicts_partner(conn, fact.id)
        pairs.append(
            AskPair(
                fact=fact,
                partner_id=partner.id if partner else None,
                partner_claim=partner.claim if partner else None,
            )
        )
    return tuple(pairs)


def _receipt_candidate(
    conn: sqlite3.Connection,
    run_id: int,
    event_id: str,
    durable: Any,
    category: Any,
    significance: Any,
    *,
    now: str,
) -> None:
    """Phase A receipts: every answer verbatim, stable question keys."""
    for question_id, answer_type, answer in (
        (Q_DURABLE, "noul", durable),
        (Q_CATEGORY, "choice", category),
        (Q_SIGNIFICANCE, "score", significance),
    ):
        record_judgment(
            conn,
            run_id=run_id,
            subject_kind="candidate",
            subject_id=event_id,
            question_id=question_id,
            type=answer_type,
            answer={"type": answer_type, **asdict(answer)},
            now=now,
        )


def _receipt_pair(
    conn: sqlite3.Connection,
    run_id: int,
    event_id: str,
    fact_id: int,
    same_claim: Any,
    contradicts: Any,
    verdict: Any,
    *,
    now: str,
) -> None:
    """Phase B receipts: every pair answer verbatim, stable question keys."""
    for question_id, answer_type, answer in (
        (Q_SAME_CLAIM, "noul", same_claim),
        (Q_CONTRADICTS, "noul", contradicts),
        (Q_VERDICT, "choice", verdict),
    ):
        record_judgment(
            conn,
            run_id=run_id,
            subject_kind="pair",
            subject_id=f"{event_id}:{fact_id}",
            question_id=question_id,
            type=answer_type,
            answer={"type": answer_type, **asdict(answer)},
            now=now,
        )
