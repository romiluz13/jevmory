"""Dream engine package (PLAN v2 M5): phases A/B composed into runs.

- ``run_dream`` — one dream over the pending queue (privacy-gated,
  capped, receipted), returning the report the CLI renders;
- ``render_jevmory`` / ``write_jevmory`` — the sentinel-guarded
  artifact agents read;
- ``resolve`` / ``bump_and_expire_asks`` — the ask lifecycle (human
  decisions and expiry);
- ``plan_phase_b`` / rules — the pieces, individually testable.
"""

from jevmory.dream.batch import PairPlan, PhaseBBatch, plan_phase_b
from jevmory.dream.engine import DreamReport, GradingNotEnabledError, run_dream
from jevmory.dream.resolve import (
    KEEP_NEW,
    KEEP_OLD,
    RESOLVE_CHOICES,
    bump_and_expire_asks,
    resolve,
)
from jevmory.dream.rules import (
    ACTION_ASK,
    ACTION_DUPLICATE,
    ACTION_NONE,
    ACTION_SUPERSEDE,
    CandidateVerdict,
    PairVerdict,
    pair_action,
    phase_a_verdict,
)
from jevmory.dream.writer import (
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
    "render_jevmory",
    "resolve",
    "run_dream",
    "write_jevmory",
]
