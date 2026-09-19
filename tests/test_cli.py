"""CLI integration tests (M6): every verb, offline, against tmp stores.

`main(argv, stdin_text)` is driven in-process with DREAM_MD_HOME
redirected to a temp dir, so nothing touches the real home and no
test ever needs the network: live paths are exercised only up to
their gates (marker, key), which fail before any request is made.

The one part of the CLI that writes OUTSIDE dream-md state —
`install --yes` patching ~/.claude/settings.json or ~/.codex/config.toml
— is tested at the function level with the target paths patched to
temp files; the real agent configs are never touched.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dream_md.cli import (
    _patch_claude_settings,
    _patch_codex_config,
    main,
)
from dream_md.dream.writer import SENTINEL_CORE
from dream_md.ingestion.eventlog import project_slug, store_path
from dream_md.memory.facts import add_fact, add_link, mark_ask
from dream_md.memory.schema import connect, migrate

CLAUDE_SID = "00000000-0000-4000-8000-0000000000c1"
CODEX_SID = "11111111-0000-4000-8000-0000000000d1"


def write_claude_transcript(path: Path, cwd: str | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "mode", "mode": "normal", "sessionId": CLAUDE_SID})
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "sessionId": CLAUDE_SID,
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
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class CliTest(unittest.TestCase):
    """End-to-end verb tests: in-process main(), tmp home, offline."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.project = self.home / "proj"
        self.project.mkdir()
        self.slug = project_slug(str(self.project))
        self.store = store_path(self.project, home=self.home)
        self.transcript = write_claude_transcript(
            self.home / "t" / "session.jsonl", str(self.project)
        )
        # Redirect every dream-md state path into the tmp home, and keep
        # the live API gate deterministically closed unless a test opens it.
        self._env = {
            name: os.environ.get(name)
            for name in ("DREAM_MD_HOME", "TYPESAFE_API_KEY")
        }
        os.environ["DREAM_MD_HOME"] = str(self.home)
        os.environ.pop("TYPESAFE_API_KEY", None)

    def tearDown(self):
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._tmp.cleanup()

    def run_cli(self, argv: list[str], stdin_text: str | None = None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(argv, stdin_text=stdin_text)
        return rc, out.getvalue(), err.getvalue()

    def store_conn(self):
        conn = connect(self.store)
        migrate(conn)
        return conn

    def events_count(self) -> int:
        conn = self.store_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            conn.close()

    def facts_with_status(self, status: str) -> list[int]:
        conn = self.store_conn()
        try:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM facts WHERE status = ? ORDER BY id",
                    (status,),
                )
            ]
        finally:
            conn.close()

    # --- init ------------------------------------------------------------------

    def test_init_prints_paths_and_writes_nothing(self):
        rc, out, err = self.run_cli(["init", "--project", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertIn(str(self.store), out)
        self.assertIn("grading:  off", out)
        self.assertIn("dream-md init --enable-grading", out)
        self.assertFalse(self.store.exists())  # prints only

    def test_init_enable_grading_creates_the_marker(self):
        rc, out, err = self.run_cli(
            ["init", "--enable-grading", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("marker:   ", out)
        self.assertIn("(created)", out)
        marker = self.home / ".dream-md" / "projects" / f"{self.slug}.optin"
        self.assertTrue(marker.exists())
        # a second init now reports the marker it finds
        rc, out, err = self.run_cli(["init", "--project", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertIn("grading:  ENABLED (marker present)", out)

    # --- ingest ----------------------------------------------------------------

    def test_ingest_transcript_with_project_flag(self):
        rc, out, err = self.run_cli(
            ["ingest", "--transcript", str(self.transcript),
             "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("source:      claude", out)
        self.assertIn("inserted:    1", out)
        self.assertEqual(self.events_count(), 1)

    def test_ingest_transcript_attributes_from_its_own_cwd(self):
        # No --project: attribution follows the session's own cwd (the
        # same store either way, but by attribution, not by accident).
        rc, out, err = self.run_cli(
            ["ingest", "--transcript", str(self.transcript)]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_count(), 1)
        self.assertIn(f"project:     {self.project}", out)

    def test_ingest_is_idempotent(self):
        argv = ["ingest", "--transcript", str(self.transcript),
                "--project", str(self.project)]
        self.run_cli(argv)
        rc, out, err = self.run_cli(argv)
        self.assertEqual(rc, 0)
        self.assertIn("inserted:    0", out)
        self.assertIn("duplicates skipped: 1", out)
        self.assertEqual(self.events_count(), 1)

    def test_ingest_missing_transcript_fails_cleanly(self):
        rc, out, err = self.run_cli(
            ["ingest", "--transcript", str(self.home / "gone.jsonl"),
             "--project", str(self.project)]
        )
        self.assertEqual(rc, 1)
        self.assertIn("transcript not found", err)

    def test_ingest_without_a_target_fails_cleanly(self):
        rc, out, err = self.run_cli(["ingest", "--project", str(self.project)])
        self.assertEqual(rc, 1)
        self.assertIn("nothing to ingest", err)

    def test_ingest_cwdless_transcript_needs_project(self):
        cwdless = write_claude_transcript(self.home / "t" / "no-cwd.jsonl", None)
        rc, out, err = self.run_cli(["ingest", "--transcript", str(cwdless)])
        self.assertEqual(rc, 1)
        self.assertIn("pass --project DIR", err)

    def test_ingest_scan_discovers_and_ingests(self):
        # The transcript embeds the RESOLVED project path (scan matches
        # the realpath spelling; /var vs /private/var on macOS).
        scanned = write_claude_transcript(
            self.home / ".claude" / "projects" / "p" / f"{CLAUDE_SID}.jsonl",
            str(self.project.resolve()),
        )
        rc, out, err = self.run_cli(
            ["ingest", "--scan", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn(str(scanned), out)
        self.assertIn("transcripts: 1", out)
        self.assertIn("inserted: 1", out)
        self.assertEqual(self.events_count(), 1)

    def test_ingest_scan_is_idempotent(self):
        write_claude_transcript(
            self.home / ".claude" / "projects" / "p" / f"{CLAUDE_SID}.jsonl",
            str(self.project.resolve()),
        )
        argv = ["ingest", "--scan", "--project", str(self.project)]
        self.run_cli(argv)
        rc, out, err = self.run_cli(argv)
        self.assertEqual(rc, 0)
        self.assertIn("inserted: 0", out)
        self.assertIn("duplicates: 1", out)
        self.assertEqual(self.events_count(), 1)

    def test_ingest_scan_empty_project_is_zero_not_error(self):
        rc, out, err = self.run_cli(
            ["ingest", "--scan", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("no transcripts found", out)

    # --- ingest --test (writes nothing) ---------------------------------------

    def test_ingest_test_verifies_payload_and_writes_nothing(self):
        payload = json.dumps(
            {"transcript_path": str(self.transcript), "cwd": str(self.project)}
        )
        rc, out, err = self.run_cli(["ingest", "--test"], stdin_text=payload)
        self.assertEqual(rc, 0)
        self.assertIn("payload: OK (JSON object)", out)
        self.assertIn("transcript: OK (claude)", out)
        self.assertIn("writes:      none", out)
        self.assertIsNone(next(self.home.rglob(".dream-md"), None))

    def test_ingest_test_rejects_non_json_stdin(self):
        rc, out, err = self.run_cli(["ingest", "--test"], stdin_text="junk")
        self.assertEqual(rc, 1)
        self.assertIn("not a JSON object", err)

    def test_ingest_test_flags_missing_transcript_file(self):
        payload = json.dumps(
            {"transcript_path": str(self.home / "gone.jsonl"),
             "cwd": str(self.project)}
        )
        rc, out, err = self.run_cli(["ingest", "--test"], stdin_text=payload)
        self.assertEqual(rc, 1)
        self.assertIn("does not exist", err)

    def test_ingest_test_with_transcript_flag(self):
        rc, out, err = self.run_cli(
            ["ingest", "--test", "--transcript", str(self.transcript)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("transcript: OK (claude)", out)
        self.assertIsNone(next(self.home.rglob(".dream-md"), None))

    # --- dream -----------------------------------------------------------------

    def _ingested(self):
        self.run_cli(
            ["ingest", "--transcript", str(self.transcript),
             "--project", str(self.project)]
        )

    def test_dream_offline_runs_the_whole_pipeline(self):
        self._ingested()
        rc, out, err = self.run_cli(
            ["dream", "--offline", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("offline: simulated grading", out)
        self.assertIn("events:       1 graded", out)
        self.assertIn("candidates:   1 groups", out)
        self.assertIn("facts:        1 added", out)
        self.assertEqual(self.facts_with_status("active"), [1])
        artifact = self.project / "dream.md"
        self.assertTrue(artifact.exists())
        self.assertIn(SENTINEL_CORE, artifact.read_text(encoding="utf-8"))

    def test_dream_offline_is_idempotent_and_reports_empty_queue(self):
        self._ingested()
        self.run_cli(["dream", "--offline", "--project", str(self.project)])
        rc, out, err = self.run_cli(
            ["dream", "--offline", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("nothing to grade", out)
        self.assertEqual(self.facts_with_status("active"), [1])  # no duplicates

    def test_dream_live_without_marker_fails_with_the_hint(self):
        # The privacy gate fires BEFORE anything is sent: no marker, no
        # egress, no matter that a key exists.
        os.environ["TYPESAFE_API_KEY"] = "test-key-not-a-real-secret"
        self._ingested()
        rc, out, err = self.run_cli(["dream", "--project", str(self.project)])
        self.assertEqual(rc, 1)
        self.assertIn("grading is off for this project", err)
        self.assertIn("dream-md init --enable-grading", err)
        self.assertEqual(self.facts_with_status("active"), [])

    def test_dream_live_with_marker_but_no_key_fails_with_the_hint(self):
        self.run_cli(["init", "--enable-grading", "--project", str(self.project)])
        self._ingested()
        rc, out, err = self.run_cli(["dream", "--project", str(self.project)])
        self.assertEqual(rc, 1)
        self.assertIn("$TYPESAFE_API_KEY is not set", err)
        self.assertEqual(self.facts_with_status("active"), [])

    def test_dream_refuses_a_foreign_dream_md_without_force(self):
        self._ingested()
        artifact = self.project / "dream.md"
        artifact.write_text("# my personal notes, not dream-md's\n", encoding="utf-8")
        rc, out, err = self.run_cli(
            ["dream", "--offline", "--project", str(self.project)]
        )
        self.assertEqual(rc, 1)
        self.assertIn("sentinel", err)
        self.assertIn("pass --force", err)
        self.assertIn("# my personal notes", artifact.read_text(encoding="utf-8"))
        # --force is the explicit overwrite
        rc, out, err = self.run_cli(
            ["dream", "--offline", "--force", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn(SENTINEL_CORE, artifact.read_text(encoding="utf-8"))

    # --- resolve ---------------------------------------------------------------

    def _make_ask(self) -> int:
        """Seed a real ask (challenger + incumbent + contradicts edge)."""
        conn = self.store_conn()
        try:
            incumbent = add_fact(
                conn, project=self.slug, claim="always deploy on fridays",
                category="convention", significance=2.0, durable_noul=0.9,
            )
            challenger = add_fact(
                conn, project=self.slug, claim="we never deploy on fridays anymore",
                category="convention", significance=2.0, durable_noul=0.85,
            )
            mark_ask(conn, challenger.id)
            add_link(conn, challenger.id, incumbent.id, "contradicts")
            return challenger.id
        finally:
            conn.close()

    def test_resolve_keep_new_via_cli(self):
        ask_id = self._make_ask()
        rc, out, err = self.run_cli(
            ["resolve", str(ask_id), "--keep-new", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"ask {ask_id}: kept", out)
        self.assertEqual(self.facts_with_status("active"), [ask_id])
        self.assertEqual(self.facts_with_status("superseded"), [1])

    def test_resolve_keep_old_via_cli(self):
        ask_id = self._make_ask()
        rc, out, err = self.run_cli(
            ["resolve", str(ask_id), "--keep-old", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"ask {ask_id}: resolved keep-old", out)
        self.assertEqual(self.facts_with_status("retired"), [ask_id])
        self.assertEqual(self.facts_with_status("active"), [1])

    def test_resolve_needs_exactly_one_choice(self):
        ask_id = self._make_ask()
        rc, out, err = self.run_cli(
            ["resolve", str(ask_id), "--project", str(self.project)]
        )
        self.assertEqual(rc, 1)
        self.assertIn("pick exactly one", err)
        # both flags: argparse's mutually-exclusive group exits 2
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(
                ["resolve", str(ask_id), "--keep-new", "--keep-old",
                 "--project", str(self.project)]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_resolve_unknown_fact_fails_cleanly(self):
        rc, out, err = self.run_cli(
            ["resolve", "999", "--keep-new", "--project", str(self.project)]
        )
        self.assertEqual(rc, 1)
        self.assertTrue(err.strip())

    # --- status ----------------------------------------------------------------

    def test_status_before_any_store(self):
        rc, out, err = self.run_cli(["status", "--project", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertIn("events:       none yet (no store)", out)
        self.assertIn("first step:   dream-md ingest --scan", out)
        self.assertIn("grading is off for this project", out)

    def test_status_reports_store_runs_and_asks(self):
        self._ingested()
        self.run_cli(["dream", "--offline", "--project", str(self.project)])
        ask_id = self._make_ask()
        rc, out, err = self.run_cli(["status", "--project", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertIn("events:       1 total, 0 pending grading", out)
        self.assertIn("facts:", out)
        self.assertIn("last run:     dream", out)
        self.assertIn(f"open ask:     #{ask_id}", out)
        self.assertIn("dream.md:     ", out)
        self.assertIn("grading:      off", out)
        self.assertIn("privacy:", out)

    def test_status_surfaces_hook_ingest_and_errors(self):
        # The hidden `hook` verb ingests; status then shows the last
        # ingest line from the hook log (the agent-visible diagnostics).
        payload = json.dumps(
            {"transcript_path": str(self.transcript), "cwd": str(self.project)}
        )
        rc, out, err = self.run_cli(["hook"], stdin_text=payload)
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_count(), 1)
        rc, out, err = self.run_cli(["status", "--project", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertIn("last ingest:  ", out)
        self.assertIn(f"1 events from claude session {CLAUDE_SID}", out)

    def test_hook_verb_never_exits_nonzero(self):
        rc, out, err = self.run_cli(["hook"], stdin_text="garbage not json")
        self.assertEqual(rc, 0)
        self.assertEqual(self.events_count(), 0)

    # --- audit -----------------------------------------------------------------

    def _memory_file(self) -> Path:
        memory = self.project / "MEMORY.md"
        memory.write_text(
            "# Project memory\n"
            "\n"
            "- Always run the full test suite before pushing in this repo.\n"
            "- Deploys happen only during the full moon.\n",
            encoding="utf-8",
        )
        return memory

    def test_audit_offline_prints_the_terminal_report(self):
        self._ingested()
        self.run_cli(["dream", "--offline", "--project", str(self.project)])
        memory = self._memory_file()
        rc, out, err = self.run_cli(
            ["audit", str(memory), "--offline", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("dream-md audit — ", out)
        self.assertIn("receipts:", out)

    def test_audit_offline_json_is_machine_readable(self):
        self._ingested()
        self.run_cli(["dream", "--offline", "--project", str(self.project)])
        memory = self._memory_file()
        rc, out, err = self.run_cli(
            ["audit", str(memory), "--offline", "--json",
             "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        # the offline banner precedes the JSON document
        payload = json.loads(out[out.index("{"):])
        self.assertIn("summary", payload)
        self.assertIn("counts", payload)
        self.assertEqual(len(payload["lines"]), 2)  # one row per memory line
        self.assertIn("disposition", payload["lines"][0])

    def test_audit_offline_md_renders_a_table(self):
        self._ingested()
        self.run_cli(["dream", "--offline", "--project", str(self.project)])
        memory = self._memory_file()
        rc, out, err = self.run_cli(
            ["audit", str(memory), "--offline", "--md",
             "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("# dream-md audit —", out)
        self.assertIn("| line |", out)

    def test_audit_missing_memory_file_fails_cleanly(self):
        rc, out, err = self.run_cli(
            ["audit", str(self.project / "MEMORY.md"), "--offline",
             "--project", str(self.project)]
        )
        self.assertEqual(rc, 1)
        self.assertIn("memory file not found", err)

    def test_audit_live_requires_the_marker_first(self):
        # The opt-in gate is checked before the key: privacy ordering.
        os.environ["TYPESAFE_API_KEY"] = "test-key-not-a-real-secret"
        memory = self._memory_file()
        rc, out, err = self.run_cli(
            ["audit", str(memory), "--project", str(self.project)]
        )
        self.assertEqual(rc, 1)
        self.assertIn("grading is off for this project", err)

    # --- install (print-only; patches are function-tested against tmp files) ---

    def test_install_claude_print_only(self):
        rc, out, err = self.run_cli(
            ["install", "--agent", "claude", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("SessionEnd", out)
        self.assertIn("settings.json", out)
        self.assertIn("(print-only", out)  # never patches without --yes

    def test_install_codex_print_only(self):
        rc, out, err = self.run_cli(
            ["install", "--agent", "codex", "--project", str(self.project)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("notify = ", out)
        self.assertIn("hooks = true", out)
        self.assertIn("(print-only", out)


class InstallPatchTest(unittest.TestCase):
    """The --yes patchers, against temp targets (never the real home)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def patched(self, name: str, target: Path):
        return mock.patch(f"dream_md.cli.{name}", return_value=target)

    # --- claude ---------------------------------------------------------------

    def test_patch_claude_settings_from_scratch(self):
        target = self.base / "settings.json"
        with self.patched("_claude_settings_path", target):
            rc = _patch_claude_settings()
        self.assertEqual(rc, 0)
        data = json.loads(target.read_text(encoding="utf-8"))
        command = data["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
        self.assertEqual(command, "python3 -m dream_md.hook")

    def test_patch_claude_settings_merges_with_existing_hooks(self):
        target = self.base / "settings.json"
        target.write_text(
            json.dumps({"hooks": {"PreToolUse": [{"hooks": []}]}, "model": "x"}),
            encoding="utf-8",
        )
        with self.patched("_claude_settings_path", target):
            rc = _patch_claude_settings()
        self.assertEqual(rc, 0)
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("PreToolUse", data["hooks"])  # untouched
        self.assertIn("SessionEnd", data["hooks"])  # added
        self.assertEqual(data["model"], "x")

    def test_patch_claude_settings_is_idempotent(self):
        target = self.base / "settings.json"
        with self.patched("_claude_settings_path", target):
            self.assertEqual(_patch_claude_settings(), 0)
            self.assertEqual(_patch_claude_settings(), 0)  # already installed
        entries = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(entries["hooks"]["SessionEnd"]), 1)

    def test_patch_claude_settings_refuses_a_non_object_file(self):
        target = self.base / "settings.json"
        target.write_text("[1, 2]", encoding="utf-8")
        with self.patched("_claude_settings_path", target):
            rc = _patch_claude_settings()
        self.assertEqual(rc, 1)

    def test_patch_claude_settings_refuses_a_non_list_session_end(self):
        target = self.base / "settings.json"
        target.write_text(
            json.dumps({"hooks": {"SessionEnd": "borked"}}), encoding="utf-8"
        )
        with self.patched("_claude_settings_path", target):
            rc = _patch_claude_settings()
        self.assertEqual(rc, 1)

    # --- codex ----------------------------------------------------------------

    def test_patch_codex_config_from_scratch(self):
        target = self.base / "config.toml"
        with self.patched("_codex_config_path", target):
            rc = _patch_codex_config()
        self.assertEqual(rc, 0)
        text = target.read_text(encoding="utf-8")
        self.assertIn('notify = ["python3", "-m", "dream_md.hook", "turn-ended"]', text)
        self.assertIn("[features]", text)
        self.assertIn("hooks = true", text)

    def test_patch_codex_config_inserts_notify_before_sections(self):
        # TOML top-level keys must precede [sections]: notify is inserted
        # before the first one, existing settings preserved.
        target = self.base / "config.toml"
        target.write_text(
            "model = 'gpt-5'\n\n[features]\nstreaming = true\n",
            encoding="utf-8",
        )
        with self.patched("_codex_config_path", target):
            rc = _patch_codex_config()
        self.assertEqual(rc, 0)
        lines = target.read_text(encoding="utf-8").splitlines()
        notify_at = next(i for i, l in enumerate(lines) if l.startswith("notify ="))
        section_at = next(i for i, l in enumerate(lines) if l.startswith("[features]"))
        self.assertLess(notify_at, section_at)
        self.assertIn("model = 'gpt-5'", lines)
        self.assertIn("hooks = true", lines)  # merged into existing [features]
        self.assertIn("streaming = true", lines)

    def test_patch_codex_config_replaces_an_existing_notify(self):
        target = self.base / "config.toml"
        target.write_text(
            'notify = ["something", "else"]\n\n[features]\n',
            encoding="utf-8",
        )
        with self.patched("_codex_config_path", target):
            rc = _patch_codex_config()
        self.assertEqual(rc, 0)
        text = target.read_text(encoding="utf-8")
        self.assertIn("dream_md.hook", text)
        self.assertNotIn("something", text)


if __name__ == "__main__":
    unittest.main()
