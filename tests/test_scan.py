"""Scan tests (M6): transcript discovery, honestly bounded.

`--scan` walks the agent roots and matches transcripts by string
containment of the project path inside the first 64KB. These tests
pin the real behaviors, including the DELIBERATE limits: containment
is a pre-filter (a transcript that merely prints the path matches —
costs one parse, never a wrong store), and a cwd buried past the head
is missed (cheapness is the point). Scan is read-only: nothing under
the home changes.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from dream_md.ingestion.scan import detect_source, discover_transcripts

CLAUDE_SID = "00000000-0000-4000-8000-0000000000c1"
CODEX_SID = "11111111-0000-4000-8000-0000000000d1"


def claude_line(cwd: str | None, sid: str = CLAUDE_SID) -> str:
    return json.dumps(
        {
            "type": "user",
            "sessionId": sid,
            "cwd": cwd,
            "promptSource": "typed",
            "origin": {"kind": "human"},
            "message": {"role": "user", "content": "Always lint before commit."},
        }
    )


def write_claude_transcript(path: Path, cwd: str | None, sid: str = CLAUDE_SID) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "mode", "mode": "normal", "sessionId": sid})
        + "\n"
        + claude_line(cwd, sid)
        + "\n",
        encoding="utf-8",
    )
    return path


def write_codex_transcript(path: Path, cwd: str | None, sid: str = CODEX_SID) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-18T08:51:01.000Z",
                "ordinal": 0,
                "type": "session_meta",
                "payload": {"session_id": sid, "id": sid, "cwd": cwd},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class DiscoverTranscriptsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.project = self.home / "proj"
        self.project.mkdir()
        # Scan matches the REALPATH of the project against transcript
        # text, so fixtures must embed the resolved spelling (on macOS
        # the tmp dir is /var/... which realpath turns into /private/var).
        self.cwd = str(self.project.resolve())

    def tearDown(self):
        self._tmp.cleanup()

    def test_finds_claude_transcript_for_the_project(self):
        transcript = write_claude_transcript(
            self.home / ".claude" / "projects" / "p" / f"{CLAUDE_SID}.jsonl",
            self.cwd,
        )
        found = discover_transcripts(str(self.project), home=self.home)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].path, str(transcript))
        self.assertEqual(found[0].source, "claude")

    def test_finds_codex_transcript_for_the_project(self):
        transcript = write_codex_transcript(
            self.home / ".codex" / "sessions" / "2026" / "05" / "18" / "rollout-x.jsonl",
            self.cwd,
        )
        found = discover_transcripts(str(self.project), home=self.home)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].path, str(transcript))
        self.assertEqual(found[0].source, "codex")

    def test_newest_first(self):
        older = write_claude_transcript(
            self.home / ".claude" / "projects" / "p" / "older.jsonl",
            self.cwd,
        )
        newer = write_claude_transcript(
            self.home / ".claude" / "projects" / "p" / "newer.jsonl",
            self.cwd,
        )
        stamp_old, stamp_new = 1_000_000.0, 2_000_000.0
        os.utime(older, (stamp_old, stamp_old))
        os.utime(newer, (stamp_new, stamp_new))
        found = discover_transcripts(str(self.project), home=self.home)
        self.assertEqual([f.path for f in found], [str(newer), str(older)])
        self.assertEqual(found[0].mtime, stamp_new)

    def test_other_projects_transcripts_do_not_match(self):
        write_claude_transcript(
            self.home / ".claude" / "projects" / "p" / "sid.jsonl",
            str(self.home / "unrelated"),
        )
        write_codex_transcript(
            self.home / ".codex" / "sessions" / "2026" / "05" / "18" / "r.jsonl",
            str(self.home / "unrelated"),
        )
        self.assertEqual(discover_transcripts(str(self.project), home=self.home), [])

    def test_missing_roots_are_not_an_error(self):
        # A machine with only one agent installed (or none) scans clean.
        self.assertEqual(discover_transcripts(str(self.project), home=self.home), [])

    def test_containment_is_a_prefilter_not_a_proof(self):
        # S8 stance, pinned: a transcript whose CWD belongs elsewhere but
        # which merely PRINTS this project's path in its head still
        # matches. Fine by design — the parser's own session cwd decides
        # the store at ingest time, so a false positive costs one parse.
        mention = write_claude_transcript(
            self.home / ".claude" / "projects" / "p" / "mention.jsonl",
            str(self.home / "elsewhere"),
        )
        with open(mention, "a", encoding="utf-8") as fh:
            fh.write(claude_line(self.cwd) + "\n")
        found = discover_transcripts(str(self.project), home=self.home)
        self.assertEqual(len(found), 1)

    def test_match_buried_past_the_head_is_missed(self):
        # The honest cost of cheapness: only the first 64KB is sampled,
        # so a cwd that first appears past the head is not discovered.
        # (Session metadata lives at the head in real transcripts.)
        transcript = self.home / ".claude" / "projects" / "p" / "buried.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        padding = json.dumps({"type": "user", "sessionId": CLAUDE_SID,
                              "cwd": str(self.home / "elsewhere"),
                              "message": {"role": "user", "content": "x" * 70_000}})
        transcript.write_text(
            padding + "\n" + claude_line(self.cwd) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(discover_transcripts(str(self.project), home=self.home), [])

    def test_scan_is_read_only(self):
        write_claude_transcript(
            self.home / ".claude" / "projects" / "p" / "sid.jsonl",
            self.cwd,
        )
        discover_transcripts(str(self.project), home=self.home)
        # no dream-md state appeared anywhere under the scanned home
        self.assertIsNone(
            next(self.home.rglob(".dream-md"), None)
        )


class DetectSourceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, first_line: str) -> Path:
        path = self.base / name
        path.write_text(first_line + "\n", encoding="utf-8")
        return path

    def test_location_hints_win_without_opening_the_file(self):
        # A path under .claude/ or .codex/ is decided by location alone;
        # these files do not even exist.
        from dream_md.ingestion.scan import detect_source

        self.assertEqual(detect_source("/nowhere/.claude/projects/x.jsonl"), "claude")
        self.assertEqual(detect_source("/nowhere/.codex/sessions/x.jsonl"), "codex")

    def test_codex_sniffed_from_session_meta_first_line(self):
        path = self.write(
            "sniff-codex.jsonl",
            json.dumps(
                {"type": "session_meta", "payload": {"id": "x", "cwd": "/p"}}
            ),
        )
        self.assertEqual(detect_source(path), "codex")

    def test_claude_sniffed_from_sessionId_first_line(self):
        path = self.write(
            "sniff-claude.jsonl",
            json.dumps({"type": "mode", "sessionId": CLAUDE_SID}),
        )
        self.assertEqual(detect_source(path), "claude")

    def test_location_beats_content(self):
        # A codex-shaped file filed under .claude/ is claude's: the
        # location is authoritative, the sniff is the fallback.
        inner = self.base / ".claude" / "projects" / "x.jsonl"
        inner.parent.mkdir(parents=True, exist_ok=True)
        inner.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "x"}}) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(detect_source(inner), "claude")

    def test_unrecognizable_file_raises_value_error(self):
        path = self.write("mystery.jsonl", json.dumps({"hello": "world"}))
        with self.assertRaises(ValueError):
            detect_source(path)


if __name__ == "__main__":
    unittest.main()
