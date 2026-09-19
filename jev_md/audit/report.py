"""Audit report renderers (PLAN M4: "screenshot-shaped terminal report").

Three shapes off one ``AuditReport``:

- **terminal** (default): the screenshot lead — a summary one-liner in
  the demo's voice ("3 stale lines and 1 wrong one"), then an aligned
  table of the PROBLEM lines only (stale / wrong / unsupported /
  review) in file order, then a receipts footer. All-keep files get
  the one-liner and a receipts footer, no table.
- **md** (``--md``): a full markdown table of EVERY line (keep lines
  included) — the complete, linkable record.
- **json** (``--json``): the machine-readable report, every number
  reproducible from the judgments receipts it references.

Formatting is deterministic (fixed widths, no env-dependent terminal
size) so tests — and screenshots — never flake.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from jev_md.audit.rules import LineVerdict, REVIEW, count_dispositions

# Terminal layout constants (presentation, not decision thresholds).
_CLAIM_WIDTH = 44  # claim column width before ellipsis
_VERDICT_WIDTH = 11  # "UNSUPPORTED"
_NUMBER_WIDTH = 4
_PROBLEM_KEYWORDS = ("STALE", "WRONG", "UNSUPPORTED", REVIEW)

_ELLIPSIS = "…"


@dataclass(frozen=True)
class AuditReport:
    """Everything the renderers need; built by the engine."""

    memory_file: str  # display name (path or "<memory>")
    lines: tuple[LineVerdict, ...] = ()
    run_id: int | None = None
    api_calls: int = 0
    usage_tokens: int = 0
    evidence_facts: int = 0
    evidence_statements: int = 0
    counts: dict[str, int] = field(default_factory=dict)

    def problem_lines(self) -> tuple[LineVerdict, ...]:
        """Non-keep lines in file order — the terminal table's rows."""
        return tuple(
            sorted(
                (v for v in self.lines if v.keyword in _PROBLEM_KEYWORDS),
                key=lambda v: v.line.number,
            )
        )

    def summary_line(self) -> str:
        """The one-liner in the demo's voice."""
        phrases = (
            ("STALE", "stale line"),
            ("WRONG", "wrong line"),
            ("UNSUPPORTED", "unsupported line"),
            (REVIEW, "line needing review"),
        )
        problems = [
            f"{self.counts[keyword]} {_plural(self.counts[keyword], noun)}"
            for keyword, noun in phrases
            if self.counts.get(keyword)
        ]
        if not problems:
            return (
                f"all {len(self.lines)} lines hold up — "
                "nothing stale, nothing wrong"
            )
        summary = "your memory has " + ", ".join(problems)
        keeps = sum(1 for v in self.lines if v.keyword == "KEEP")
        if keeps:
            summary += f"; {keeps} keep"
        return summary


def render_terminal(report: AuditReport) -> str:
    """The screenshot-shaped report: aligned columns + receipts."""
    out: list[str] = []
    out.append(f"jev-md audit — {report.memory_file}")
    out.append(report.summary_line())
    out.append("")
    problems = report.problem_lines()
    if problems:
        header = (
            f"{'LINE':>{_NUMBER_WIDTH}}  "
            f"{'VERDICT':<{_VERDICT_WIDTH}}  "
            f"{'CONF':>4}  "
            f"{'SUPP':>4}  "
            f"{'CONTRA':>6}  "
            f"CLAIM"
        )
        out.append(header)
        for verdict in problems:
            claim = _clip(verdict.line.text, _CLAIM_WIDTH)
            out.append(
                f"{verdict.line.number:>{_NUMBER_WIDTH}}  "
                f"{verdict.keyword:<{_VERDICT_WIDTH}}  "
                f"{verdict.confidence:>4.2f}  "
                f"{verdict.supported:>4.2f}  "
                f"{verdict.contradicted:>6.2f}  "
                f"{claim}"
            )
        out.append("")
    out.append(_receipts_footer(report))
    return "\n".join(out)


def render_md(report: AuditReport) -> str:
    """Full markdown report — every line, keep or not."""
    out: list[str] = []
    out.append(f"# jev-md audit — {report.memory_file}")
    out.append("")
    out.append(report.summary_line())
    out.append("")
    out.append("| line | section | verdict | confidence | supported | contradicted | claim |")
    out.append("|---:|---|---|---:|---:|---:|---|")
    for verdict in report.lines:
        section = verdict.line.section or ""
        claim = verdict.line.text.replace("|", "\\|")
        out.append(
            f"| {verdict.line.number} | {section} | {verdict.keyword} "
            f"| {verdict.confidence:.2f} | {verdict.supported:.2f} "
            f"| {verdict.contradicted:.2f} | {claim} |"
        )
    out.append("")
    out.append(_receipts_footer(report))
    return "\n".join(out)


def render_json(report: AuditReport) -> str:
    """The machine-readable report (every number traceable to receipts)."""
    payload: dict[str, Any] = {
        "memory_file": report.memory_file,
        "summary": report.summary_line(),
        "run_id": report.run_id,
        "counts": dict(report.counts),
        "api_calls": report.api_calls,
        "usage_tokens": report.usage_tokens,
        "evidence": {
            "facts": report.evidence_facts,
            "statements": report.evidence_statements,
        },
        "lines": [
            {
                "line": verdict.line.number,
                "id": verdict.line.id,
                "section": verdict.line.section,
                "claim": verdict.line.text,
                "disposition": verdict.disposition,
                "decisive": verdict.decisive,
                "keyword": verdict.keyword,
                "confidence": verdict.confidence,
                "supported": verdict.supported,
                "contradicted": verdict.contradicted,
            }
            for verdict in report.lines
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def build_report(
    *,
    memory_file: str,
    verdicts: Sequence[LineVerdict],
    run_id: int | None = None,
    api_calls: int = 0,
    usage_tokens: int = 0,
    evidence_facts: int = 0,
    evidence_statements: int = 0,
) -> AuditReport:
    """Assemble an ``AuditReport`` (counts derived, never hand-passed)."""
    return AuditReport(
        memory_file=memory_file,
        lines=tuple(verdicts),
        run_id=run_id,
        api_calls=api_calls,
        usage_tokens=usage_tokens,
        evidence_facts=evidence_facts,
        evidence_statements=evidence_statements,
        counts=count_dispositions(verdicts),
    )


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else noun + "s"


def _receipts_footer(report: AuditReport) -> str:
    parts = [
        f"receipts: run {report.run_id}"
        if report.run_id is not None
        else "receipts: no run (nothing graded)",
        f"{report.api_calls} api calls",
        f"{report.usage_tokens} tokens",
        (
            f"evidence: {report.evidence_facts} facts, "
            f"{report.evidence_statements} statements"
        ),
    ]
    return " · ".join(parts)


def _clip(text: str, width: int) -> str:
    """Ellipsize to ``width`` chars (deterministic, unicode-aware)."""
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + _ELLIPSIS
