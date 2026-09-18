"""Named thresholds for every tunable decision in dream.md (PLAN v2).

One module, named constants: the gates, budgets, and bounds that code
applies but never invents inline. Priors, hand-tuned on the fixture
corpus before the demo; every tuning change is recorded in the DDD note
(``.ddd/notes/dream-md.md``) per the threshold policy.

Confidence — one formula (DOMAIN "Confidence", PLAN "Confidence")::

    fact.confidence = clamp01(2 * |durable_noul - 0.5|)

Choice/Score confidences drive their own rules and are never blended
into ``fact.confidence``. Raw Jev answers stay verbatim in the judgments
table so every number in dream.md is reproducible from receipts.
"""

from __future__ import annotations

# --- Ingestion / extraction -------------------------------------------------

# Candidate chunking: sentence-boundary packing (never split a sentence),
# hard cap 600 chars (privacy statement: "redacted candidate quotes
# (<=600 chars)").
CHUNK_MIN_CHARS = 280  # packing aim; tail chunks may be shorter
CHUNK_MAX_CHARS = 600  # hard cap; one sentence longer than this is hard-split at whitespace
MIN_CANDIDATE_CHARS = 12  # sub-12-char chunks are noise ("ok", "keep going"); they burn grading budget

# Verbatim context captured per candidate: 1-2 preceding exchange turns.
CONTEXT_MAX_CHARS = 800

# --- Judgment gates (Phase A/B rules, applied by code not Jev) ---------------

DURABLE_GATE_USER = 0.7  # user statements are decisions
DURABLE_GATE_ASSISTANT = 0.8  # assistant statements are narration
NEAR_MISS_LOW = 0.5  # near-miss band [0.5, gate) is surfaced in status, never silent
SIGNIFICANCE_GATE = 1.0  # Score levels: 0 trivial -> 3 critical
# Phase B pairing bar (PLAN Phase B #3): only candidates with significance
# >= 2 are paired against remembered facts (contradiction questions are the
# v1 cost cap); below it survivors add directly, no pair questions spent.
PAIR_SIGNIFICANCE_GATE = 2
SAME_CLAIM_GATE = 0.8  # >= -> duplicate: bump support_count, add source event id
CONTRADICTION_GATE = 0.6  # >= -> conflict pair goes to Phase B questions
SUPERSEDE_CONFIDENCE = 0.8  # destructive verdicts need high confidence (DOMAIN #3)
ASK_EXPIRY_DREAMS = 3  # asks expire after N consecutive unresolved dreams
STALENESS_BADGE_DAYS = 30  # visual-only badge; no decay in v1 (DOMAIN #8)

# --- Memory (M3) --------------------------------------------------------------

FTS_RETRIEVE_K = 10  # similar active facts fetched from facts_fts per new fact
FTS_KEEP_TOP = 5  # kept after re-rank by token overlap -> Phase B pairs
JACCARD_GATE = 0.6  # code-level dedupe: normalized-hash equal OR Jaccard >= 0.6

# --- Judgment transport / budget (M2) -----------------------------------------

CHARS_PER_TOKEN = 4  # estimator: chars / 4
BATCH_TOKEN_BUDGET = 28_000  # per-request cap (~112k chars of state + questions)
RETRY_MAX_ATTEMPTS = 3  # 429/529: backoff schedule + jitter, then give up gracefully
RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)  # sleep before retry k is schedule[k-1]
# (3 total attempts => the 1s and 2s sleeps are taken; the 4s term is the
# schedule's tail if attempts are ever raised — truncated, never exceeded)
RETRY_JITTER_MAX = 0.25  # uniform jitter added to each backoff sleep
REQUEST_TIMEOUT_SECONDS = 30.0  # per Jev HTTP request

# --- Audit (M4, Phase C) ------------------------------------------------------

# Evidence pool sent with every audit request: the N most recent active
# facts and the N most recently ingested statements (redacted at rest).
AUDIT_EVIDENCE_FACTS = 30
AUDIT_EVIDENCE_STATEMENTS = 40
# A disposition (keep|stale|wrong|unsupported) is DECISIVE only when the
# choice answer's confidence clears this gate; below it the line lands in
# the review band — surfaced, never silent (same stance as near-misses).
AUDIT_DISPOSITION_GATE = 0.6

# --- First-dream cost bound (M5, review R10) -----------------------------------

# Per-dream candidate cap + user-role-first event ordering (both shipped in
# M5's engine) bound the first `dream` cost on long-lived projects. Context:
# review measured 981 candidates from just 26 machine-wide transcripts, 88%
# assistant-role; a user's first dream on a long-lived project is the budget
# spike. The cap counts candidate GROUPS (deduped by normalized text + role);
# events left beyond it stay pending (graded next dream, counted in stats as
# events_deferred). None means uncapped; the value is tuned at the live smoke
# run against the ~40-request budget cap, not guessed in advance.
MAX_CANDIDATES_PER_DREAM: int | None = None


def clamp01(value: float) -> float:
    """Clamp a probability-like value to [0.0, 1.0]."""
    return max(0.0, min(1.0, value))


def fact_confidence(durable_noul: float) -> float:
    """Certainty of durability: ``clamp01(2 * |durable_noul - 0.5|)``.

    The one confidence formula (DOMAIN "Confidence"); durable_noul is the
    verbatim-stored Noul answer for the fact's candidate.
    """
    return clamp01(2.0 * abs(durable_noul - 0.5))
