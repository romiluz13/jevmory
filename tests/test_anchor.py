"""Stage-1 anchor tests (v0.2): whole-claim equality,
substring refusal, oldest-match determinism, no coin flips."""

from __future__ import annotations

import unittest

from jevmory.audit.anchor import anchor_line, anchor_map
from jevmory.memory.facts import Fact


def make_fact(fact_id, claim, status="active"):
    return Fact(
        id=fact_id, project="p", claim=claim, context=None,
        category="convention", significance=1.2, confidence=0.8,
        status=status, source_event_ids=("ev1",), support_count=1,
        ask_seen_count=0, created_at="2026-09-01T00:00:00Z",
        updated_at="2026-09-01T00:00:00Z", last_supported_at=None,
    )


LONG = "always run the full test suite before pushing to main"
# same claim, whitespace/case drift only — normalization handles it
LONG_DRIFT = "Always  run the full test suite   before pushing to main"


class AnchorLineTest(unittest.TestCase):
    def test_exact_match_after_normalization(self):
        facts = [make_fact(1, LONG)]
        self.assertIs(
            anchor_line(LONG_DRIFT, facts), facts[0]
        )

    def test_containment_requires_semantic_review(self):
        # the memory line CONTAINS the fact (fact is the shorter side)
        fact = make_fact(1, LONG)
        line = f"reminder: {LONG} — every time, no exceptions"
        self.assertIsNone(anchor_line(line, [fact]))
        # the fact CONTAINS the line (line is the shorter side, still
        # at/above the floor)
        wrapper = make_fact(2, f"note: {LONG} — pinned by the team")
        self.assertIsNone(anchor_line(LONG, [wrapper]))

    def test_short_substring_never_anchors(self):
        # the trivial-substring trap: without the floor, "we use ruff"
        # would "verify" against any fact containing those words
        facts = [make_fact(1, "we use ruff and mypy, configured in pyproject")]
        self.assertIsNone(anchor_line("we use ruff", facts))

    def test_short_whole_claim_can_match_without_a_substring_floor(self):
        fact = make_fact(1, "we use ruff")
        self.assertIs(anchor_line("we use ruff", [fact]), fact)

    def test_oldest_match_wins_deterministically(self):
        older, newer = make_fact(1, LONG), make_fact(2, LONG_DRIFT)
        self.assertIs(anchor_line(LONG, [newer, older]), older)

    def test_empty_text_never_anchors(self):
        self.assertIsNone(anchor_line("", [make_fact(1, LONG)]))
        self.assertIsNone(anchor_line("   ", [make_fact(1, LONG)]))


class AnchorMapTest(unittest.TestCase):
    def test_only_anchored_ids_are_mapped(self):
        facts = [make_fact(1, LONG)]
        texts = {
            "line:4": LONG_DRIFT,        # anchors
            "line:5": "we use ruff",     # too short: no anchor
            "line:6": "nothing matches", # no anchor
        }
        anchors = anchor_map(texts, facts)
        self.assertEqual(set(anchors), {"line:4"})
        self.assertIs(anchors["line:4"], facts[0])


if __name__ == "__main__":
    unittest.main()
