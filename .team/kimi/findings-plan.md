# Findings — PLAN attack (Kimi, devil's advocate)

Reviewer: Kimi. Scope: `.team/MISSION.md`, `docs/PLAN.md`, `docs/DOMAIN.md`,
`docs/reference/typesafe-api.md`, `.ddd/notes/jev-md.md`. Read-only review.

**Verdict up top:** the plan is buildable and the Jev integration is unusually
well-grounded (the API reference is real, the fan-out pattern matches the docs).
But the load-bearing bet — verbatim-only memory — survives attack only if three
things change: facts need verbatim context snippets, extraction needs a garbage
filter, and "confidence" needs one explicit formula. Privacy as specced is a
submission-killer. And `audit`, not `dream`, is the viral demo.

Severity: **[BLOCKER]** kills launch or leaks user data · **[MAJOR]** degrades the
product or the demo materially · **[MINOR]** fix-it-now-cheap, hurts later.

---

## 1. The verbatim-quote-only invariant (DOMAIN invariant 1)

### [MAJOR] F1.1 — Verbatim quotes are not self-contained; recall collapses to aphorisms

**Rationale.** Durable knowledge in real sessions is rarely phrased as a
standalone sentence. "that breaks the lockfile", "let's just use bun for this",
"the auth middleware fix worked" — all durable, all meaningless without the
preceding exchange. The durable Noul ("judge only what the quote itself
states") will correctly reject these, which means jev.md captures only the
minority of knowledge that happened to be phrased as an aphorism. The flagship
example ("always use `uv run` in this repo, plain python breaks the lockfile")
is suspiciously well-formed; real transcripts yield "no not that one, the other
flag". Net effect: sparse memory that looks inferior to LLM-summarized
MEMORY.md on information density — the exact comparison the demo invites.

**Alternative.** Keep the verbatim claim (invariant holds) but: (a) store the
surrounding 1–2 exchange pairs as a verbatim `context` field on the receipt,
surfaced via `--verbose` or in audit reports; (b) bias extraction toward
user-role statements (users state decisions; assistants narrate); (c) treat
assistant quotes as durable candidates only when the user confirms in-session.
jev.md stays verbatim; receipts get context.

### [MAJOR] F1.2 — 600-char chunking manufactures false claims

**Rationale.** M1 chunks messages at ≤600 chars with no sentence-boundary rule.
Assistant prose often states a claim and its qualifier hundreds of chars apart
("the cause is X … [chunk boundary] … actually no, it was Y"). A walked-back
claim becomes a durable candidate, and Phase B can only catch it if FTS5
retrieves the later correction — which BM25 on short quotes often won't (F2.2).

**Alternative.** Chunk on sentence boundaries (stdlib `re`), target 280–600
chars, never split a sentence. Drop chunks whose first tokens are anaphoric
("that/this/it/they/the same …") before spending Jev tokens. Deterministic,
testable, cheap.

### [MINOR] F1.3 — The invariant is the differentiator; instrument it, don't assume it

**Rationale.** Verbatim-only is defensible as *auditable* memory, but the plan
never states the metric that proves it isn't just noisier memory. If jev.md
can't beat MEMORY.md on "wrong things it tells the agent", this is a science
project.

**Alternative.** The demo fixture must include a planted memory error that
jev.md avoids by construction (quote + confidence + ask section). Add
`jev-md stats`: facts by category, % with support ≥2, open asks. If the tool
can't show its own precision, reviewers assume it has none.

---

## 2. Phase B token math

### [MAJOR] F2.1 — The budget estimator must count questions, not just state; worst case is ~2× the plan's mental model

**Rationale.** The plan says "~32k token budget estimator + batcher" but never
says what is counted. Run the numbers at 100 statements/session: ~80 candidates
→ Phase A = 240 questions in 4 batched requests — fine. Say 20 survive →
Phase B = 20×5×2 = 200 Noul questions + conditional verdict Choices. State
(20 candidates + ~60 unique facts × 600 chars ≈ 10k tokens) + 200 instructions
(~40 tok each ≈ 8k tokens) ≈ 18k — fits, once. But a heavy day-long session
yields 300–500 candidates → 60+ survivors → 600+ Phase B questions → question
instructions *alone* (~24k tokens) approach the budget before state. The
"≤24 candidates/request" heuristic bounds Phase A only; nothing bounds Phase B.

**Alternative.** Token estimator = `chars/4` over (serialized state +
concatenated question instructions); batch Phase B by estimated total tokens,
falling back to per-candidate requests (1 candidate + its k facts) when large.
Tests at 300- and 500-candidate synthetic loads, not just fixtures of 10.

### [MAJOR] F2.2 — FTS5 top-5 caps Phase B's value: you'll spend tokens on irrelevant pairs and miss the real conflicts

**Rationale.** FTS5/BM25 is lexical. "use bun for scripts" vs "we migrated off
npm" share almost no tokens — that contradiction pair is never retrieved, never
asked. Meanwhile "always use uv run" vs "uv is the runner here" share plenty of
tokens and *do* get asked — a pair code could dedupe for free. So the expensive
pairwise machinery fires on easy pairs and misses the paraphrase-level
conflicts that are the entire justification for using Jev. k=5 over short
quotes will mostly return noise.

**Alternative.** (a) Dedupe in code first: normalized text hash + Jaccard ≥0.6
→ auto-duplicate, zero Jev spend. (b) FTS retrieve k=10, re-rank by token
overlap, keep top-5. (c) For v1, spend the contradiction question only on
high-significance candidates (score ≥2). (d) Document semantic retrieval as v2.

### [MINOR] F2.3 — Conditional verdict fan-out is worse than speculative fan-out

**Rationale.** "If contradiction ≥ 0.6: ask verdict Choice" branches inside the
hot loop, making batch shapes data-dependent exactly when batches are largest
(real drift → many pairs cross 0.6). The API docs explicitly endorse asking
speculatively and ignoring answers ("no cost to asking").

**Alternative.** Always ask all three pair questions; code ignores the verdict
when contradiction < 0.6. Slight token cost, deterministic batches, one less
branch, matches the documented pattern.

---

## 3. Thresholds and confidence semantics

### [MAJOR] F3.1 — "Confidence" is three different things; the schema stores one REAL; no formula exists

**Rationale.** Each fact accrues three confidence-bearing judgments: durable
(Noul — *no* confidence field, per the API reference; distance-from-0.5 is our
proxy), category (Choice — has confidence), significance (Score — has
confidence). DOMAIN.md defines Confidence as the Noul distance, but the PLAN's
jev.md example shows "confidence 0.91", which implies a ×2 normalization
(|0.955−0.5|×2) that appears nowhere in any doc. Which signal lands in
`facts.confidence`? Unspecified. The decision rules then mix raw nouls
(same_claim ≥ 0.8, contradiction ≥ 0.6) with Choice confidences (new_overrides
≥ 0.8) under one word. A receipts-first product cannot afford semantic fuzz on
its headline number — a jev.directory reviewer asking "what is 0.91?" currently
gets no answer.

**Alternative.** `thresholds.py` defines one named formula (e.g.
`fact.confidence = clamp01(2*|durable−0.5|)`, or that times
`category_confidence` — pick one, document it), the raw judgment JSON is stored
per F7.1, and the PLAN's example is regenerated so it is reproducible by the
formula.

### [MINOR] F3.2 — The 0.7 gate is fine; the silent-drop zone (0.5–0.7) is not specced

**Rationale.** 0.7 on a calibrated probability for a reversible write is
defensible per the confidence docs. But candidates scoring 0.5–0.7 vanish —
never surfaced, never re-asked, never counted. For a tool whose headline
features include an offline queue and an "ask" section, silently dropping
uncertain input is an unforced omission.

**Alternative.** Keep the gate; record `dropped_low_durable` in `runs.stats`
and surface "N near-miss candidates" in `jev-md status`. No new UX.

### [MINOR] F3.3 — Thresholds are priors, not measurements

**Rationale.** RLCD calibration is aggregate; a specific question template can
be systematically shifted (the durable Noul may run hot or cold). 0.7/0.8/0.6
are plausible but untested.

**Alternative.** Ship as-is, but hand-tune on the fixture corpus before the
demo and record the tuning in the DDD note — otherwise "calibrated" is a claim,
not a result.

---

## 4. Hand-rolled confidence decay

### [MAJOR] F4.1 — Decay is pseudo-science and, worse, backwards for settled facts

**Rationale.** "Decays when Dreams pass without re-support" punishes exactly
the facts that are *most* settled: once "use uv" is absorbed into the team's
workflow, nobody repeats it, re-support stops, confidence decays, and the fact
drifts toward the ask section. Absence of mention is not evidence of staleness
— session topics drive mentions; truth doesn't. And since `retire` requires
contradiction ≥0.8, decay never actually retires anything; it only re-sorts
jev.md and generates spurious asks. So it adds noise to the one section
("questions for you") that must stay high-precision to demo well. A
hand-rolled linear decay also hands reviewers an easy "where did this formula
come from?" attack on a product whose pitch is calibration.

**Alternative.** v1: no decay. Sort by significance × confidence; show
`last_supported_at` as a receipt field ("last seen Sep 18") and a purely visual
staleness badge at >30 days. Only contradiction evidence changes status. If
"what it forgot" is wanted for the pitch, frame it as *surfacing*, not
mutation. Record the decision in DOMAIN.md — "we don't do decay because it's
unfounded" is a stronger calibration story than an arbitrary slope.

---

## 5. Privacy

### [BLOCKER] F5.1 — Hooks make transcript upload the default path; opt-in is specced as a question mark and enforced nowhere

**Rationale.** Once `install` writes the SessionEnd hook, ingest is automatic
per session and grading fires on next dream — the user never makes a
per-project decision at the moment it matters. The plan's own risk section
leaves opt-in as "(lead decision pending)". Claude Code hooks in
`~/.claude/settings.json` are *global*: unless the installed hook checks a
per-project marker, every repo on the machine — including client work under
NDA — starts shipping candidate quotes to a third-party API. "README must
state it" is disclosure, not consent. For a tool whose entire pitch is trust,
one "it uploaded my client's session" screenshot ends the launch.

**Alternative.** (a) Hooks ingest locally, always. Grading requires a
per-project marker (`~/.jev-md/projects/<slug>.optin`) created by
`jev-md init --enable-grading`; no marker → candidates queue, `status` says
so. (b) `install` prints exactly this behavior. (c) README privacy section:
what leaves (candidate quotes ≤600 chars, never whole transcripts), when (only
during dream/audit with `TYPESAFE_API_KEY` set *and* project opted in), how to
stay fully local (`--offline`, no key).

### [BLOCKER] F5.2 — No secret redaction before quotes leave the machine

**Rationale.** Transcripts routinely contain pasted API keys, tokens,
connection strings, and customer data. The pipeline sends candidate quotes
verbatim; a 600-char window is plenty to contain a live secret. MIT-licensed
viral tool + "we beam your session quotes to an API" + no scrubber = the
post-mortem writes itself.

**Alternative.** Deterministic redaction pass at ingest, *before storage*
(so even the local DB never holds raw secrets): stdlib `re` patterns for
`sk-*`, `AKIA*`, `gh[ps]_*`, JWTs (`eyJ…`), `-----BEGIN`, `password=…`, plus a
high-entropy token heuristic. This becomes a feature: "redaction at rest".

### [MAJOR] F5.3 — jev.md at project root will be committed, leaking verbatim private quotes into git

**Rationale.** Facts are verbatim human quotes — frustration, names, client
mentions. The file sits at project root; the default behavior of every
developer is to commit new files. The plan says nothing about .gitignore.

**Alternative.** Quickstart step: add `jev.md` to .gitignore (or commit
deliberately — shared team memory is a real use); `jev-md dream` prints a
one-time hint when jev.md is untracked and not ignored.

---

## 6. Hook safety

### [MAJOR] F6.1 — The real failure mode isn't breaking sessions, it's silently never working

**Rationale.** SessionEnd runs *after* the session, so "never break a session"
is nearly free — worst case is noise on exit. The dangerous modes: (a) the
hook's environment PATH lacks python3 → command not found, logged nowhere the
user looks; (b) the parser crashes on a real transcript shape (payload shape is
still unverified per the DDD note!) → ingest dies every single time; (c) the
user believes they have memory; jev.md never updates. Silent death for weeks.

**Alternative.** Ingest wrapped in a top-level `try/except Exception → log →
exit(0)` — always exit 0. `install` emits a verify one-liner
(`echo '{"session_id":"t","transcript_path":"…"}' | jev-md ingest --test`).
`jev-md status` shows last ingest timestamp + last error; `dream` warns when
pending events exist but the last ingest failed.

### [MAJOR] F6.2 — Concurrent ingests vs SQLite: no WAL, no busy_timeout anywhere in the plan

**Rationale.** Two sessions ending simultaneously (normal with parallel agents)
→ two detached ingest processes → `database is locked` → one ingest lost,
which silently drops events from the queue the architecture promises never to
lose. The schema section never mentions journal mode or busy timeouts.

**Alternative.** `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;` on every
connection; keep write transactions short. One line each; testable with two
threads hammering ingest.

### [MINOR] F6.3 — Detached spawn is over-specced

**Rationale.** "Spawn detached child if needed" — ingest is local parse + sqlite
insert with no network; it should take <500ms synchronously. A detached child
adds zombie/log-rotation concerns for zero benefit at ingest time. Grading must
never be in the hook path — the plan agrees but doesn't say it plainly.

**Alternative.** Hook = synchronous ingest only; grading only via `dream`.
Simpler than specced, and simpler is safer at a trust boundary.

### [MINOR] F6.4 — Partial JSONL tail

**Rationale.** The transcript's last line may be unterminated/partially flushed
at SessionEnd; a naive `json.loads` per line crashes (feeding F6.1).

**Alternative.** Parser skips a trailing unparseable line; content-hash event
ids (F7.3) make re-ingesting that line on the next run safe.

---

## 7. SQLite / FTS5 schema and the fact lifecycle

### [MAJOR] F7.1 — No judgments table: the receipts aren't actually stored

**Rationale.** DOMAIN.md says "Judgments are evidence, recorded in the run
log", but the schema records runs and never the judgments themselves. The raw
Jev answers — the actual receipts — are discarded; only derived confidence
survives. A receipts product that throws away its receipts cannot defend a
single number in jev.md, cannot debug threshold behavior, cannot re-grade
after tuning.

**Alternative.** Add
`judgments(run_id, subject_kind, subject_id, question_id, type, answer_json, created_at)`
with an index on (subject_kind, subject_id). Trivial storage cost.

### [MAJOR] F7.2 — The `ask` lifecycle has no exit

**Rationale.** "Questions for you" surfaces low-confidence conflicts — to whom,
and then what? There is no `jev-md resolve`, no resolutions table, no expiry.
Asks accumulate forever; the section becomes noise; and since that section is
the demo's money shot, unbounded growth directly degrades the artifact.

**Alternative.** Pick one for v1: asks expire after N unresolved dreams (with a
stat), or a `jev-md resolve <fact_id> --keep-new|--keep-old` command writing
a resolution row. Do not ship an appendix that only grows.

### [MINOR] F7.3 — Event id is fragile: sha256(path + line_no)

**Rationale.** Line-number-based ids break if the agent tool rewrites/compacts
a transcript file (resume flows do this in some versions) → duplicate events →
duplicate candidates → wasted Jev tokens and double support counts.

**Alternative.** `id = sha256(normalized line content)`. Self-healing under
file rewrites; extends the dedupe-by-hash approach already claimed for
candidates.

### [MINOR] F7.4 — FTS5 sync and status filtering unspecified

**Rationale.** External-content FTS5 needs triggers or manual index
maintenance; retired/superseded facts must not pollute similarity search (a
superseded "npm is the runtime" fact matching new candidates inverts the
conflict logic). Also: no UNIQUE on `fact_links`, and no `schema_version`
table in the shown DDL even though M3 promises migrations.

**Alternative.** Delete-from-FTS on retire/supersede (keep the row for
provenance), add `UNIQUE(fact_id, related_id, relation)`, include
`schema_version` in the initial migration rather than retrofitting it.

---

## 8. Product / demo

### [MAJOR] F8.1 — The viral demo is `audit`, and the plan sequences it as a side quest

**Rationale.** jev.md generation requires setup, transcripts, and opt-in — a
slow first act for a directory listing. `jev-md audit MEMORY.md` works on a
file the viewer *already has*, needs no hooks, one command, and produces the
visceral moment: "your agent's memory contains 3 stale lines and 1 wrong one —
here are the receipts." Every Claude Code user has a MEMORY.md; almost none
have transcripts handy. Sequencing audit at M5, behind the full dream pipeline,
risks the best demo being the least polished.

**Alternative.** Make audit the demo lead; consider pulling M5 earlier — audit
needs only the judgment module + a line parser, not the full memory store.
30-second script: (1) `audit` a real MEMORY.md, catch a planted stale line
with its evidence receipt (10s); (2) `dream` on the fixture repo, show
jev.md with per-line receipts (10s); (3) show the planted contradiction
surfaced in "questions for you" (10s). Plant the contradiction in the fixture
repo on purpose — do not hope one appears.

### [MAJOR] F8.2 — No share artifact: the output never leaves the terminal

**Rationale.** Viral means someone screenshots something. A markdown file of
quotes is not a screenshot; a graded audit report with stale/wrong verdicts and
confidence numbers is. M7 lists a "GIF placeholder" — a placeholder at launch
is a launch without the asset.

**Alternative.** Design the audit terminal report for screenshotting (aligned
columns, verdict keywords, receipt lines), add `--json`/`--md` output, and make
an actual recorded GIF part of M7's done definition.

### [MINOR] F8.3 — Name/file collisions and first-run friction

**Rationale.** `jev.md` at project root may collide with a user's existing
file; the path-slug scheme (sha1 of path) is opaque when debugging hook issues.

**Alternative.** The format's sentinel comment is already specced — use it:
refuse to overwrite a jev.md that lacks it, unless `--force`. `status`
prints all resolved paths.

---

## 9. Governance nits (for the lead)

- **[MINOR] Finish-condition conflict.** MISSION bars "API spend beyond smoke
  tests" but PASS #3 requires "real jev.md generated from real transcripts" —
  a full Phase A+B run is dozens of requests, not a 3-candidate smoke. Resolve:
  declare the real-transcript dream part of the smoke budget, capped at N
  requests, logged in `runs.stats`.
- **[MINOR] Codex notify is unverified.** The DDD note marks the Codex hook
  payload "pending". If Codex notify doesn't deliver a transcript path on
  session end, M6's codex leg collapses to manual/cron. Verify before M1 — it
  shapes the ingest CLI contract — not at M6.
- **[MINOR] FakeJev needs an adversarial mode.** If the fake always returns
  peaked distributions, tests exercise only the happy path and the ask/queue
  logic — the differentiating logic — ships untested. Flat distributions and
  0.5 nouls must be first-class fake behaviors.

---

## What I'd change before M0 starts, in order

1. F5.1 + F5.2 (privacy: per-project grading opt-in + ingest-time redaction) —
   these change the CLI surface and README, so decide now.
2. F3.1 (one confidence formula, written down) — cheap now, painful after M2.
3. F4.1 (drop decay in v1) — removes code and removes a demo-risk.
4. F7.1 (judgments table) — schema changes are free today, migrations tomorrow.
5. F8.1 (audit-first demo strategy) — may reorder M4/M5.
