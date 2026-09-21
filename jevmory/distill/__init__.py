"""Distill engine package (PLAN v2 M5): phases A/B composed into runs.

- ``run_distill`` — one distill over the pending queue (privacy-gated,
  capped, receipted), returning the report the CLI renders;
- ``render_jevmory`` / ``write_jevmory`` — the sentinel-guarded
  artifact agents read;
- ``resolve`` / ``bump_and_expire_asks`` — the ask lifecycle (human
  decisions and expiry);
- ``plan_phase_b`` / rules — the pieces, individually testable.
"""

from jevmory.distill.batch import PairPlan, PhaseBBatch, plan_phase_b
from jevmory.distill.engine import DistillReport, GradingNotEnabledError, run_distill
from jevmory.distill.resolve import (
    KEEP_NEW,
    KEEP_OLD,
    RESOLVE_CHOICES,
    bump_and_expire_asks,
    resolve,
)
from jevmory.distill.rules import (
    ACTION_ASK,
    ACTION_DUPLICATE,
    ACTION_NONE,
    ACTION_SUPERSEDE,
    CandidateVerdict,
    PairVerdict,
    pair_action,
    phase_a_verdict,
)
from jevmory.distill.writer import (
    CATEGORY_ORDER,
    SENTINEL,
    AskPair,
    SentinelError,
    render_jevmory,
    write_jevmory,
)

__all__ = [
    "ACTION_ASK",
    "ACTION_DUPLICATE",
    "ACTION_NONE",
    "ACTION_SUPERSEDE",
    "AskPair",
    "CATEGORY_ORDER",
    "CandidateVerdict",
    "DistillReport",
    "GradingNotEnabledError",
    "KEEP_NEW",
    "KEEP_OLD",
    "PairPlan",
    "PairVerdict",
    "PhaseBBatch",
    "RESOLVE_CHOICES",
    "SENTINEL",
    "SentinelError",
    "bump_and_expire_asks",
    "pair_action",
    "phase_a_verdict",
    "plan_phase_b",
    "render_jevmory",
    "resolve",
    "run_distill",
    "write_jevmory",
]
