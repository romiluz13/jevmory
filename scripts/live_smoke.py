"""Live smoke against the real Jev API (PLAN "Testing strategy").

LEAD-RUN ONLY — the one script in the tree that talks to the network.
The offline suite never touches it (unittest discovers ``test*.py``
only) and it refuses to start without ``$TYPESAFE_API_KEY``.

Two stages, both budget-capped so a smoke can never run away:

``probe`` (default) — ONE Phase A request over three SYNTHETIC
candidates (below: no project data, no store, no opt-in marker — the
point is to prove auth, endpoint, envelope parsing, and usage
accounting against the live API, cheaply).

``--dream`` — ONE real dream run on a project's real store, exactly
like ``dream-md dream``: requires the grading opt-in marker AND the
key, both checked BEFORE any request. Usage lands in the store's
``runs.stats`` receipts like every dream. Budget: ``--cap`` API
requests (default 40, PLAN's smoke ceiling); the cap is enforced by
``CountingTransport`` below — every HTTP request including retries
counts, and the smoke fails closed (run row closed with the error,
events stay queued) rather than overspending.

    python3 scripts/live_smoke.py                        # probe only
    TYPESAFE_API_KEY=... python3 scripts/live_smoke.py --probe
    TYPESAFE_API_KEY=... python3 scripts/live_smoke.py --dream --project .

Exit codes: 0 smoke passed, 1 clean refusal/failure (never a traceback
swallow — errors print with their run-row fate).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dream_md.dream.engine import DreamReport, GradingNotEnabledError, run_dream
from dream_md.dream.writer import (
    SentinelError,
    render_dream_md,
    write_dream_md,
)
from dream_md.ingestion.eventlog import optin_path, project_slug, store_path
from dream_md.judgment.client import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    JevClient,
    JevResponse,
)
from dream_md.judgment.errors import JevError
from dream_md.judgment.questions import phase_a_questions
from dream_md.memory.schema import connect, migrate

# The synthetic probe corpus: durable-sounding, project-free statements.
# Nothing here is or references real project data, so the probe needs no
# marker — it is the same class of text the test fixtures use.
PROBE_CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "id": "probe-c0",
        "role": "user",
        "text": "We standardize on uv for Python dependency management in this repo.",
        "context": "",
    },
    {
        "id": "probe-c1",
        "role": "assistant",
        "text": "The integration tests run against a local SQLite database, never production.",
        "context": "",
    },
    {
        "id": "probe-c2",
        "role": "user",
        "text": "Never commit the .env file; secrets live in the shell environment only.",
        "context": "",
    },
)

DEFAULT_CAP = 40  # PLAN: "capped at ~40 API requests"


class BudgetExhausted(JevError):
    """The smoke's request cap was hit — fail closed, never overspend."""


class CountingTransport:
    """Wraps a transport, counting EVERY request (retries included).

    Raises ``BudgetExhausted`` (a ``JevError``) when a call would exceed
    the cap, so the dream engine closes the run row with the error and
    leaves every event queued — the same recovery path as a rate limit.
    """

    def __init__(
        self,
        cap: int,
        inner: Callable[[str, dict[str, str], bytes, float], bytes],
    ) -> None:
        if cap < 1:
            raise ValueError("cap must be >= 1")
        self.cap = cap
        self.inner = inner
        self.calls = 0

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> bytes:
        if self.calls >= self.cap:
            raise BudgetExhausted(
                f"live smoke budget of {self.cap} API requests exhausted — "
                "stopping before this request; any run row is closed with "
                "this error and events stay queued"
            )
        self.calls += 1
        return self.inner(url, headers, body, timeout)


def probe_state(count: int) -> dict[str, Any]:
    """Phase A state over the first ``count`` synthetic candidates."""
    if not 1 <= count <= len(PROBE_CANDIDATES):
        raise ValueError(f"count must be 1..{len(PROBE_CANDIDATES)}")
    return {
        "project_context": {"name": "live-smoke-probe", "synthetic": True},
        "candidates": [dict(c) for c in PROBE_CANDIDATES[:count]],
    }


def run_probe(
    key: str,
    *,
    cap: int = DEFAULT_CAP,
    count: int = 3,
    transport: Callable[[str, dict[str, str], bytes, float], bytes]
    | None = None,
) -> tuple[JevResponse, CountingTransport]:
    """One live Phase A request over synthetic candidates.

    ``transport`` is injectable so the offline test drives the exact
    request -> envelope -> strict-parse path without a network.
    """
    budget = CountingTransport(cap, transport or _default_transport())
    client = JevClient(key, transport=budget)
    state = probe_state(count)
    questions = phase_a_questions(count)
    response = client.ask(state, questions)
    return response, budget


@dataclass
class DreamSmokeResult:
    """One dream smoke's outcome: the engine report + smoke bookkeeping."""

    report: DreamReport
    artifact: str  # dream.md path written (sentinel rules applied)
    budget_calls: int  # requests counted against the cap


def run_dream_smoke(
    project_dir: str,
    key: str,
    *,
    cap: int = DEFAULT_CAP,
    force: bool = False,
    transport: Callable[[str, dict[str, str], bytes, float], bytes]
    | None = None,
):
    """One real dream run, budget-capped. Marker + key gate FIRST.

    Mirrors ``dream-md dream``: same opt-in gate, same store, same
    dream.md sentinel rules — the only addition is the request cap.
    """
    marker = optin_path(project_dir)
    if not marker.exists():
        # BEFORE any client construction or egress (privacy by
        # architecture; the engine re-checks with enforce_optin=True)
        raise GradingNotEnabledError(project_dir, str(marker))

    budget = CountingTransport(cap, transport or _default_transport())
    client = JevClient(key, transport=budget)
    conn = connect(store_path(project_dir))
    migrate(conn)
    try:
        report = run_dream(
            conn,
            project=project_slug(project_dir),
            client=client,
            project_dir=project_dir,
            project_context={"name": Path(project_dir).name},
            enforce_optin=True,
        )
    finally:
        conn.close()
    artifact = Path(project_dir) / "dream.md"
    write_dream_md(
        artifact,
        render_dream_md(
            report.facts,
            report.ask_pairs,
            sessions_by_fact=dict(report.sessions_by_fact),
        ),
        force=force,
    )
    return DreamSmokeResult(
        report=report, artifact=str(artifact), budget_calls=budget.calls
    )


def _default_transport():
    """The stdlib POST transport JevClient uses when none is injected.

    Private-symbol import on purpose: the budget wrapper must wrap the
    REAL transport, and this is the only way to reach it explicitly.
    """
    from dream_md.judgment.client import _urllib_transport

    return _urllib_transport


# --- printing ------------------------------------------------------------------


def _fmt(answer: Any) -> str:
    kind = type(answer).__name__
    if kind == "NoulAnswer":
        return f"noul {answer.noul:.2f}"
    if kind == "ChoiceAnswer":
        return f"choice {answer.choice} conf {answer.confidence:.2f}"
    return f"score {answer.score:.2f} conf {answer.confidence:.2f}"


def _print_probe(response: JevResponse, budget: CountingTransport) -> None:
    print(f"endpoint:     {DEFAULT_ENDPOINT}")
    print(f"model:        {DEFAULT_MODEL}")
    print(f"budget:       {budget.calls}/{budget.cap} requests")
    for qid in sorted(response.answers):
        print(f"  {qid:<22} {_fmt(response.answers[qid])}")
    usage = response.usage
    print(
        f"usage:        {usage.input_tokens} in / {usage.output_tokens} out "
        f"({usage.input_tokens + usage.output_tokens} total)"
    )
    print("probe: OK — auth, envelope, strict parse, usage all live-verified")


def _print_dream(result: DreamSmokeResult) -> None:
    report = result.report
    run = report.run_id
    print(f"run:          #{run}" if run is not None else "run:          -")
    print(
        f"events:       {report.events_graded} graded, "
        f"{report.events_deferred} deferred"
    )
    print(
        f"candidates:   {report.candidates_graded} groups "
        f"({report.occurrences} occurrences)"
    )
    print(
        f"facts:        {len(report.facts_added)} added, "
        f"{report.duplicates} duplicates"
    )
    if report.asks:
        print(f"asks:         {list(report.asks)} — dream-md resolve <id>")
    print(
        f"api:          {report.api_calls} calls, {report.usage_tokens} tokens "
        f"({result.budget_calls} counted against the cap)"
    )
    print(
        "receipts:     usage + every answer are in the store's runs/"
        f"judgments tables (run #{run})"
    )
    print(f"dream.md:     {result.artifact}")


# --- entry ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="live_smoke.py",
        description="Lead-run live smoke against the real Jev API (budget-capped).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--probe", action="store_true", help="3-candidate synthetic probe (default)"
    )
    mode.add_argument(
        "--dream", action="store_true", help="one real dream run (needs opt-in)"
    )
    parser.add_argument("--project", metavar="DIR", default=os.getcwd())
    parser.add_argument(
        "--cap", type=int, default=DEFAULT_CAP, help=f"max API requests (default {DEFAULT_CAP})"
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=3,
        help="probe size, 1-3 (default 3)",
    )
    args = parser.parse_args(argv)

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        print(
            "live-smoke: $TYPESAFE_API_KEY is not set — this script is the "
            "only thing in the tree that needs it",
            file=sys.stderr,
        )
        return 1
    if args.cap < 1:
        print("live-smoke: --cap must be >= 1", file=sys.stderr)
        return 1

    try:
        if args.dream:
            project = os.path.realpath(os.path.expanduser(args.project))
            print(f"live smoke — dream on {project} (cap {args.cap} requests)")
            result = run_dream_smoke(project, key, cap=args.cap)
            _print_dream(result)
            print("dream smoke: OK")
            return 0
        print(
            f"live smoke — probe: {args.candidates} synthetic candidates, "
            f"one request (cap {args.cap})"
        )
        response, budget = run_probe(key, cap=args.cap, count=args.candidates)
        _print_probe(response, budget)
        return 0
    except GradingNotEnabledError as exc:
        print(f"live-smoke: {exc}", file=sys.stderr)
        return 1
    except JevError as exc:
        print(
            f"live-smoke: {exc}\n  (run row closed with this error where one "
            "was open; events stay queued — nothing is lost)",
            file=sys.stderr,
        )
        return 1
    except SentinelError as exc:
        print(f"live-smoke: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
