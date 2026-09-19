"""Planted-error demo (PLAN M7): one command, a guaranteed screenshot.

    python3 demo/run_demo.py

What happens, offline and deterministic:

1. a throwaway store is created in a temp dir (``$JEV_MD_HOME`` is
   NOT touched — your real stores and markers stay exactly as they are);
2. ``demo/transcript.jsonl`` (a synthetic Claude session) is ingested
   into it — six statements of evidence, redacted at rest like always;
3. ``demo/MEMORY.md`` (five lines, three planted errors) is audited
   against that evidence and the terminal report is printed — the same
   renderer ``jev-md audit`` uses.

The asker is ``PlantedJev`` below: a deterministic, offline stand-in
whose judgments are pinned to the planted lines (matched by substring,
never by line number) so the report ALWAYS shows one stale line, one
wrong line, and one unsupported line, with receipts. It exists so the
report shape is demonstrable anywhere, any time, zero-cost. Real runs
grade with the real Jev API:

    jev-md init --enable-grading
    jev-md ingest --transcript demo/transcript.jsonl --project demo
    jev-md audit demo/MEMORY.md --project demo

Exit code 0 always (it is a demo; failure means the fixture broke, and
the test suite pins that it does not).
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

# Runnable straight from a source checkout (python3 demo/run_demo.py)
# without installing: the repo root shadows any installed jev_md so
# the demo always exercises the tree it ships with.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from jev_md.audit.engine import run_audit
from jev_md.audit.questions import DISPOSITION_OPTIONS
from jev_md.audit.report import render_terminal
from jev_md.ingestion.claude import parse_claude_transcript
from jev_md.ingestion.eventlog import EventLog, project_slug, store_path
from jev_md.judgment.answers import parse_answer
from jev_md.judgment.client import DEFAULT_MODEL, JevResponse, Usage
from jev_md.judgment.errors import JevProtocolError
from jev_md.judgment.questions import Question
from jev_md.memory.schema import connect, migrate

DEMO_DIR = Path(__file__).resolve().parent
MEMORY_FILE = DEMO_DIR / "MEMORY.md"
TRANSCRIPT = DEMO_DIR / "transcript.jsonl"

# Planted judgments: substring of the memory LINE (case-insensitive) ->
# (supported, contradicted, disposition, confidence). Matched in order;
# anything else is keep. Values are what a real run would conclude from
# the transcript evidence: the stale line used to be true, the wrong
# line is contradicted head-on, the unsupported line is about something
# no evidence ever mentions.
PLANTED: tuple[tuple[str, tuple[float, float, str, float]], ...] = (
    ("pytest", (0.07, 0.93, "wrong", 0.93)),
    ("bun", (0.34, 0.71, "stale", 0.82)),
    ("retry", (0.06, 0.05, "unsupported", 0.74)),
)
_KEEP = (0.92, 0.04, "keep", 0.90)
_PEAK = 0.7  # one option at the peak, the rest share — sums to 1.0

_LINE_Q_RE = re.compile(r"^l(\d+)_(supported|contradicted|disposition)$")


class PlantedJev:
    """Offline asker with pinned judgments (see module docstring).

    Duck-type compatible with ``JevClient.ask``: same arguments, same
    ``JevResponse`` return, answers through the SAME strict parser —
    off-spec planted values fail loudly instead of rendering.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[Any, dict[str, Question]]] = []
        self.call_count = 0

    def ask(
        self, state: Any, questions: Mapping[str, Question]
    ) -> JevResponse:
        self.call_count += 1
        self.requests.append((state, dict(questions)))
        lines = state["memory_lines"]
        raw: dict[str, Any] = {}
        for qid, question in questions.items():
            match = _LINE_Q_RE.match(qid)
            if match is None:
                raise JevProtocolError(f"not a Phase C question id: {qid!r}")
            i, kind = int(match.group(1)), match.group(2)
            supported, contradicted, disposition, confidence = _planted_for(
                lines[i]["text"]
            )
            if kind in ("supported", "contradicted"):
                value = supported if kind == "supported" else contradicted
                raw[qid] = {"type": "noul", "noul": value}
            else:
                options = list(question.criteria)
                rest = (1.0 - _PEAK) / (len(options) - 1)
                raw[qid] = {
                    "type": "choice",
                    "choice": disposition,
                    "probabilities": {
                        option: (
                            _PEAK if option == disposition else rest
                        )
                        for option in options
                    },
                    "confidence": confidence,
                }
        parsed = {
            qid: parse_answer(questions[qid].type, raw[qid]) for qid in questions
        }
        return JevResponse(
            answers=parsed,
            usage=Usage(input_tokens=742, output_tokens=96),
            model=DEFAULT_MODEL,
        )


def _planted_for(line_text: str) -> tuple[float, float, str, float]:
    lowered = line_text.lower()
    for substring, planted in PLANTED:
        if substring in lowered:
            return planted
    return _KEEP


def build_demo_report(home: Path | None = None) -> str:
    """Run the whole demo against ``home`` (a fresh temp dir by default).

    Importable so the test suite pins the planted output without any
    subprocess; ``main`` only adds the preamble around this.
    """
    cleanup = home is None
    if home is None:
        home = Path(tempfile.mkdtemp(prefix="jev-md-demo-"))
    try:
        parsed = parse_claude_transcript(str(TRANSCRIPT))
        EventLog.for_project(DEMO_DIR, home=home).append(parsed)
        conn = connect(store_path(DEMO_DIR, home=home))
        migrate(conn)
        try:
            report = run_audit(
                MEMORY_FILE.read_text(encoding="utf-8"),
                conn,
                project=project_slug(str(DEMO_DIR)),
                client=PlantedJev(),
                memory_file="demo/MEMORY.md",
                project_context={"name": "planted-error demo"},
            )
        finally:
            conn.close()
        return render_terminal(report)
    finally:
        if cleanup:
            _rmtree(home)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    print("jev-md planted-error demo — deterministic, offline, zero cost")
    print()
    print("fixture:   demo/MEMORY.md (5 memory lines, 3 planted errors)")
    print("evidence:  demo/transcript.jsonl (6 statements, ingested into a")
    print("           throwaway store in a temp dir; your ~/.jev-md is")
    print("           never touched)")
    print("grading:   simulated — judgments pinned to the planted lines so")
    print("           the errors are guaranteed present. Live equivalent,")
    print("           same report shape:")
    print()
    print("  jev-md init --enable-grading")
    print("  jev-md ingest --transcript demo/transcript.jsonl --project demo")
    print("  jev-md audit demo/MEMORY.md --project demo")
    print()
    print(build_demo_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
