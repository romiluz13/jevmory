"""Extraction tests: sentence chunking, anaphoric drop, context, no dedupe."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dream_md.ingestion.claude import parse_claude_transcript
from dream_md.ingestion.eventlog import EventLog, event_id
from dream_md.ingestion.extract import (
    candidates_from_statements,
    chunk_text,
    extract_candidates,
)
from dream_md.ingestion.models import ParsedTranscript, Statement
from dream_md.ingestion.redact import redact
from dream_md.thresholds import (
    CHUNK_MAX_CHARS,
    CONTEXT_MAX_CHARS,
    MIN_CANDIDATE_CHARS,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLAUDE_SID = "00000000-0000-4000-8000-0000000000c1"


def make_transcript(statements, session_id="s1", source="claude", path="t.jsonl"):
    return ParsedTranscript(
        path=path,
        source=source,
        session_id=session_id,
        cwd=None,
        statements=tuple(statements),
    )


def user_statement(text, session_id="s1", line_no=1, index=0, ts=None):
    return Statement(
        line_no=line_no, index=index, ts=ts, role="user", text=text,
        session_id=session_id,
    )


class ChunkTextTest(unittest.TestCase):
    def test_short_text_is_single_chunk(self):
        self.assertEqual(chunk_text("One. Two. Three."), ["One. Two. Three."])

    def test_two_long_sentences_split_at_sentence_boundary(self):
        first = "A" * 350 + "."
        second = "B" * 350 + "."
        self.assertEqual(chunk_text(f"{first} {second}"), [first, second])

    def test_single_chunk_at_cap_is_not_split(self):
        self.assertEqual(chunk_text("x" * 600), ["x" * 600])

    def test_oversize_sentence_hard_split(self):
        self.assertEqual(chunk_text("x" * 700), ["x" * 600, "x" * 100])

    def test_hard_split_prefers_whitespace_boundary(self):
        # 699 chars, no sentence punctuation: one span, split at spaces.
        text = ("word " * 140).strip()
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 2)
        self.assertLessEqual(len(chunks[0]), 600)
        self.assertTrue(chunks[0].endswith("word"))  # no partial word
        self.assertTrue(chunks[1].startswith("word"))
        self.assertEqual(" ".join(chunks), " ".join(text.split()))

    def test_empty_and_whitespace_only(self):
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   \n\t "), [])

    def test_zero_max_chars_raises(self):
        with self.assertRaises(ValueError):
            chunk_text("anything", max_chars=0)

    def test_chunks_join_back_to_normalized_text(self):
        text = (
            "First convention. " * 30
            + "A much longer final convention sentence that goes on and on "
            "about uv run and docker compose and conventional commits."
        )
        chunks = chunk_text(text)
        self.assertEqual(" ".join(chunks), " ".join(text.split()))
        for chunk in chunks:
            self.assertLessEqual(len(chunk), CHUNK_MAX_CHARS)

    def test_chunks_are_substrings_of_stripped_text(self):
        text = "Alpha beta gamma. Delta epsilon. Zeta."
        for chunk in chunk_text(text):
            self.assertIn(chunk, text.strip())


class AnaphoricDropTest(unittest.TestCase):
    def _extract(self, *texts):
        return extract_candidates(
            make_transcript([user_statement(text) for text in texts])
        )

    def test_anaphoric_starts_dropped(self):
        for text in (
            "That is the convention here.",
            "This is how we deploy.",
            "It must never use npm, use bun instead.",
            "They always fail like that.",
            "The same rule applies to staging.",
            "Those logs are irrelevant.",
        ):
            self.assertEqual(self._extract(text), [], text)

    def test_anaphoric_drop_is_case_insensitive(self):
        self.assertEqual(self._extract("that is the rule here."), [])

    def test_non_anaphoric_statements_kept(self):
        candidates = self._extract(
            "Never commit secrets to the repo.",
            "The repo standardizes on uv.",
            "Use it carefully, the tool is sharp.",
        )
        self.assertEqual(len(candidates), 3)

    def test_first_chunk_is_not_exempt(self):
        # Pinned PLAN v2 stance: the drop applies to ALL chunks including
        # a statement's first. If the lead wants first-chunk exemption,
        # it is a one-line change in extract.py.
        self.assertEqual(self._extract("It must never use npm, use bun instead."), [])

    def test_anaphoric_second_chunk_dropped_first_kept(self):
        candidates = self._extract(
            "Use bun for all scripts. That said, keep node for the build step."
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].text.startswith("Use bun"))


class ContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parse_claude_transcript(FIXTURES / "claude_session.jsonl")
        cls.candidates = extract_candidates(cls.parsed)

    def test_first_statement_has_empty_context(self):
        self.assertEqual(self.candidates[0].context, "")

    def test_single_preceding_turn_context(self):
        expected = f"[user] {self.parsed.statements[0].text}"
        self.assertEqual(self.candidates[1].context, expected)

    def test_two_preceding_turns_context(self):
        expected = (
            f"[user] {self.parsed.statements[0].text}"
            f"\n\n[assistant] {self.parsed.statements[1].text}"
        )
        self.assertEqual(self.candidates[2].context, expected)

    def test_context_uses_last_two_turns_only(self):
        # With 4 preceding statements available, context keeps only the
        # last two (the secrets candidate: dispatch + conventions).
        context = self.candidates[5].context
        self.assertIn("You are dispatched", context)
        self.assertIn("Repo conventions", context)
        self.assertNotIn("Always use uv run", context)

    def test_chunks_of_one_statement_share_context(self):
        self.assertEqual(self.candidates[3].context, self.candidates[4].context)

    def test_context_truncated_to_cap_at_word_boundary(self):
        context = self.candidates[5].context
        self.assertLessEqual(len(context), CONTEXT_MAX_CHARS)
        self.assertTrue(context.startswith("[user] You are dispatched"))
        self.assertEqual(context, context.rstrip())  # cut at whitespace, not mid-word
        self.assertTrue(context[-1].isalpha() or context[-1] in ".!?")

    def test_context_is_redacted(self):
        candidates = extract_candidates(
            make_transcript(
                [
                    user_statement("the password=changeme12345 is shared"),
                    user_statement("Rotate it weekly."),
                ]
            )
        )
        self.assertIn("[redacted:credential]", candidates[1].context)
        self.assertNotIn("changeme12345", candidates[1].context)

    def test_context_never_crosses_sessions(self):
        statements = [
            user_statement("Session A turn.", session_id="a"),
            user_statement("Session B turn.", session_id="b"),
        ]
        candidates = candidates_from_statements(statements)
        by_session = {c.session_id: c for c in candidates}
        self.assertEqual(by_session["b"].context, "")
        self.assertEqual(by_session["a"].context, "")


class FixtureExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parse_claude_transcript(FIXTURES / "claude_session.jsonl")
        cls.candidates = extract_candidates(cls.parsed)

    def test_candidate_count_and_roles(self):
        # 5 statements; the 823-char conventions statement yields 2 chunks.
        self.assertEqual(len(self.candidates), 6)
        self.assertEqual(
            [c.role for c in self.candidates],
            ["user", "assistant", "user", "user", "user", "user"],
        )

    def test_all_chunks_within_cap(self):
        for candidate in self.candidates:
            self.assertLessEqual(len(candidate.text), CHUNK_MAX_CHARS)
            self.assertGreaterEqual(len(candidate.text), MIN_CANDIDATE_CHARS)

    def test_long_statement_yields_two_chunks(self):
        statement = self.parsed.statements[3]  # line 10, 823 chars
        chunks = [c for c in self.candidates if c.ts == statement.ts]
        self.assertEqual([len(c.text) for c in chunks], [573, 249])
        self.assertEqual(
            " ".join(c.text for c in chunks),
            " ".join(redact(statement.text).split()),
        )

    def test_secrets_candidate_is_redacted(self):
        candidate = self.candidates[5]
        self.assertIn("[redacted:credential]", candidate.text)
        self.assertNotIn("changeme12345", candidate.text)

    def test_event_ids_match_stored_events(self):
        # Integration: extraction ids are exactly the event ids the store
        # holds (chunks of one statement share its event id).
        with tempfile.TemporaryDirectory() as tmp:
            with EventLog(Path(tmp) / "store.db", project="slug12345678") as log:
                log.append(self.parsed)
                row_ids = {
                    row[0] for row in log._conn.execute("SELECT id FROM events")
                }
        candidate_ids = {c.event_id for c in self.candidates}
        self.assertEqual(candidate_ids, row_ids)  # 5 unique ids, 6 candidates
        for candidate in self.candidates:
            self.assertIn(candidate.event_id, row_ids)

    def test_ts_and_session_propagated(self):
        for candidate in self.candidates:
            self.assertEqual(candidate.session_id, CLAUDE_SID)
        self.assertEqual(
            [c.ts for c in self.candidates],
            [s.ts for s in self.parsed.statements for _ in range(
                2 if s.line_no == 10 else 1
            )],
        )

    def test_extraction_is_deterministic(self):
        again = extract_candidates(self.parsed)
        self.assertEqual(again, self.candidates)


class NoDedupeTest(unittest.TestCase):
    def test_cross_session_repeats_both_survive(self):
        # R2 downstream contract: no content dedupe at extraction. M3's
        # memory-level dedupe (hash + Jaccard) bumps support_count on
        # these repeats instead of never seeing them.
        text = "Always run the linter before pushing."
        candidates = extract_candidates(
            make_transcript([user_statement(text, "s1")]),
            make_transcript([user_statement(text, "s2")]),
        )
        self.assertEqual(len(candidates), 2)
        self.assertNotEqual(candidates[0].event_id, candidates[1].event_id)
        self.assertEqual(candidates[0].text, candidates[1].text)

    def test_same_session_repeat_yields_two_candidates_one_event(self):
        # Extraction keeps every occurrence; storage collapses the event.
        text = "Always run the linter before pushing."
        candidates = extract_candidates(
            make_transcript(
                [user_statement(text, "s1"), user_statement(text, "s1")]
            )
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].event_id, candidates[1].event_id)


class StatementsVariantTest(unittest.TestCase):
    def test_candidates_from_statements_matches_extract(self):
        parsed = parse_claude_transcript(FIXTURES / "claude_session.jsonl")
        self.assertEqual(
            candidates_from_statements(parsed.statements),
            extract_candidates(parsed),
        )

    def test_groups_by_session_with_per_session_context(self):
        statements = [
            user_statement("Session A turn one.", session_id="a"),
            user_statement("Session A turn two.", session_id="a"),
            user_statement("Session B turn one.", session_id="b"),
        ]
        candidates = candidates_from_statements(statements)
        self.assertEqual(
            [c.session_id for c in candidates], ["a", "a", "b"]
        )
        by_session = {c.session_id: c for c in candidates}
        self.assertEqual(by_session["b"].context, "")
        self.assertIn("Session A turn one.", by_session["a"].context)


if __name__ == "__main__":
    unittest.main()
