# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-21

Two-stage audit with deterministic verification, vintage receipts, a
local grading backend, and a planted-truth fidelity benchmark.

### Added

- **Two-stage audit**: stage 1 is a deterministic anchor check — a
  memory line that matches an active stored fact verbatim
  (normalized equality or containment above a trivial-substring
  floor, oldest-fact-wins determinism) is `VERIFIED` with zero API
  spend and no judgment receipts; only the unanchored remainder goes
  to the model pipeline. Stage 2 runs the existing Phase C Jev
  grading unchanged.
- **Vintage receipts (schema v3)**: anchored facts get a
  `verified_at` stamp written back, and `jevmory.md` receipts now
  read "said {date} · verified {date}" or "· unverified" — the
  memory shows when each quote was captured and last confirmed,
  never silently.
- **kev backend**: `--backend kev` (or `$JEVMORY_BACKEND`) points
  grading at a local wire-compatible Jev server
  ([jaredpalmer/kev](https://github.com/jaredpalmer/kev),
  `$JEVMORY_KEV_ENDPOINT`, default `127.0.0.1:8009`) — no key, no
  external egress, opt-in marker still required. Receipts are
  experimental: a general model grading durability runs hotter than
  the tuned Jev.
- **Fidelity benchmark** (`scripts/bench_fidelity.py`): a
  planted-truth fixture measuring stage-1 anchor precision/recall
  offline (the guaranteed floor), a full-pipeline harness self-check
  against an ideal scripted model (must be perfectly diagonal), and
  `--live` for the real model's end-to-end confusion matrix.

### Changed

- **`dream` → `distill`** throughout: module, CLI command, docs —
  the name now says what it does.
- **`AUDIT_DISPOSITION_GATE` 0.6 → 0.8**: the review band tightens
  to match the citation_check cookbook's AUTO_ACCEPT stance — only
  decisive answers become verdict keywords.
- 553 offline tests, up from 508.

### Schema

- `facts.verified_at TEXT` (v3); v2 stores migrate forward
  idempotently via `ALTER TABLE`, existing rows keep `NULL`.

## [0.1.0] - 2026-09-19

First public release. Local, zero-dependency memory for coding agents,
where every fact is a verbatim quote graded by calibrated confidence.

### Added

- **Ingestion**: parsers for Claude Code and Codex CLI session
  transcripts; transcript discovery/scan; sentence-boundary candidate
  extraction; deterministic redaction at rest — secrets are scrubbed
  before anything is written to the store.
- **Judgment**: typed client for the TypeSafe Jev API — strict answer
  parsing, retries with backoff, batched requests, token/usage
  accounting — plus an offline `FakeJev` for tests and demos.
- **Memory**: per-project SQLite store (WAL), fact lifecycle
  (keep/supersede/retire/ask), hash + Jaccard dedupe, FTS retrieval
  with re-rank, and receipts for every judgment and run (any number in
  a report is reproducible from the store).
- **Dream engine**: Phase A candidate grading and Phase B
  contradiction pairing composed into runs, ask expiry with human
  `resolve`, and the `jevmory.md` writer with a versioned sentinel guard.
- **Audit**: any memory file graded line-by-line against stored
  evidence — screenshot-shaped terminal report plus `--md`/`--json`
  renderers, verdicts `KEEP / STALE / WRONG / UNSUPPORTED` with
  confidence, support, contradiction, and run receipts.
- **Hooks and CLI**: SessionEnd (Claude Code) and notify (Codex CLI)
  hooks that always exit 0 and never break a session; commands `init`,
  `ingest`, `dream`, `audit`, `resolve`, `status`, `install`.
- **Privacy**: grading is opt-in per project (`init --enable-grading`)
  and sends only redacted quotes with verbatim context; transcripts,
  metadata, and secrets never leave the machine.
- **Tooling**: offline planted-error demo (`demo/run_demo.py`),
  budget-capped live smoke (`scripts/live_smoke.py`), 508 offline
  tests.

### Engineering

- Zero runtime dependencies; Python 3.10+ standard library only —
  enforced by a packaging test that AST-scans the package.
- No LLM generation anywhere: Jev judges; code selects and composes.

[0.2.0]: https://github.com/romiluz13/jevmory/releases/tag/v0.2.0
[0.1.0]: https://github.com/romiluz13/jevmory/releases/tag/v0.1.0
