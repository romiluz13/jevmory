#!/usr/bin/env python3
"""Reproducible real-corpus smoke for the ingestion pipeline (review R6).

Pinned, explicit file list (no globbing): the same files on every run,
so the numbers are comparable across commits and across machines that
share the corpus. Reads only; writes nothing; no network, no API key.

Exit code 0 when at least one pinned file parsed; 1 when none did (the
corpus moved, or a machine without transcripts — visible, not silent).

Run from the repo root:

    python3 scripts/corpus_smoke.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dream_md.ingestion.claude import parse_claude_transcript
from dream_md.ingestion.codex import parse_codex_transcript
from dream_md.ingestion.extract import extract_candidates
from dream_md.ingestion.redact import redactions_in
from dream_md.thresholds import CHUNK_MAX_CHARS, CONTEXT_MAX_CHARS, MIN_CANDIDATE_CHARS

# (path, parser) — deliberate mix: mainline Claude sessions, one Claude
# sidechain (agent-*.jsonl carrying the PARENT session id), and Codex
# rollouts including the exact sessions the fixtures were rebuilt from.
PINNED: tuple[tuple[str, str], ...] = (
    (
        "/Users/rom.iluz/.claude/projects/-Users-rom-iluz-Dev-orderly2/"
        "f617f58f-40f9-4a35-9d77-69bb271400b2.jsonl",
        "claude",
    ),
    (
        "/Users/rom.iluz/.claude/projects/-Users-rom-iluz-Dev/"
        "e37ab1d6-522b-4508-8df6-f5741bd07c38.jsonl",
        "claude",
    ),
    (
        "/Users/rom.iluz/.claude/projects/-Users-rom-iluz-Dev-mongodb-startup-sales/"
        "1bca0ee8-600c-4d73-bace-907f5c018c94.jsonl",
        "claude",
    ),
    (
        "/Users/rom.iluz/.claude/projects/-Users-rom-iluz-Dev-mongodb-startup-sales/"
        "1bca0ee8-600c-4d73-bace-907f5c018c94/subagents/"
        "agent-abaffe40e686fe40f.jsonl",
        "claude",
    ),
    (
        "/Users/rom.iluz/.codex/sessions/2026/05/18/"
        "rollout-2026-05-18T11-50-14-019e3a47-561e-73a0-9f5e-fa10a3c6deec.jsonl",
        "codex",
    ),
    (
        "/Users/rom.iluz/.codex/sessions/2026/05/18/"
        "rollout-2026-05-18T08-26-23-019e398c-b549-7e63-ad82-eaca7a638dd9.jsonl",
        "codex",
    ),
    (
        "/Users/rom.iluz/.codex/sessions/2026/05/16/"
        "rollout-2026-05-16T10-06-44-019e2f9b-ddc8-78b0-99e4-37c7c939f937.jsonl",
        "codex",
    ),
    (
        "/Users/rom.iluz/.codex/sessions/2026/05/05/"
        "rollout-2026-05-05T18-11-08-019df8b1-6331-7490-88c1-9706016c931d.jsonl",
        "codex",
    ),
)

PARSERS = {
    "claude": parse_claude_transcript,
    "codex": parse_codex_transcript,
}


def main() -> int:
    files_parsed = 0
    statements_total = 0
    skipped_total = 0
    candidates_total = 0
    unique_events: set[str] = set()
    role_split: Counter[str] = Counter()
    redactions_total = 0
    max_candidate_len = 0
    violations: list[str] = []

    print(f"pinned files: {len(PINNED)}")
    for path, source in PINNED:
        file_path = Path(path)
        if not file_path.exists():
            print(f"  MISSING  {path}")
            continue
        parsed = PARSERS[source](file_path)
        candidates = extract_candidates(parsed)
        files_parsed += 1
        statements_total += len(parsed.statements)
        skipped_total += len(parsed.skipped_lines)
        candidates_total += len(candidates)
        unique_events.update(c.event_id for c in candidates)
        role_split.update(c.role for c in candidates)
        redactions_total += sum(redactions_in(s.text) for s in parsed.statements)
        file_max = max((len(c.text) for c in candidates), default=0)
        max_candidate_len = max(max_candidate_len, file_max)
        for candidate in candidates:
            if len(candidate.text) > CHUNK_MAX_CHARS:
                violations.append(f"{path}: candidate over cap")
            if len(candidate.text) < MIN_CANDIDATE_CHARS:
                violations.append(f"{path}: candidate under floor")
            if len(candidate.context) > CONTEXT_MAX_CHARS:
                violations.append(f"{path}: context over cap")
        name = file_path.name
        users = sum(1 for c in candidates if c.role == "user")
        assistants = sum(1 for c in candidates if c.role == "assistant")
        print(
            f"  ok  {source:6s} {name[:60]:60s} "
            f"stmts={len(parsed.statements):4d} skipped={len(parsed.skipped_lines):2d} "
            f"cands={len(candidates):4d} max_len={file_max:3d} "
            f"user={users}/assistant={assistants}"
        )

    print()
    print(f"files parsed:        {files_parsed}")
    print(f"statements:          {statements_total}")
    print(f"malformed lines:     {skipped_total}")
    print(f"candidates:          {candidates_total}")
    print(f"unique event ids:    {len(unique_events)}")
    print(f"max candidate len:   {max_candidate_len} (cap {CHUNK_MAX_CHARS})")
    print(
        "role split:          "
        + " ".join(f"{role}={count}" for role, count in sorted(role_split.items()))
    )
    print(f"secret replacements: {redactions_total}")
    print(f"violations:          {len(violations)}")
    for violation in violations:
        print(f"  {violation}")
    return 0 if files_parsed else 1


if __name__ == "__main__":
    raise SystemExit(main())
