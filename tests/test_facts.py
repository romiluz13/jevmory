"""Fact-store tests (M3): lifecycle, dedupe, FTS retrieve + maintenance.

All timestamps are pinned strings so every assertion is deterministic.
The store lives in a fresh temp db per test (schema v2 via migrate).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

from dream_md.memory.facts import (
    STATUS_ACTIVE,
    STATUS_ASK,
    STATUS_RETIRED,
    STATUS_SUPERSEDED,
    Fact,
    active_facts,
    add_fact,
    add_link,
    bump_ask,
    bump_support,
    claim_hash,
    claim_tokens,
    find_code_duplicate,
    finish_run,
    get_fact,
    jaccard,
    mark_ask,
    normalize_claim,
    record_judgment,
    retrieve_similar,
    retire,
    start_run,
    supersede,
)
from dream_md.memory.schema import connect, migrate
from dream_md.thresholds import JACCARD_GATE

T0 = "2026-09-19T00:00:00Z"
T1 = "2026-09-19T00:01:00Z"
T2 = "2026-09-19T00:02:00Z"
T3 = "2026-09-19T00:03:00Z"


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(os.path.join(self._tmp.name, "store.db"))
        migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def add(self, claim, *, category="convention", significance=1.2,
            durable=0.9, project="proj", context=None, event_ids=("ev1",),
            now=T0):
        return add_fact(
            self.conn, project=project, claim=claim, category=category,
            significance=significance, durable_noul=durable, context=context,
            source_event_ids=event_ids, now=now,
        )


class AddFactTest(StoreTestCase):
    def test_add_fact_fields_and_fts_row(self):
        fact = self.add(
            "Always run make lint before pushing to main.",
            context="[assistant] understood", event_ids=("ev1", "ev2"), now=T0,
        )
        self.assertEqual(
            fact,
            Fact(
                id=fact.id, project="proj",
                claim="Always run make lint before pushing to main.",
                context="[assistant] understood", category="convention",
                significance=1.2, confidence=0.8, status=STATUS_ACTIVE,
                source_event_ids=("ev1", "ev2"), support_count=1,
                ask_seen_count=0, created_at=T0, updated_at=T0,
                last_supported_at=T0,
            ),
        )
        fts = self.conn.execute(
            "SELECT claim FROM facts_fts WHERE rowid = ?", (fact.id,)
        ).fetchone()
        self.assertEqual(fts[0], fact.claim)

    def test_confidence_is_the_one_formula(self):
        cases = {0.9: 0.8, 0.5: 0.0, 0.0: 1.0, 1.0: 1.0, 0.65: 0.3, 0.35: 0.3}
        for durable, expected in cases.items():
            fact = self.add("x " * 10, durable=durable, event_ids=())
            self.assertAlmostEqual(fact.confidence, expected, places=9)

    def test_get_fact_missing_returns_none(self):
        self.assertIsNone(get_fact(self.conn, 999))


class LifecycleTest(StoreTestCase):
    def test_bump_support_appends_event_and_count(self):
        fact = self.add("Use bun not npm.", event_ids=("ev1",))
        bumped = bump_support(
            self.conn, fact.id, source_event_id="ev2", now=T1
        )
        self.assertEqual(bumped.support_count, 2)
        self.assertEqual(bumped.source_event_ids, ("ev1", "ev2"))
        self.assertEqual(bumped.last_supported_at, T1)
        self.assertEqual(bumped.updated_at, T1)

    def test_bump_support_idempotent_per_event_id(self):
        fact = self.add("Use bun not npm.", event_ids=("ev1",))
        once = bump_support(self.conn, fact.id, source_event_id="ev1", now=T1)
        self.assertEqual(once.support_count, 1)  # same event: no double count
        self.assertEqual(once.source_event_ids, ("ev1",))

    def _fts_match_ids(self, token: str) -> set[int]:
        rows = self.conn.execute(
            'SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?', (token,)
        ).fetchall()
        return {row[0] for row in rows}

    def test_supersede_removes_fts_keeps_row_and_links(self):
        old = self.add("Deploy with git push heroku main.")
        new = self.add("Deploy with the platform CLI, not git push.")
        self.assertEqual(self._fts_match_ids("heroku"), {old.id})
        superseded = supersede(
            self.conn, old.id, by_fact_id=new.id, now=T1
        )
        self.assertEqual(superseded.status, STATUS_SUPERSEDED)
        self.assertEqual(superseded.updated_at, T1)
        # row kept for provenance
        self.assertIsNotNone(get_fact(self.conn, old.id))
        # external-content FTS: deleted rowids no longer MATCH
        # (a plain rowid= select would read through to the content table)
        self.assertEqual(self._fts_match_ids("heroku"), set())
        # link recorded: new supersedes old
        link = self.conn.execute(
            "SELECT fact_id, related_id, relation FROM fact_links"
        ).fetchone()
        self.assertEqual(link, (new.id, old.id, "supersedes"))

    def test_retire_removes_fts_keeps_row(self):
        fact = self.add("Always commit directly to main.")
        self.assertEqual(self._fts_match_ids("commit"), {fact.id})
        retired = retire(self.conn, fact.id, now=T1)
        self.assertEqual(retired.status, STATUS_RETIRED)
        self.assertIsNotNone(get_fact(self.conn, fact.id))
        self.assertEqual(self._fts_match_ids("commit"), set())

    def test_mark_ask_resets_seen_count(self):
        fact = self.add("Use tabs for indentation.")
        self.conn.execute(
            "UPDATE facts SET ask_seen_count = 2 WHERE id = ?", (fact.id,)
        )
        asked = mark_ask(self.conn, fact.id, now=T1)
        self.assertEqual(asked.status, STATUS_ASK)
        self.assertEqual(asked.ask_seen_count, 0)
        self.assertEqual(asked.updated_at, T1)

    def test_bump_ask_counts_only_ask_status(self):
        asking = self.add("Fact A.")
        other = self.add("Fact B.")
        mark_ask(self.conn, asking.id, now=T1)
        self.assertEqual(bump_ask(self.conn, asking.id, now=T2), 1)
        self.assertEqual(bump_ask(self.conn, asking.id, now=T3), 2)
        # active facts never accumulate
        self.assertEqual(bump_ask(self.conn, other.id, now=T2), 0)

    def test_missing_fact_raises(self):
        for call in (
            lambda: bump_support(self.conn, 99, source_event_id="ev"),
            lambda: supersede(self.conn, 99, by_fact_id=1),
            lambda: retire(self.conn, 99),
            lambda: mark_ask(self.conn, 99),
            lambda: bump_ask(self.conn, 99),
        ):
            with self.assertRaises(ValueError):
                call()

    def test_active_facts_scoped_and_ordered(self):
        first = self.add("First fact.")
        self.add("Other project fact.", project="other")
        retired = self.add("Second fact, later retired.")
        retire(self.conn, retired.id)
        third = self.add("Third fact.")
        self.assertEqual(
            [f.id for f in active_facts(self.conn, "proj")],
            [first.id, third.id],
        )

    def test_add_link_deduped_by_unique_constraint(self):
        add_link(self.conn, 1, 2, "contradicts")
        add_link(self.conn, 1, 2, "contradicts")  # INSERT OR IGNORE
        count = self.conn.execute(
            "SELECT COUNT(*) FROM fact_links"
        ).fetchone()[0]
        self.assertEqual(count, 1)


class DedupeTest(StoreTestCase):
    def test_normalize_claim_case_and_whitespace(self):
        self.assertEqual(
            normalize_claim("  Always   RUN\nmake lint  "),
            "always run make lint",
        )

    def test_claim_hash_insensitive_to_case_and_whitespace(self):
        self.assertEqual(
            claim_hash("Always run MAKE lint"),
            claim_hash("always   run make  lint\n"),
        )

    def test_claim_tokens(self):
        self.assertEqual(
            claim_tokens("Run `make-lint`, please! A 42-step flow."),
            ["run", "make", "lint", "please", "42", "step", "flow"],
        )

    def test_jaccard_values(self):
        self.assertEqual(jaccard(["a", "b"], ["a", "b"]), 1.0)
        self.assertEqual(jaccard(["a"], ["b"]), 0.0)
        self.assertEqual(jaccard([], ["a"]), 0.0)
        self.assertEqual(jaccard(["a"], []), 0.0)
        self.assertAlmostEqual(
            jaccard(["a", "b", "c"], ["a", "b", "d"]), 2 / 4
        )

    def test_hash_equal_duplicate_scores_one(self):
        fact = self.add("Always run make lint before pushing to main.")
        found = find_code_duplicate(
            self.conn, "ALWAYS   run make lint\nbefore pushing to main."
        )
        self.assertIsNotNone(found)
        self.assertEqual(found[0].id, fact.id)
        self.assertEqual(found[1], 1.0)

    def test_jaccard_duplicate_above_gate(self):
        fact = self.add("Always run make lint before pushing to main.")
        found = find_code_duplicate(
            self.conn, "Always run make lint before pushing to main branch."
        )
        self.assertIsNotNone(found)
        self.assertEqual(found[0].id, fact.id)
        self.assertGreaterEqual(found[1], JACCARD_GATE)

    def test_below_gate_is_not_a_duplicate(self):
        self.add("Always run make lint before pushing to main.")
        self.assertIsNone(
            find_code_duplicate(
                self.conn, "Gardening needs different tools entirely."
            )
        )

    def test_only_active_facts_are_duplicate_targets(self):
        kept = self.add("Use bun for package installs.")
        retired = self.add("Deployments go through the platform CLI.")
        retire(self.conn, retired.id)
        # near-identical to the RETIRED fact, dissimilar from the kept one
        found = find_code_duplicate(
            self.conn, "Deployments go through the platform CLI nightly."
        )
        # retired fact must not match; the kept claim has no overlap
        self.assertIsNone(found)
        # sanity: it WOULD have matched had the fact stayed active
        self.assertGreater(
            jaccard(
                claim_tokens("Deployments go through the platform CLI nightly."),
                claim_tokens("Deployments go through the platform CLI."),
            ),
            JACCARD_GATE,
        )
        # and a near-identical claim still matches the ACTIVE fact
        found_kept = find_code_duplicate(
            self.conn, "Use bun for package installs, always."
        )
        self.assertEqual(found_kept[0].id, kept.id)

    def test_best_jaccard_match_wins(self):
        weak = self.add("Run make lint sometimes before pushing.")
        strong = self.add("Always run make lint before pushing to main.")
        found = find_code_duplicate(
            self.conn, "Always run make lint before pushing to main now."
        )
        self.assertEqual(found[0].id, strong.id)
        self.assertGreater(
            found[1],
            jaccard(
                claim_tokens("Always run make lint before pushing to main now."),
                claim_tokens(weak.claim),
            ),
        )


class RetrieveTest(StoreTestCase):
    QUERY = "Run make lint before pushing to main."

    def seed(self):
        a = self.add("Always run make lint before pushing to main.")
        b = self.add("Run make lint before pushing.")
        c = self.add("Make lint is the linting target.")
        d = self.add("Pushing to main requires review.")
        e = self.add("SQLite is preferred for stores.")
        f = self.add("The linter runs via make lint.")
        return a, b, c, d, e, f

    def test_retrieve_ranks_by_overlap_keeps_top5(self):
        a, b, c, d, e, f = self.seed()
        similar = retrieve_similar(self.conn, self.QUERY)
        # e shares no tokens -> never retrieved; the rest rank by overlap.
        # c and f tie at 2/11 — ties break to the oldest fact.
        self.assertEqual(
            [fact.id for fact in similar], [a.id, b.id, d.id, c.id, f.id]
        )

    def test_retrieve_k_and_keep_bounds(self):
        self.seed()
        few = retrieve_similar(self.conn, self.QUERY, k=3, keep=2)
        self.assertLessEqual(len(few), 2)
        overlap_ids = {fact.id for fact in few}
        for fact in few:
            self.assertGreater(
                jaccard(claim_tokens(self.QUERY), claim_tokens(fact.claim)),
                0.0,
            )
        # k=3 bounds the pool before re-rank
        self.assertLessEqual(len(overlap_ids), 3)

    def test_retrieve_only_active_facts(self):
        a, b, c, d, e, f = self.seed()
        retire(self.conn, a.id)
        supersede(self.conn, b.id, by_fact_id=c.id)
        similar = retrieve_similar(self.conn, self.QUERY)
        # c/f tie at 2/11 -> oldest first; a and b left the index
        self.assertEqual([fact.id for fact in similar], [d.id, c.id, f.id])

    def test_retrieve_no_tokens_returns_empty(self):
        self.add("Run make lint before pushing to main.")
        self.assertEqual(retrieve_similar(self.conn, "?? !!"), [])

    def test_fts_healthy_after_deletes(self):
        a, b, c, d, e, f = self.seed()
        retire(self.conn, a.id)
        retire(self.conn, b.id)
        # querying must not raise and must not return ghosts
        similar = retrieve_similar(self.conn, self.QUERY)
        self.assertNotIn(a.id, [fact.id for fact in similar])
        self.assertNotIn(b.id, [fact.id for fact in similar])
        # and the index still serves remaining rows
        hit = retrieve_similar(self.conn, "SQLite preferred for stores")
        self.assertEqual([fact.id for fact in hit], [e.id])


class ReceiptsTest(StoreTestCase):
    def test_record_judgment_stores_verbatim_answer(self):
        run_id = start_run(self.conn, project="proj", kind="dream", now=T0)
        jid = record_judgment(
            self.conn, run_id=run_id, subject_kind="candidate",
            subject_id="ev0001", question_id="c0_durable", type="noul",
            answer={"type": "noul", "noul": 0.92}, now=T1,
        )
        row = self.conn.execute(
            "SELECT run_id, subject_kind, subject_id, question_id, type, "
            "answer_json, created_at FROM judgments WHERE id = ?",
            (jid,),
        ).fetchone()
        self.assertEqual(
            row,
            (run_id, "candidate", "ev0001", "c0_durable", "noul",
             json.dumps({"type": "noul", "noul": 0.92}), T1),
        )

    def test_start_and_finish_run_roundtrip(self):
        run_id = start_run(self.conn, project="proj", kind="audit", now=T0)
        finish_run(
            self.conn, run_id,
            stats={"api_calls": 2, "usage": 312, "dropped_low_durable": 1},
            now=T1,
        )
        row = self.conn.execute(
            "SELECT project, kind, started_at, finished_at, stats, error "
            "FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(row[0], "proj")
        self.assertEqual(row[1], "audit")
        self.assertEqual(row[2], T0)
        self.assertEqual(row[3], T1)
        self.assertEqual(
            json.loads(row[4]),
            {"api_calls": 2, "usage": 312, "dropped_low_durable": 1},
        )
        self.assertIsNone(row[5])

    def test_finish_run_records_error(self):
        run_id = start_run(self.conn, project="proj", kind="dream", now=T0)
        finish_run(self.conn, run_id, error="JevRetryExhausted", now=T1)
        row = self.conn.execute(
            "SELECT stats, error FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1], "JevRetryExhausted")


if __name__ == "__main__":
    unittest.main()
