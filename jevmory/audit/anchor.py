"""Audit stage 1 — deterministic anchor check (zero Jev spend, v0.2).

A memory line that exists VERBATIM (normalized) in the project's active
facts is not a judgment call: it is a string match. This module does
that match before any request is built, so anchored lines are graded
for free while everything else goes to stage 2 (the Jev pipeline).

Match rule (``thresholds.ANCHOR_MIN_CHARS``): normalized equality, or
containment where the shorter side is at least ``ANCHOR_MIN_CHARS``
characters. The floor kills the trivial-substring trap — a short line
("we use ruff") would otherwise "verify" against any fact that happens
to contain those words. Below the floor: no anchor, stage 2 grades it.

Determinism: when several facts match a line, the OLDEST (lowest id)
wins — the first time the project recorded the claim. No scores, no
ties, no coin flips; the same store + lines always anchor the same way.
"""

from __future__ import annotations

from jevmory.memory.facts import Fact, normalize_claim
from jevmory.thresholds import ANCHOR_MIN_CHARS


def anchor_line(
    text: str, facts: list[Fact], *, min_chars: int = ANCHOR_MIN_CHARS
) -> Fact | None:
    """Oldest active fact whose normalized claim anchors ``text``, or None."""
    normalized = normalize_claim(text)
    if not normalized:
        return None
    # oldest (lowest id) first regardless of caller order: the first
    # time the project recorded the claim wins — deterministic
    for fact in sorted(facts, key=lambda fact: fact.id):
        fact_normalized = normalize_claim(fact.claim)
        if not fact_normalized:
            continue
        if normalized == fact_normalized:
            return fact
        shorter = min(len(normalized), len(fact_normalized))
        if shorter >= min_chars and (
            normalized in fact_normalized or fact_normalized in normalized
        ):
            return fact
    return None


def anchor_map(
    texts: dict[str, str], facts: list[Fact]
) -> dict[str, Fact]:
    """Map each line id -> its anchor fact (only the anchored lines).

    ``texts`` is keyed by line id (e.g. ``"line:4"``) so the audit engine
    can move anchored ids straight into VERIFIED verdicts and write back
    ``facts.verified_at`` for the matched facts.
    """
    anchors: dict[str, Fact] = {}
    for line_id, text in texts.items():
        fact = anchor_line(text, facts)
        if fact is not None:
            anchors[line_id] = fact
    return anchors
