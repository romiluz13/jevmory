"""Claude Code transcript parser tests.

The fixture is a sanitized excerpt rebuilt from REAL transcript lines
observed on 2026-09-19 under ~/.claude/projects/ (field shapes exact,
text content replaced with benign equivalents — no secrets). Sanitized
per review R3: home paths -> /Users/dev/example, synthetic session
UUIDs (0000... pattern), secret samples are obviously-fake shapes for
the redaction corpus.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dream_md.ingestion.claude import parse_claude_transcript

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "claude_session.jsonl"
SESSION_ID = "00000000-0000-4000-8000-0000000000c1"


class ClaudeParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parse_claude_transcript(FIXTURE)

    def test_source_session_and_cwd(self):
        self.assertEqual(self.parsed.source, "claude")
        self.assertEqual(self.parsed.session_id, SESSION_ID)
        self.assertEqual(self.parsed.cwd, "/Users/dev/example")

    def test_statements_come_from_message_lines_only(self):
        # fixture lines: 3 typed user, 4 assistant text, 9 sidechain user,
        # 10 long user prompt, 11 secrets user prompt. Lines 1, 2, 5, 6,
        # 7, 8 yield nothing; line 12 is malformed.
        self.assertEqual([s.line_no for s in self.parsed.statements], [3, 4, 9, 10, 11])

    def test_typed_user_prompt_extracted_verbatim(self):
        statement = self.parsed.statements[0]
        self.assertEqual(statement.role, "user")
        self.assertEqual(statement.index, 0)
        self.assertEqual(statement.ts, "2026-09-01T19:41:02.101Z")
        self.assertEqual(statement.session_id, SESSION_ID)
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
        self.assertEqual(statement.session_id, SESSION_ID)

    def test_long_user_prompt_is_one_statement(self):
        statement = self.parsed.statements[3]
        self.assertGreater(len(statement.text), 600)  # chunking is extract's job
        self.assertEqual(statement.index, 0)

    def test_raw_secrets_statement_passes_through_unredacted(self):
        # Parsers stay raw and verbatim (DOMAIN: raw-parser boundary).
        # Redaction happens at the storage boundary (eventlog), pinned
        # in test_eventlog.test_redaction_at_rest.
        statement = self.parsed.statements[4]
        self.assertTrue(statement.text.startswith("One more thing"))
        self.assertIn("password=changeme12345", statement.text)
        self.assertIn("never log any of them", statement.text)

    def test_unknown_injection_flavor_passes_through(self):
        # Review R8: the exclusion list is observed-only (denylist). A
        # future IDE injecting text under a marker never observed in the
        # 2026-09 corpus carries none of the known markers, so it passes
        # through raw — Phase A grades it ephemeral instead of the
        # parser guessing. An allowlist would also drop sidechain
        # dispatch prompts, so denylist is the pinned stance.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "future.jsonl"
            lines = [
                '{"type":"mode","mode":"normal","sessionId":"00000000-0000-4000-8000-0000000000ff"}',
                '{"parentUuid":null,"isSidechain":false,'
                '"message":{"role":"user","content":"<flavor_xyz> Some future '
                'machine injection flavor never observed in the 2026-09 corpus."},'
                '"sessionId":"00000000-0000-4000-8000-0000000000ff","type":"user",'
                '"uuid":"00000000-0000-4000-8000-0000000000f1",'
                '"timestamp":"2026-09-01T20:00:00.000Z","cwd":"/Users/dev/example",'
                '"version":"9.9.999","userType":"external"}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            parsed = parse_claude_transcript(path)
            self.assertEqual(len(parsed.statements), 1)
            self.assertTrue(parsed.statements[0].text.startswith("<flavor_xyz>"))
            self.assertEqual(parsed.skipped_lines, ())

    def test_malformed_line_recorded_not_raised(self):
        self.assertEqual(self.parsed.skipped_lines, (12,))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_claude_transcript(FIXTURE.parent / "does_not_exist.jsonl")


if __name__ == "__main__":
    unittest.main()
