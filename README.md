# jevmory — your coding agent's memory, with receipts

Local, zero-dependency Python CLI. Turns session transcripts into a
`jevmory.md` memory file where every fact is a **verbatim quote** graded by
[TypeSafe Jev](https://typesafe.ai)'s calibrated confidence, with redaction
at rest.

The viral one-liner:

```
jevmory audit MEMORY.md
```

> your agent's memory has 1 stale line, 1 wrong one, and 1 unsupported one;
> here are the receipts.

## What it does

Your coding agent (Claude Code, Codex CLI) writes session transcripts to
disk. jevmory watches, ingests, and grades them:

1. **Hooks ingest locally** — a `SessionEnd` hook (Claude Code) or `notify`
   hook (Codex CLI) streams transcript events into a per-project SQLite
   store at `~/.jevmory/projects/<slug>.db`. Secrets are redacted at rest,
   before storage. Hooks always exit 0; they can never break a session.
2. **`jevmory dream` grades while you sleep** — sentence candidates are
   extracted, deduped, and (only with your opt-in) sent to the Jev API for
   calibrated judgment: durability, category, significance, support,
   contradiction. Raw judgments are stored — every fact has receipts.
3. **`jevmory audit MEMORY.md` checks any memory file** — each line is
   graded against the evidence in the store and printed as a
   screenshot-shaped report: `STALE / WRONG / UNSUPPORTED / KEEP`, with
   confidence, support, contradiction, and the run receipt.
4. **`jevmory.md` lands at your project root** — grouped by category,
   confidence-ordered, every line a verbatim quote with provenance.
   Sentinel-guarded: never overwritten without your say-so.

No LLM generation anywhere: **Jev judges; code selects and composes.**
Facts are quotes, not summaries — the memory can't hallucinate.

## Quickstart

Requires Python ≥3.10 and nothing else — stdlib only, no pip deps.

```sh
git clone https://github.com/romiluz13/jevmory && cd jevmory

# in your project:
jevmory init                     # setup; the store appears on first ingest
jevmory install --agent claude   # prints the Claude Code SessionEnd hook JSON
jevmory install --agent codex    # prints the Codex notify snippet (--yes patches config)

# stay fully local (nothing ever leaves):
jevmory ingest --scan            # finds this project's transcripts and ingests
jevmory status                   # queued candidates, last ingest, errors

# or opt in to grading (needs $TYPESAFE_API_KEY):
jevmory init --enable-grading    # per-project opt-in marker
jevmory dream                    # grade queued candidates
jevmory audit MEMORY.md          # receipts for every memory line
jevmory resolve <id>             # answer a contradiction question
```

Add `jevmory.md` to your project's `.gitignore` if you don't want agent
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
jevmory audit — demo/MEMORY.md
your memory has 1 stale line, 1 wrong line, 1 unsupported line; 2 keep

LINE  VERDICT      CONF  SUPP  CONTRA  CLAIM
   5  STALE        0.82  0.34    0.71  The build runs on Bun; bun run build is the…
   6  WRONG        0.93  0.07    0.93  The test suite runs with pytest.
   7  UNSUPPORTED  0.74  0.06    0.05  Failed API requests retry up to five times…

receipts: run 1 · 1 api calls · 838 tokens · evidence: 0 facts, 6 statements
```

The live `jevmory audit` produces exactly this shape, with real Jev
judgments behind the numbers.

## Privacy

- **What leaves:** redacted candidate quotes (≤600 chars) + verbatim
  context (≤800 chars) + minimal project context — only during
  `dream`/`audit` with `$TYPESAFE_API_KEY`
  set **and** the project opted in (`jevmory init --enable-grading`).
- **Never:** whole transcripts, transcript metadata (the project context
  is just the project name — no file paths), or secrets (redacted at
  rest, before any storage — `sk-*`, AWS keys, GitHub tokens, JWTs, PEM
  blocks, `password=`/`token=`/`api_key=` assignments, high-entropy
  hex/base64, bearer tokens → `[redacted:<kind>]`).
- **Honest caveat:** quotes are verbatim conversation text. Anything
  non-secret you typed in chat — a file path like
  `/Users/you/proj/main.py`, an internal hostname, a person's name —
  stays inside the quote that leaves. The redactor scrubs secrets, not
  paths; if that matters for your project, stay in local mode.
- **Fully local mode:** `--offline` / no key / no opt-in — events queue,
  nothing leaves. No marker → candidates queue; `jevmory status` says so.

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
   synchronous, always exits 0, and errors surface in `jevmory status`.
6. **Receipts for everything.** Raw judgments and usage are stored —
   `runs`/`judgments` tables — so any number in the report can be
   traced to the API call that produced it.

## Live smoke (lead-run)

The one network path is pinned by a budget-capped smoke script:

```sh
TYPESAFE_API_KEY=… python3 scripts/live_smoke.py --probe   # 3 synthetic candidates, 1 request
TYPESAFE_API_KEY=… python3 scripts/live_smoke.py --dream   # 1 real dream run, capped
```

Hard cap on requests (`--cap`, default 40, PLAN's smoke ceiling);
`BudgetExhausted` is a `JevError`, so the run row closes and events stay
queued — the smoke cannot overspend. Live-verified against the real API:
probe (auth, envelope, strict parse, usage accounting), a capped dream
over real transcripts, and a live audit that caught a planted wrong line
with receipts in the store's `runs`/`judgments` tables.

## Status

508 offline tests (`python3 -m unittest discover`) — no network, no API
key, FakeJev including adversarial mode. Modules M0–M7 complete;
pipeline live-verified against the real Jev API (probe, capped dream,
audit).

## License

MIT — see [LICENSE](LICENSE).
