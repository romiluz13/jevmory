# dream.md — your coding agent's memory, with receipts

Local, zero-dependency Python CLI. Turns session transcripts into a
`dream.md` memory file where every fact is a **verbatim quote** graded by
[TypeSafe Jev](https://typesafe.ai)'s calibrated confidence, with redaction
at rest.

The viral one-liner:

```
dream-md audit MEMORY.md
```

> your agent's memory has 1 stale line, 1 wrong one, and 1 unsupported one;
> here are the receipts.

## What it does

Your coding agent (Claude Code, Codex CLI) writes session transcripts to
disk. dream-md watches, ingests, and grades them:

1. **Hooks ingest locally** — a `SessionEnd` hook (Claude Code) or `notify`
   hook (Codex CLI) streams transcript events into a per-project SQLite
   store at `~/.dream-md/projects/<slug>.db`. Secrets are redacted at rest,
   before storage. Hooks always exit 0; they can never break a session.
2. **`dream-md dream` grades while you sleep** — sentence candidates are
   extracted, deduped, and (only with your opt-in) sent to the Jev API for
   calibrated judgment: durability, category, significance, support,
   contradiction. Raw judgments are stored — every fact has receipts.
3. **`dream-md audit MEMORY.md` checks any memory file** — each line is
   graded against the evidence in the store and printed as a
   screenshot-shaped report: `STALE / WRONG / UNSUPPORTED / KEEP`, with
   confidence, support, contradiction, and the run receipt.
4. **`dream.md` lands at your project root** — grouped by category,
   confidence-ordered, every line a verbatim quote with provenance.
   Sentinel-guarded: never overwritten without your say-so.

No LLM generation anywhere: **Jev judges; code selects and composes.**
Facts are quotes, not summaries — the memory can't hallucinate.

## Quickstart

Requires Python ≥3.10 and nothing else — stdlib only, no pip deps.

```sh
git clone https://github.com/you/dream-md && cd dream-md

# in your project:
dream-md init                     # creates the ~/.dream-md/projects/<slug>.db store
dream-md install claude           # prints the Claude Code SessionEnd hook JSON
dream-md install codex            # prints the Codex notify snippet (--yes patches config)

# stay fully local (nothing ever leaves):
dream-md ingest --scan            # finds this project's transcripts and ingests
dream-md status                   # queued candidates, last ingest, errors

# or opt in to grading (needs $TYPESAFE_API_KEY):
dream-md init --enable-grading    # per-project opt-in marker
dream-md dream                    # grade queued candidates
dream-md audit MEMORY.md          # receipts for every memory line
dream-md resolve <id>             # answer a contradiction question
```

Add `dream.md` to your project's `.gitignore` if you don't want agent
memory in version control — it's yours, not the repo's.

Fully offline? Just never run `init --enable-grading`. Everything else —
ingest, status, scan — is local by architecture (see Privacy).

## Demo (offline, deterministic, zero cost)

```sh
python3 demo/run_demo.py
```

A planted-error fixture: `demo/MEMORY.md` has 5 memory lines — 3 with
planted errors (stale, wrong, unsupported) contradicted by the evidence in
`demo/transcript.jsonl`. Judgments are pinned offline, so the errors are
guaranteed present and the demo costs nothing:

```
dream-md audit — demo/MEMORY.md
your memory has 1 stale line, 1 wrong line, 1 unsupported line; 2 keep

LINE  VERDICT      CONF  SUPP  CONTRA  CLAIM
   5  STALE        0.82  0.34    0.71  The build runs on Bun; bun run build is the…
   6  WRONG        0.93  0.07    0.93  The test suite runs with pytest.
   7  UNSUPPORTED  0.74  0.06    0.05  Failed API requests retry up to five times…

receipts: run 1 · 1 api calls · 838 tokens · evidence: 0 facts, 6 statements
```

The live `dream-md audit` produces exactly this shape, with real Jev
judgments behind the numbers.

## Privacy

- **What leaves:** redacted candidate quotes (≤600 chars) + verbatim
  context (≤800 chars) + minimal project context — only during
  `dream`/`audit` with `$TYPESAFE_API_KEY`
  set **and** the project opted in (`dream-md init --enable-grading`).
- **Never:** whole transcripts, file paths beyond the project name,
  secrets (redacted at rest, before any storage — `sk-*`, AWS keys, GitHub
  tokens, JWTs, PEM blocks, `password=`/`token=`/`api_key=` assignments,
  high-entropy hex/base64, bearer tokens → `[redacted:<kind>]`).
- **Fully local mode:** `--offline` / no key / no opt-in — events queue,
  nothing leaves. No marker → candidates queue; `dream-md status` says so.

## Design decisions (non-negotiable)

1. **Python 3 stdlib only.** No pip deps, ever. Runs anywhere python3 runs.
2. **Zero LLM generation.** Facts are verbatim quotes with verbatim
   context. Jev judges; code selects and composes.
3. **Per-project SQLite store**, WAL + busy_timeout, `schema_version`
   migrations, FTS retrieval with re-rank.
4. **Privacy by architecture.** Hooks only ingest locally; grading
   requires an explicit per-project opt-in marker; redaction at rest
   before any storage.
5. **Hooks never break sessions and never die silently.** Ingest is
   synchronous, always exits 0, and errors surface in `dream-md status`.
6. **Receipts for everything.** Raw judgments and usage are stored —
   `runs`/`judgments` tables — so any number in the report can be
   traced to the API call that produced it.

## Live smoke (lead-run)

The one network path is pinned by a budget-capped smoke script:

```sh
TYPESAFE_API_KEY=… python3 scripts/live_smoke.py --probe   # 3 synthetic candidates, 1 request
TYPESAFE_API_KEY=… python3 scripts/live_smoke.py --dream   # 1 real dream run, capped
```

Hard cap on requests (`--cap`, default 50); `BudgetExhausted` is a
`JevError`, so the run row closes and events stay queued — the smoke
cannot overspend.

## Status

504 offline tests (`python3 -m unittest discover`) — no network, no API
key, FakeJev including adversarial mode. Modules M0–M7 complete.

## License

MIT — see [LICENSE](LICENSE).
