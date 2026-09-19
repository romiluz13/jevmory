"""Audit sub-domain (M4): judge an external memory file (Phase C).

Public surface:

- Parsing: ``MemoryLine``, ``parse_memory_file``, ``strip_inline_markup``.
- Questions/state: ``DISPOSITION_OPTIONS``, ``audit_state``,
  ``phase_c_questions``, ``phase_c_triple``, ``line_entry``.
- Batching: ``plan_phase_c`` (returns judgment ``Batch`` objects).
- Rules: ``LineVerdict``, ``line_verdict``, ``count_dispositions``,
  ``DISPOSITION_KEYWORDS``, ``REVIEW``.
- Report: ``AuditReport``, ``build_report``, ``render_terminal``,
  ``render_md``, ``render_json``.
- Engine: ``run_audit`` (offline-friendly: any object with an
  ``ask(state, questions)`` method).
"""

from jevmory.audit.batch import plan_phase_c
from jevmory.audit.engine import run_audit
from jevmory.audit.memfile import (
    MemoryLine,
    parse_memory_file,
    strip_inline_markup,
)
from jevmory.audit.questions import (
    DISPOSITION_OPTIONS,
    audit_state,
    line_entry,
    phase_c_questions,
    phase_c_triple,
)
from jevmory.audit.report import (
    AuditReport,
    build_report,
    render_json,
    render_md,
    render_terminal,
)
from jevmory.audit.rules import (
    DISPOSITION_KEYWORDS,
    REVIEW,
    LineVerdict,
    count_dispositions,
    line_verdict,
)

__all__ = [
    "DISPOSITION_KEYWORDS",
    "DISPOSITION_OPTIONS",
    "MemoryLine",
    "REVIEW",
    "AuditReport",
    "LineVerdict",
    "audit_state",
    "build_report",
    "count_dispositions",
    "line_entry",
    "line_verdict",
    "parse_memory_file",
    "phase_c_questions",
    "phase_c_triple",
    "plan_phase_c",
    "render_json",
    "render_md",
    "render_terminal",
    "run_audit",
    "strip_inline_markup",
]
