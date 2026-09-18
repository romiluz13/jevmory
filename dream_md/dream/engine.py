"""Dream engine (PLAN M5): one ``dream`` run over the pending queue.

Composes the M1-M4 layers into a single deterministic pass:

1. **Privacy gate** — grading calls the Jev API, so it requires the
   per-project opt-in marker (``optin_path``); no marker means the
   engine refuses and the queue keeps waiting (PLAN #4).
2. **Queue drain** — pending events (``graded_at IS NULL``) are
   re-derived into candidates (``candidates_from_statements``;
   context rebuilt per session) and grouped by normalized text + role
   so a claim repeated across sessions is graded ONCE (support bumps
   apply per occurrence event id).
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
8. **Close** — graded events stamped, run stats written, report
   loaded from the store (facts + open asks for the writer).

On a ``JevError`` the run row is finished with the error and the
exception re-raises; events stay pending (grading incomplete), and
whatever was already written — receipts, facts — stands, exactly as
recorded. The CLI (M6) owns messaging and exit codes.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from dream_md.dream.batch import PairPlan, plan_phase_b
from dream_md.dream.resolve import bump_and_expire_asks
from dream_md.dream.rules import (
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
from dream_md.dream.writer import AskPair
from dream_md.ingestion.eventlog import optin_path
from dream_md.ingestion.extract import Candidate, candidates_from_statements
from dream_md.ingestion.models import ROLE_USER, Statement
from dream_md.judgment.batch import plan_phase_a
from dream_md.judgment.errors import JevError
from dream_md.memory.facts import (
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
from dream_md.thresholds import MAX_CANDIDATES_PER_DREAM, PAIR_SIGNIFICANCE_GATE

# Anything shaped like JevClient / FakeJev: an ``ask(state, questions)``.
Asker = Callable[..., Any]


class GradingNotEnabledError(Exception):
    """No opt-in marker: the engine refuses to call the Jev API."""

    def __init__(self, project_dir: str, marker: str) -> None:
        super().__init__(
            f"grading is not enabled for {project_dir} (no opt-in marker at "
            f"{marker}); run `dream-md init --enable-grading`. Candidates "
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
) -> DreamReport:
    """Run one dream over the project's pending queue. See module docstring."""
    marker = optin_path(project_dir, home=home)
    if not marker.exists():
        raise GradingNotEnabledError(project_dir, str(marker))
    if max_candidates is not None and max_candidates <= 0:
        raise ValueError("max_candidates must be positive or None")
    context = dict(project_context) if project_context is not None else {
        "name": project
    }
    stamp = now or _utc_now()  # one timestamp for the whole run

    events = _pending_events(conn, project)
    candidates = candidates_from_statements(events.statements)
    keys = [(normalize_claim(c.text), c.role) for c in candidates]

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
            facts=tuple(active_facts(conn, project)),
            ask_pairs=_ask_pairs(conn, project),
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
        verdicts: dict[str, CandidateVerdict] = {}
        by_event = {c.event_id: c for c in representatives}
        if representatives:
            for batch in plan_phase_a(representatives, context):
                response = client.ask(batch.state, batch.questions)
                api_calls += 1
                usage_tokens += (
                    response.usage.input_tokens
                    + response.usage.output_tokens
                )
                for position, event_id in enumerate(batch.candidate_ids):
                    durable = response.answers[f"c{position}_durable"]
                    category = response.answers[f"c{position}_category"]
                    significance = response.answers[
                        f"c{position}_significance"
                    ]
                    _receipt_candidate(
                        conn, run_id, event_id,
                        durable, category, significance, now=stamp,
                    )
                    verdicts[event_id] = phase_a_verdict(
                        by_event[event_id],
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
        pair_verdicts: dict[str, list[PairVerdict]] = {}
        for verdict in (v for v in verdicts.values() if v.kept):
            occurrences = tuple(
                c.event_id for c in grade_groups[
                    (normalize_claim(verdict.candidate.text),
                     verdict.candidate.role)
                ]
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
                        plan.candidate.event_id, []
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
            event_id = plan.candidate.event_id
            verdict = verdicts[event_id]
            occurrences = tuple(
                c.event_id for c in grade_groups[
                    (normalize_claim(verdict.candidate.text),
                     verdict.candidate.role)
                ]
            )
            of_candidate = pair_verdicts.get(event_id, [])
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
        facts=tuple(active_facts(conn, project)),
        ask_pairs=_ask_pairs(conn, project),
    )


# --- helpers -------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _PendingEvents:
    """Pending event rows in rowid order, Statement-shaped for extraction."""

    def __init__(self, rows: Sequence[sqlite3.Row | tuple]) -> None:
        self.ids: list[str] = [row[0] for row in rows]
        self.roles: list[str] = [row[3] for row in rows]
        self.statements = [
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


def _pending_events(conn: sqlite3.Connection, project: str) -> _PendingEvents:
    rows = conn.execute(
        "SELECT id, session_id, ts, role, text FROM events "
        "WHERE project = ? AND graded_at IS NULL ORDER BY rowid",
        (project,),
    ).fetchall()
    return _PendingEvents(rows)


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
    """Open asks with their contradicts partner, for the writer."""
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
