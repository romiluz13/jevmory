"""Ask lifecycle tests (M5): resolve (human decision) and
bump_and_expire_asks (distill-time expiry)."""

from __future__ import annotations

import json
import tempfile
import unittest

from jevmory.distill.resolve import (
    KEEP_NEW,
    KEEP_OLD,
    RESOLVE_CHOICES,
    bump_and_expire_asks,
    resolve,
)
from jevmory.memory.facts import (
    STATUS_ACTIVE,
    STATUS_ASK,
    STATUS_RETIRED,
    STATUS_SUPERSEDED,
    add_fact,
    add_link,
    ask_facts,
    bump_ask,
    get_fact,
    mark_ask,
    start_run,
)
from jevmory.memory.schema import connect, migrate
from jevmory.thresholds import ASK_EXPIRY_DISTILLS

NOW = "2026-09-19T02:00:00Z"


class AskLifecycleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(f"{self._tmp.name}/store.db")
        migrate(self.conn)
        self.incumbent = add_fact(
            self.conn, project="p", claim="always deploy on fridays",
            category="convention", significance=2.0, durable_noul=0.9,
            now=NOW,
        )

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def make_ask(self, claim="we never deploy on fridays anymore"):
        challenger = add_fact(
            self.conn, project="p", claim=claim, category="convention",
            significance=2.0, durable_noul=0.85, now=NOW,
        )
        mark_ask(self.conn, challenger.id, now=NOW)
        add_link(self.conn, challenger.id, self.incumbent.id, "contradicts")
        return get_fact(self.conn, challenger.id)

    def run_row(self):
        return self.conn.execute(
            "SELECT kind, stats, error FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def judgments(self):
        return self.conn.execute(
            "SELECT subject_kind, subject_id, question_id, type, answer_json "
            "FROM judgments ORDER BY id"
        ).fetchall()

    # --- resolve ----------------------------------------------------------

    def test_resolve_keep_new_supersedes_incumbent(self):
        ask = self.make_ask()
        final = resolve(self.conn, ask.id, KEEP_NEW, now=NOW)
        self.assertEqual(final.status, STATUS_ACTIVE)
        self.assertEqual(final.id, ask.id)
        self.assertEqual(
            get_fact(self.conn, self.incumbent.id).status, STATUS_SUPERSEDED
        )
        links = self.conn.execute(
            "SELECT fact_id, related_id, relation FROM fact_links "
            "WHERE relation = 'supersedes'"
        ).fetchall()
        self.assertEqual(links, [(ask.id, self.incumbent.id, "supersedes")])
        kind, stats, error = self.run_row()
        self.assertEqual(kind, "resolve")
        self.assertIsNone(error)
        self.assertEqual(
            json.loads(stats),
            {"resolution": "keep_new", "fact_id": ask.id,
             "partner_ids": [self.incumbent.id]},
        )

    def test_resolve_keep_new_supersedes_every_partner(self):
        # S7: an ask can conflict with SEVERAL facts (the engine keeps
        # every over-gate contradicts edge, S2); one keep-new decision
        # settles the whole conflict set, not just the oldest link.
        ask = self.make_ask()
        second = add_fact(
            self.conn, project="p",
            claim="deploy windows are announced tuesday mornings",
            category="convention", significance=2.0, durable_noul=0.9,
            now=NOW,
        )
        add_link(self.conn, ask.id, second.id, "contradicts")
        final = resolve(self.conn, ask.id, KEEP_NEW, now=NOW)
        self.assertEqual(final.status, STATUS_ACTIVE)
        self.assertEqual(
            get_fact(self.conn, self.incumbent.id).status, STATUS_SUPERSEDED
        )
        self.assertEqual(get_fact(self.conn, second.id).status,
                         STATUS_SUPERSEDED)
        supersedes = self.conn.execute(
            "SELECT fact_id, related_id FROM fact_links "
            "WHERE relation = 'supersedes' ORDER BY rowid"
        ).fetchall()
        self.assertEqual(
            supersedes,
            [(ask.id, self.incumbent.id), (ask.id, second.id)],
        )
        kind, stats, error = self.run_row()
        self.assertIsNone(error)
        self.assertEqual(
            json.loads(stats),
            {"resolution": "keep_new", "fact_id": ask.id,
             "partner_ids": [self.incumbent.id, second.id]},
        )

    def test_resolve_keep_old_with_many_partners_retires_challenger_only(self):
        ask = self.make_ask()
        second = add_fact(
            self.conn, project="p",
            claim="deploy windows are announced tuesday mornings",
            category="convention", significance=2.0, durable_noul=0.9,
            now=NOW,
        )
        add_link(self.conn, ask.id, second.id, "contradicts")
        final = resolve(self.conn, ask.id, KEEP_OLD, now=NOW)
        self.assertEqual(final.status, STATUS_RETIRED)
        # every partner untouched: they stayed active all along
        self.assertEqual(
            get_fact(self.conn, self.incumbent.id).status, STATUS_ACTIVE
        )
        self.assertEqual(get_fact(self.conn, second.id).status, STATUS_ACTIVE)

    def test_resolve_keep_old_retires_challenger(self):
        ask = self.make_ask()
        final = resolve(self.conn, ask.id, KEEP_OLD, now=NOW)
        self.assertEqual(final.status, STATUS_RETIRED)
        self.assertEqual(
            get_fact(self.conn, self.incumbent.id).status, STATUS_ACTIVE
        )
        kind, stats, _ = self.run_row()
        self.assertEqual(json.loads(stats)["resolution"], "keep_old")

    def test_resolve_receipt_is_certain_by_fiat(self):
        ask = self.make_ask()
        resolve(self.conn, ask.id, KEEP_NEW, now=NOW)
        rows = self.judgments()
        self.assertEqual(len(rows), 1)
        kind, subject_id, question_id, answer_type, answer_json = rows[0]
        self.assertEqual((kind, subject_id, question_id, answer_type),
                         ("fact", str(ask.id), "resolution", "choice"))
        answer = json.loads(answer_json)
        self.assertEqual(answer["choice"], "keep_new")
        self.assertEqual(answer["confidence"], 1.0)
        self.assertEqual(answer["probabilities"], {"keep_new": 1.0})

    def test_resolve_errors(self):
        with self.assertRaises(ValueError):  # unknown fact
            resolve(self.conn, 999, KEEP_NEW, now=NOW)
        with self.assertRaises(ValueError):  # bad choice vocabulary
            resolve(self.conn, self.make_ask().id, "keep_both", now=NOW)
        with self.assertRaises(ValueError):  # not an ask
            resolve(self.conn, self.incumbent.id, KEEP_NEW, now=NOW)
        orphan = add_fact(  # ask without a contradicts link
            self.conn, project="p", claim="an orphan ask",
            category="convention", significance=1.0, durable_noul=0.9,
            now=NOW,
        )
        mark_ask(self.conn, orphan.id, now=NOW)
        with self.assertRaises(ValueError):
            resolve(self.conn, orphan.id, KEEP_NEW, now=NOW)
        self.assertEqual(RESOLVE_CHOICES, (KEEP_NEW, KEEP_OLD))
        self.assertEqual(len(self.judgments()), 0)  # nothing recorded

    def test_resolved_ask_leaves_the_ask_queue(self):
        ask = self.make_ask()
        resolve(self.conn, ask.id, KEEP_OLD, now=NOW)
        self.assertEqual(ask_facts(self.conn, "p"), [])

    # --- bump_and_expire_asks ----------------------------------------------

    def test_bump_counts_one_per_distill_pass(self):
        ask = self.make_ask()
        run_id = start_run(self.conn, project="p", kind="distill", now=NOW)
        for expected in range(1, ASK_EXPIRY_DISTILLS):
            expired = bump_and_expire_asks(
                self.conn, run_id, project="p", now=NOW
            )
            self.assertEqual(expired, ())
            self.assertEqual(
                get_fact(self.conn, ask.id).ask_seen_count, expected
            )
        self.assertEqual(get_fact(self.conn, ask.id).status, STATUS_ASK)

    def test_expiry_at_limit_is_keep_old(self):
        ask = self.make_ask()
        run_id = start_run(self.conn, project="p", kind="distill", now=NOW)
        for _ in range(ASK_EXPIRY_DISTILLS - 1):
            bump_and_expire_asks(self.conn, run_id, project="p", now=NOW)
        expired = bump_and_expire_asks(self.conn, run_id, project="p", now=NOW)
        self.assertEqual(expired, (ask.id,))
        self.assertEqual(
            get_fact(self.conn, ask.id).status, STATUS_RETIRED
        )
        self.assertEqual(
            get_fact(self.conn, self.incumbent.id).status, STATUS_ACTIVE
        )

    def test_expiry_receipt_under_the_distill_run(self):
        ask = self.make_ask()
        run_id = start_run(self.conn, project="p", kind="distill", now=NOW)
        for _ in range(ASK_EXPIRY_DISTILLS):
            bump_and_expire_asks(self.conn, run_id, project="p", now=NOW)
        rows = self.judgments()
        self.assertEqual(len(rows), 1)
        _, subject_id, question_id, answer_type, answer_json = rows[0]
        self.assertEqual(
            (subject_id, question_id, answer_type),
            (str(ask.id), "expiry", "choice"),
        )
        answer = json.loads(answer_json)
        self.assertEqual(answer["choice"], "keep_old")
        self.assertEqual(answer["confidence"], 1.0)

    def test_non_ask_facts_are_untouched_by_the_pass(self):
        self.make_ask()
        bump_ask(self.conn, self.incumbent.id, now=NOW)  # no-op on active
        run_id = start_run(self.conn, project="p", kind="distill", now=NOW)
        expired = bump_and_expire_asks(self.conn, run_id, project="p", now=NOW)
        self.assertEqual(expired, ())
        self.assertEqual(
            get_fact(self.conn, self.incumbent.id).ask_seen_count, 0
        )

    def test_fresh_ask_starts_at_zero_count(self):
        ask = self.make_ask()
        self.assertEqual(ask.ask_seen_count, 0)


if __name__ == "__main__":
    unittest.main()
