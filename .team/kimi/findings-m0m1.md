# Findings — M0/M1 code review (Kimi)

Scope: commits `4e82091` (M0 skeleton) and `607e3b5` (M1 ingestion) — all code
under `jev_md/` and `tests/`. Reviewed against PLAN **v2** (`docs/PLAN.md`),
DOMAIN invariants 1–8, and the observed schemas recorded in
`.ddd/notes/jev-md.md`. Working tree at review time: only lead-authored doc
edits (PLAN v2, DOMAIN, MISSION, DDD note) are uncommitted; the code reviewed
is exactly the committed code.

## Checks I ran (independent verification, all read-only)

| Check | Result |
|---|---|
| `python3 -m unittest discover` (no network, no key, bytecode writes disabled) | **52/52 green in 0.029s** — claim confirmed |
| Re-parsed ALL real Claude transcripts (`~/.claude/projects/**/*.jsonl`) | **15 files; line types 372 user / 526 assistant / 18 system — exactly matches the DDD note's observed counts**; parser yields 16 user + 133 assistant statements |
| Re-parsed 11 most-recent real Codex rollouts | 64 user + 450 assistant statements, zero file errors, zero parser crashes |
| Extraction over the above | 981 candidates, max len 599 (≤600 guaranteed), hashes unique by construction |
| Fixture sanitization audit (both .jsonl files read line by line; git history checked for earlier fixture versions) | Text content is benign replacement prose; **no secrets found**; shapes match observed schemas; fixtures exist only in `607e3b5` (no earlier leak) |

## Test quality verdict (asked directly: behavior or smoke?)

**Behavioral, not smoke.** The 52 tests assert exact verbatim strings, exact
source line numbers ([3,4,9,10] / [4,7,15]), and carry real negative
assertions: thinking blocks never surface, tool_use ids never surface,
tool_result is not a statement, `task_complete.last_agent_message` is not
double-counted, meta/task-notification/AGENTS.md/heartbeat wrappers are
skipped, malformed tails recorded as `skipped_lines` not raised. Count
breakdown 11 + 12 + 8 + 17 + 4 = 52 matches the claim. The M0 stdlib gate
AST-walks every import against `sys.stdlib_module_names` — a real gate, not a
ritual. Test gaps map exactly to the pending v2 deltas below (no redaction
corpus test, no two-thread ingest test); I found no padding.

---

## Findings

### [MAJOR] R1 — Committed M1 implements PLAN v1 extraction; every M1-affecting v2 delta is absent, and M2 is about to build on the v1 shapes

**Evidence** (confirmed by reading the committed code, not inferred):

- No redaction module or call — `EventLog.append` stores raw statement text
  (violates invariant 6 / PLAN v2 decision 5 once secrets appear).
- `chunk_text` breaks at whitespace, not sentence boundaries; no
  anaphoric-start drop (v2 ingestion spec).
- No `context` field anywhere: not on `Statement`, not on `Candidate`, not in
  the events DDL (v2 requires verbatim 1–2 preceding exchanges, ≤800 chars).
- Event ids are `sha256(path:line:index)`; v2 switched to content-hash ids.
- No `PRAGMA journal_mode=WAL` (see R9 for the busy_timeout nuance).
- Partial-tail tolerance **is** present (malformed lines recorded, never
  raised) — one delta already satisfied.

**Fairness:** commit `607e3b5` (01:08) predates the v2 adjudication (~01:25),
and the DDD note already lists these as GLM's pending deltas. This finding is
confirmation plus a sequencing flag, not a surprise attack: **PLAN v2's Phase A
state includes `context`, and `Candidate` doesn't have one.** If M2 builds the
Jev fan-out against the current `Candidate` shape, it gets built twice.

**Alternative.** Rework ingestion before M2 (context capture, redact pass,
content-hash ids, sentence chunking, WAL), and mark the DDD note's "M1 done"
as "done against v1; v2 deltas pending" until then.

### [MAJOR] R2 — Content-hash event ids + `INSERT OR IGNORE` will silently kill cross-session re-support (breaks an accepted v2 receipt)

**Rationale.** The adjudication switches event ids to normalized content and
says "identical content collapsing across sessions is acceptable — GLM may
combine content-hash with statement index." Walk it through: session 2 repeats
"always use uv run in this repo…" verbatim → identical content hash →
`INSERT OR IGNORE` swallows it → no pending event → never re-graded →
`support_count` never bumps, `last_supported_at` never advances. The
"seen in 3 sessions" receipt in PLAN v2's own jev.md example becomes
**impossible for exact verbatim repeats** — the most common way a convention
re-affirms itself. Statement index fixes uniqueness *within* one file only;
the same content at index 0 in two different session files still collapses.

**Alternative.** `event_id = sha256(normalized_content + session_id)`:
idempotent under transcript rewrites within a session (same session + same
content → same id), and each new session's occurrence survives as its own
event so Phase B can bump support. (Or keep an occurrences table — heavier.)
This must be decided **in the rework, before M3** builds `support_count` and
the receipts format on top of it.

### [MINOR] R3 — Fixtures leak the maintainer's real home path and plausibly-real session ids

Both fixtures carry `"cwd": "/Users/rom.iluz/Dev/example"` and realistic
session uuids (`f617f58f-…`, `019e3a47-…`). Texts are sanitized (verified), so
severity is minor — but the repo flips **public** in the morning, and these
leak the maintainer's username plus real session identifiers. Replace with
`/Users/dev/example` and obviously synthetic ids when the fixtures are next
touched. (The truncated tails — claude line 11, codex line 16 — are confirmed
malformed-on-purpose by the passing skip-assertions. Good.)

### [MINOR] R4 — "Verbatim" is actually whitespace-normalized verbatim; invariant 1 doesn't say so

Both parsers `strip()` statement text; the chunker strips and breaks on
whitespace. Chunks are exact substrings of the *stripped* text (test-enforced),
never byte-exact substrings of the raw line. Benign and probably the right
call — but a receipts-first product should name its one allowed normalization
in DOMAIN.md ("verbatim modulo outer whitespace") before a jev.directory
reviewer gets to nitpick the provenance claim first.

### [MINOR] R5 — `event_id` uses `abspath`, not `realpath` (symlink duplicates)

On macOS `/tmp` → `/private/tmp` and symlinked project dirs produce different
ids for the same file → duplicate events if a transcript is ever ingested via
two path spellings. Hooks pass real absolute paths, so risk is low; one-line
hardening (`os.path.realpath`) in the rework. Note the existing test
(`test_event_id_stable_and_unique_per_statement`) bakes in abspath+cwd
semantics and will need updating with R2's id change anyway.

### [MINOR] R6 — Smoke numbers are internally consistent but not independently reproducible

335 = 43 + 292 ✓; 769 candidates ≈ 2.3 chunks/statement (plausible for
long assistant outputs); my Claude-side rerun matched the note's observed
counts exactly. **But** the Codex side says "11 sampled files" with the
selection unrecorded: my 11-most-recent sample yields 514 statements vs the
note's implied ~186-from-Codex. Rollout files vary wildly in length, so both
are plausible — which is precisely the problem: nobody can re-check the claim.
Also, "unique hashes" is trivially true (dedupe is by hash); it evidences
nothing.

**Alternative.** Commit `scripts/corpus_smoke.py` with a pinned sample
(explicit list or sorted-first-N rule) so smoke claims are reproducible per
DDD method; report max_len and role split instead of the tautological hash
uniqueness.

### [MINOR] R7 — Schema drift seed: events DDL lives in `eventlog.py` ("schema v1") while v2 schema lands in M3

Committed `events.text` is nullable (v2 says `NOT NULL`); DBs created by this
code have **no `schema_version` row** for M3's "schema_version in first
migration" to key on. Store is disposable pre-release, so severity is minor,
but: centralize all DDL in one module (memory owns schema) and create
`schema_version` in the rework's first migration with an explicit "events
exists, no version row" branch — or add the table now, one line.

### [MINOR] R8 — Claude user-role filter is a denylist; unobserved injection flavors pass through as human statements

Only `isMeta`, `promptSource == "system"`, and `origin.kind ==
"task-notification"` are excluded. Any unobserved injection flavor (future
hook-injected user strings, IDE prompts) will be ingested as human statements.
An allowlist (`promptSource == "typed"`) would be stricter but breaks sidechain
dispatch prompts (observed with no `promptSource`; deliberately kept — correct
call, documented in the DDD note). Keep the denylist; add a test that documents
unknown flavors pass through, so the stance is explicit rather than accidental.

### [MINOR] R9 — Precision note: busy_timeout is effectively already present; WAL is the real gap

PLAN v2 says "WAL + busy_timeout on every connection." Python's `sqlite3.connect`
defaults to `timeout=5.0` — a 5-second busy handler already exists, so two
concurrent ingests mostly survive today. WAL is what's actually missing
(readers block writers under the rollback journal; also crash-safety). Still
required by v2; land it in the rework together with the two-thread ingest test
from the v2 testing strategy (currently absent).

### [MINOR] R10 — First-dream cost on real history is the expensive case; per-project isolation saves the smoke budget

My corpus run produced **981 candidates from just 26 transcripts**
machine-wide. Per-project stores mean a dream grades only its own project's
pending events, so the adjudicated ~40-request smoke cap is realistic for one
project — but a user's first `dream` on a long-lived project is still the
budget spike (and the assistant skew is real: 88% assistant statements in my
run, consistent with the note's 292/335 and with v2's asymmetric 0.7/0.8
gates). Cheap insurance for M5: a per-dream candidate cap or user-role-first
ordering, homed in `thresholds.py`.

---

## What's solid (no action)

- Parsers faithfully implement the **observed** schemas; my independent rerun
  of the full Claude corpus reproduced the DDD note's counts exactly.
  Defensive posture is real: malformed lines never raise, non-dict JSON
  recorded, `session_meta` repeats handled, filename-vs-payload id trap
  avoided on both sides.
- Ingest idempotency (`INSERT OR IGNORE`) is tested, not just claimed.
- `Candidate`/`Statement` are frozen dataclasses; extraction is deterministic
  (tested); chunk verbatim-substring property is tested.
- M0: packaging gate and CLI tests are genuine; `tomllib` absence on 3.10 is
  skip-guarded; zero-dependency invariant is enforced by a test that would
  actually catch a violation.
- GLM self-flagged the path+line+index deviation in the progress log instead
  of hiding it — good hygiene; the lead's adjudication answered it.

## Gate recommendation

- **M0: verified.** Clean.
- **M1: verified against PLAN v1 only.** Do not mark v2-complete until the
  seven deltas land (R1). Sequence: rework ingestion first — including the
  R2 id decision (content-hash + session_id) — then start M2, so the Jev
  fan-out is built once, against the final `Candidate` shape (with `context`,
  redacted).
