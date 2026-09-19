"""Hook tests (M6): the never-die contract, end to end.

`python3 -m jevmory.hook` runs after every agent session; a bug here
must never surface to the agent. These tests pin the contract from
every angle: happy-path ingest (claude stdin, codex argv), tolerant
key spellings, attribution precedence, idempotent re-ingest, and the
failure modes that must degrade to a logged skip or logged error —
never a nonzero exit, never an exception.

All state lands under a temp ``home`` (``JEVMORY_HOME`` territory);
the real home is never touched.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jevmory.hook import (
    extract_payload,
    global_log_path,
    log_path,
    run_hook,
)
from jevmory.ingestion.eventlog import project_slug, store_path
from jevmory.memory.schema import connect

CLAUDE_SID = "00000000-0000-4000-8000-0000000000c1"
CODEX_SID = "11111111-0000-4000-8000-0000000000d1"


def write_claude_transcript(path: Path, cwd: str | None, sid: str = CLAUDE_SID) -> Path:
    """Minimal claude-shaped transcript: one typed user statement."""
    lines = [
        json.dumps({"type": "mode", "mode": "normal", "sessionId": sid}),
        json.dumps(
            {
                "type": "user",
                "sessionId": sid,
                "cwd": cwd,
                "timestamp": "2026-09-01T19:41:02.101Z",
                "promptSource": "typed",
                "origin": {"kind": "human"},
                "message": {
                    "role": "user",
                    "content": (
                        "Always run the full test suite before pushing "
                        "in this repo."
                    ),
                },
            }
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_codex_transcript(path: Path, cwd: str | None, sid: str = CODEX_SID) -> Path:
    """Minimal codex-shaped rollout: session_meta + one user message."""
    lines = [
        json.dumps(
            {
                "timestamp": "2026-05-18T08:51:01.000Z",
                "ordinal": 0,
                "type": "session_meta",
                "payload": {"session_id": sid, "id": sid, "cwd": cwd},
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-05-18T08:51:02.000Z",
                "ordinal": 1,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Never push directly to main; always open "
                                "a pull request instead."
                            ),
                        }
                    ],
                },
            }
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class HookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.project = self.home / "proj"
        self.project.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def events_in(self, project_dir: Path | str) -> int:
        store = store_path(project_dir, home=self.home)
        if not store.exists():
            return 0
        conn = connect(store)
        try:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            conn.close()

    def claude_payload(self, transcript: Path, **extra) -> str:
        return json.dumps(
            {
                "session_id": CLAUDE_SID,
                "transcript_path": str(transcript),
                "cwd": str(self.project),
                **extra,
            }
        )

    # --- never-die contract --------------------------------------------------

    def test_exit_code_is_always_zero(self):
        transcript = write_claude_transcript(
            self.home / "t" / "one.jsonl", str(self.project)
        )
        cases = [
            ([], None),                                   # nothing at all
            ([], "not json {{{"),                          # garbage stdin
            ([], "[]"),                                    # JSON, not an object
            (["turn-ended"], None),                        # codex notify, no payload
            ([], self.claude_payload(transcript)),         # the happy path
            ([], json.dumps({"cwd": str(self.project)})),  # payload, no transcript
            ([], self.claude_payload(self.home / "gone.jsonl")),  # missing file
        ]
        for argv, stdin_text in cases:
            with self.subTest(argv=argv, stdin=stdin_text):
                self.assertEqual(run_hook(argv, stdin_text, home=self.home), 0)

    def test_hook_error_logged_when_body_raises(self):
        # Contract floor: even a crash INSIDE the hook body is logged to
        # the global log and swallowed — the agent never sees it.
        with mock.patch(
            "jevmory.hook.extract_payload", side_effect=RuntimeError("boom")
        ):
            rc = run_hook([], "{}", home=self.home)
        self.assertEqual(rc, 0)
        entries = read_log(global_log_path(self.home))
        self.assertEqual(entries[-1]["event"], "hook_error")
        self.assertIn("RuntimeError: boom", entries[-1]["error"])

    # --- happy path: claude stdin --------------------------------------------

    def test_claude_sessionend_payload_ingests_and_logs(self):
        transcript = write_claude_transcript(
            self.home / "t" / "one.jsonl", str(self.project)
        )
        rc = run_hook([], self.claude_payload(transcript), home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 1)
        entries = read_log(log_path(str(self.project), home=self.home))
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["event"], "ingest")
        self.assertEqual(entry["source"], "claude")
        self.assertEqual(entry["inserted"], 1)
        self.assertEqual(entry["duplicates"], 0)
        self.assertEqual(entry["statements"], 1)
        self.assertEqual(entry["session_id"], CLAUDE_SID)
        self.assertEqual(entry["transcript"], str(transcript))

    # --- happy path: codex argv -----------------------------------------------

    def test_codex_notify_payload_via_argv_ingests(self):
        # Codex notify passes the payload as an argv JSON string.
        transcript = write_codex_transcript(
            self.home / ".codex" / "sessions" / "2026" / "05" / "18" / "roll.jsonl",
            str(self.project),
        )
        payload = json.dumps(
            {"rollout_path": str(transcript), "workspace": str(self.project)}
        )
        rc = run_hook([payload], None, home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 1)
        entry = read_log(log_path(str(self.project), home=self.home))[-1]
        self.assertEqual(entry["event"], "ingest")
        self.assertEqual(entry["source"], "codex")
        self.assertEqual(entry["inserted"], 1)

    def test_alternate_key_spellings_are_tolerated(self):
        # Agents spell fields differently across versions: the first
        # recognized spelling wins (cwd/project_dir/…, path/file/…).
        transcript = write_claude_transcript(
            self.home / "t" / "one.jsonl", str(self.project)
        )
        payload = json.dumps(
            {
                "sessionId": CLAUDE_SID,
                "session_file": str(transcript),
                "working_directory": str(self.project),
            }
        )
        rc = run_hook([], payload, home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 1)

    def test_stdin_payload_wins_over_argv(self):
        # Claude pipes on stdin; codex passes argv. When both exist,
        # stdin is authoritative.
        stdin_transcript = write_claude_transcript(
            self.home / "t" / "stdin.jsonl", str(self.project)
        )
        other_project = self.home / "other"
        other_project.mkdir()
        argv_transcript = write_claude_transcript(
            self.home / "t" / "argv.jsonl", str(other_project)
        )
        argv_payload = json.dumps(
            {"transcript_path": str(argv_transcript), "cwd": str(other_project)}
        )
        rc = run_hook([argv_payload], self.claude_payload(stdin_transcript), home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 1)
        self.assertEqual(self.events_in(other_project), 0)

    # --- attribution ----------------------------------------------------------

    def test_project_override_beats_payload_cwd(self):
        # --project DIR is the explicit human override: attribution goes
        # there even when the payload says otherwise.
        elsewhere = self.home / "elsewhere"
        transcript = write_claude_transcript(
            self.home / "t" / "one.jsonl", str(elsewhere)
        )
        payload = json.dumps(
            {"transcript_path": str(transcript), "cwd": str(elsewhere)}
        )
        rc = run_hook(["--project", str(self.project)], payload, home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 1)
        self.assertEqual(self.events_in(elsewhere), 0)
        # pre-parse logs also attribute to the override
        self.assertTrue(log_path(str(self.project), home=self.home).exists())

    def test_attribution_falls_back_to_transcript_cwd(self):
        # Payload without a cwd: the transcript's own session cwd decides.
        transcript = write_claude_transcript(
            self.home / "t" / "one.jsonl", str(self.project)
        )
        payload = json.dumps({"transcript_path": str(transcript)})
        rc = run_hook([], payload, home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 1)

    def test_no_attribution_anywhere_is_a_skip(self):
        transcript = write_claude_transcript(self.home / "t" / "one.jsonl", None)
        payload = json.dumps({"transcript_path": str(transcript)})
        rc = run_hook([], payload, home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 0)
        entry = read_log(global_log_path(self.home))[-1]
        self.assertEqual(entry["event"], "skipped")
        self.assertEqual(entry["reason"], "no project dir in payload or transcript")

    # --- skips and errors (logged, never fatal) -------------------------------

    def test_payload_without_transcript_is_a_logged_skip(self):
        payload = json.dumps({"cwd": str(self.project), "session_id": CLAUDE_SID})
        rc = run_hook([], payload, home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 0)
        entry = read_log(log_path(str(self.project), home=self.home))[-1]
        self.assertEqual(entry["event"], "skipped")
        self.assertEqual(entry["reason"], "no transcript path in payload")
        self.assertEqual(entry["session_id"], CLAUDE_SID)

    def test_payload_less_invocation_logs_global_skip(self):
        # Codex notify fires without a payload for many events; nothing
        # is attributable, so the skip lands in the global log.
        rc = run_hook(["turn-ended"], None, home=self.home)
        self.assertEqual(rc, 0)
        entry = read_log(global_log_path(self.home))[-1]
        self.assertEqual(entry["event"], "skipped")
        self.assertEqual(entry["reason"], "no JSON payload")
        self.assertFalse(log_path(str(self.project), home=self.home).exists())

    def test_missing_transcript_file_logs_ingest_error(self):
        payload = self.claude_payload(self.home / "t" / "gone.jsonl")
        rc = run_hook([], payload, home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 0)
        entry = read_log(log_path(str(self.project), home=self.home))[-1]
        self.assertEqual(entry["event"], "ingest_error")
        self.assertIn("FileNotFoundError", entry["error"])

    def test_directory_transcript_logs_ingest_error(self):
        # open() on a directory raises OSError — logged, never fatal.
        target = self.home / "t" / "adir"
        target.mkdir(parents=True)
        payload = self.claude_payload(target)
        rc = run_hook([], payload, home=self.home)
        self.assertEqual(rc, 0)
        entry = read_log(log_path(str(self.project), home=self.home))[-1]
        self.assertEqual(entry["event"], "ingest_error")

    # --- idempotence -----------------------------------------------------------

    def test_reingest_is_idempotent(self):
        # SessionEnd can fire twice for one session (resumed sessions):
        # the second run must insert nothing and log the duplicates.
        transcript = write_claude_transcript(
            self.home / "t" / "one.jsonl", str(self.project)
        )
        run_hook([], self.claude_payload(transcript), home=self.home)
        rc = run_hook([], self.claude_payload(transcript), home=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_in(self.project), 1)
        entries = read_log(log_path(str(self.project), home=self.home))
        self.assertEqual([e["event"] for e in entries], ["ingest", "ingest"])
        self.assertEqual(entries[1]["inserted"], 0)
        self.assertEqual(entries[1]["duplicates"], 1)

    def test_redaction_happens_at_rest(self):
        # The hook stores through EventLog: secret-shaped spans never
        # reach the store, even via the hook path.
        transcript = self.home / "t" / "one.jsonl"
        lines = [
            json.dumps({"type": "mode", "mode": "normal", "sessionId": CLAUDE_SID}),
            json.dumps(
                {
                    "type": "user",
                    "sessionId": CLAUDE_SID,
                    "cwd": str(self.project),
                    "promptSource": "typed",
                    "origin": {"kind": "human"},
                    "message": {
                        "role": "user",
                        "content": "the deploy password=changeme12345, keep it safe",
                    },
                }
            ),
        ]
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rc = run_hook([], self.claude_payload(transcript), home=self.home)
        self.assertEqual(rc, 0)
        conn = connect(store_path(self.project, home=self.home))
        try:
            text = conn.execute("SELECT text FROM events").fetchone()[0]
        finally:
            conn.close()
        self.assertNotIn("changeme12345", text)


class ExtractPayloadTest(unittest.TestCase):
    def test_stdin_dict_returned(self):
        self.assertEqual(extract_payload([], '{"a": 1}'), {"a": 1})

    def test_stdin_garbage_falls_back_to_argv(self):
        self.assertEqual(extract_payload(["not json", '{"a": 1}'], "junk"), {"a": 1})

    def test_non_dict_json_is_not_a_payload(self):
        self.assertIsNone(extract_payload([], '["still", "not", "it"]'))
        self.assertIsNone(extract_payload(["42"], None))

    def test_nothing_parseable_returns_none(self):
        self.assertIsNone(extract_payload(["turn-ended"], None))
        self.assertIsNone(extract_payload([], None))


class LogPathTest(unittest.TestCase):
    def test_project_log_is_slug_addressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = log_path("/x/proj", home=home)
            self.assertEqual(
                path,
                home / ".jevmory" / "hooks" / f"{project_slug('/x/proj')}.jsonl",
            )

    def test_global_log_is_fixed_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                global_log_path(Path(tmp)),
                Path(tmp) / ".jevmory" / "hooks" / "global.jsonl",
            )

    def test_append_log_survives_unwritable_home(self):
        # best effort, never raises: a home path blocked by a regular
        # file must not kill the hook (the write fails, is swallowed)
        from jevmory.hook import append_log

        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("i am a file", encoding="utf-8")
            append_log(None, {"event": "x"}, home=blocker)  # must not raise
            self.assertFalse(
                (blocker / ".jevmory").exists()
            )  # nothing was written


if __name__ == "__main__":
    unittest.main()
