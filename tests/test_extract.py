"""Deterministic candidate extraction tests: chunking, dedupe, provenance."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from dream_md.ingestion.claude import parse_claude_transcript
from dream_md.ingestion.codex import parse_codex_transcript
from dream_md.ingestion.eventlog import event_id
from dream_md.ingestion.extract import (
    MIN_CANDIDATE_CHARS,
    chunk_text,
    extract_candidates,
)
from dream_md.ingestion.models import ParsedTranscript, Statement

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _statement(line_no, index, role, text):
    return Statement(
        line_no=line_no,
        index=index,
        ts="2026-09-19T00:00:00Z",
        role=role,
        text=text,
        session_id="sess-1",
    )


def _parsed(path, *statements):
    return ParsedTranscript(
        path=path,
        source="claude",
        session_id="sess-1",
        cwd=None,
        statements=statements,
    )


class ChunkTextTest(unittest.TestCase):
    def test_short_text_is_single_chunk(self):
        self.assertEqual(chunk_text("always use bun here"), ["always use bun here"])

    def test_empty_and_whitespace_only(self):
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   \n  "), [])

    def test_text_at_exact_limit_unchanged(self):
        text = "a" * 600
        self.assertEqual(chunk_text(text), [text])

    def test_breaks_at_whitespace_within_limit(self):
        text = " ".join(["word"] * 200)  # 999 chars
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 600)
            self.assertIn(chunk, text)  # verbatim substrings only
        normalize = lambda s: " ".join(s.split())
        self.assertEqual(normalize(" ".join(chunks)), normalize(text))

    def test_hard_cut_when_no_whitespace(self):
        self.assertEqual(chunk_text("a" * 601), ["a" * 600, "a"])

    def test_newline_boundary_preferred_over_hard_cut(self):
        text = "x" * 595 + "\n" + "y" * 10
        self.assertEqual(chunk_text(text), ["x" * 595, "y" * 10])

    def test_bad_limit_raises(self):
        with self.assertRaises(ValueError):
            chunk_text("text", limit=0)


class ExtractCandidatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claude = parse_claude_transcript(FIXTURES / "claude_session.jsonl")
        cls.codex = parse_codex_transcript(FIXTURES / "codex_session.jsonl")

    def test_roles_preserved(self):
        candidates = extract_candidates(self.claude, self.codex)
        roles = {c.role for c in candidates}
        self.assertEqual(roles, {"user", "assistant"})

    def test_all_candidates_within_limit(self):
        candidates = extract_candidates(self.claude, self.codex)
        self.assertGreater(len(candidates), 0)
        for candidate in candidates:
            self.assertLessEqual(len(candidate.text), 600)
            self.assertGreaterEqual(len(candidate.text), MIN_CANDIDATE_CHARS)

    def test_long_prompt_chunked_into_multiple_candidates(self):
        long_statement = self.claude.statements[3]
        self.assertGreater(len(long_statement.text), 600)
        long_chunks = [
            c for c in extract_candidates(self.claude)
            if c.text in long_statement.text
        ]
        self.assertGreater(len(long_chunks), 1)
        for candidate in long_chunks:
            self.assertLessEqual(len(candidate.text), 600)
            self.assertIn(candidate.text, long_statement.text)  # verbatim

    def test_min_length_floor_drops_filler(self):
        parsed = _parsed(
            "/tmp/t1.jsonl",
            _statement(1, 0, "user", "ok"),  # 2 chars, observed real prompt
            _statement(2, 0, "user", "keep going"),  # 10 chars, observed
            _statement(3, 0, "user", "please use bun not npm in here"),  # kept
        )
        candidates = extract_candidates(parsed)
        self.assertEqual([c.text for c in candidates], ["please use bun not npm in here"])

    def test_dedupe_by_content_hash_first_occurrence_wins(self):
        text = "always run the linter before every commit in this repo"
        parsed = _parsed(
            "/tmp/t2.jsonl",
            _statement(1, 0, "user", text),
            _statement(5, 0, "assistant", text),  # same content again
        )
        candidates = extract_candidates(parsed)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].event_id, event_id("/tmp/t2.jsonl", 1, 0))
        self.assertEqual(candidates[0].role, "user")  # first occurrence

    def test_dedupe_across_transcripts(self):
        text = "the staging cluster is off limits for load testing"
        first = _parsed("/tmp/a.jsonl", _statement(1, 0, "user", text))
        second = _parsed("/tmp/b.jsonl", _statement(1, 0, "user", text))
        candidates = extract_candidates(first, second)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].event_id, event_id("/tmp/a.jsonl", 1, 0))

    def test_content_hash_is_sha256_of_text(self):
        parsed = _parsed("/tmp/c.jsonl", _statement(1, 0, "user", "hash me please, i am a statement"))
        candidate = extract_candidates(parsed)[0]
        self.assertEqual(
            candidate.content_hash,
            hashlib.sha256(candidate.text.encode("utf-8")).hexdigest(),
        )

    def test_multi_statement_line_gets_distinct_event_ids(self):
        parsed = _parsed(
            "/tmp/d.jsonl",
            _statement(4, 0, "assistant", "first text block of the message"),
            _statement(4, 1, "assistant", "second text block of the message"),
        )
        candidates = extract_candidates(parsed)
        self.assertEqual(len(candidates), 2)
        self.assertNotEqual(candidates[0].event_id, candidates[1].event_id)
        self.assertNotEqual(event_id("/tmp/d.jsonl", 4, 0), event_id("/tmp/d.jsonl", 4, 1))

    def test_ts_and_session_propagated(self):
        candidate = extract_candidates(self.claude)[0]
        self.assertEqual(candidate.ts, "2026-09-01T19:41:02.101Z")
        self.assertEqual(candidate.session_id, "f617f58f-40f9-4a35-9d77-69bb271400b2")

    def test_extraction_is_deterministic(self):
        self.assertEqual(
            extract_candidates(self.claude, self.codex),
            extract_candidates(self.claude, self.codex),
        )


if __name__ == "__main__":
    unittest.main()
