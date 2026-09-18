# dream.md — implementation plan

Status: approved plan, implementation pending. Owner: GLM (code), Kimi (review), lead (decisions).
Method: DDD — see `.ddd/notes/dream-md.md` for the documentation basis.

## Pitch (one line)

**dream.md — your coding agent's memory, with receipts.** A local, zero-dependency
Python CLI that turns session transcripts into a `dream.md` memory file where every
fact is a verbatim quote graded by TypeSafe Jev's calibrated confidence. While you
sleep, your agent dreams — and in the morning it knows what it learned, what it
forgot, and how sure it is.

Why now: Claude Code has built-in auto-memory (LLM-vibes, no confidence, no
provenance, no contradiction detection). No memory tool in the ecosystem uses
calibrated judgments. Jev launched this week; the directory is empty. First mover.

## Non-negotiable design decisions

1. **Python 3, stdlib only** (sqlite3, json, argparse, urllib.request, hashlib, ftplib no). No pip install, no virtualenv. Runs on any mac/linux with python3.
2. **Zero LLM generation** — invariant #1 in DOMAIN.md. Facts are verbatim quotes.
3. **Per-project SQLite store** at `~/.dream-md/projects/<slug>.db` (slug = sha1 of absolute project path, short). dream.md file written at project root.
4. **Hooks are fire-and-forget** — `dream-md ingest` returns immediately (spawn detached child if needed); failures log to `~/.dream-md/logs/` and never affect the agent session.
5. **Offline-first** — Events are always stored locally; Jev grading happens on next successful Dream if API was down.

## Architecture

```
session transcript (jsonl)                     ┌─ Claude Code SessionEnd hook
        │                                      ├─ Codex notify hook
   [ingestion]                                 ├─ manual: dream-md dream
   events ── statements ── candidates          └─ cron
        │
   [judgment]  Jev fan-out (batched, retried)
        │
   [memory]    SQLite + FTS5: facts, events, runs, receipts
        │
   [dream]     verdicts: keep/supersede/retire/ask
        │
   dream.md  (the artifact agents read)
```

## Jev question set (fan-out, speculative — code ignores irrelevant answers)

### Phase A — grade candidates (batch ≤ ~24 candidates/request, ~32k token budget)

State:
```json
{
  "project_context": {"name": "...", "recent_files": ["..."], "build_cmd": "..."},
  "candidates": [
    {"id": "e12", "role": "user", "text": "<verbatim quote, <=600 chars>", "session_ts": "..."}
  ]
}
```

Per candidate `i` (speculative — all asked, code filters):

- `c{i}_durable` (Noul): "Is `candidates[i].text` a durable fact about how to work in this project (a preference, tool choice, constraint, convention, or pitfall) rather than ephemeral task chatter? Judge only what the quote itself states."
  - true: "A future session in this project would benefit from knowing this"
  - false: "Specific to the current task, temporary, or pure conversation filler"
- `c{i}_category` (Choice): "Which kind of durable guidance is `candidates[i].text`, if any?"
  - `preference` / `tooling` / `architecture` / `pitfall` / `convention` / `none` (each with what/not_for/examples)
- `c{i}_significance` (Score): "How much would knowing this change a future session's behavior in this project?"
  - levels: trivial / useful / important / critical (with signals)

### Phase B — dedupe & conflict (only for candidates that survived Phase A)

Retrieve top-k (k=5) similar existing Facts per candidate via FTS5. State includes
surviving candidates + their similar facts (verbatim claims). Per pair (candidate `i`, fact `j`):

- `p{i}_{j}_same_claim` (Noul): "Do `candidates[i].text` and `facts[j].claim` make the same underlying claim?"
- `p{i}_{j}_contradicts` (Noul): "Does `candidates[i].text` contradict `facts[j].claim`?"
- If contradiction ≥ 0.6: `p{i}_{j}_verdict` (Choice): new_overrides / old_stands / unclear (+ confidence)

### Phase C — audit external memory file (standalone viral command)

State: the memory file's lines (parsed to claims) + recent project evidence
(recent Facts + recent session statements). Per line `i`:

- `l{i}_supported` (Noul): "Is `lines[i].claim` supported by `evidence`?"
- `l{i}_contradicted` (Noul): "Does `evidence` contradict `lines[i].claim`?"
- `l{i}_disposition` (Choice): keep / stale / wrong / unsupported (+ confidence)

## Decision rules (code, not Jev)

- Candidate becomes Fact: `durable ≥ 0.7` AND `significance ≥ 1.0` AND not duplicate.
- Duplicate (same_claim ≥ 0.8): bump the existing Fact's support count, record additional source event; don't store a second copy.
- Conflict resolution (new_overrides with confidence ≥ 0.8): old Fact status → `superseded`, new Fact stored, link recorded. Below 0.8 → both kept, Verdict `ask`, surfaced in dream.md's "questions for you" section.
- Fact confidence over time: a Fact's confidence decays when Dreams pass without re-support and its last_supported_at ages; `retire` requires contradiction evidence with confidence ≥ 0.8, else `ask`.
- Exact thresholds live in one module (`thresholds.py`) as named constants — tunable, documented.

## SQLite schema (v1)

```sql
CREATE TABLE events (          -- immutable raw log
  id TEXT PRIMARY KEY,         -- sha256(transcript_path + line_no)
  project TEXT NOT NULL,
  source TEXT NOT NULL,        -- claude | codex | manual
  session_id TEXT,
  ts TEXT,                     -- event timestamp from transcript
  role TEXT,                   -- user | assistant | system | tool
  text TEXT,                   -- verbatim statement text
  created_at TEXT NOT NULL,
  graded_at TEXT               -- NULL while ungraded (offline queue)
);
CREATE TABLE facts (
  id INTEGER PRIMARY KEY,
  project TEXT NOT NULL,
  claim TEXT NOT NULL,         -- verbatim, never rewritten
  category TEXT NOT NULL,
  significance REAL NOT NULL,
  confidence REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',  -- active | superseded | retired
  source_event_ids TEXT NOT NULL,         -- JSON array
  support_count INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  last_supported_at TEXT
);
CREATE TABLE fact_links (
  fact_id INTEGER NOT NULL, related_id INTEGER NOT NULL,
  relation TEXT NOT NULL       -- duplicate_of | supersedes | contradicts
);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,  -- dream | audit | ingest
  started_at TEXT NOT NULL, finished_at TEXT,
  stats TEXT,                  -- JSON: candidates, facts_added, retired, api_calls, usage tokens
  error TEXT
);
CREATE VIRTUAL TABLE facts_fts USING fts5(claim, content='facts', content_rowid='id');
```

## dream.md output format

```markdown
# dream.md
> Memory with receipts — graded by Jev, written by dream-md v0.1.0. Do not edit; regenerate with `dream-md dream`.

## Tooling
- **"always use `uv run` in this repo, plain python breaks the lockfile"**
  `pitfall · important` — confidence **0.91** · seen in 3 sessions · last supported Sep 18

## Questions for you
- "we migrated to Bun" (confidence 0.55) contradicts "npm is the runtime here" — which is current?
```

## CLI surface

```
dream-md ingest --transcript PATH [--project DIR]   # log events; async-safe, offline-tolerant
dream-md dream   [--project DIR]                    # consolidation run; writes dream.md
dream-md audit   [MEMORY.md] [--project DIR]        # grade an external memory file → report
dream-md status  [--project DIR]                    # store stats + pending counts
dream-md install --agent claude|codex [--project DIR]  # print hook config (never auto-patch user settings without --yes)
```

## Transcripts (ground truth is LOCAL — verify before coding)

- Claude Code: `~/.claude/projects/<slug>/*.jsonl` — inspect real files for exact fields (role, message content blocks, timestamps, sessionId). SessionEnd hook receives `session_id` + `transcript_path` via stdin JSON.
- Codex: `~/.codex/sessions/**/*.jsonl` — inspect real files; notify hook config in `~/.codex/config.toml`.
- **GLM must read real transcript files on this machine before finalizing the parsers** and record the observed schema in `.ddd/notes/dream-md.md`.

## Milestones (slices; each ends green tests + report to lead)

- **M0** repo skeleton: package layout, `pyproject.toml` (name `dream-md`, entry point), stdlib-only check, test harness (stdlib `unittest`), `.gitignore`, initial commit.
- **M1** ingestion: transcript parsers (claude + codex, verified against real files), Event log, deterministic candidate extraction (user messages + assistant text blocks, chunked ≤600 chars, deduped by hash). Tests with fixtures built from real transcript samples (sanitized).
- **M2** judgment: Jev HTTP client (stdlib), fan-out builder (question templating with `c{i}_...` ids), 32k-token budget estimator + batcher, retry/backoff for 429/529, typed answer parsing, `FakeJev` for tests.
- **M3** memory: SQLite schema + FTS5, fact lifecycle ops, top-k similar retrieval, migrations (schema_version table).
- **M4** dream engine: phases A/B composed, verdict rules, confidence decay, dream.md writer, "questions for you" section.
- **M5** audit: parse external MEMORY.md lines, phase C, terminal report (receipts!) — the viral standalone command.
- **M6** integration: CLI wiring, `install` command emitting Claude Code hook JSON + Codex config TOML snippet, async ingest safety (detached spawn, timeout, log).
- **M7** packaging: README (pitch + GIF placeholder + quickstart), demo fixture repo, MIT LICENSE, version stamp.

## Testing strategy

- Unit tests per module, `python3 -m unittest discover` — must pass with **no network and no API key** (FakeJev).
- One live smoke script `scripts/live_smoke.py` (not in test suite): real API call against `TYPESAFE_API_KEY`, 3-candidate mini state, asserts typed answers come back. Lead runs it.
- Fixtures: real transcript excerpts from this machine, secrets scrubbed.

## Risks / open questions (Kimi's brief)

- Token math for Phase B pairwise questions (candidates × 5 facts × 2 questions) near budget limits — batcher must be tested.
- Noul has no `confidence` — our distance-from-0.5 proxy needs explicit thresholds; is 0.7 durable gate right?
- Confidence decay model is hand-rolled — simplest defensible version v1 (linear age decay + contradiction evidence), documented as such.
- Transcripts leave the machine (sent to Jev API) — privacy note must be prominent in README; per-project opt-in via `dream-md init`? (lead decision pending Kimi's review)
- Claude Code auto-memory might itself write MEMORY.md concurrently — audit is read-only, dream.md is a separate file, no conflict.
