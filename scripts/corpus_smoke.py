#!/usr/bin/env python3
"""Reproducible real-corpus smoke for the ingestion pipeline (review R6).

Pinned, explicit file list (no globbing): the same files on every run,
so the numbers are comparable across commits and across machines that
share the corpus. Reads only; writes nothing; no network, no API key.

The corpus list lives OUTSIDE this repo — real transcript paths are
machine-local and private. Point JEVMORY_SMOKE_CORPUS at a text file with
one `<path> <parser>` pair per line (`#` comments and blank lines
ignored, `~` expanded):

    JEVMORY_SMOKE_CORPUS=~/.jevmory/smoke-corpus.txt python3 scripts/corpus_smoke.py

Pinning the same file keeps runs comparable; the env indirection keeps
personal paths out of the repo and lets each machine pin its own corpus.

Exit code 0 when at least one pinned file parsed; 1 when none did (the
corpus moved, the list is unset, or a machine without transcripts —
visible, not silent).

Run from the repo root:

    python3 scripts/corpus_smoke.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevmory.ingestion.claude import parse_claude_transcript
from jevmory.ingestion.codex import parse_codex_transcript
from jevmory.ingestion.extract import extract_candidates
from jevmory.ingestion.redact import redactions_in
from jevmory.thresholds import CHUNK_MAX_CHARS, CONTEXT_MAX_CHARS, MIN_CANDIDATE_CHARS

CORPUS_ENV = "JEVMORY_SMOKE_CORPUS"


def load_pinned() -> tuple[tuple[str, str], ...]:
    """Load (path, parser) pairs from the env-specified corpus file."""
    list_path = os.environ.get(CORPUS_ENV)
    if not list_path:
        print(f"error: {CORPUS_ENV} is not set")
        print(f"       write a corpus list file (one '<path> <parser>' per line,")
        print("       '#' comments ignored) and point the env var at it, e.g.:")
        print(f"       export {CORPUS_ENV}=~/.jevmory/smoke-corpus.txt")
        return ()
    file = Path(os.path.expanduser(list_path))
    if not file.exists():
        print(f"error: corpus list {file} does not exist")
        return ()
    pinned: list[tuple[str, str]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or parts[1] not in ("claude", "codex"):
            print(f"error: bad line in {file}: {line!r}")
            print("       expected: <path> <claude|codex>")
            return ()
        pinned.append((os.path.expanduser(parts[0]), parts[1]))
    return tuple(pinned)


PINNED: tuple[tuple[str, str], ...] = load_pinned()

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
