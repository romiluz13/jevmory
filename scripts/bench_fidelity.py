"""Fidelity benchmark (PLAN "Testing strategy", v0.2): planted-truth fixture.

Offline by default — zero network, zero API key, no opt-in marker:

1. STAGE-1 METRICS — the deterministic anchor check
   (``audit/anchor.py``) against a store with planted truth. What is
   measured, honestly: verbatim recall (planted true claims that
   anchor) and false-anchor count (planted errors that must NEVER
   anchor — a stale line matches no ACTIVE fact, a one-word mutation
   is not equality, an unstored claim has nothing to match). This is
   the offline FLOOR: what the pipeline guarantees without a model.

2. HARNESS SELF-CHECK — the same fixture through the FULL engine with
   an ideal scripted model (every unanchored line answered with its
   planted disposition, decisively). The confusion matrix must be
   perfectly diagonal, proving the measurement path itself (engine →
   rules → report) is sound before any real model is measured. Exits 1
   if not.

``--live`` measures the REAL model end-to-end (the only interesting
number): the same planted fixture through the full engine against a
live client — typesafe ($TYPESAFE_API_KEY) or ``--backend kev`` (local
server, no key; receipts experimental). Prints the confusion matrix,
accuracy, and the API spend; no pass/fail (this is measurement, not a
gate — the run row lands in the fixture's own store like any audit).

    python3 scripts/bench_fidelity.py                  # offline floor + self-check
    python3 scripts/bench_fidelity.py --json           # machine-readable
    TYPESAFE_API_KEY=... python3 scripts/bench_fidelity.py --live
    python3 scripts/bench_fidelity.py --live --backend kev

Exit codes: 0 benchmark ran (and, offline, passed the self-check);
1 offline self-check failed or setup refused (never a swallowed
traceback).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from jevmory.audit.anchor import anchor_map
from jevmory.audit.engine import run_audit
from jevmory.audit.memfile import parse_memory_file
from jevmory.audit.questions import DISPOSITION_OPTIONS
from jevmory.judgment.fake import MODE_SCRIPTED, FakeJev
from jevmory.memory.facts import active_facts, add_fact, supersede
from jevmory.memory.schema import connect, migrate

PROJECT = "bench-fixture"

# --- planted truth ------------------------------------------------------------
# Long enough to clear ANCHOR_MIN_CHARS after normalization; realistic
# project-memory claims, not lorem ipsum (the model has to actually
# read them in --live mode).
_TRUE_CLAIMS = [
    "The persistence layer is SQLite with WAL mode enabled by default",
    "Grading requests retry on 429 and 529 with exponential backoff up to three attempts",
    "The CLI gate checks the per-project opt-in marker before reading the API key",
]
# superseded by _TRUE_CLAIMS[2] — the STALE line quotes it verbatim
_OLD_CLAIM = "The CLI gate reads the API key before checking the opt-in marker"
# one-word mutation of _TRUE_CLAIMS[0] — not equality, must go to the model
_WRONG_CLAIM = "The persistence layer is Postgres with WAL mode enabled by default"
# plausible, never stored — UNSUPPORTED
_UNSUPPORTED_CLAIM = "The audit report is emailed to the project owner at midnight"

_EVIDENCE_STATEMENTS = [
    (
        "evt-bench-1",
        "assistant",
        "Switched the persistence layer to SQLite and enabled WAL mode by default",
    ),
    (
        "evt-bench-2",
        "assistant",
        "The gate order is fixed: the opt-in marker is checked before the API key is read",
    ),
    (
        "evt-bench-3",
        "user",
        "Make sure nothing is emailed anywhere — keep every report local",
    ),
]

# category -> (expected stage-1 route, expected verdict keyword)
_EXPECTED_KEYWORD = {
    "verbatim": "VERIFIED",
    "stale": "STALE",
    "wrong": "WRONG",
    "unsupported": "UNSUPPORTED",
}


def _fixture() -> tuple[sqlite3.Connection, str, dict[int, str]]:
    """A fresh store with planted truth + the memory file to audit.

    Returns (conn, memory_text, line_number -> planted category).
    """
    tmp = tempfile.TemporaryDirectory(prefix="jevmory-bench-")
    conn = connect(str(Path(tmp.name) / "bench.db"))
    migrate(conn)

    old = add_fact(
        conn,
        project=PROJECT,
        claim=_OLD_CLAIM,
        category="convention",
        significance=1.2,
        durable_noul=0.9,
        now="2026-09-01T00:00:00Z",
    )
    for claim in _TRUE_CLAIMS:
        add_fact(
            conn,
            project=PROJECT,
            claim=claim,
            category="convention",
            significance=1.2,
            durable_noul=0.9,
            now="2026-09-20T00:00:00Z",
        )
    supersede(conn, old.id, by_fact_id=old.id + 3, now="2026-09-20T00:00:00Z")

    with conn:
        for event_id, role, text in _EVIDENCE_STATEMENTS:
            conn.execute(
                "INSERT OR IGNORE INTO events "
                "(id, project, source, session_id, ts, role, text, "
                " created_at, graded_at) "
                "VALUES (?, ?, 'bench', 'bench-session', "
                "'2026-09-20T00:00:00Z', ?, ?, '2026-09-20T00:00:00Z', NULL)",
                (event_id, PROJECT, role, text),
            )

    lines = [
        "# Project memory — bench fixture",  # heading: not auditable
        "",
        "## Conventions",  # heading: not auditable
        f"- {_TRUE_CLAIMS[0]}",
        f"- {_TRUE_CLAIMS[1]}",
        f"- {_TRUE_CLAIMS[2]}",
        f"- {_OLD_CLAIM}",  # verbatim quote of a SUPERSEDED fact
        f"- {_WRONG_CLAIM}",  # one-word mutation of an active fact
        f"- {_UNSUPPORTED_CLAIM}",  # plausible, never stored
    ]
    memory_text = "\n".join(lines) + "\n"
    planted = {
        4: "verbatim",
        5: "verbatim",
        6: "verbatim",
        7: "stale",
        8: "wrong",
        9: "unsupported",
    }
    return conn, memory_text, planted


class IdealJev:
    """The model that answers every line with its planted disposition.

    Used ONLY for the offline harness self-check: it proves the engine
    → rules → report measurement path, not any real model. Anchored
    lines never reach it (stage 1), so it only ever sees the planted
    stale / wrong / unsupported lines.
    """

    def __init__(self, planted: dict[int, str]) -> None:
        self._planted = planted
        self.calls = 0

    def ask(self, state: Any, questions: Any) -> Any:
        self.calls += 1
        scripted: dict[str, dict[str, Any]] = {}
        for i, entry in enumerate(state["memory_lines"]):
            number = int(entry["id"].split(":")[1])
            disposition = self._planted[number]
            supported = 0.9 if disposition == "keep" else 0.1
            contradicted = (
                0.9 if disposition in ("stale", "wrong") else 0.1
            )
            scripted[f"l{i}_supported"] = {"type": "noul", "noul": supported}
            scripted[f"l{i}_contradicted"] = {
                "type": "noul",
                "noul": contradicted,
            }
            scripted[f"l{i}_disposition"] = {
                "type": "choice",
                "choice": disposition,
                "probabilities": {
                    option: 0.7 if option == disposition else 0.1
                    for option in DISPOSITION_OPTIONS
                },
                "confidence": 0.95,
            }
        return FakeJev(mode=MODE_SCRIPTED, answers=scripted).ask(
            state, questions
        )


# --- measurement ---------------------------------------------------------------


def _stage1_metrics(
    conn: sqlite3.Connection, memory_text: str, planted: dict[int, str]
) -> dict[str, Any]:
    """Anchor-stage precision/recall against planted truth (no model)."""
    parsed = {line.number: line for line in parse_memory_file(memory_text)}
    anchors = anchor_map(
        {line.id: line.text for line in parsed.values()},
        active_facts(conn, PROJECT),
    )
    anchored_numbers = {
        int(line_id.split(":")[1]) for line_id in anchors
    }
    true_numbers = [n for n, cat in planted.items() if cat == "verbatim"]
    error_numbers = [n for n, cat in planted.items() if cat != "verbatim"]

    true_anchored = [n for n in true_numbers if n in anchored_numbers]
    errors_anchored = [n for n in error_numbers if n in anchored_numbers]
    return {
        "verbatim_lines": len(true_numbers),
        "verbatim_anchored": len(true_anchored),
        "verbatim_recall": len(true_anchored) / len(true_numbers),
        "error_lines": len(error_numbers),
        "errors_anchored": len(errors_anchored),
        "false_anchor_rate": len(errors_anchored) / len(error_numbers),
        "routed_to_model": len(error_numbers) - len(errors_anchored),
    }


def _confusion(
    memory_text: str, planted: dict[int, str], report: Any
) -> dict[str, dict[str, int]]:
    """planted category -> predicted keyword -> count."""
    by_number = {v.line.number: v for v in report.lines}
    matrix: dict[str, dict[str, int]] = {
        category: {} for category in _EXPECTED_KEYWORD
    }
    for number, category in planted.items():
        keyword = by_number[number].keyword
        matrix[category][keyword] = matrix[category].get(keyword, 0) + 1
    return matrix


def _accuracy(matrix: dict[str, dict[str, int]]) -> float:
    total = sum(sum(row.values()) for row in matrix.values())
    correct = sum(
        row.get(_EXPECTED_KEYWORD[category], 0)
        for category, row in matrix.items()
    )
    return correct / total if total else 1.0


# --- output --------------------------------------------------------------------


def _print_human(
    stage1: dict[str, Any],
    matrix: dict[str, dict[str, int]] | None,
    *,
    live: bool,
    spend: dict[str, Any] | None = None,
) -> None:
    print("jevmory fidelity benchmark — planted-truth fixture")
    print()
    print("stage 1 (deterministic anchors, zero API):")
    print(f"  verbatim recall:     {stage1['verbatim_anchored']}/"
          f"{stage1['verbatim_lines']}")
    print(f"  false anchors:       {stage1['errors_anchored']}/"
          f"{stage1['error_lines']} (must be 0)")
    print(f"  routed to model:     {stage1['routed_to_model']}")
    print()
    if matrix is None:
        print("full pipeline: skipped (offline mode measures the floor)")
        return
    title = (
        "full pipeline — LIVE model (measurement, not a gate)"
        if live
        else "full pipeline — ideal scripted model (harness self-check)"
    )
    print(title + ":")
    keywords = ["VERIFIED", "STALE", "WRONG", "UNSUPPORTED", "KEEP", "REVIEW"]
    header = f"  {'planted':<12}" + "".join(f"{k:>12}" for k in keywords)
    print(header)
    for category in ("verbatim", "stale", "wrong", "unsupported"):
        row = matrix[category]
        cells = "".join(f"{row.get(k, 0):>12}" for k in keywords)
        print(f"  {category:<12}{cells}")
    print(f"  accuracy: {_accuracy(matrix):.0%}")
    if spend is not None:
        print(
            f"  spend: {spend['api_calls']} api calls, "
            f"{spend['usage_tokens']} tokens"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fidelity benchmark on a planted-truth fixture"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="measure the real model end-to-end (typesafe key or kev)",
    )
    parser.add_argument(
        "--backend",
        choices=("typesafe", "kev"),
        default="typesafe",
        help="live backend (kev = local server, no key; experimental)",
    )
    parser.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    args = parser.parse_args(argv)

    conn, memory_text, planted = _fixture()

    stage1 = _stage1_metrics(conn, memory_text, planted)

    matrix: dict[str, dict[str, int]] | None = None
    spend: dict[str, Any] | None = None
    live = False
    ok = stage1["false_anchor_rate"] == 0.0 and stage1["verbatim_recall"] == 1.0

    if not args.live:
        # harness self-check: ideal scripted model must be perfectly diagonal
        report = run_audit(
            memory_text,
            conn,
            project=PROJECT,
            client=IdealJev(planted),
            memory_file="bench-fixture.md",
        )
        matrix = _confusion(memory_text, planted, report)
        spend = {
            "api_calls": report.api_calls,
            "usage_tokens": report.usage_tokens,
        }
        ok = ok and _accuracy(matrix) == 1.0
    else:
        # LEAD-RUN ONLY: the real model against the same planted fixture
        from jevmory.cli import _backend_choice, _live_client

        try:
            backend = _backend_choice(args.backend)
        except ValueError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 1
        client, endpoint = _live_client(backend)
        if client is None:
            print(
                "bench: live mode needs $TYPESAFE_API_KEY "
                "(or --backend kev for the local server)",
                file=sys.stderr,
            )
            return 1
        live = True
        print(f"live backend: {backend} ({endpoint})", file=sys.stderr)
        report = run_audit(
            memory_text,
            conn,
            project=PROJECT,
            client=client,
            memory_file="bench-fixture.md",
        )
        matrix = _confusion(memory_text, planted, report)
        spend = {
            "api_calls": report.api_calls,
            "usage_tokens": report.usage_tokens,
        }

    conn.close()

    if args.json:
        payload = {
            "stage1": stage1,
            "confusion": matrix,
            "accuracy": None if matrix is None else _accuracy(matrix),
            "live": live,
            "spend": spend,
            "ok": ok,
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_human(stage1, matrix, live=live, spend=spend)

    if not args.live and not ok:
        print(
            "\nbench: FAILED — stage 1 misrouted or the harness "
            "self-check is off-diagonal",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
