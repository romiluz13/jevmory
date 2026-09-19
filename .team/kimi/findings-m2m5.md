# Findings — M2–M5 code review (Kimi)

Scope: commits `44ed1fc` (ingestion v2 deltas), `4e187bc` (M2 judgment),
`4e8eaee` (M3 memory), `34848b7` (M4 audit), `0447cdf` (M5 dream engine).
All code under `jev_md/` + `tests/` + `scripts/`. Reviewed against PLAN
**v2**, DOMAIN invariants, `docs/reference/typesafe-api.md`, my M0/M1 findings
(`findings-m0m1.md`), and the DDD note's M2–M5 progress claims. Working tree
at review time: two **untracked** WIP files (`jev_md/hook.py`,
`jev_md/ingestion/scan.py`) — M6 territory, reviewed only as observations
(S8); the five commits under review are exactly the committed code.

## Checks I ran (independent verification, not the DDD note's word)

| Check | Result |
|---|---|
| `python3 -m unittest discover -s tests -t .` | **399/399 green in 0.818s** — claim confirmed |
| `python3 scripts/corpus_smoke.py` (R6 pinned smoke) | **Reproducible**: 8 pinned files, 147 statements, 0 malformed, 357 candidates, 143 unique event ids, max len exactly 600, roles a=257/u=100, **32 secret replacements on the real corpus**, 0 cap/floor violations; exits 1 when corpus absent (visible, not silent) |
| R2 end-to-end trace (my own script, real `EventLog.append` → `run_dream`) | **R2 fixed and works**: same-session verbatim repeat collapses (inserted=1/ignored=1); cross-session repeat survives as its own event; re-ingest idempotent; secret stored only as `[redacted:secret-key]`; cross-session dream yields `support_count=2` with 2 distinct source event ids; second dream is zero-spend (`run_id=None`, 0 API calls) |
| Multi-chunk event probe (my own script — see S1) | **BUG CONFIRMED**: 1 statement → 2 chunks → both graded and receipted (6 receipts) → only 1 fact added; the earlier chunk silently discarded |
| Client vs `docs/reference/typesafe-api.md`, line by line | Exact match: endpoint, Bearer auth, `jev-latest`, `{state, model, questions}` body, envelope parsing, 401/422 fail-fast, 429/529 with 1s/2s/4s + jitter, strict usage validation (bool rejected). The ~32k reference budget vs the 28k planner budget leaves ~12% headroom — correct direction |
| Fixture re-audit (R3) | **Fixed**: fixtures rebuilt on `/Users/dev/example` + zero-UUIDs; includes deliberate `password=changeme12345` redaction bait; no real paths or secrets |
| Writer bugs (GLM self-reported: unknown-category sections, `## None`) | **Both fixed and pinned by tests**: unknown categories render as their own sections (`test_unknown_category_is_rendered_not_hidden`); `none` facts stay out of the file without a `## None` heading |

## Test quality verdict

**Behavioral, adversarial, no padding found.** The suite pins exact wire
shapes (question ids never leak into instructions; instructions point at
backticked state paths per the API reference), exact backoff schedules
(sleep collector: `[1.0]`, `[1.0, 2.0]`, exhaustion at 3 attempts), the
compositional char identity the planners rely on, per-batch re-verification
tripwires, position resets across batch boundaries (Phase A 120-candidate
split, Phase C fat-line split, Phase B group-boundary rebuild), FTS
external-content maintenance (delete-on-retire verified via MATCH, with the
rowid-read-through trap explicitly called out), and both error paths
(JevError → run finished with error, events stay pending, recovery dream
grades cleanly). Scripted FakeJev answers go through the **same strict
parser** as real envelopes, and a test feeds an off-spec answer (`noul: 1.5`)
to prove it rejects. The one structural gap is that no engine test feeds a
**multi-chunk statement** through `run_dream` — which is exactly where S1
lives.

Prior reviews closed: R1 (all seven v2 ingestion deltas present), R2
(scheme correct end-to-end), R3 (fixtures), R5 (realpath), R6 (pinned
smoke), R7 (DDL centralization, asserted by test), R8 (denylist/no-secret
path exercised — 32 real replacements).

---

## Findings

### [MAJOR] S1 — `run_dream` keys verdicts by `event_id`; multi-chunk statements are graded, paid for, receipted, then silently discarded

**Evidence (empirically confirmed, not inferred).** A statement over
`CHUNK_MAX_CHARS` yields several chunks; every chunk carries the **same**
`event_id` (`extract.py:213` hashes the un-chunked statement text). In
`dream/engine.py`:

- `by_event = {c.event_id: c for c in representatives}` — one event with N
  chunks appears N times in `representatives` (N distinct groups); the dict
  keeps the last.
- `verdicts[event_id] = phase_a_verdict(...)` — each chunk's verdict
  **overwrites** the previous one. Only the last survives to routing.
- Survivor routing iterates `verdicts.values()` — the earlier chunks are
  never added, deduped, paired, or asked. The event is still stamped
  `graded_at`.

My probe: one 973-char user statement with three distinct durable claims →
2 chunks extracted → `candidates_graded=2`, `occurrences=2`, 6 receipts
written, 1 API call spent — **1 fact added** (chunk 2's text). Chunk 1
("always run the full test suite before merging…") passed every gate and
vanished. The stats don't count the loss anywhere (`dropped_*` only count
surviving verdicts), so it is invisible to `runs.stats` too.

**Why this is major, not edge:** the pinned smoke shows **357 candidates /
147 statements ≈ 2.4 chunks per statement** on the real corpus. The first
real `jev-md dream` would silently lose on the order of half the claims
contained in long statements — precisely the dense, decision-heavy user
messages that matter most — while paying Jev to grade them and writing
receipts for verdicts never acted on.

**Latent twin in Phase B:** `pair_verdicts.setdefault(plan.candidate.event_id, [])`
and the apply loop's `verdicts[event_id]` have the same collision. It
cannot fire today only because Phase A collapses first; a naive Phase A
fix that keys verdicts per group but leaves Phase B keyed by event id
would merge two groups' pair verdicts into one list and cross-apply them.

**Alternative.** Key the Phase A verdict map, `by_event`, and Phase B
`pair_verdicts` by the **group key** (`(normalize_claim(text), role)` — it
is already the grouping identity and is 1:1 with representatives) or by
representative index; keep `event_id` only as the receipt subject
(provenance). Add an engine test that ingests one multi-chunk statement
and asserts every chunk's group is routed. Note the surviving verdict
today is at least self-consistent (last chunk's text with its own
answers), so no mislabeled facts exist in stores — the damage is loss,
not corruption.

### [MINOR] S2 — Supersede/duplicate act on the single "best" partner; runner-up decisive conflicts get no edge and no consequence — asymmetric with the ask path

In the ask branch the engine links **all** conflicting partners
(`for pair in conflicts: add_link(..., "contradicts")`). In the supersede
and duplicate branches it picks `best` and ignores the rest. A candidate
that decisively overrides **two** active facts supersedes one; the other
stays active, now contradicting the new fact, with **no `fact_links`
edge** — and it is never re-examined, because Phase B pairs candidates
against facts, never facts against facts. The pair receipts are in
`judgments` (nothing is lost from the log), but the graph and the writer
never see the runner-up conflict. **Alternative:** record `contradicts`
edges for every pair over `CONTRADICTION_GATE` regardless of which action
wins (the edge is evidence, not a decision), so later audits and a future
fact-fact pass inherit the full conflict set.

### [MINOR] S3 — Non-`JevError` exceptions escape `run_dream` without closing the run row

`except JevError` is the only handler; a `BatchError` (the planner's own
drift tripwire), a sqlite error, or a `ValueError` mid-run leaves
`runs.finished_at` NULL forever. The receipts philosophy ("every run
closes") holds only for Jev failures. **Alternative:** `except JevError`
as today, plus a bare `except BaseException: finish_run(error=type(e).__name__); raise`
(or a `finally` that closes any unfinished run). Cost: one clause.

### [MINOR] S4 — Dream-time context is rebuilt from *pending* events only

`run_dream` feeds `_pending_events` (graded_at IS NULL) to
`candidates_from_statements`, and `_candidates` builds context from
preceding turns **within that pending list**. After a partial backlog
drain (cap hit, or crash recovery), a statement's context misses the
session's earlier, already-graded turns — the exact turns most likely to
frame it. The claim text is verbatim and unaffected; only the Jev-facing
context degrades. **Alternative:** load the session's recent events
(regardless of graded_at) for context construction, grade only pending
ones. Cheap: one more query scoped by session ids already in the queue.

### [MINOR] S5 — `MAX_CANDIDATES_PER_DREAM` defaults to `None` (uncapped)

R10's first-dream cost bound exists only if a caller passes the cap; the
engine default is no cap, and the CLI that would set one is M6 (not yet
built). If `dream` ships in M6 without pinning this, the first dream over
a real backlog is exactly the unbounded-spend scenario R10 was raised
against. **Alternative:** give the parameter a real default (PLAN pinned
the mechanism, not the number — pick one, e.g. 50) before M6 wires the
subcommand, or make the CLI pass it explicitly and assert that in an M6
test.

### [MINOR] S6 — "seen in N sessions" counts distinct source *events*, and same-session distinct-wording duplicates inflate it

Support bumps are idempotent per **event id**. Two different wordings of
the same claim in one session are two events; if the second dedupes into
the first's fact (Jaccard or Jev same-claim), both bump — `support_count=2`
from one session, rendered as "seen in 2 sessions". Ground truth
(`source_event_ids` + `events.session_id`) is exact; the receipt line
isn't. **Alternative:** either compute the session count at render time
(one join, facts carry event ids) or reword to "seen N times" — the
former matches the PLAN's wording, the latter matches the semantics.

### [MINOR] S7 — `resolve` acts on the oldest `contradicts` link only

`contradicts_partner` returns one partner (oldest link). With a
multi-conflict ask (S2's scenario), `keep-new` supersedes one incumbent;
the other conflicting incumbents stay active beside the reactivated
challenger, and their `contradicts` edges now point at an active fact —
which the writer never renders (asks section shows only ask-status
facts). Rare (needs multiple retrieved partners over the contradiction
gate), bounded, and receipted. **Alternative:** resolve over **all**
contradicts links of the ask, not the oldest.

### [OBSERVATION] S8 — Untracked M6 WIP files import a module that does not exist

`jev_md/hook.py:32` (`from .ingestion.parsers import …`) and
`jev_md/ingestion/scan.py:23` (`from .parsers import …`) both reference
`jev_md.ingestion.parsers`; the parser modules are `claude.py` /
`codex.py` exporting `parse_claude_transcript` / `parse_codex_transcript`.
**Both files fail at import time today** (verified). Untracked, so nothing
committed is broken and the suite is green — but M6 is being built on a
broken import, and `scan.py` additionally imports both parsers without
using them. Fix the module names before either file lands. (Also a
docstring nit in `scan.py`: "false positives are impossible" overclaims —
a session that prints another project's path inside its first 64KB would
match containment; harmless as documented, because the parser's own cwd
decides at ingest.)

### Trivial / noted, no action requested

- **T1** — `mark_ask` leaves the FTS row in place: ask facts consume slots
  in the `k=10` retrieval window and are filtered post-hoc by status.
  Documented stance, recall cost only; noted so nobody "optimizes" it into
  the reactivate-collision the code comments warn about.
- **T2** — The writer reconstructs noul as `0.5 + confidence/2`, exact only
  for engine-gated facts (noul > 0.5). A fact written directly to the store
  with noul < 0.5 renders a wrong (though self-consistent) noul. If the
  receipt line must always be exact, store `durable_noul` on the row.
- **T3** — `_select_events` docstring: "events yielding no candidates are
  always selected" holds only until the first overflow `break`; after it,
  no-candidate events defer too. Harmless (they drain next dream);
  docstring tension only.
- **T4** — Crash-recovery re-grading writes a second set of Phase A
  receipts for the same subjects under a new run. By design (every run's
  answers verbatim); noted so nobody dedupes receipts destructively.
- **T5** — Adversarial FakeJev never reaches Phase B in the dream engine
  (durable 0.5 drops everything in Phase A); the pair uncertain bands are
  exercised via scripted mode instead. Coverage is real; just not via the
  fake's adversarial mode.

## What I verified that needs no action

- **R2 scheme, end-to-end**: id → `INSERT OR IGNORE` → pending queue →
  grouping → per-occurrence support bumps; zero-spend second dream;
  redaction at the storage boundary; idempotent re-ingest.
- **Batchers (A/B/C)**: greedy fill under the 28k budget, per-batch
  re-verification tripwire, floor of one with `BatchError`, order and
  coverage exact under 120/300/500-load synthetics, project_context and
  shared evidence counted toward budget.
- **Adversarial fake**: 0.5 nouls, flat distributions, 0.0 confidence, 1.5
  midpoint significance — lands in the near-miss band (dream) and REVIEW
  band (audit); below every gate by construction.
- **Schema v2**: fresh / v1-era / future migration branches; v1 events
  table tolerated with documented nullability caveat; WAL + busy_timeout
  on every connection; DDL centralization asserted by test; FTS
  external-content delete-on-retire/supersede verified via MATCH.
- **Ask lifecycle**: resolve keep-new/keep-old with by-fiat receipts and a
  `resolve` run row; expiry at 3 unresolved dreams with keep-old
  disposition; orphan-ask and not-an-ask errors; expiry receipts under the
  dream run.
- **Phase B rules**: duplicate > supersede > ask > none priority pinned at
  the boundaries (0.8 / 0.6 / 0.8 inclusive); same-claim dominates even
  with simultaneous contradiction.
- **Writer**: sentinel with version-independent core (older-version files
  regenerate, foreign files refused unless `--force`), byte-identical pure
  renders, pinned category order, stale badge visual-only (no decay),
  newline flattening, asks section with resolve commands.
- **Audit**: evidence pool scoping (project + recency caps, oldest-first),
  multi-batch line-position reset, zero-spend on structural-only files,
  three verbatim receipts per line, problem-lines-only terminal render vs
  full md/json.

## Gate recommendation

**M2, M3, M4: verified against PLAN v2. M5: one MAJOR (S1) that must be
fixed before `dream` is exposed to a real backlog in M6** — the fix is
small (re-key three maps by group identity) but the loss is silent and on
the common path. S2–S7 are minors worth taking in the same pass; none
blocks M6 alone. The five-commit body as a whole is the strongest code
this project has produced so far — receipts discipline, adversarial
testing, and the privacy gate are all real, not decorative.
