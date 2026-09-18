"""Claude Code transcript parser tests.

The fixture is a sanitized excerpt rebuilt from REAL transcript lines
observed on 2026-09-19 under ~/.claude/projects/ (field shapes exact,
text content replaced with benign equivalents — no secrets).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dream_md.ingestion.claude import parse_claude_transcript

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "claude_session.jsonl"


class ClaudeParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parse_claude_transcript(FIXTURE)

    def test_source_session_and_cwd(self):
        self.assertEqual(self.parsed.source, "claude")
        self.assertEqual(
            self.parsed.session_id, "f617f58f-40f9-4a35-9d77-69bb271400b2"
        )
        self.assertEqual(self.parsed.cwd, "/Users/rom.iluz/Dev/example")

    def test_statements_come_from_message_lines_only(self):
        # fixture lines: 3 typed user, 4 assistant text, 9 sidechain user,
        # 10 long user prompt. Lines 1, 2, 5, 6, 7, 8 yield nothing.
        self.assertEqual([s.line_no for s in self.parsed.statements], [3, 4, 9, 10])

    def test_typed_user_prompt_extracted_verbatim(self):
        statement = self.parsed.statements[0]
        self.assertEqual(statement.role, "user")
        self.assertEqual(statement.index, 0)
        self.assertEqual(statement.ts, "2026-09-01T19:41:02.101Z")
        self.assertEqual(
            statement.session_id, "f617f58f-40f9-4a35-9d77-69bb271400b2"
        )
        self.assertEqual(
            statement.text,
            "Always use uv run in this repo; plain python breaks the "
            "lockfile and the tests hang on asyncio teardown.",
        )

    def test_assistant_yields_only_text_block(self):
        statement = self.parsed.statements[1]
        self.assertEqual(statement.role, "assistant")
        self.assertEqual(
            statement.text,
            "The test suite needs pytest-asyncio; I will add the plugin "
            "config to pyproject so the suite stops hanging.",
        )
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("internal reasoning that must never surface", all_text)
        self.assertNotIn("toolu_01", all_text)

    def test_tool_result_is_not_a_statement(self):
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("has been updated", all_text)

    def test_meta_and_task_notifications_skipped(self):
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("Caveat", all_text)
        self.assertNotIn("task-notification", all_text)
        self.assertNotIn("Completed subagent task", all_text)

    def test_system_line_skipped(self):
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("offline sources", all_text)

    def test_sidechain_user_statement_extracted(self):
        # Observed shape: agent-*.jsonl sidechains carry the parent
        # sessionId; their dispatched prompts are still statements.
        statement = self.parsed.statements[2]
        self.assertEqual(statement.role, "user")
        self.assertTrue(statement.text.startswith("You are dispatched"))
        self.assertEqual(
            statement.session_id, "f617f58f-40f9-4a35-9d77-69bb271400b2"
        )

    def test_long_user_prompt_is_one_statement(self):
        statement = self.parsed.statements[3]
        self.assertGreater(len(statement.text), 600)  # chunking is extract's job
        self.assertEqual(statement.index, 0)

    def test_malformed_line_recorded_not_raised(self):
        self.assertEqual(self.parsed.skipped_lines, (11,))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_claude_transcript(FIXTURE.parent / "does_not_exist.jsonl")


if __name__ == "__main__":
    unittest.main()
