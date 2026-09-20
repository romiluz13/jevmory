# GLM research — the Jev compaction wave (2026-09-17 → 09-21)

Assignment (lead): close-read the "Jev compaction wave" — four repos + two
GitHub items — answer six questions per item, then assess whether jevmory's
niche is still open. Nothing else in this repo was touched for this task.

Method: shallow clones of all four repos into `/tmp/jev-research/`
(2026-09-20, re-read 2026-09-21); full source reads for
tamaratran/fast-jev-compaction, zaycruz/fast-jev-compaction-pi, and
willfish/pi-observational-memory-jev; for KamilPostrozny/pi-fast-jev-compaction:
full README (complete 0.3.0 → 0.7.4 release history), `policy.ts`,
`task-excerpts.ts`, and the hook/persistence sections of `fast-jev.ts`
(2,079 lines), with the vendored upstream core cross-validated two ways
(my full read of tamaratran's `src/`, plus odradekk's independent read in
pi-square #409). GitHub items read via API, bodies in full. Zero API spend.

Six questions per item: (1) mechanism, (2) scope, (3) runtime hook,
(4) fate of kept content, (5) dependencies, (6) what jevmory should steal.

## TL;DR verdict matrix

| Item | What it is | Scope | Kept content | Cross-session | Cross-agent | In jevmory's niche |
|---|---|---|---|---|---|---|
| tamaratran/fast-jev-compaction | Claude Code compaction plugin + npm lib | one live session | verbatim, delete-only | no | no | no |
| zaycruz/fast-jev-compaction-pi | pi port + continuity layer | one session file | verbatim + deterministic tombstones | restarts of the same session file only | no | no |
| KamilPostrozny/pi-fast-jev-compaction | pi, non-destructive logical filter | one session (transcript intact) | verbatim + bounded deterministic excerpts | reload / branch-restore of the same session | no | no |
| willfish/pi-observational-memory-jev | pi observational memory, Jev backend | per-session-id `.memory/` dir | verbatim single-line excerpts, kind-typed | topic files survive `/tree` + session end; forks inherit; new sessions start empty | no | **nearest neighbor — still no** |
| odradekk/pi-square #409 | research issue inside a Context Memory program | pi-square's rendered prompt | n/a (investigating verbatim pruning) | n/a | no | no (watch #215) |
| Kylejeong2/nanocode PR #1 | stdlib-Python port for a ~250-line agent | one session (`history.md` pruned) | verbatim | no (unpruned `history.full.md` companion only) | no | no |

**Niche verdict: OPEN.** Every wave member manages the context window of a
single agent runtime. None ingest other agents' transcripts; none keep a
per-project store shared across unrelated sessions; none grade facts with
calibrated confidence + receipts + conflict resolution. willfish is the
nearest neighbor and the one to watch. Details and steal-list below.

---

## 1. tamaratran/fast-jev-compaction (the flagship)

MIT, TypeScript, created 2026-09-17 (per #409). Two shapes: an npm library
(`src/`, ~950 lines, knows nothing about Claude Code, transport abstracted
behind a one-method `JevAsker`) and a Claude Code function-hook plugin
(`hooks/fast-jev.ts` + `.claude-plugin/`, requires Claude Code ≥ 2.1.274).
Most commits come from `devin/*` branches.

**(1) Mechanism.** Fires on PreCompact (user `/compact` or auto). Builds the
Jev `state` = the whole conversation, oldest first, every tool result
replaced by a size note (`ok, 4213 chars (omitted)` / error notes), then
fitted into 25k tokens (`maxStateTokens`) through seven escalating stages
(tool inputs → 1000/200/60 chars; texts abridged head-and-tail oldest-first;
old messages collapsed to a note; old calls reduced to one line; old
call-less messages dropped; runs of call-only messages folded) and throws if
it still doesn't fit. Two `noul` questions per non-pinned tool call —
`keepCall`, `keepResult` — threshold 0.5 → three-way action: keep both /
keep call + truncate result to first 300 chars + note / drop call and result
together. First message pinned; newest `preserveRecentMessages` = 6 pinned.
A message emptied of all content is removed; a result is never orphaned from
its call. Tokens estimated without a tokenizer, calibrated to land 2–18%
high. Full state is resent with every batch of questions; requests run
concurrently.

**(2) Scope.** Strictly within-session. Nothing is persisted anywhere; the
output is a compacted transcript handed back to Claude Code. On any failure
(Jev error, malformed answer, missing key, unfittable state, or reduction
below 25%) it falls back to the host's built-in summary compaction.

**(3) Hook.** Claude Code function-hook plugin (`hooks.json`) + npm library
for other hosts.

**(4) Fate of kept content.** Verbatim-only deletion. User and assistant
text is never removed or shortened in the output (only abridged inside the
scorer state). Kept calls/results stay byte-identical. The README is honest
that a probability is not proof a result is safe to delete, and leans on the
assistant being able to re-run the tool.

**(5) Dependencies.** Node ≥ 18, `TYPESAFE_API_KEY`, System One API
(`jev-latest`). Zero production deps beyond `fetch`.

**(6) Steal.** The state-fitting ladder (cascading, deterministic, honest
throw at the end) beats any single hard cap for grading context; concurrent
request batching; fail-open to the host's native path; the README's
"probability is not proof" honesty pattern belongs in `jevmory audit` output.

## 2. zaycruz/fast-jev-compaction-pi (pi port + continuity)

pi extension (earendil-works/pi-mono), MIT. Hooks `session_before_compact`.
Vendors the upstream core unmodified; zero runtime deps. Claims ~50× faster
than an LLM summary.

**(1) Mechanism.** Same upstream core (same two `noul` questions, 0.5
threshold, size-note state, staged fitting, fallback on failure or
insufficient reduction). Adds tuned retention: `preserveCallInputs`
(tombstone one-line call records for dropped calls), `preserveErrorTails`
(keep error tails up to 1200 chars), `inputRetention: "jev"` (Jev decides
per-call whether the input itself is worth keeping), and `routing: "jev"`
(ask Jev whether to use scored pruning or the native summary).

**(2) Scope.** One session FILE, but with continuity: the pruned transcript
is persisted in the session file's compaction entry (`details.fastJev.messages`)
and re-decided at each subsequent compaction. So deletions survive
restarts/reloads of that session — "lossless-with-deletions across sessions"
means across compaction events inside one session file. Not a per-project
store; not shared by other sessions.

**(3) Hook.** pi `session_before_compact` extension.

**(4) Fate of kept content.** Verbatim (upstream core); tombstones are
deterministic one-line records written by code, not model text.

**(5) Dependencies.** Node, `TYPESAFE_API_KEY`, pi runtime; zero additional
runtime deps (vendored). Optional Vercel AI Gateway mode.

**(6) Steal.** Error-tail preservation: errors are disproportionately
high-signal evidence, and head-only truncation of a long error loses the
part that matters. jevmory's evidence capture should keep head + tail for
long evidence strings. Tombstone one-liners are a good pattern for
"something was here and was dropped" without inventing text.

## 3. KamilPostrozny/pi-fast-jev-compaction (non-destructive; best engineering)

pi extension, 0.3.0 → 0.7.4 in a fast release cadence. The most
architecturally distinct member: **non-destructive**.

**(1) Mechanism.** The `context` hook fires before every model request and
is **apply-only** — it never calls Jev. It filters the session's raw
messages through the committed Jev decision map and returns the logical
message list (`return { messages: logicalMessages }` only when decisions
exist). Each request it reconciles the decision map against the raw tool
call ids actually present, and fail-opens on any error. Evaluation runs at
`turn_end` (plus `agent_end` promotes deferred drops). Scoring itself is the
vendored upstream core: two `noul` questions per call, 0.5 threshold,
keep / drop_result / drop_call, size-note state fitted to 25k.

Pressure management is the standout: a state machine
`ARMED → JEV ATTEMPT → AWAITING VALIDATION → (ARMED | EXHAUSTED)` driven by
Pi's native real-usage boundary (`contextWindow − reserveTokens`).
`session_before_compact` can **cancel Pi's pending threshold compaction**
while awaiting validation, or when real usage is already under Pi's own
ceiling; once an episode is EXHAUSTED, native compaction is always allowed.
Automatic pruning only commits if it saves ≥ the hysteresis margin
(≈5,632 estimated tokens with default settings) — a cost gate, not a
substitute for validation. During an active agent run, `drop_call` is
demoted to `drop_result` (breadcrumb preserved; full drops deferred to
`agent_end`). 0.7.4 adds bounded task-status excerpts: deterministic
Markdown heading extraction from status/handoff/progress docs
(priority: constraints/known issues > next/current task > task state),
whole blocks never cut mid-line, ≤2,400 chars/result, ≤7,200 total, with an
explicit "not current file contents; re-read the status document" intro.

**(2) Scope.** One session's transcript, which is never modified — only the
rendered prompt is filtered. Decisions persist as a custom entry
(`fast-jev-compaction-state`, schema v1, non-keep decisions only) in the
session branch, so they survive reload, restart, and branch restore, and are
monotonic. Not cross-session; not cross-agent.

**(3) Hook.** pi extension: `context`, `turn_end`, `agent_end`,
`session_before_compact`, `session_compact(_failed)`, `session_tree`,
`session_start/shutdown`, `before_provider_request`, `after_provider_response`,
`turn_start`, `agent_settled`; commands `jev-on/off/refresh/status/stats/
diagnostics(-clear)`.

**(4) Fate of kept content.** Verbatim — kept content is byte-identical;
excerpts are deterministic slices of real documents, not summaries.

**(5) Dependencies.** Zero runtime deps, Node ≥ 22, `TYPESAFE_API_KEY`.
Diagnostics as JSONL at `~/.pi/agent/logs/fast-jev-compaction.jsonl`.

**(6) Steal.** Three things, ranked: (a) the pressure/hysteresis machine as
a template for dream-engine gating — arm only when the pending queue is big
enough that a run must clear a margin, validate the run actually graded
enough before re-arming, and never oscillate; (b) deferred destructive
decisions — jevmory's `resolve()` supersede/contradict actions are the
destructive kind and could be deferred to batch end the way `drop_call` is
deferred to `agent_end`; (c) bounded deterministic excerpts with the
"re-read the source" caveat for surfacing context around facts in
`jevmory.md` — no LLM needed, domain invariant preserved.

## 4. willfish/pi-observational-memory-jev (nearest neighbor)

pi extension. Observational memory with Jev as the observation backend
("same /om ergonomics as Eero Alvar's observational-memory extension:
per-session gate, parallel observers, branch-local ledger, deterministic
compaction, consolidator into `.memory/<sessionId>/`"). Gate defaults OFF
(`/om on`; `om.enabled` persisted in the ledger).

**(1) Mechanism.** Two Jev stages, both triggered at `turn_end` /
`agent_start`:

- **Observe** (parallel, up to `observerConcurrency` = 4): when unprocessed
  raw branch tokens ≥ `chunkTokens` (10,000), slice the branch, extract
  candidates — verbatim text blocks of 24–500 chars, deduplicated, capped at
  `maxCandidates` = 32 — serialize them source-addressed, and ask Jev TWO
  questions per candidate: `keep` (`noul`, threshold 0.5) and `kind`
  (`choice` among six fixed kinds: fact / decision / constraint / question /
  correction / hypothesis). Accepted observations are committed as custom
  entries (`om.observations.recorded`) with orchestrator-assigned unique
  timestamps, `tokenCount`, and `sourceEntryId`. A `coversUpToId` watermark
  prevents re-observation; observers abort on shutdown; per-run cost is
  recorded (`om.cost`).
- **Consolidate**: when the active pool ≥ `consolidateAtPoolTokens` (15,000),
  promote the overflow past `poolTargetTokens` (10,000) — one `noul`
  "still durable?" question per observation — by writing kind-keyed topic
  files (`facts.md`, `decisions.md`, `constraints.md`, `questions.md`,
  `corrections.md`, `hypotheses.md`) plus `INDEX.md` and a size-capped
  `JOURNEY.md` (1,000 tokens, oldest dropped) under `.memory/<session-id>/`,
  all via atomic writes. Tombstones (`om.observations.dropped`) are written
  ONLY after successful topic-file writes.

Compaction (`session_before_compact`) is **deterministic render** from the
ledger + topic files — model-free; no LLM summary ever. State/requests capped
at 25k/30k tokens, model `jev-latest`, baseUrl
`https://api.typesafe.ai/v1/systemone`, `TYPESAFE_API_KEY`.

**(2) Scope.** The most durable in the wave: topic files + `JOURNEY.md`
survive `/tree` rollback and session end, and a FORKED session seeds its
`.memory` directory from the parent. But the root is
`.memory/<session-id>/` — a brand-new session in the same project starts
EMPTY. Per-project sharing, cross-session accumulation across unrelated
sessions, and cross-agent ingestion are all absent.

**(3) Hook.** pi extension: `session_start/shutdown`, `turn_end`,
`agent_start`, `session_before_compact`; commands `/om`, `/om status`,
`/om compact`, `/om consolidate`.

**(4) Fate of kept content.** Verbatim single-line excerpts, never
model-rewritten (`isObservation` enforces no newlines); kind and keep score
come from Jev; everything else is code.

**(5) Dependencies.** Node, `TYPESAFE_API_KEY`, pi runtime; zero additional
runtime deps. Cost ledger summed across ALL branches so spend never rolls
back under `/tree`.

**(6) Steal + niche evidence.** This is the closest philosophical cousin to
jevmory's dream → facts pipeline: two-tier ledger-then-promotion, fixed-kind
verbatim facts in structured files, `sourceEntryId` evidence links,
cross-session file survival. Steal: tombstone-after-write ordering stated as
an explicit invariant; fork seeding; per-kind topic files; the verbatim
single-line candidate discipline (24–500 chars); the rollback-proof cost
ledger. Niche evidence: even this nearest neighbor is per-session-silo'd,
pi-only, with a flat six-kind taxonomy, no confidence grades, no receipts,
no conflict resolution, and no cross-agent ingestion.

## 5. odradekk/pi-square #409 — "Research: verbatim tool-output pruning as a Context Memory complement"

Open research issue (2026-09-19, 0 comments), child of #215 "Implement
experimental Context Memory". Not code — but the sharpest thinking in the
wave about the costs.

Context: pi-square's own Context Memory compresses old history by having
the parent main agent REWRITE it as Markdown blocks — lossy by construction
(#227 spends a qualification gate on critical recall and fabrication
review). #409 investigates whether tamaratran's verbatim pruning belongs as
a complement, on the grounds that pruning stale tool output doesn't need the
faithfulness argument narrative summarization does.

Their independent first-pass read of tamaratran@e3f262a matches my source
read on every point (two questions/call, 0.5 threshold, 300-char truncation,
size-note state, staged fitting, fallback to host summary,
probability-not-proof honesty) — my tamaratran findings are cross-validated.

Their six investigation items: (1) verify the precedent read; (2) resolve
the extra-model-call question — #215 user story 6 forbids hidden model
calls, so choose between the parent agent emitting keep/drop decisions
itself, an optional external scorer behind explicit config, or rejecting the
scoring seam and reusing only mechanical parts; (3) **measure the
prompt-cache cost** — Context Memory is append-only precisely so the older
prefix stays byte-stable, and deleting an old tool result invalidates the
provider cache from that point forward; pruning that saves tokens but forces
a full re-read may be a net loss; (4) fit with source recovery — the session
tree is the durable store, `read_memory_source` can still recover originals,
decide if pruned calls should be visible in `/context`; (5) mechanically
reusable parts independent of Jev (pairing by id, pinning first + newest N,
three-way decision, staged fitting ladder, minimum-reduction fallback);
(6) licensing (MIT, attribute in `THIRD_PARTY_NOTICES.md`). Explicitly:
nothing lands from this ticket; no implementation.

Relevance to jevmory: (a) a second platform is converging on
verbatim-over-paraphrase for tool output; (b) their cache-invalidation
concern is a cost jevmory does NOT pay — jevmory never mutates live context,
it only derives a memory file — worth stating in the README as a design
advantage; (c) their qualification methodology (critical-recall +
fabrication gates) is the right template for `jevmory audit` to grow into;
(d) if jevmory ever adds a live-context hook, their cache trade-off analysis
is the cost model to copy first.

## 6. Kylejeong2/nanocode PR #1 — "feat: Jev-based memory compaction (port of fast-jev-compaction)"

Open PR by devin-ai-integration bot (2026-09-17/18), 6 commits, +968/−44,
5 files, 25 unit tests, branch `devin/1789689442-jev-memory-classification`.
nanocode is a minimal Claude Code alternative: single Python file, zero
dependencies, ~250 lines. The npm package isn't published and its git
install ships no build output, so the PR PORTS the flagship to stdlib-only
Python (`jev.py`) — dependency-freedom was the stated reason.

**(1) Mechanism.** Same core: `collect_tool_calls` pairs `tool_use` /
`tool_result` by id, first message pinned, `fit_state` uses the upstream
stages, two `noul` questions per call → keep / drop_result (300 chars +
note) / drop_call; user/assistant text never rewritten. Adds mid-turn
robustness: when compaction fires between tool calls, the newest call/result
pair is pinned so the agent doesn't re-fetch what it just read; compaction
is best-effort (applied whenever it shrinks, even if still over
`--compact-at`); a no-op compaction is skipped until the context grows;
forced `/compact` with no shrink over budget still raises. Any
Jev/HTTP/validation failure raises BEFORE `state.json` or `history.md` is
touched (fail-closed on persistence).

**(2) Scope.** One session — but distinctive: the ON-DISK transcript
`history.md` is pruned too (atomically, deleting `### tool_use` /
`### tool_result` sections per the same Jev decisions), while `record()`
also appends everything to `history.full.md`, which is never pruned — the
complete transcript stays recoverable. Claimed live run: 424 → 32 lines
after one compaction, user request and agent answers kept verbatim. Not
cross-session; not cross-agent.

**(3) Hook.** None (nanocode has no hook system) — compaction is internal
to `Memory.compact`, plus `jev_ask` / `jev_compact` tools exposed to the
agent itself.

**(4) Fate of kept content.** Verbatim; deletions deterministic; the
unpruned `history.full.md` companion keeps the source of truth whole.

**(5) Dependencies.** stdlib-only Python; `TYPESAFE_API_KEY` (optional
`JEV_MODEL`, default `jev-latest`); without it, falls back to the
OpenRouter summary path. `OPENROUTER_API_KEY` still needed for the agent
itself.

**(6) Steal.** The never-pruned full-transcript companion: jevmory's SQLite
event log is exactly this and should be documented as the invariant —
`jevmory.md` is a derived view, the event log is the durable transcript,
nothing is ever lost. Newest-pair pinning is the right guard if jevmory ever
grades mid-turn. Fail-closed-before-write ordering matches what our engine
already does; keep it tested.

---

## Cross-wave invariants (all six items)

- **Jev = System One API**: single POST with `{model, state, questions}`,
  Bearer `TYPESAFE_API_KEY`, default model `jev-latest`, typed answers
  (`noul` number / `choice` string). No one uses Jev for generation.
- **Verbatim-only ethos**: Jev scores, code deletes/truncates. Nobody lets a
  model rewrite kept content. The one narrative-rewrite design (pi-square's
  Context Memory) is the one running a fabrication-review gate — and
  investigating verbatim pruning as the fix.
- **Fail-open / fallback to native** on Jev failure or insufficient
  reduction (tamaratran, zaycruz, Kamil, nanocode→OpenRouter summary).
- **Zero-dependency discipline**: vendoring (zaycruz) or porting (nanocode)
  rather than adding a dep; stdlib-only where possible.
- **Everything is context-window management for ONE runtime.** No member
  ingests another agent's transcripts, keeps a per-project store shared
  across unrelated sessions, or grades facts with calibrated confidence.

## What jevmory should steal (ranked)

1. **Kamil's pressure/hysteresis machine** → dream-engine gating: arm only
   when the pending queue clears a margin, validate the run graded enough
   before re-arming, never oscillate, always allow the fallback path once
   exhausted.
2. **Kamil's deferred-destructive pattern** (`drop_call` → `agent_end`) →
   defer `resolve()` supersede/contradict actions to batch end.
3. **willfish's tombstone-after-write ordering** as a stated, tested
   invariant (we transaction already; make the ordering explicit).
4. **Kamil's bounded deterministic status-doc excerpts** (whole blocks,
   priority headings, "re-read the source" caveat) for context around facts
   in `jevmory.md` — no LLM, invariant safe.
5. **zaycruz's error-tail preservation** → head + tail for long evidence.
6. **tamaratran's README honesty** → "confidence is not proof; the agent
   can re-read the source" printed by `jevmory audit`.
7. **nanocode's unpruned companion** → document the invariant: the SQLite
   event log is the never-pruned transcript; `jevmory.md` is derived.
8. **pi-square's cache analysis** → N/A for jevmory today (we never mutate
   live context) — state that as an advantage; copy the cost model if a
   live hook ever ships.

## Niche-openness assessment (the lead's question)

jevmory's claimed niche: persistent, cross-session, cross-agent
(Claude Code + Codex + Droid) coding-agent memory where every fact is a
verbatim quote graded by Jev's calibrated confidence, with receipts, conflict
resolution (supersede / contradict links), a per-project SQLite store, and a
no-LLM-generation core invariant.

**Verdict: OPEN.** Support:

- The wave is 100% within-session / single-runtime context management.
  Cross-session durability appears only as (a) same-session-file persistence
  (zaycruz, Kamil), (b) fork inheritance (willfish), or (c) an unpruned
  transcript companion (nanocode). None accumulate memory across unrelated
  sessions of one project; none are per-project stores.
- No member is cross-agent. Each is bound to one runtime (Claude Code, pi ×3,
  nanocode, pi-square). Nobody parses other agents' transcript formats.
- Grading style: keep/drop `noul` scores on tool calls (4 of 6) or keep+kind
  on verbatim excerpts (willfish). Nobody emits calibrated-confidence facts
  with receipts, and nobody does conflict resolution.
- The wave VALIDATES the substrate: Jev-scored context decisions are being
  adopted across at least four runtimes in four days, and a fifth (pi-square)
  is formally investigating. The bet on Jev as grading backend is sound; the
  differentiation is what you do with the grades.

Threats to watch, ranked:

1. **willfish/pi-observational-memory-jev** — if it moves to a project-shared
   `.memory/` root (instead of `<session-id>` silos) and adds receipts, the
   overlap becomes real on pi. Today it is per-session, pi-only, flat kinds.
2. **pi-square #215 Context Memory** — the largest platform effort; narrative
   memory plus a possible verbatim-pruning stage, but pi-square-internal and
   prompt-rendering scoped. Its qualification methodology (critical recall +
   fabrication gates) is worth matching in `jevmory audit`.
3. **The flagship growing persistence** — tamaratran's lib is
  runtime-agnostic by design (`JevAsker` seam); a persistence layer on top
   would be a generic competitor. No sign of it today.

## Research gaps (honesty section)

- Kamil's vendored `fast-jev-core.ts` (754 lines) was not read line-by-line;
  README states it is upstream unmodified, and the mechanism is
  cross-validated by my full tamaratran read + #409's independent read.
- nanocode PR: body + metadata read via API; the 6-commit diff (`jev.py`
  itself) and its 1 review comment were not pulled.
- Repos are live; clones date 2026-09-20/21 and HEADs may have moved.
- No live API runs against any wave member (read-only research, zero spend).
