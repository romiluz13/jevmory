"""jevmory.md writer tests (M5): pure rendering (pinned order, sorting,
receipts, stale badge, asks) and the sentinel guard."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jevmory import __version__
from jevmory.dream.writer import (
    CATEGORY_ORDER,
    SENTINEL,
    SENTINEL_CORE,
    AskPair,
    SentinelError,
    render_jevmory,
    write_jevmory,
)
from jevmory.memory.facts import Fact

NOW = "2026-09-19T02:00:00Z"


def make_fact(
    id=1,
    claim="always run tests with uv run pytest",
    category="convention",
    significance=2.0,
    confidence=0.8,
    status="active",
    support_count=1,
    created_at="2026-09-01T00:00:00Z",
    updated_at="2026-09-01T00:00:00Z",
    last_supported_at="2026-09-01T00:00:00Z",
    **overrides,
):
    fields = dict(
        id=id, project="p", claim=claim, context=None, category=category,
        significance=significance, confidence=confidence, status=status,
        source_event_ids=("ev1",), support_count=support_count,
        ask_seen_count=0, created_at=created_at, updated_at=updated_at,
        last_supported_at=last_supported_at,
    )
    fields.update(overrides)
    return Fact(**fields)


class RenderDreamMdTest(unittest.TestCase):
    def test_sentinel_first_and_versioned(self):
        md = render_jevmory([], now=NOW)
        self.assertTrue(md.startswith(SENTINEL + "\n"))
        self.assertIn(f"v{__version__}", md.splitlines()[0])
        self.assertIn(SENTINEL_CORE, SENTINEL)

    def test_empty_renders_header_only(self):
        md = render_jevmory([], now=NOW)
        self.assertEqual(md, SENTINEL + "\n# jevmory.md\n")

    def test_categories_in_pinned_order_without_none(self):
        facts = [
            make_fact(id=1, claim="pitfall claim", category="pitfall"),
            make_fact(id=2, claim="architecture claim", category="architecture"),
            make_fact(id=3, claim="preference claim", category="preference"),
            make_fact(id=4, claim="tooling claim", category="tooling"),
            make_fact(id=5, claim="convention claim", category="convention"),
            make_fact(id=6, claim="uncategorized claim", category="none"),
        ]
        md = render_jevmory(facts, now=NOW)
        headings = [line for line in md.splitlines() if line.startswith("## ")]
        self.assertEqual(
            headings,
            [f"## {c.title()}" for c in CATEGORY_ORDER],
        )
        self.assertNotIn("## None", md)
        # the none-categorized fact carries no section and no entry
        for fact in facts[:5]:
            self.assertIn(fact.claim, md)
        self.assertNotIn(facts[5].claim, md)

    def test_sorted_significance_confidence_then_id(self):
        facts = [
            make_fact(id=1, claim="claim 1", significance=1.0, confidence=0.9),
            make_fact(id=2, claim="claim 2", significance=2.0, confidence=0.6),
            make_fact(id=3, claim="claim 3", significance=2.0, confidence=0.8),
            make_fact(id=4, claim="claim 4", significance=2.0, confidence=0.8),
        ]
        md = render_jevmory(facts, now=NOW)
        order = [
            line.split('"')[1]
            for line in md.splitlines()
            if line.startswith('- **"claim')
        ]
        # significance desc first: the 2.0 group (3, 4 by confidence/id,
        # then 2 at lower confidence) ahead of the 1.0 group
        self.assertEqual(order, ["claim 3", "claim 4", "claim 2", "claim 1"])

    def test_receipt_line_formula_sessions_last_seen(self):
        fact = make_fact(
            claim="always run tests with uv run pytest",
            category="convention",
            significance=1.6,
            confidence=0.8,
            support_count=3,
        )
        md = render_jevmory([fact], now=NOW, sessions_by_fact={fact.id: 3})
        self.assertIn('- **"always run tests with uv run pytest"**', md)
        self.assertIn("`convention · important`", md)  # 1.6 rounds to 2
        self.assertIn("confidence **0.80** (2·|0.900−0.5|)", md)
        self.assertIn("seen in 3 sessions", md)
        self.assertIn("last seen Sep 1", md)
        self.assertNotIn("stale", md)  # 18 days: no badge
        # without the session counts (pure caller, no store): the receipt
        # counts occurrences and says so — it never dresses an event
        # count up as a session count (S6)
        fallback = render_jevmory([fact], now=NOW)
        self.assertIn("seen 3 times", fallback)
        self.assertNotIn("sessions", fallback)

    def test_single_session_wording(self):
        md = render_jevmory(
            [make_fact(support_count=1)], now=NOW, sessions_by_fact={1: 1}
        )
        self.assertIn("seen in 1 session", md)
        self.assertNotIn("seen in 1 sessions", md)
        # fallback without counts: one occurrence, honest singular
        fallback = render_jevmory([make_fact(support_count=1)], now=NOW)
        self.assertIn("seen 1 time", fallback)

    def test_stale_badge_is_visual_only(self):
        fact = make_fact(last_supported_at="2026-05-01T00:00:00Z")
        md = render_jevmory([fact], now=NOW)
        self.assertIn("last seen May 1 · stale", md)
        # same fact, recent now: identical confidence (no decay, ever)
        fresh = render_jevmory(
            [make_fact(last_supported_at="2026-09-18T00:00:00Z")], now=NOW
        )
        self.assertIn("confidence **0.80**", fresh)
        self.assertNotIn("stale", fresh)

    def test_unknown_category_is_rendered_not_hidden(self):
        fact = make_fact(category="legacy")  # data drift
        md = render_jevmory([fact], now=NOW)
        self.assertIn("## Legacy", md)
        self.assertIn(fact.claim, md)

    def test_newlines_in_claims_are_flattened(self):
        fact = make_fact(claim="line one\nline two\r\nline three")
        md = render_jevmory([fact], now=NOW)
        self.assertIn('- **"line one line two line three"**', md)
        self.assertNotIn("one\nline", md)  # claim never spans lines

    def test_significance_words_clamped_to_scale(self):
        md = render_jevmory(
            [make_fact(significance=0.3), make_fact(significance=2.9)],
            now=NOW,
        )
        self.assertIn("`convention · trivial`", md)
        self.assertIn("`convention · critical`", md)

    def test_asks_render_questions_section(self):
        challenger = make_fact(
            id=7, claim="we never deploy on fridays", confidence=0.6,
            status="ask",
        )
        asks = [
            AskPair(fact=challenger, partner_id=1,
                    partner_claim="always deploy on fridays"),
            AskPair(fact=make_fact(id=8, status="ask"),
                    partner_id=None, partner_claim=None),
        ]
        md = render_jevmory([], asks, now=NOW)
        self.assertIn("## Questions for you", md)
        self.assertIn(
            '- "we never deploy on fridays" (confidence 0.60) '
            'vs "always deploy on fridays" — which is current?',
            md,
        )
        self.assertIn("`jevmory resolve 7 --keep-new|--keep-old`", md)
        # a missing partner link still renders with the resolve command
        self.assertIn("`jevmory resolve 8 --keep-new|--keep-old`", md)
        self.assertNotIn("vs", md.split("resolve 8")[1].split("\n")[0])

    def test_render_is_pure_byte_identical(self):
        facts = [make_fact(id=i, claim=f"claim {i}") for i in range(3)]
        asks = [AskPair(fact=make_fact(id=9, status="ask"),
                        partner_id=1, partner_claim="partner")]
        first = render_jevmory(facts, asks, now=NOW)
        second = render_jevmory(facts, asks, now=NOW)
        self.assertEqual(first, second)


class WriteDreamMdTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "jevmory.md"

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_path_writes(self):
        write_jevmory(self.path, render_jevmory([], now=NOW))
        self.assertTrue(self.path.exists())
        self.assertIn(SENTINEL_CORE, self.path.read_text())

    def test_regenerating_own_sentinel_is_fine(self):
        write_jevmory(self.path, render_jevmory([], now=NOW))
        write_jevmory(self.path, render_jevmory([], now=NOW))
        self.assertIn(SENTINEL_CORE, self.path.read_text())

    def test_foreign_file_is_refused(self):
        self.path.write_text("someone's hand-written notes\n")
        with self.assertRaises(SentinelError):
            write_jevmory(self.path, render_jevmory([], now=NOW))
        # untouched
        self.assertEqual(self.path.read_text(), "someone's hand-written notes\n")

    def test_older_version_sentinel_still_regenerates(self):
        old = "<!-- jevmory v0.0.1 sentinel — generated file, do not edit; " \
              "regenerate with `jevmory dream` -->\nold contents\n"
        self.path.write_text(old)
        write_jevmory(self.path, render_jevmory([], now=NOW))
        self.assertIn(f"v{__version__}", self.path.read_text())

    def test_force_overrides_foreign_file(self):
        self.path.write_text("someone's hand-written notes\n")
        write_jevmory(self.path, render_jevmory([], now=NOW), force=True)
        self.assertIn("# jevmory.md", self.path.read_text())


if __name__ == "__main__":
    unittest.main()
