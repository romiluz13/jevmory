"""Audit engine (PLAN M4): memory file + store evidence -> verdicts.

Composes the M2/M3/M4 layers into one run:

1. parse the memory file into auditable lines (zero lines -> zero
   spend, no run row);
2. assemble the evidence pool — the ``AUDIT_EVIDENCE_FACTS`` most
   recent active facts and the ``AUDIT_EVIDENCE_STATEMENTS`` most
   recently ingested statements (both already redacted at rest);
3. plan Phase C batches (greedy, evidence shared across requests);
4. ask the client (``JevClient`` live, ``FakeJev`` offline — anything
   with an ``ask(state, questions)`` method);
5. apply the disposition rule per line (code, not Jev);
6. record every answer verbatim in ``judgments`` (subject_kind
   ``'line'``, stable question keys) and bracket the run with stats.

On a ``JevError`` the run row is finished with the error and the
exception re-raises — audit is a human-run command; the caller (CLI,
M6) owns exit codes and messaging. Nothing is silently swallowed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any, Callable, Mapping, Sequence

from dream_md.audit.batch import plan_phase_c
from dream_md.audit.memfile import MemoryLine, parse_memory_file
from dream_md.audit.report import AuditReport, build_report
from dream_md.audit.rules import (
    Q_CONTRADICTED,
    Q_DISPOSITION,
    Q_SUPPORTED,
    LineVerdict,
    count_dispositions,
    line_verdict,
)
from dream_md.judgment.batch import Batch
from dream_md.judgment.errors import JevError
from dream_md.memory.facts import finish_run, record_judgment, start_run
from dream_md.thresholds import (
    AUDIT_EVIDENCE_FACTS,
    AUDIT_EVIDENCE_STATEMENTS,
)

# Anything shaped like JevClient / FakeJev: an ``ask(state, questions)``.
Asker = Callable[..., Any]


def run_audit(
    memory_file_text: str,
    conn: sqlite3.Connection,
    *,
    project: str,
    client: Asker,
    memory_file: str = "<memory>",
    project_context: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> AuditReport:
    """Audit one memory file against the project's store. See module docstring."""
    lines = parse_memory_file(memory_file_text)
    if not lines:
        # zero spend: no run row, no api calls, empty report
        return build_report(
            memory_file=memory_file,
            verdicts=[],
            evidence_facts=0,
            evidence_statements=0,
        )

    facts, statements = _evidence_pool(conn, project)
    batches = plan_phase_c(
        lines, facts, statements, project_context or {"name": project}
    )

    run_id = start_run(conn, project=project, kind="audit", now=now)
    by_id = {line.id: line for line in lines}
    verdicts: list[LineVerdict] = []
    api_calls = 0
    usage_tokens = 0
    try:
        for batch in batches:
            response = client.ask(batch.state, batch.questions)
            api_calls += 1
            usage_tokens += (
                response.usage.input_tokens + response.usage.output_tokens
            )
            verdicts.extend(
                _verdicts_from(batch, response.answers, by_id)
            )
            _record_receipts(
                conn, run_id, batch, response.answers, now=now
            )
    except JevError as error:
        finish_run(conn, run_id, error=type(error).__name__, now=now)
        raise

    counts = count_dispositions(verdicts)
    finish_run(
        conn,
        run_id,
        stats={
            "api_calls": api_calls,
            "usage": usage_tokens,
            "lines_audited": len(lines),
            **{f"lines_{key.lower()}": value for key, value in counts.items()},
        },
        now=now,
    )
    return build_report(
        memory_file=memory_file,
        verdicts=verdicts,
        run_id=run_id,
        api_calls=api_calls,
        usage_tokens=usage_tokens,
        evidence_facts=len(facts),
        evidence_statements=len(statements),
    )


def _evidence_pool(
    conn: sqlite3.Connection, project: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The evidence: most recent active facts + most recent statements.

    Both redacted at rest already (invariant 6). Most recent = highest
    fact id / highest events rowid (ingest order); presented oldest
    first so the state reads chronologically.
    """
    fact_rows = conn.execute(
        "SELECT id, claim FROM facts "
        "WHERE project = ? AND status = 'active' "
        "ORDER BY id DESC LIMIT ?",
        (project, AUDIT_EVIDENCE_FACTS),
    ).fetchall()
    facts = [{"id": row[0], "text": row[1]} for row in reversed(fact_rows)]

    statement_rows = conn.execute(
        "SELECT id, role, text, ts FROM events "
        "WHERE project = ? "
        "ORDER BY rowid DESC LIMIT ?",
        (project, AUDIT_EVIDENCE_STATEMENTS),
    ).fetchall()
    statements = [
        {"id": row[0], "role": row[1], "text": row[2], "ts": row[3]}
        for row in reversed(statement_rows)
    ]
    return facts, statements


def _verdicts_from(
    batch: Batch,
    answers: Mapping[str, Any],
    by_id: Mapping[str, MemoryLine],
) -> list[LineVerdict]:
    """LineVerdicts for one batch: map l{i}_ answers back to lines."""
    verdicts = []
    for i, entry in enumerate(batch.state["memory_lines"]):
        line = by_id[entry["id"]]
        stable = {
            Q_SUPPORTED: answers[f"l{i}_supported"],
            Q_CONTRADICTED: answers[f"l{i}_contradicted"],
            Q_DISPOSITION: answers[f"l{i}_disposition"],
        }
        verdicts.append(line_verdict(line, stable))
    return verdicts


def _record_receipts(
    conn: sqlite3.Connection,
    run_id: int,
    batch: Batch,
    answers: Mapping[str, Any],
    *,
    now: str | None = None,
) -> None:
    """Every answer, verbatim, keyed by stable subject + question ids."""
    for i, entry in enumerate(batch.state["memory_lines"]):
        for stable_key, position_id, answer_type in (
            (Q_SUPPORTED, f"l{i}_supported", "noul"),
            (Q_CONTRADICTED, f"l{i}_contradicted", "noul"),
            (Q_DISPOSITION, f"l{i}_disposition", "choice"),
        ):
            # the wire shape, reconstructed: type + the parsed fields
            answer = {"type": answer_type, **asdict(answers[position_id])}
            record_judgment(
                conn,
                run_id=run_id,
                subject_kind="line",
                subject_id=entry["id"],
                question_id=stable_key,
                type=answer_type,
                answer=answer,
                now=now,
            )
