"""Droid transcript parser tests.

The fixture is a sanitized excerpt rebuilt from REAL session files
observed on 2026-09-20 under ~/.factory/sessions/<project-dir-slug>/
(field shapes exact, text content replaced with benign equivalents —
no secrets; sanitized like review R3: home paths -> /Users/dev/example,
synthetic session UUIDs, 2222... pattern).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jevmory.ingestion.droid import parse_droid_transcript

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "droid_session.jsonl"
SESSION_ID = "22222222-0000-4000-8000-0000000000e1"


class DroidParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parse_droid_transcript(FIXTURE)

    def test_source_session_and_cwd_from_session_start(self):
        # Observed: session_start is flat (no payload wrapper); id/cwd
        # always come from it, never the file name.
        self.assertEqual(self.parsed.source, "droid")
        self.assertEqual(self.parsed.session_id, SESSION_ID)
        self.assertEqual(self.parsed.cwd, "/Users/dev/example")

    def test_statements_come_from_message_text_blocks_only(self):
        # fixture lines: 2 user text, 3 assistant text (next to thinking),
        # 15-16 human survivors (ad hoc dispatch + chitchat), 17 assistant
        # text, 22 user text next to an image, 24 assistant text (with
        # compactionSummaryId). All other lines skipped (machine-injected,
        # hook, non-message, or malformed).
        self.assertEqual(
            [s.line_no for s in self.parsed.statements], [2, 3, 15, 16, 17, 22, 24]
        )

    def test_real_user_prompt_extracted_verbatim(self):
        statement = self.parsed.statements[0]
        self.assertEqual(statement.role, "user")
        self.assertEqual(statement.index, 0)
        self.assertEqual(statement.ts, "2026-09-20T10:00:01.000Z")
        self.assertEqual(statement.session_id, SESSION_ID)
        self.assertEqual(
            statement.text,
            "Pin the retry backoff test to the fake clock, the real timer "
            "makes it flaky under load.",
        )

    def test_assistant_text_extracted_with_thinking_skipped(self):
        statement = self.parsed.statements[1]
        self.assertEqual(statement.role, "assistant")
        self.assertEqual(statement.index, 0)  # thinking block is not a statement
        self.assertEqual(
            statement.text,
            "The retry test now runs on the fake clock; backoff sleeps are "
            "deterministic and the suite is stable.",
        )

    def test_harness_injections_skipped(self):
        # Observed: <system-reminder> is the dominant machine family in
        # the user role (10,488 of 16,120 user text blocks in the census).
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("<system-reminder>", all_text)
        self.assertNotIn("Cache warmed", all_text)
        self.assertNotIn("BYOK Error", all_text)
        self.assertNotIn("Request interrupted", all_text)

    def test_scheduler_notifications_and_monitor_prompts_skipped(self):
        # Background task notices, task-tool feedback, skill activation
        # notices, and the "Bounded r<N> ... monitoring check" family
        # (numeric prefix varies, hence a pattern).
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("Background task completed", all_text)
        self.assertNotIn("Task Tool Invocation", all_text)
        self.assertNotIn('Skill "commit" activated', all_text)
        self.assertNotIn("Bounded r4", all_text)

    def test_orchestrator_templates_skipped(self):
        # "# Follow-up Instructions" (herdr follow-up dispatch) and
        # "You are implementing " (herdr task dispatch, excluded in
        # codex.py too) are fixed templates, machine-class.
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("Follow-up Instructions", all_text)
        self.assertNotIn("You are implementing", all_text)

    def test_genuine_human_and_ad_hoc_texts_survive(self):
        # Judgment rule (same as codex): no prefix may be broad enough
        # to catch normal speech. Ad hoc root-agent dispatches with
        # VARIED phrasing ("Read /Users/... Begin ...") are typed, not
        # templated: kept, the dream grader downweights commands.
        texts = {s.text for s in self.parsed.statements}
        self.assertIn("keep going", texts)
        self.assertIn(
            "Read /Users/dev/example/workstreams/REVIEW-2.md. Begin your "
            "independent review now. The writer is released; product read-only.",
            texts,
        )

    def test_hook_generated_user_message_skipped(self):
        # Observed: hook messages carry hookEventName + empty content
        # (8,953 in the census, all empty); a hook message is machine
        # by definition, skipped even if content were non-empty.
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("hookMatcher", all_text)

    def test_tool_result_and_tool_use_never_statements(self):
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("40 lines of test code", all_text)
        self.assertNotIn("test_retry.py", all_text)

    def test_todo_outcome_and_compaction_lines_skipped(self):
        # compaction_state.summaryText is a machine digest, not a quote.
        all_text = " ".join(s.text for s in self.parsed.statements)
        self.assertNotIn("machine digest of prior turns", all_text)
        self.assertNotIn("fix retry test", all_text)

    def test_image_ignored_sibling_text_kept(self):
        statement = self.parsed.statements[5]
        self.assertEqual(statement.role, "user")
        self.assertTrue(statement.text.startswith("The screenshot shows"))
        self.assertNotIn("base64", statement.text)

    def test_malformed_line_recorded_not_raised(self):
        self.assertEqual(self.parsed.skipped_lines, (23,))

    def test_string_content_tolerated(self):
        # Not observed in real droid files (content was always a list),
        # but tolerated claude-style: a plain string is still a statement.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "string-content.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "id": "33333333-0000-4000-8000-000000000099",
                        "timestamp": "2026-09-20T11:00:00.000Z",
                        "message": {
                            "role": "assistant",
                            "content": "Plain string content, tolerated.",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            parsed = parse_droid_transcript(path)
        self.assertEqual(len(parsed.statements), 1)
        self.assertEqual(parsed.statements[0].text, "Plain string content, tolerated.")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_droid_transcript(FIXTURE.parent / "does_not_exist.jsonl")


if __name__ == "__main__":
    unittest.main()
