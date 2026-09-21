"""Ask lifecycle: human resolution and distill-time expiry (PLAN M5).

An ask is a conflict the Jev verdict could not decide: both rows are
kept, the challenger is held in the ``ask`` status (out of active
memory, receipts intact) with ``contradicts`` links to every incumbent
it conflicted with (review S2 keeps all over-gate edges).

- ``resolve`` (human): ``jevmory resolve <fact_id> --keep-new|--keep-old``
  writes a resolution receipt under a ``resolve`` run and applies it —
  against ALL of the ask's contradicts partners (review S7): one
  decision settles the whole conflict set, not just the oldest edge.
- ``bump_and_expire_asks`` (distill): every distill that begins with an
  ask unresolved increments ``ask_seen_count``; at
  ``ASK_EXPIRY_DISTILLS`` the ask expires — disposition ``keep-old``:
  the incumbents stay active (they never left), the challenger
  retires. Absent a human decision the remembered facts win, mirroring
  the no-decay stance (absence of resolution is not evidence against
  the incumbents; a low-confidence challenger never displaces them).

Resolution/expiry receipts are choice answers with confidence 1.0 and
a peaked distribution: a decision by fiat, not a calibrated guess —
the confidence belongs to the decider, and code/humans are certain.
"""

from __future__ import annotations

import sqlite3

from jevmory.memory.facts import (
    STATUS_ASK,
    Fact,
    ask_facts,
    bump_ask,
    contradicts_partners,
    finish_run,
    get_fact,
    record_judgment,
    reactivate,
    retire,
    start_run,
    supersede,
)
from jevmory.thresholds import ASK_EXPIRY_DISTILLS

# CLI flag vocabulary (kebab-case at the CLI: --keep-new / --keep-old).
KEEP_NEW = "keep_new"
KEEP_OLD = "keep_old"
RESOLVE_CHOICES = (KEEP_NEW, KEEP_OLD)

_EXPIRY_ANSWER = {
    "type": "choice",
    "choice": KEEP_OLD,
    "probabilities": {KEEP_OLD: 1.0},
    "confidence": 1.0,
}


def resolve(
    conn: sqlite3.Connection,
    fact_id: int,
    choice: str,
    *,
    now: str | None = None,
) -> Fact:
    """Close one ask by human decision and return the fact's final state.

    ``keep-new``: the challenger becomes the active truth — every
    contradicts partner is superseded (linked, out of FTS, rows kept;
    review S7: one decision settles the whole conflict set) and the
    challenger reactivates (back in FTS). ``keep-old``: the challenger
    retires; the partners are untouched (they stayed active all along).
    """
    fact = get_fact(conn, fact_id)
    if fact is None:
        raise ValueError(f"no fact {fact_id}")
    if fact.status != STATUS_ASK:
        raise ValueError(
            f"fact {fact_id} is not an ask (status {fact.status!r}); "
            "only asks can be resolved"
        )
    if choice not in RESOLVE_CHOICES:
        raise ValueError(
            f"choice must be one of {RESOLVE_CHOICES}, got {choice!r}"
        )
    partners = contradicts_partners(conn, fact_id)
    if not partners:
        raise ValueError(
            f"ask fact {fact_id} has no contradicts partners to resolve against"
        )

    run_id = start_run(conn, project=fact.project, kind="resolve", now=now)
    if choice == KEEP_NEW:
        for partner in partners:
            supersede(conn, partner.id, by_fact_id=fact.id, now=now)
        fact = reactivate(conn, fact.id, now=now)
    else:
        fact = retire(conn, fact.id, now=now)

    record_judgment(
        conn,
        run_id=run_id,
        subject_kind="fact",
        subject_id=str(fact_id),
        question_id="resolution",
        type="choice",
        answer={
            "type": "choice",
            "choice": choice,
            "probabilities": {choice: 1.0},
            "confidence": 1.0,
        },
        now=now,
    )
    finish_run(
        conn,
        run_id,
        stats={
            "resolution": choice,
            "fact_id": fact_id,
            "partner_ids": [partner.id for partner in partners],
        },
        now=now,
    )
    return fact


def bump_and_expire_asks(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    project: str,
    now: str | None = None,
) -> tuple[int, ...]:
    """One distill pass over the project's asks: bump counts, expire at limit.

    Runs at the START of a distill, before grading — so an ask created by
    this very distill is not counted for it (its first bump is the next
    distill that begins with it still open). Returns the expired fact ids
    in id order; every expiry writes its keep-old receipt under
    ``run_id``.
    """
    expired: list[int] = []
    for fact in ask_facts(conn, project):
        count = bump_ask(conn, fact.id, now=now)
        if count >= ASK_EXPIRY_DISTILLS:
            retire(conn, fact.id, now=now)
            record_judgment(
                conn,
                run_id=run_id,
                subject_kind="fact",
                subject_id=str(fact.id),
                question_id="expiry",
                type="choice",
                answer=_EXPIRY_ANSWER,
                now=now,
            )
            expired.append(fact.id)
    return tuple(expired)
