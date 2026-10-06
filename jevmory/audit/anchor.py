"""Audit stage 1 — deterministic anchor check (zero Jev spend, v0.2).

A memory line that exists VERBATIM (normalized) in the project's active
facts is not a judgment call: it is a string match. This module does
that match before any request is built, so anchored lines are graded
for free while everything else goes to stage 2 (the Jev pipeline).

Match rule: normalized whole-claim equality only. A substring can omit
a negation or condition, so containment always needs semantic review.
Matching records provenance, never current factual correctness.

Determinism: when several facts match a line, the OLDEST (lowest id)
wins — the first time the project recorded the claim. No scores, no
ties, no coin flips; the same store + lines always anchor the same way.
"""

from __future__ import annotations

from jevmory.memory.facts import Fact, normalize_claim


def anchor_line(text: str, facts: list[Fact]) -> Fact | None:
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
    return None


def anchor_map(
    texts: dict[str, str], facts: list[Fact]
) -> dict[str, Fact]:
    """Map each line id -> its anchor fact (only the anchored lines).

    ``texts`` is keyed by line id (e.g. ``"line:4"``) so the audit engine
    can move anchored ids straight into MATCHED verdicts without
    refreshing any fact verification timestamp.
    """
    anchors: dict[str, Fact] = {}
    for line_id, text in texts.items():
        fact = anchor_line(text, facts)
        if fact is not None:
            anchors[line_id] = fact
    return anchors
