# jevmory.md — implementation plan (v2, post-review)

Status: v2 — incorporates Kimi's plan attack (`.team/kimi/findings-plan.md`); all
blockers/majors accepted, adjudication logged in `.ddd/notes/jevmory.md`.
Owner: GLM (code), Kimi (review), lead (decisions). Method: DDD.

v2 changes from v1 (what GLM must re-read):
1. **Privacy (was F5.1/F5.2, blockers)**: hooks ingest locally only; grading requires
   per-project opt-in marker; deterministic redaction at ingest, before storage.
2. **Extraction (F1.1/F1.2)**: sentence-boundary chunking, anaphoric-start drop,
   user-role bias, verbatim `context` field (1–2 surrounding exchanges) on facts.
3. **Confidence (F3.1)**: one explicit formula; raw judgments stored (judgments table).
4. **No decay in v1 (F4.1)**: staleness is a visual badge, never a confidence mutation.
5. **Phase B (F2.1–F2.3)**: code-level dedupe first; token estimator counts state +
   questions; contradiction question only for high-significance candidates; always
   ask all three pair questions speculatively.
6. **Schema (F7.1–F7.4)**: judgments table, content-hash event ids, FTS maintenance,
   UNIQUE constraint, schema_version in first migration, WAL + busy_timeout.
7. **Hook safety (F6.1–F6.4)**: synchronous local ingest, always exit 0, status
   shows last ingest/error, partial-tail tolerant parser.
8. **Milestones reordered (F8.1)**: audit before dream engine — audit is the demo lead.
9. **New CLI**: `init --enable-grading`, `resolve`, `--scan`, `--offline`, `--test`.
10. **Demo (F8.1/F8.2)**: planted-error fixture is mandatory; audit report designed
    for screenshots; real GIF in M7.

## Pitch (one line)

**jevmory.md — your coding agent's memory, with receipts.** Local, zero-dependency
Python CLI. Turns session transcripts into a `jevmory.md` memory file where every
fact is a verbatim quote graded by TypeSafe Jev's calibrated confidence, with
redaction at rest. The viral one-liner: `jevmory audit MEMORY.md` — "your agent's
memory has 3 stale lines and 1 wrong one; here are the receipts."

## Non-negotiable design decisions

1. **Python 3, stdlib only.** No pip deps. Runs anywhere python3 runs.
2. **Zero LLM generation.** Facts are verbatim quotes (with verbatim context).
   Jev judges; code selects and composes.
3. **Per-project SQLite store** at `~/.jevmory/projects/<slug>.db`; `jevmory.md`
   written at project root, sentinel-guarded (refuse overwrite without sentinel
   unless `--force`).
4. **Privacy by architecture:** hooks only ever ingest locally. Grading (anything
   that calls the Jev API) requires a per-project opt-in marker
   `~/.jevmory/projects/<slug>.optin`, created by `jevmory init --enable-grading`.
   No marker → candidates queue; `status` says so. README documents exactly what
   leaves (redacted candidate quotes ≤600 chars, never whole transcripts).
5. **Redaction at rest:** deterministic scrub pass at ingest, BEFORE storage —
   the local DB never holds raw secrets. Patterns: `sk-*`, `AKIA*`, `gh[ps]_*`,
   `ghp_*`, JWTs (`eyJ…`), `-----BEGIN … KEY-----`, `password=…`/`token=`/`api_key=`
   assignments, hex/base64 high-entropy ≥20 chars, bearer tokens. Replace with
   `[redacted:<kind>]`. Unit-tested on a corpus of realistic secrets.
6. **Hooks never break sessions and never die silently:** ingest is synchronous,
   local-only (<500ms), wrapped `try/except → log → exit(0)`. `status` shows last
   ingest timestamp + last error. Partial/unparseable trailing JSONL lines are
   skipped (content-hash ids make re-ingest safe).
7. **SQLite robustness:** `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`
   on every connection; short write transactions (concurrent session ends are normal).
8. **Offline-first:** Events always stored locally; ungraded queue waits for a
   Dream with API access. `--offline` flag forces local-only everywhere.

## Architecture

```
transcripts (jsonl) ── hooks (claude SessionEnd / codex notify) ── or --scan discovery ── or manual
        │
   [ingestion]  parse → redact → events (immutable) → candidates (sentence chunks)
        │
   [judgment]   Jev fan-out (batched by token estimate, retried) — only when opted in
        │
   [memory]     SQLite+WAL+FTS5: facts, events, judgments, runs, receipts
        │
   [dream]      verdicts: keep / supersede / retire / ask(→ resolve or expire)
        │
   jevmory.md  (sentinel-guarded artifact agents read)
```

## Ingestion details

- **Candidate extraction (deterministic):**
  - Statements = user messages always; assistant text-block statements too.
  - Chunk on sentence boundaries (stdlib `re`), target 280–600 chars, never split
    a sentence. Drop chunks starting with anaphoric tokens (that/this/it/they/the
    same/those) — they're not self-contained.
  - Event id = `sha256(normalized line content)` (self-healing under transcript
    rewrites). Candidate dedupe by content hash.
  - Every candidate carries a verbatim `context` field: the 1–2 preceding exchange
    turns (redacted, ≤800 chars). Receipts include it; `--verbose` surfaces it.
- **Transcript formats (ground truth is LOCAL):** Claude `~/.claude/projects/…`,
  Codex `~/.codex/sessions/**`. GLM records observed schemas in the DDD note.
- **`--scan` mode:** discover the project's recent transcripts by matching the
  project path inside transcript records — hooks become a convenience, never a
  requirement. Hook payload verification (claude SessionEnd stdin JSON; codex
  notify argv JSON with `turn-ended`) stays a DDD-note item with `--scan` as the
  guaranteed fallback.

## Jev question set (fan-out, speculative — code ignores irrelevant answers)

### Phase A — grade candidates (batch by token estimate: state + all question instructions, chars/4, ≤ ~28k tokens)

State: `{ "project_context": {...}, "candidates": [ {id, role, text, context} ] }`

Per candidate `i`:
- `c{i}_durable` (Noul): durable fact about working in this project vs ephemeral
  chatter (true/false criteria per v1).
- `c{i}_category` (Choice): preference | tooling | architecture | pitfall | convention | none.
- `c{i}_significance` (Score): trivial | useful | important | critical.

Speculative: always ask all three; code filters.

### Phase B — dedupe & conflict (only survivors; code-level pre-pass first)

1. **Code-level dedupe first (zero Jev spend):** normalized-text hash equal, or
   Jaccard ≥ 0.6 against an existing fact → auto-duplicate (bump support).
2. FTS5 retrieve k=10 per candidate, re-rank by token overlap, keep top-5.
3. Contradiction questions only for candidates with `significance ≥ 2`
   (v1 cost cap; note in thresholds.py).
4. Per pair, ask all three speculatively (no conditional branching in batch shapes):
   - `p{i}_{j}_same_claim` (Noul)
   - `p{i}_{j}_contradicts` (Noul)
   - `p{i}_{j}_verdict` (Choice: new_overrides | old_stands | unclear) — code
     ignores unless `contradicts ≥ 0.6`.
5. Batcher: if state+questions exceed budget → per-candidate requests
   (1 candidate + its facts). Synthetic load tests at 300 and 500 candidates.

### Phase C — audit external memory file (the demo lead)

State: parsed claims from the memory file + evidence (recent facts + recent
statements, redacted). Per line `i`:
- `l{i}_supported` (Noul), `l{i}_contradicted` (Noul),
- `l{i}_disposition` (Choice: keep | stale | wrong | unsupported + confidence).

## Confidence — one formula (F3.1 resolution)

```
fact.confidence = clamp01(2 * |durable_noul − 0.5|)      # certainty of durability
```
Raw Jev answers are stored verbatim in the `judgments` table — every number in
jevmory.md is reproducible from stored receipts. Choice/Score confidences are used
in their own decision rules, never blended into `fact.confidence`.
Documented in `thresholds.py` as named constants + this formula.

## Decision rules (code, not Jev)

- Candidate → Fact: `durable ≥ 0.7` (user role) / `≥ 0.8` (assistant role — user
  statements are decisions, assistant statements are narration) AND
  `significance ≥ 1.0` AND not duplicate.
- Near-misses (0.5–0.7) are NOT silent: counted in `runs.stats.dropped_low_durable`,
  surfaced in `status` as "N near-miss candidates".
- Duplicate (`same_claim ≥ 0.8`): bump support_count, add source event id.
- Conflict: `new_overrides` AND `confidence ≥ 0.8` → supersede (old status
  `superseded`, removed from FTS, kept for provenance). Otherwise → both kept,
  Verdict `ask`.
- **No decay in v1.** `last_supported_at` shown as receipt ("last seen Sep 18");
  purely visual staleness badge at >30 days. Only contradiction evidence changes
  status. (Rationale: absence of mention is not evidence of staleness.)
- **Ask lifecycle:** asks expire after 3 consecutive unresolved dreams (counted in
  stats); `jevmory resolve <fact_id> --keep-new|--keep-old` writes a resolution
  row (judgment_kind='human') and applies it.
- Thresholds are priors, hand-tuned on the fixture corpus before the demo; tuning
  recorded in the DDD note.

## SQLite schema (v2)

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE events (
  id TEXT PRIMARY KEY,                -- sha256(normalized line content)
  project TEXT NOT NULL, source TEXT NOT NULL, session_id TEXT,
  ts TEXT, role TEXT, text TEXT NOT NULL,   -- redacted at ingest
  created_at TEXT NOT NULL, graded_at TEXT
);
CREATE TABLE facts (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL,
  claim TEXT NOT NULL,                -- verbatim, redacted
  context TEXT,                       -- verbatim surrounding exchange, redacted, <=800 chars
  category TEXT NOT NULL, significance REAL NOT NULL,
  confidence REAL NOT NULL,           -- formula above
  status TEXT NOT NULL DEFAULT 'active',
  source_event_ids TEXT NOT NULL,     -- JSON array
  support_count INTEGER NOT NULL DEFAULT 1,
  ask_seen_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_supported_at TEXT
);
CREATE TABLE fact_links (
  fact_id INTEGER NOT NULL, related_id INTEGER NOT NULL,
  relation TEXT NOT NULL,             -- duplicate_of | supersedes | contradicts
  UNIQUE(fact_id, related_id, relation)
);
CREATE TABLE judgments (              -- the actual receipts (F7.1)
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
  subject_kind TEXT NOT NULL,         -- candidate | fact | pair | line
  subject_id TEXT NOT NULL,
  question_id TEXT NOT NULL, type TEXT NOT NULL,
  answer_json TEXT NOT NULL,          -- verbatim Jev answer
  created_at TEXT NOT NULL
);
CREATE INDEX idx_judgments_subject ON judgments(subject_kind, subject_id);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  stats TEXT,                         -- JSON incl. api_calls, usage tokens, dropped_low_durable
  error TEXT
);
CREATE VIRTUAL TABLE facts_fts USING fts5(claim, content='facts', content_rowid='id');
-- FTS maintenance: remove from FTS on retire/supersede (row kept for provenance);
-- active-status facts only ever participate in similarity search.
```

## jevmory.md output format

```markdown
<!-- jevmory v0.1.0 sentinel — generated file, do not edit; regenerate with `jevmory dream` -->
# jevmory.md

## Tooling
- **"always use `uv run` in this repo, plain python breaks the lockfile"**
  `pitfall · important` — confidence **0.91** (2·|0.955−0.5|) · seen in 3 sessions · last seen Sep 18

## Questions for you
- "we migrated to Bun" (confidence 0.55) vs "npm is the runtime here" — which is current? `jevmory resolve <id> --keep-new|--keep-old`
```

## CLI surface (v2)

```
jevmory init [--enable-grading] [--project DIR]   # per-project setup; opt-in marker
jevmory ingest --transcript PATH | --scan [--project DIR] [--test]  # local-only; --test verifies hook payload
jevmory dream   [--project DIR] [--offline]       # grading + consolidation; writes jevmory.md
jevmory audit   [MEMORY.md] [--project DIR] [--json|--md]  # demo lead; screenshot-shaped report
jevmory resolve <fact_id> --keep-new|--keep-old   # human closes an ask
jevmory status  [--project DIR]                   # store stats, pending, near-misses, last ingest/error, paths
jevmory install --agent claude|codex [--project DIR]  # PRINTS hook config; never patches without --yes
```

## Milestones (v2 order — audit is the demo lead)

- **M0** repo skeleton: package layout, pyproject (name jevmory, entry point),
  stdlib-only, unittest harness, initial commit.
- **M1** ingestion: parsers verified against REAL local transcripts (schemas
  recorded in DDD note), redaction pass + corpus tests, sentence-boundary
  chunking + anaphoric drop + context capture, content-hash event ids, Event log
  (WAL, busy_timeout), partial-tail tolerance. Fixtures: sanitized real excerpts.
- **M2** judgment: Jev HTTP client, fan-out builder, token estimator counting
  state+questions, batcher with per-candidate fallback, retry/backoff (429/529),
  typed answers, `FakeJev` with **adversarial mode** (flat distributions, 0.5
  nouls, error injection) so ask/queue logic is tested.
- **M3** memory: schema v2 (judgments table, schema_version), fact lifecycle,
  code-level dedupe (hash + Jaccard), FTS k=10→re-rank top-5, FTS maintenance.
- **M4** audit (MOVED UP): memory-file line parser, Phase C, screenshot-shaped
  terminal report (aligned columns, verdict keywords, receipts), `--json`/`--md`.
- **M5** dream engine: phases A/B composed, verdict rules, ask expiry + resolve,
  jevmory.md writer with sentinel guard.
- **M6** integration: CLI wiring, `init --enable-grading`, `install` (claude hook
  JSON + codex notify snippet, print-only), `--scan` discovery, `--test` payload
  verifier, ingest never-exit-nonzero wrapper.
- **M7** packaging: README (pitch, quickstart incl. .gitignore hint for jevmory.md,
  privacy section: what leaves/when/how to stay local), **planted-error demo
  fixture repo** (stale line + contradiction guaranteed present), real recorded
  GIF, MIT LICENSE, stats in `status`.

## Testing strategy

- `python3 -m unittest discover` green with no network, no API key (FakeJev incl.
  adversarial mode).
- Synthetic load tests at 300/500 candidates for the batcher (F2.1).
- Concurrency test: two threads ingesting simultaneously (WAL/busy_timeout).
- Redaction corpus test with realistic secrets.
- Live smoke (lead-run, budget-capped): `scripts/live_smoke.py` 3-candidate call,
  plus ONE real-transcript dream run on this machine — capped at ~40 API requests,
  usage logged in `runs.stats` (adjudicated: part of smoke budget per MISSION).

## Privacy statement (README, verbatim intent)

- What leaves: redacted candidate quotes (≤600 chars) + minimal project context —
  only during `dream`/`audit` with `TYPESAFE_API_KEY` set AND project opted in.
- Never: whole transcripts, file paths beyond project name, secrets (redacted at
  rest before any storage).
- Fully local mode: `--offline` / no key / no opt-in — events queue, nothing leaves.
