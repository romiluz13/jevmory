"""Dream engine package (PLAN v2 M5): phases A/B composed into runs.

- ``run_dream`` — one dream over the pending queue (privacy-gated,
  capped, receipted), returning the report the CLI renders;
- ``render_jev_md`` / ``write_jev_md`` — the sentinel-guarded
  artifact agents read;
- ``resolve`` / ``bump_and_expire_asks`` — the ask lifecycle (human
  decisions and expiry);
- ``plan_phase_b`` / rules — the pieces, individually testable.
"""

from jev_md.dream.batch import PairPlan, PhaseBBatch, plan_phase_b
from jev_md.dream.engine import DreamReport, GradingNotEnabledError, run_dream
from jev_md.dream.resolve import (
    KEEP_NEW,
    KEEP_OLD,
    RESOLVE_CHOICES,
    bump_and_expire_asks,
    resolve,
)
from jev_md.dream.rules import (
    ACTION_ASK,
    ACTION_DUPLICATE,
    ACTION_NONE,
    ACTION_SUPERSEDE,
    CandidateVerdict,
    PairVerdict,
    pair_action,
    phase_a_verdict,
)
from jev_md.dream.writer import (
    CATEGORY_ORDER,
    SENTINEL,
    AskPair,
    SentinelError,
    render_jev_md,
    write_jev_md,
)

__all__ = [
    "ACTION_ASK",
    "ACTION_DUPLICATE",
    "ACTION_NONE",
    "ACTION_SUPERSEDE",
    "AskPair",
    "CATEGORY_ORDER",
    "CandidateVerdict",
    "DreamReport",
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
    "render_jev_md",
    "resolve",
    "run_dream",
    "write_jev_md",
]
