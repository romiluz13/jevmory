"""Codex transcript parser tests.

The fixture is a sanitized excerpt rebuilt from REAL rollout files
observed on 2026-09-19 under ~/.codex/sessions/ (field shapes exact,
text content replaced with benign equivalents — no secrets). Sanitized
per review R3: home paths -> /Users/dev/example, synthetic session
UUIDs (1111... pattern).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from jevmory.ingestion.codex import parse_codex_transcript

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "codex_session.jsonl"
SESSION_ID = "11111111-0000-4000-8000-0000000000d1"


class CodexParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parse_codex_transcript(FIXTURE)

    def test_source_session_and_cwd_from_session_meta(self):
        # Observed: session_id must come from the session_meta payload,
        # never the file name (filename uuid != session_id in 457/1077
        # real files, subagent threads).
        self.assertEqual(self.parsed.source, "codex")
        self.assertEqual(self.parsed.session_id, SESSION_ID)
        self.assertEqual(self.parsed.cwd, "/Users/dev/example")

    def test_statements_come_from_message_payloads_only(self):
        # fixture lines: 4 user prompt, 7 assistant output_text,
        # 15 user text next to an input_image, 22-24 genuine human
        # texts (dogfood round 1 survivors). All other lines skipped
        # (machine-injected, non-message, or malformed).
        self.assertEqual(
            [s.line_no for s in self.parsed.statements], [4, 7, 15, 22, 23, 24]
        )

    def test_real_user_prompt_extracted_verbatim(self):
        statement = self.parsed.statements[0]
        self.assertEqual(statement.role, "user")
        self.assertEqual(statement.index, 0)
        self.assertEqual(statement.ts, "2026-05-18T08:51:01.000Z")
        self.assertEqual(statement.session_id, SESSION_ID)
        self.assertEqual(
            statement.text,
            "Run the integration tests with DATABASE_URL pointing at the "
            "local docker postgres, never the staging cluster.",
        )

    def test_assistant_output_text_extracted(self):
        statement = self.parsed.statements[1]
        self.assertEqual(statement.role, "assistant")
        self.assertEqual(
            statement.text,
            "The integration suite is green against the local docker "
            "postgres; the migrations run automatically before the tests.",
        )

    def test_machine_injected_user_texts_skipped(self):
        # Observed wrappers: environment_context, AGENTS.md header,
        # heartbeat (plus subagent_notification/skill/turn_aborted).
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("<environment_context>", all_text)
        self.assertNotIn("AGENTS.md", all_text)
        self.assertNotIn("<heartbeat>", all_text)

    def test_herdr_dispatch_prompts_skipped(self):
        # Dogfood round 1 (F2): herdr agent prompts land in the USER
        # role ("You are running ...", "PLEASE IMPLEMENT THIS PLAN:")
        # and became junk facts before this exclusion.
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("Build-Bench", all_text)
        self.assertNotIn("You are running", all_text)

    def test_codex_wrapper_injections_skipped(self):
        # Dogfood round 1 (F2): more codex-pushed wrappers in the USER
        # role beyond the original six.
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("recommended_plugins", all_text)
        self.assertNotIn("Files mentioned by the user", all_text)

    def test_old_format_replay_lines_skipped(self):
        # Dogfood round 1 (F2): older rollout formats replay the prior
        # conversation inside the USER role as numbered lines — the
        # numeric prefix varies, so these need EXCLUDED_USER_PATTERNS.
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("[3] tool exec result", all_text)
        self.assertNotIn("[8] tool exec call", all_text)

    def test_genuine_human_texts_survive(self):
        # Judgment rule (dogfood round 1): exclusions must never be
        # broad enough to catch normal speech. Bare "You are " and
        # "In /Users/..." opens are human-plausible and stay.
        texts = {s.text for s in self.parsed.statements}
        self.assertIn("keep going", texts)
        self.assertIn("In /Users/dev/example, read-only triage.", texts)
        self.assertIn(
            "You are right, the Safari fix belongs in styles.css.", texts
        )

    def test_developer_role_is_machine_text_skipped(self):
        roles = {s.role for s in self.parsed.statements}
        self.assertNotIn("developer", roles)
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("Filesystem sandboxing", all_text)

    def test_reasoning_and_tool_lines_skipped(self):
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("Considered sqlite", all_text)
        self.assertNotIn("12 passed", all_text)

    def test_task_complete_message_not_duplicated(self):
        # Observed: event_msg task_complete.last_agent_message duplicates
        # the assistant output_text; it must not become a second statement.
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("The suite is green.", all_text)

    def test_input_image_ignored_sibling_text_kept(self):
        statement = self.parsed.statements[2]
        self.assertEqual(statement.role, "user")
        self.assertTrue(statement.text.startswith("The screenshot shows"))
        self.assertNotIn("base64", statement.text)

    def test_compacted_line_skipped(self):
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("compaction summary", all_text)

    def test_malformed_line_recorded_not_raised(self):
        self.assertEqual(self.parsed.skipped_lines, (16,))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_codex_transcript(FIXTURE.parent / "does_not_exist.jsonl")


if __name__ == "__main__":
    unittest.main()
