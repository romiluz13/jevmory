"""Dream engine tests (M5): end-to-end runs against a real store with
FakeJev — gate, queue/cap, phases A/B, routing, ask lifecycle, errors."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dream_md.dream import GradingNotEnabledError, run_dream
from dream_md.dream.resolve import KEEP_NEW
from dream_md.dream.writer import render_dream_md
from dream_md.ingestion.eventlog import event_id, optin_path, store_path
from dream_md.ingestion.extract import chunk_text
from dream_md.judgment.errors import JevRetryExhausted
from dream_md.judgment.fake import FakeJev
from dream_md.memory.facts import (
    STATUS_ASK,
    STATUS_SUPERSEDED,
    get_fact,
)
from dream_md.memory.schema import connect, migrate
from dream_md.thresholds import (
    ASK_EXPIRY_DREAMS,
    MAX_CANDIDATES_PER_DREAM,
    NEAR_MISS_LOW,
    PAIR_SIGNIFICANCE_GATE,
)

NOW = "2026-09-19T02:00:00Z"
LATER = {
    n: f"2026-09-{n:02d}T02:00:00Z" for n in range(19, 26)
}

INCUMBENT = "always deploy on fridays before noon cutoff"
DUPLICATE_WORDING = "friday deploys land before noon, always"
UNRELATED = "deploy windows are announced tuesday mornings in standup"
CHALLENGER = "we never deploy on fridays anymore, we deploy tuesday mornings"


def scripted_phase_a(prefix="c0", category="convention", significance=2.0):
    options = ["preference", "tooling", "architecture", "pitfall",
               "convention", "none"]
    return {
        f"{prefix}_durable": {"type": "noul", "noul": 0.9},
        f"{prefix}_category": {
            "type": "choice", "choice": category,
            "probabilities": {
                option: (0.75 if option == category else 0.05)
                for option in options
            },
            "confidence": 0.9,
        },
        f"{prefix}_significance": {
            "type": "score", "score": significance,
            "legend": {"0": "trivial", "1": "useful", "2": "important",
                       "3": "critical"},
            "probabilities": {"0": 0.05, "1": 0.05, "2": 0.8, "3": 0.1},
            "confidence": 0.9,
        },
    }


def scripted_pair(prefix="p0_0", same=0.15, contra=0.9,
                  verdict="old_stands", confidence=0.3):
    probabilities = {"new_overrides": 0.1, "old_stands": 0.1, "unclear": 0.1}
    probabilities[verdict] = 0.8
    return {
        f"{prefix}_same_claim": {"type": "noul", "noul": same},
        f"{prefix}_contradicts": {"type": "noul", "noul": contra},
        f"{prefix}_verdict": {
            "type": "choice", "choice": verdict,
            "probabilities": probabilities, "confidence": confidence,
        },
    }


class DreamEngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.proj = self.home / "proj"
        self.proj.mkdir()
        marker = optin_path(self.proj, home=self.home)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("opted in\n")
        self.conn = connect(store_path(self.proj, home=self.home))
        migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    # --- fixtures ----------------------------------------------------------

    def add_event(self, text, role="user", session="s1"):
        self.conn.execute(
            "INSERT INTO events (id, project, source, session_id, ts, role, "
            "text, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (event_id(text, session), "p", "test", session,
             "2026-09-01T00:00:00Z", role, text, NOW),
        )
        self.conn.commit()
        return event_id(text, session)

    def dream(self, client, now=NOW, **kwargs):
        return run_dream(
            self.conn, project="p", client=client,
            project_dir=str(self.proj), home=self.home, now=now, **kwargs
        )

    def run_row(self):
        return self.conn.execute(
            "SELECT kind, stats, error, finished_at FROM runs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def judgments(self, run_id=None):
        sql = ("SELECT run_id, subject_kind, subject_id, question_id, type, "
               "answer_json FROM judgments")
        if run_id is not None:
            return self.conn.execute(
                sql + " WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return self.conn.execute(sql + " ORDER BY id").fetchall()

    def pending_count(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE graded_at IS NULL"
        ).fetchone()[0]

    # --- privacy gate --------------------------------------------------------

    def test_no_marker_refuses_and_leaves_no_trace(self):
        other = self.home / "locked"  # no opt-in marker for this slug
        other.mkdir()
        self.add_event("we prefer uv over pip for this project")
        with self.assertRaises(GradingNotEnabledError) as raised:
            run_dream(
                self.conn, project="p", client=FakeJev(),
                project_dir=str(other), home=self.home, now=NOW,
            )
        self.assertIn("dream-md init --enable-grading", str(raised.exception))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0
        )
        self.assertEqual(self.pending_count(), 1)  # stays queued

    def test_marker_enables_grading_for_that_project(self):
        self.add_event("we prefer uv over pip for this project")
        report = self.dream(FakeJev())
        self.assertEqual(report.api_calls, 1)
        self.assertEqual(self.pending_count(), 0)

    # --- queue drain and grouping -------------------------------------------

    def test_normal_mode_groups_occurrences_and_adds_facts(self):
        repeat = "always run tests with uv run pytest in this repo"
        first = self.add_event(repeat)
        self.add_event("we migrated to bun, npm is no longer the runtime")
        self.add_event("ok")  # below MIN_CANDIDATE_CHARS: no candidates
        second = self.add_event(repeat, session="s2")  # cross-session repeat

        report = self.dream(FakeJev())
        self.assertEqual(report.run_id, 1)
        self.assertEqual(report.api_calls, 1)
        self.assertEqual(report.usage_tokens, 360)
        self.assertEqual(report.events_graded, 4)
        self.assertEqual(report.events_deferred, 0)
        self.assertEqual(report.candidates_graded, 2)  # deduped groups
        self.assertEqual(report.occurrences, 3)
        self.assertEqual(report.facts_added, (1, 2))
        self.assertEqual(report.near_misses, 0)
        self.assertEqual(self.pending_count(), 0)
        self.assertEqual(len(report.facts), 2)

        # the cross-session repeat accrued support on ONE fact
        repeated = get_fact(self.conn, 1)
        self.assertEqual(repeated.support_count, 2)
        self.assertEqual(
            set(repeated.source_event_ids), {first, second}
        )
        self.assertEqual(get_fact(self.conn, 2).support_count, 1)

        kind, stats, error, finished = self.run_row()
        self.assertEqual((kind, error, finished), ("dream", None, NOW))
        stats = json.loads(stats)
        self.assertEqual(stats["api_calls"], 1)
        self.assertEqual(stats["usage"], 360)
        self.assertEqual(stats["events_graded"], 4)
        self.assertEqual(stats["candidates_graded"], 2)
        self.assertEqual(stats["occurrences"], 3)
        self.assertEqual(stats["facts_added"], [1, 2])
        self.assertEqual(stats["duplicates_code"], 0)

    def test_events_without_candidates_are_graded_not_requeued(self):
        self.add_event("ok")  # no candidates, deterministic empty yield
        report = self.dream(FakeJev())
        self.assertEqual(report.events_graded, 1)
        self.assertEqual(report.candidates_graded, 0)
        self.assertEqual(report.api_calls, 0)
        self.assertEqual(report.facts_added, ())
        self.assertEqual(self.pending_count(), 0)

    def test_context_includes_already_graded_turns(self):
        # S4: a pending candidate's context is the real conversation —
        # including turns an earlier dream already graded. Building
        # context from the pending tail only starved new candidates of
        # the session's earlier decisions.
        self.add_event("we settled on postgres fifteen for the audit service")
        self.dream(FakeJev())  # grades it into fact 1
        self.add_event("the retention window is ninety days, not thirty")
        report = self.dream(
            FakeJev(mode="scripted", answers=scripted_phase_a(significance=1.0))
        )
        self.assertEqual(report.events_graded, 1)  # only the new event
        self.assertEqual(report.facts_added, (2,))
        self.assertEqual([f.id for f in report.facts], [1, 2])
        fact = get_fact(self.conn, 2)
        self.assertIsNotNone(fact.context)
        self.assertIn("postgres fifteen", fact.context)  # graded turn as context

    def test_sessions_by_fact_counts_distinct_sessions(self):
        # S6: "seen in N sessions" must count SESSIONS, not source
        # events — one session observing a claim across several turns
        # is ONE session; support_count says three, sessions say two.
        claim = "always run migrations from the makefile target in this repo"
        self.add_event(claim)                        # s1, first occurrence
        self.add_event(claim.upper(), session="s1")  # s1 again, new wording
        self.add_event(claim, session="s2")          # a second session
        report = self.dream(FakeJev())
        fact = get_fact(self.conn, 1)
        self.assertEqual(fact.support_count, 3)      # three source events
        self.assertEqual(report.sessions_by_fact[fact.id], 2)  # two sessions
        md = render_dream_md(
            report.facts,
            report.ask_pairs,
            sessions_by_fact=report.sessions_by_fact,
        )
        self.assertIn("seen in 2 sessions", md)
        self.assertNotIn("seen in 3 sessions", md)

    def test_state_reaches_client_with_default_context(self):
        self.add_event("we prefer uv over pip for this project")
        client = FakeJev()
        self.dream(client)
        state, questions = client.requests[0]
        self.assertEqual(state["project_context"], {"name": "p"})
        self.assertEqual(len(state["candidates"]), 1)
        self.assertEqual(
            sorted(questions), ["c0_category", "c0_durable",
                                "c0_significance"],
        )

    def test_candidate_receipts_verbatim_with_stable_keys(self):
        event = self.add_event("we prefer uv over pip for this project")
        report = self.dream(FakeJev())
        rows = self.judgments(report.run_id)
        self.assertEqual(len(rows), 3)
        by_question = {}
        for (_, kind, subject_id, question_id, answer_type,
             answer_json) in rows:
            self.assertEqual(kind, "candidate")
            self.assertEqual(subject_id, event)
            self.assertEqual(json.loads(answer_json)["type"], answer_type)
            by_question[question_id] = json.loads(answer_json)
        self.assertEqual(
            sorted(by_question), ["category", "durable", "significance"]
        )
        self.assertEqual(by_question["durable"], {"type": "noul", "noul": 0.9})
        self.assertEqual(by_question["category"]["choice"], "convention")

    # --- multi-chunk statements (review S1) -----------------------------------

    def test_multi_chunk_statement_routes_every_chunk(self):
        # S1 regression: a statement long enough to chunk into several
        # candidates shares ONE event id across distinct groups. Verdict
        # routing is by group key (normalized text + role); event-id
        # routing silently kept only the LAST chunk's verdict — the
        # earlier chunk was graded, receipted, then discarded without
        # its fact ever being created.
        sentence_a = (
            "deployment for this service always runs through the blue "
            "green pipeline with a two minute bake before traffic shifts "
            "over, and the release checklist requires a signed changelog "
            "entry attached to every rollout ticket before the on call "
            "engineer approves the shift during weekday mornings, and "
            "rollback is a single revert of the traffic shift flag"
        )
        sentence_b = (
            "database backups are taken nightly to the cold storage bucket "
            "with thirty five day retention, restored quarterly during the "
            "disaster recovery rehearsal, and the restore drill logs go to "
            "the audit folder that compliance reviews every quarter, and "
            "the rehearsal results are filed with the quarterly audit"
        )
        statement = f"{sentence_a} {sentence_b}"
        chunks = chunk_text(statement)
        self.assertEqual(len(chunks), 2)  # fixture shape: two chunks, one event
        event = self.add_event(statement)
        report = self.dream(FakeJev())
        self.assertEqual(report.events_graded, 1)  # ONE event...
        self.assertEqual(report.candidates_graded, 2)  # ...TWO groups
        self.assertEqual(report.occurrences, 2)
        self.assertEqual(report.facts_added, (1, 2))  # both chunks became facts
        self.assertEqual(
            [fact.claim for fact in report.facts], chunks
        )
        # receipts: three questions per chunk, all provenance-tagged with
        # the shared event id (subject = provenance, routing = group key)
        rows = [
            row for row in self.judgments(report.run_id)
            if row[1] == "candidate"
        ]
        self.assertEqual(len(rows), 6)
        self.assertEqual({row[2] for row in rows}, {event})
        self.assertEqual(self.pending_count(), 0)

    # --- cap and ordering (review R10) --------------------------------------

    def test_cap_is_user_first_and_progressive(self):
        for n in range(3):
            self.add_event(
                f"assistant narration number {n} about progress",
                role="assistant", session=f"a{n}",
            )
        for n in range(4):
            self.add_event(
                f"user decision number {n} about tooling choices",
                session=f"u{n}",
            )
        first = self.dream(FakeJev(), max_candidates=2)
        self.assertEqual(first.events_graded, 2)
        self.assertEqual(first.events_deferred, 5)
        self.assertEqual(first.candidates_graded, 2)
        roles = [row[0] for row in self.conn.execute(
            "SELECT role FROM events WHERE graded_at IS NOT NULL "
            "ORDER BY rowid"
        )]
        self.assertEqual(roles, ["user", "user"])  # decisions first

        second = self.dream(FakeJev(), max_candidates=2)
        self.assertEqual(second.events_graded, 2)
        self.assertEqual(second.events_deferred, 3)
        third = self.dream(FakeJev(), max_candidates=2)
        self.assertEqual(third.events_graded, 2)
        self.assertEqual(third.events_deferred, 1)
        self.assertEqual(self.pending_count(), 1)

    def test_zero_spend_writes_no_run_row(self):
        client = FakeJev()
        report = self.dream(client)
        self.assertIsNone(report.run_id)
        self.assertEqual(report.api_calls, 0)
        self.assertEqual(client.call_count, 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0
        )
        self.assertEqual(self.judgments(), [])

    def test_nonpositive_cap_is_rejected(self):
        with self.assertRaises(ValueError):
            self.dream(FakeJev(), max_candidates=0)

    # --- phase A verdicts ----------------------------------------------------

    def test_adversarial_mode_drops_with_near_misses_surfaced(self):
        self.add_event("assistant chatter about maybe trying something")
        self.add_event("another assistant narration of progress",
                       role="assistant")
        report = self.dream(FakeJev(mode="adversarial"))
        self.assertEqual(report.events_graded, 2)
        self.assertEqual(report.dropped_low_durable, 2)
        self.assertEqual(report.near_misses, 2)  # 0.5 is in the band
        self.assertGreaterEqual(NEAR_MISS_LOW, 0.5)
        self.assertEqual(report.facts_added, ())
        self.assertEqual(len(report.facts), 0)
        stats = json.loads(self.run_row()[1])
        self.assertEqual(stats["near_misses"], 2)

    def test_low_significance_drops_are_counted(self):
        scripted = scripted_phase_a(significance=0.0)  # trivial
        self.add_event("we slightly prefer tabs in this one file")
        report = self.dream(FakeJev(mode="scripted", answers=scripted))
        self.assertEqual(report.dropped_low_significance, 1)
        self.assertEqual(report.facts_added, ())
        self.assertEqual(json.loads(self.run_row()[1])
                         ["dropped_low_significance"], 1)

    def test_below_pairing_bar_adds_directly_with_zero_pair_spend(self):
        # significance ~1 < PAIR_SIGNIFICANCE_GATE: plain add, no Phase B
        self.add_event(INCUMBENT)
        client = FakeJev(mode="scripted",
                         answers=scripted_phase_a(significance=1.0))
        report = self.dream(client)
        self.assertEqual(report.facts_added, (1,))
        self.assertEqual(report.pair_skipped_low_significance, 1)
        self.assertEqual(client.call_count, 1)  # Phase A only
        asked = set(client.requests[0][1])
        self.assertTrue(all(qid.startswith("c0_") for qid in asked))
        self.assertLess(1.0, PAIR_SIGNIFICANCE_GATE)

    # --- code-level dedupe ----------------------------------------------------

    def test_normalized_repeat_bumps_support_without_jev_pairs(self):
        self.add_event(INCUMBENT)
        self.dream(FakeJev(mode="scripted", answers=scripted_phase_a()))
        self.add_event(INCUMBENT.upper(), session="s9")  # same normalized
        client = FakeJev(mode="scripted", answers=scripted_phase_a())
        report = self.dream(client)
        self.assertEqual(report.facts_added, ())
        self.assertEqual(report.duplicates, 1)
        fact = get_fact(self.conn, 1)
        self.assertEqual(fact.support_count, 2)
        self.assertEqual(len(fact.source_event_ids), 2)
        stats = json.loads(self.run_row()[1])
        self.assertEqual(stats["duplicates_code"], 1)
        self.assertEqual(stats["duplicates_jev"], 0)

    # --- phase B actions (scripted) -------------------------------------------

    def seed_fact(self, claim, significance=2.0):
        """Add one fact via a dream. The scripted pair answers mark the
        new candidate unrelated to any earlier fact (action none), so
        seeding never trips dedupe/conflict logic."""
        self.add_event(claim)
        return self.dream(FakeJev(
            mode="scripted",
            answers={
                **scripted_phase_a(significance=significance),
                **scripted_pair(same=0.05, contra=0.05, verdict="unclear",
                                confidence=0.5),
            },
        ))

    def pair_dream(self, answers):
        self.add_event(CHALLENGER)
        return self.dream(FakeJev(mode="scripted", answers=answers))

    def test_jev_duplicate_bumps_support_no_new_fact(self):
        self.seed_fact(INCUMBENT)
        self.add_event(DUPLICATE_WORDING)
        report = self.dream(FakeJev(
            mode="scripted",
            answers={**scripted_phase_a(),
                     **scripted_pair(same=0.95, contra=0.05)},
        ))
        self.assertEqual(report.facts_added, ())
        self.assertEqual(report.duplicates, 1)
        fact = get_fact(self.conn, 1)
        self.assertEqual(fact.support_count, 2)
        self.assertEqual(json.loads(self.run_row()[1])["duplicates_jev"], 1)

    def test_unrelated_pair_adds_plainly(self):
        self.seed_fact(INCUMBENT)
        report = self.pair_dream({
            **scripted_phase_a(),
            **scripted_pair(same=0.05, contra=0.05, verdict="unclear",
                            confidence=0.5),
        })
        self.assertEqual(report.facts_added, (2,))
        self.assertEqual(report.duplicates, 0)
        self.assertEqual(report.superseded, ())
        self.assertEqual(report.asks, ())
        self.assertEqual(get_fact(self.conn, 1).status, "active")

    def test_decisive_override_supersedes_incumbent(self):
        self.seed_fact(INCUMBENT)
        report = self.pair_dream({
            **scripted_phase_a(),
            **scripted_pair(same=0.1, contra=0.9, verdict="new_overrides",
                            confidence=0.9),
        })
        self.assertEqual(report.facts_added, (2,))
        self.assertEqual(report.superseded, (1,))
        self.assertEqual(get_fact(self.conn, 1).status,
                         STATUS_SUPERSEDED)
        self.assertEqual(get_fact(self.conn, 2).status, "active")
        links = self.conn.execute(
            "SELECT fact_id, related_id, relation FROM fact_links "
            "WHERE relation = 'supersedes'"
        ).fetchall()
        self.assertEqual(links, [(2, 1, "supersedes")])
        # superseded fact leaves active memory (writer never sees it)
        self.assertEqual([f.id for f in report.facts], [2])

    def test_low_confidence_conflict_becomes_ask(self):
        self.seed_fact(INCUMBENT)
        report = self.pair_dream({
            **scripted_phase_a(),
            **scripted_pair(verdict="old_stands", confidence=0.3),
        })
        self.assertEqual(report.asks, (2,))
        challenger = get_fact(self.conn, 2)
        self.assertEqual(challenger.status, STATUS_ASK)
        self.assertEqual(challenger.ask_seen_count, 0)  # not bumped by its own dream
        self.assertEqual(get_fact(self.conn, 1).status, "active")
        links = self.conn.execute(
            "SELECT fact_id, related_id, relation FROM fact_links "
            "WHERE relation = 'contradicts'"
        ).fetchall()
        self.assertEqual(links, [(2, 1, "contradicts")])
        # report carries the ask pair for the writer's Questions section
        [(pair)] = report.ask_pairs
        self.assertEqual((pair.fact.id, pair.partner_id,
                          pair.partner_claim), (2, 1, INCUMBENT))
        self.assertEqual([f.id for f in report.facts], [1])  # incumbent only

    def test_duplicate_beats_supersede_across_partners(self):
        self.seed_fact(INCUMBENT)
        self.seed_fact(UNRELATED)
        # facts array is retrieval-ranked: p0_0/p0_1 both exist
        self.add_event(CHALLENGER)
        answers = {
            **scripted_phase_a(),
            **scripted_pair("p0_0", same=0.15, contra=0.9,
                            verdict="new_overrides", confidence=0.9),
            **scripted_pair("p0_1", same=0.9, contra=0.05),
        }
        report = self.dream(FakeJev(mode="scripted", answers=answers))
        self.assertEqual(report.duplicates, 1)
        self.assertEqual(report.superseded, ())
        self.assertEqual(report.facts_added, ())
        self.assertEqual(get_fact(self.conn, 1).status, "active")
        self.assertEqual(get_fact(self.conn, 2).status, "active")
        self.assertEqual(get_fact(self.conn, 2).support_count, 2)

    def test_duplicate_winner_keeps_runner_up_conflict_edges(self):
        # S2: the winning pair records its decision; every OTHER
        # over-gate pair keeps its contradicts edge — the edge is
        # evidence, not a decision. Old code dropped the runner-up
        # evidence entirely (no link, no ask, nothing).
        self.seed_fact(INCUMBENT)
        self.seed_fact(UNRELATED)
        self.add_event(CHALLENGER)
        answers = {
            **scripted_phase_a(),
            # vs fact 1: conflict, not decisively overridden -> ask action
            **scripted_pair("p0_0", same=0.15, contra=0.9,
                            verdict="old_stands", confidence=0.3),
            # vs fact 2: same claim -> duplicate wins the disposition
            **scripted_pair("p0_1", same=0.9, contra=0.05),
        }
        report = self.dream(FakeJev(mode="scripted", answers=answers))
        self.assertEqual(report.duplicates, 1)
        self.assertEqual(report.asks, ())  # duplicate won: no ask created
        self.assertEqual(get_fact(self.conn, 1).status, "active")
        self.assertEqual(get_fact(self.conn, 2).support_count, 2)
        # the merged claim carries its conflict evidence against fact 1
        links = self.conn.execute(
            "SELECT fact_id, related_id, relation FROM fact_links "
            "WHERE relation = 'contradicts'"
        ).fetchall()
        self.assertEqual(links, [(2, 1, "contradicts")])

    def test_supersede_winner_keeps_runner_up_conflict_edges(self):
        # S2, supersede branch: the new fact supersedes the decisive
        # partner and keeps contradicts edges to the over-gate
        # runner-ups — one decision, all the evidence.
        self.seed_fact(INCUMBENT)
        self.seed_fact(UNRELATED)
        self.add_event(CHALLENGER)
        answers = {
            **scripted_phase_a(),
            # vs fact 1: decisive override -> supersede wins
            **scripted_pair("p0_0", same=0.1, contra=0.9,
                            verdict="new_overrides", confidence=0.9),
            # vs fact 2: conflict, not decisive -> runner-up evidence only
            **scripted_pair("p0_1", same=0.1, contra=0.85,
                            verdict="old_stands", confidence=0.3),
        }
        report = self.dream(FakeJev(mode="scripted", answers=answers))
        self.assertEqual(report.superseded, (1,))
        self.assertEqual(report.facts_added, (3,))
        links = sorted(self.conn.execute(
            "SELECT fact_id, related_id, relation FROM fact_links "
            "WHERE relation IN ('supersedes', 'contradicts')"
        ).fetchall())
        self.assertEqual(
            links, [(3, 1, "supersedes"), (3, 2, "contradicts")]
        )
        # the runner-up fact stays active: evidence recorded, no decision
        self.assertEqual(get_fact(self.conn, 2).status, "active")

    def test_pair_receipts_recorded_per_fact(self):
        self.seed_fact(INCUMBENT)
        report = self.pair_dream({
            **scripted_phase_a(),
            **scripted_pair(verdict="old_stands", confidence=0.3),
        })
        event = event_id(CHALLENGER, "s1")
        rows = [
            row for row in self.judgments(report.run_id)
            if row[1] == "pair"
        ]
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            {row[2] for row in rows}, {f"{event}:1"}
        )
        self.assertEqual(
            sorted(row[3] for row in rows),
            ["contradicts", "same_claim", "verdict"],
        )

    # --- ask lifecycle end-to-end ---------------------------------------------

    def test_ask_resolves_keep_new_end_to_end(self):
        from dream_md.dream import resolve

        self.seed_fact(INCUMBENT)
        self.pair_dream({
            **scripted_phase_a(),
            **scripted_pair(verdict="old_stands", confidence=0.3),
        })
        final = resolve(self.conn, 2, KEEP_NEW, now=NOW)
        self.assertEqual(final.status, "active")
        self.assertEqual(get_fact(self.conn, 1).status,
                         STATUS_SUPERSEDED)
        runs = self.conn.execute(
            "SELECT kind FROM runs ORDER BY kind"
        ).fetchall()
        self.assertIn(("resolve",), runs)

    def test_ask_expires_after_three_unresolved_dreams(self):
        self.seed_fact(INCUMBENT)
        self.pair_dream({
            **scripted_phase_a(),
            **scripted_pair(verdict="old_stands", confidence=0.3),
        })
        # dreams with no events still run the expiry pass (ask is open)
        for n in range(1, ASK_EXPIRY_DREAMS + 1):
            report = self.dream(FakeJev(), now=LATER[18 + n])
            self.assertEqual(report.api_calls, 0)
            self.assertEqual(report.events_graded, 0)
            self.assertIsNotNone(report.run_id)  # a real run row each time
            challenger = get_fact(self.conn, 2)
            if n < ASK_EXPIRY_DREAMS:
                self.assertEqual(challenger.status, STATUS_ASK)
                self.assertEqual(challenger.ask_seen_count, n)
                self.assertEqual(report.asks_expired, ())
            else:
                self.assertEqual(report.asks_expired, (2,))
                self.assertEqual(challenger.status, "retired")
        # keep-old expiry: incumbent never left active memory
        self.assertEqual(get_fact(self.conn, 1).status, "active")
        receipts = self.conn.execute(
            "SELECT question_id FROM judgments WHERE question_id = 'expiry'"
        ).fetchall()
        self.assertEqual(receipts, [("expiry",)])

    # --- cap default (review S5) ----------------------------------------------

    def test_default_cap_is_fifty_groups(self):
        # 60 single-candidate events: the DEFAULT cap (no explicit
        # max_candidates) grades 50 groups and defers the rest — an
        # uncapped default was a first-dream budget footgun (S5)
        for n in range(60):
            self.add_event(
                f"decision number {n:02d} about distinct tooling topic",
                session=f"s{n:02d}",
            )
        report = self.dream(FakeJev())
        self.assertEqual(report.candidates_graded, 50)
        self.assertEqual(report.events_graded, 50)
        self.assertEqual(report.events_deferred, 10)
        self.assertEqual(self.pending_count(), 10)
        self.assertEqual(MAX_CANDIDATES_PER_DREAM, 50)

    # --- multi-batch phase A ----------------------------------------------------

    def test_many_candidates_split_across_batches(self):
        # one session of 120 fat single-sentence statements: unique word
        # salad per claim (Jaccard ~0.09, far under the dedupe gate) so
        # all 120 become facts; the ~800-char same-session context makes
        # entries exceed the Phase A batch budget and split across API
        # calls; every position must map back to its own event.
        # max_candidates=None: this test is about BATCH SPLITTING, not
        # the default cap (that is test_default_cap_is_fifty_groups).
        for i in range(120):
            words = " ".join(f"w{i:03d}{j:02d}" for j in range(60))
            self.add_event(
                f"always prefer reviewing {words} in standup notes",
                session="s0",
            )
        client = FakeJev()
        report = self.dream(client, max_candidates=None)
        self.assertGreater(client.call_count, 1)
        self.assertEqual(report.api_calls, client.call_count)
        self.assertEqual(report.candidates_graded, 120)
        self.assertEqual(len(report.facts), 120)
        self.assertEqual(self.pending_count(), 0)
        # receipts cover every event exactly once, three judgments each
        rows = self.conn.execute(
            "SELECT subject_id, COUNT(*) FROM judgments "
            "WHERE run_id = ? AND subject_kind = 'candidate' "
            "GROUP BY subject_id",
            (report.run_id,),
        ).fetchall()
        self.assertEqual(len(rows), 120)
        for subject_id, count in rows:
            self.assertEqual(count, 3)

    # --- error path ---------------------------------------------------------------

    def test_client_error_finishes_run_and_events_stay_pending(self):
        self.add_event("one more durable user statement about linting")
        client = FakeJev(errors={"always": JevRetryExhausted("429 stuck")})
        with self.assertRaises(JevRetryExhausted):
            self.dream(client)
        kind, stats, error, finished = self.run_row()
        self.assertEqual(kind, "dream")
        self.assertEqual(error, "JevRetryExhausted")
        self.assertEqual(finished, NOW)
        self.assertIsNone(stats)
        self.assertEqual(self.pending_count(), 1)  # grading incomplete
        # recovery: the same queue grades cleanly next dream
        report = self.dream(FakeJev())
        self.assertEqual(report.events_graded, 1)
        self.assertEqual(self.pending_count(), 0)

    def test_unexpected_error_also_closes_the_run_row(self):
        # S3: JevError closes the run row — and so must ANY exception.
        # A KeyboardInterrupt or MemoryError tearing the run down mid-
        # flight must never leave an open run row behind.
        self.add_event("one more durable user statement about linting")
        client = FakeJev(errors={"always": RuntimeError("interpreter blew up")})
        with self.assertRaises(RuntimeError):
            self.dream(client)
        kind, stats, error, finished = self.run_row()
        self.assertEqual((kind, error, finished),
                         ("dream", "RuntimeError", NOW))
        self.assertIsNone(stats)
        self.assertEqual(self.pending_count(), 1)  # grading incomplete


if __name__ == "__main__":
    unittest.main()
