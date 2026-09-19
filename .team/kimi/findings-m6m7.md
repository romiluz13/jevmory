# Kimi findings — M6 (f1ea7bc) + M7 (91567da) review

Reviewer: kimi (devil's advocate). Scope: verify every S-finding from
`findings-m2m5.md` landed in f1ea7bc; re-probe S1 empirically; review the
new M6/M7 surface (hook, scan, CLI, README, demo, live_smoke) for anything
that would embarrass on a real API call or a public repo.

Method: full read of the diff (engine, resolve, writer, facts, eventlog,
thresholds) plus full reads of hook.py, scan.py, cli.py, README.md,
demo/*, scripts/live_smoke.py; independent run of the test suite; live
probes against a scratch `$JEV_MD_HOME` (multi-chunk collision replay,
hook pipes, CLI behavior, demo run). No network used anywhere.

## Part 1 — S-finding verification (all eight landed)

| Finding | Verdict | Evidence |
|---|---|---|
| S1 (MAJOR) multi-chunk verdict collision | **FIXED, empirically confirmed** | Verdicts now keyed by `_group_key(c)` = (normalize_claim(text), role); `by_event` holds a list of representatives per event id, popped per batch position. Replay: one 741-char statement → 2 chunks sharing ONE event id → `candidates_graded=2`, 2 independent verdicts, 2 facts created (`We picked Postgres…` + `Background jobs run…`). Under the old keying, chunk 2's verdict overwrote chunk 1's. The exact failing setup now passes. |
| S2 conflict evidence dropped | FIXED | `_conflict_evidence()` returns all over-gate pairs that didn't win the action; `add_link` for each in duplicate + supersede branches. `contradicts_partners()` (plural) in facts.py returns every contradicts link. |
| S3 run row left open on unexpected exception | FIXED | `except BaseException as error: finish_run(…); raise` below the `except JevError` handler in engine.py. |
| S4 grading context was pending-tail only | FIXED | `_session_statements()` pulls pending events PLUS already-graded turns of those sessions; pending-only filter applied after extraction via `pending_ids`. |
| S5 unbounded candidate cap | FIXED | `MAX_CANDIDATES_PER_DREAM: int \| None = 50` in thresholds.py (was `None`); engine defaults to it. |
| S6 "seen in N sessions" lied (counted events) | FIXED | `_sessions_by_fact()` counts DISTINCT session_id per fact (chunked IN queries vs SQLite variable limit, min 1); writer renders "seen in N sessions" when known, honest "seen N times" fallback when the map is absent. |
| S7 resolve acted on oldest contradicts partner only | FIXED | `resolve()` iterates `contradicts_partners()`; keep-new supersedes EVERY partner; run stats record `partner_ids` (list). |
| S8 scan.py untracked + broken import | FIXED | `default_home()` with `$JEV_MD_HOME` override added to eventlog.py; scan.py is tracked in f1ea7bc and imports it. |

No regressions found in any fix. The S1 fix design (representative lists
popped per batch position) is correct because the planner preserves
representative order and coverage is exact; a planner bug would surface as
IndexError → caught by the S3 handler → run row closed. Fail-closed, good.

## Part 2 — independent verification

- **Test suite: 504 tests, all pass** (`python3 -m unittest discover -s tests -t .`, 1.394s). README's "504 offline tests" claim is exact.
- **Demo: output is byte-identical to the README's claimed block**, including `receipts: run 1 · 1 api calls · 838 tokens · evidence: 0 facts, 6 statements`. PlantedJev answers through the real strict parser; matching is by substring, not line number; the throwaway home never touches `$JEV_MD_HOME` (verified while my shell had one exported).
- **Stdlib-only claim verified**: import scan of the whole package — no third-party imports.
- **Redaction kinds match the README list exactly**: PEM, bearer, `sk-`, AWS `AKIA`/`ASIA`, `gh[pousr]_`, JWT, credential assignments, hex ≥20, base64-ish ≥20-with-digit.
- **Hook never-die contract holds empirically**: garbage stdin → exit 0 + "skipped" log; missing transcript → exit 0 + logged `ingest_error` (visible via `status`); valid payload → exit 0, 6 events ingested, per-project log written. Redaction at rest confirmed at the storage boundary (`EventLog.append` redacts before INSERT).
- **CLI exit codes as documented**: 0 success incl. nothing-to-do, 1 clean failure, 2 argparse. Offline-safety: ingest/scan/status/install/resolve never construct a client; dream/audit live paths gated by marker AND key with actionable hints; `--offline` prints "nothing leaves this machine"; config patches require `--yes`; Claude settings merge is careful, shape-checked, and idempotent.
- **live_smoke.py**: probe drives the real Phase A path (state + `phase_a_questions` → strict parse); `CountingTransport` counts every request including retries and raises `BudgetExhausted` (a `JevError`) BEFORE the capped request fires — the engine's fail-closed path closes the run row and leaves events queued; `--dream` checks the opt-in marker BEFORE client construction.
- **LICENSE present**, MIT, matches README.

## Part 3 — new findings (M6/M7 surface)

### T1 (MINOR, docs; highest-visibility item here) — README quickstart commands are wrong

- `jev-md install claude` and `jev-md install codex` (Quickstart) fail:
  the parser requires `--agent` (verified: `error: the following arguments
  are required: --agent`, exit 2). The tool's own `init` output prints the
  correct `jev-md install --agent claude|codex` form — the README
  disagrees with the tool. This is the first command a new user runs.
- Same block: `jev-md init  # creates the ~/.jev-md/projects/<slug>.db
  store` — init creates nothing (verified: 0 db files after init; the store
  is created lazily by ingest/status/dream). Harmless but wrong.

### T2 (MINOR, privacy overclaim) — "Never: file paths beyond the project name"

README Privacy says what leaves is "redacted candidate quotes (≤600 chars)
+ verbatim context (≤800 chars) + minimal project context", and under
**Never**: "file paths beyond the project name". True for metadata
(`project_context` is only `{"name": basename}` — verified), but the quotes
are verbatim conversation text: a path a user typed in chat (e.g.
`/Users/rom/secret-project/main.py`) leaves inside the ≤600-char quote.
The redactor scrubs secrets, not paths. On a privacy-claiming public repo
someone will check what hits the wire; reword ("no transcript metadata
paths; quotes are verbatim and may contain whatever the conversation
contained") or add path-scrubbing. The rest of the Privacy section checks
out exactly (600/800 caps, marker+key double gate, redaction list).

### T3 (MINOR) — `jev-md hook` CLI alias silently drops piped stdin

`main()` never reads stdin; `_cmd_hook` passes `stdin_text=None` to
`run_hook`, which then finds no payload. Empirically: a VALID payload piped
to `jev-md hook` exits 0 and logs "skipped: no JSON payload" to
global.jsonl — a silent no-op. Real installs use `python3 -m jev_md.hook`
(works correctly, verified), and the alias is hidden from help, so blast
radius is small — but the alias claims the same contract as the module
entry and doesn't keep it. Read stdin in `_cmd_hook` or drop the alias.

### T4 (MINOR, docs) — live-smoke cap number mismatch

README: "Hard cap on requests (`--cap`, default 50)". Script:
`DEFAULT_CAP = 40` (and PLAN v2 says ~40). The code matches PLAN; the
README number is wrong.

### T5 (MINOR, edge) — codex `--yes` patch can hijack a section-scoped `notify=`

`_patch_codex_config` finds an existing notify line with
`re.match(r"\s*notify\s*=", l)` over ALL lines, including inside `[...]`
sections. A config with a section-scoped `notify = …` key would be
rewritten in place — wrong TOML scope (codex reads top-level `notify`) and
it silently clobbers somebody else's setting. Rare, but `--yes` patches
should only match before the first section header. The Claude-side patch
(JSON merge) has no analogous issue.

### T6 (OBSERVATION) — no `--dry-run` exists

The review checklist expected "`--dry-run` sends nothing". PLAN v2 does not
require one; the shipped design satisfies the safety intent differently
(probe = synthetic candidates, no store, no marker, one request; `--dream`
gated by marker+key; hard cap). If "print the exact request body, send
nothing" is wanted, it's a small add (`probe_state` + `phase_a_questions`
are already pure). Not a PLAN deviation — flagging so the expectation is
closed explicitly.

### T7 (NIT, bundle)

- `live_smoke.run_dream_smoke(force=…)` is unreachable from the CLI (no
  `--force` flag); a foreign jev.md exits 1 with a clean SentinelError
  message. Either expose the flag or drop the param.
- Demo `main()` preamble prints the "live equivalent" as ingest + audit but
  omits `jev-md init --enable-grading` (the module docstring above it has
  all three steps). Copy-pasting the printed two commands hits the opt-in
  gate.
- `audit --json --md` both set → `--json` silently wins; make them mutually
  exclusive or document precedence.

## Part 4 — gate recommendation

**M6/M7 hold up. All eight S-fixes verified, S1 confirmed with the exact
failing setup, 504/504 independently green, demo output byte-identical to
the README, hook never-die contract holds under garbage/missing-file
pipes, and the one network path is budget-capped and fail-closed.**

Nothing here blocks the lead-run live smoke. T1 + T4 are one-line README
fixes and should land before any public push (broken quickstart + wrong cap
number are the first two things a reader runs into). T2 deserves a reword
before the repo goes public — the privacy section is the project's
credibility, and it currently overclaims by omission about quote content.
T3/T5 are small code fixes that can ride the same polish commit.
