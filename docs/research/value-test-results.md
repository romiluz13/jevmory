# Fresh-Session Value Test — Results

Experiment date: 2026-09-21. Dogfood store: `/Users/rom.iluz/Dev/memongo/jevmory.md` (10 facts: 7 architecture, 1 pitfall, 1 convention, 1 meta; confidence 0.58–0.76; generated from 1,173 transcripts — 716 Codex + 457 Droid, 36,020 events — via 10 live dreams, 25 API calls).

## Design

Controlled A/B: two fresh worker agents, same model, same toolset, identical five-question prompt about memongo. One arm started with the full `jevmory.md` content injected (simulating a hooked session); the control arm got nothing and was instructed not to read `jevmory.md` or `.jevmory/`. Questions were drawn from the store's facts: embedding modes, write flow, admin auth, identity model, erasure-epoch pitfall.

The lead independently verified ground truth against source before scoring: the `embeddingMode !== "automated"` throw (`backend-config.ts`), the six-scope `MEMORY_SCOPE_VALUES` array (`contract.ts:35-42`), the scope precedence rule (`mongodb-scope.ts:104-122`), `WRITE_QUEUE_MAX_DEPTH_DEFAULT = 256` (`mongodb-manager-write.ts:365`), `ADMIN_ONLY_V1_PATHS` (`app.ts:425-438`), and the pitfall investigation doc.

| Measure | WITH jevmory.md | WITHOUT |
|---|---|---|
| Questions answered correctly | 5/5 | 5/5 |
| Files opened | ~9 | ~18 |
| Questions answered near-instantly from memory | 4 of 5 | 0 |
| Questions requiring multi-file tracing | 1 (Q2) | 2 (Q2, Q5) |
| Wall time | 203.3s | 188.6s |

Task receipts: WITH = session `cd0ae31b`, WITHOUT = session `672e38ab`.

## What the memory did for the session

1. **Effort halved.** The with-memory arm read roughly half the files and answered four of five questions from memory with only quick confirmation reads. The receipts doubled as file pointers: the agent went straight to `backend-config.ts`, `contract.ts`, `mongodb-scope.ts`, `app.ts` instead of discovering them.
2. **Instant recall of hard-won knowledge.** The pitfall (Q5) and the auth model (Q3) — the two questions requiring the most tracing in the control arm — were immediate for the with-memory arm. On a less-documented repo the gap would be larger.
3. **Receipts held at file level.** Every file the memory pointed at was the right file. Substance of all architecture facts was confirmed accurate by both arms independently.

## What the memory got wrong — two distinct failure modes

### Failure 1: receipt rot (expected, bounded)

The with-memory arm caught four drift items, exactly as the competitive review predicted:

- Line anchors stale: `writeConversationEvent` cited at `:385`, actually `:710`.
- Dead-letter/lease logic cited in `mongodb-derived-memory.ts:677`; it lives in `mongodb-memory-jobs.ts:33` (`MEMORY_JOB_MAX_ATTEMPTS = 3`).
- "Non-transactional fallback leaves a repairable marker" — the mechanism moved: current code routes everything through `withFencedWrite`, and the repairable marker (`extractionJobPendingAt` outbox) now attaches to post-commit staged-job release failure.
- The memory understates the evidence it sits on (the reclaim race is runtime-confirmed by a probe, not just theorized).

File-level pointers survived; line-level anchors rotted. This is tolerable — an agent lands in the right file and re-anchors in seconds.

### Failure 2: stale vintage presented as current state (the important finding)

Q5 is the standout. The memory says: *"keep explicit session plumbing usable for a later atomic owner/token condition; do not add a generic lease subsystem"* — decision-time guidance quoted from `.orchestrator/investigations/coordinator-job-ownership.md`, ingested Sep 19, graded as a durable pitfall.

But git history shows the fence **already shipped on 2026-09-06** (`b26d61093a`, "job ownership safety"): `withClaimedMemoryJobEffectBatch` commits effect batches only while lease owner/token AND admission epoch still match (`mongodb-memory-jobs.ts:483-519`), pinned by the e2e test *"blocks stale worker effects after a same-epoch lease reclaim"* (`mongodb-memory-jobs.e2e.test.ts:281`).

So the memory was stale at birth, not stale from decay: the transcripts contained the investigation-doc narration, the dream engine graded that narration as the durable fact, and no later statement about the implemented fence displaced it. The with-memory arm answered Q5 with the outdated guidance and marked it "verified" — because it verified against the investigation doc, which is a real file that still says that. The control arm, forced to trace code, found the actual fence.

**This is a content-selection limitation, not an anchor problem:** agents narrate design intent ("keep X usable for later") even after the thing shipped, and verbatim quotes preserve that narration with high confidence. A memory system that stores what was *said* must distinguish what was *said-then* from what *is*.

## Honest scorecard

- **Accuracy:** tie, 5/5 both arms. The control did not fail because memongo is unusually well documented (droid-wiki, ADRs, investigation docs). On a typical repo the memory's advantage would be larger, but this experiment cannot prove that.
- **Effort:** clear win for memory — half the files, one traced question instead of two.
- **Speed:** no measurable win (203s vs 189s). The with-memory arm chose to verify everything; wall-clock is noisy at n=1. Effort, not latency, is the honest metric.
- **Caveats:** n=1 per arm, same model, questions derived from the store's fact set (coverage-favoring, though both arms still had to verify), durations self-reported by the harness.

## Product implications (feed straight into v0.2)

1. **`audit`/re-verify is the core loop, not a feature.** The Q5 failure is precisely what a receipt-reverification pass catches: the fence's e2e test contradicts the stored guidance. The competitive-review positioning (audit layer as the moat) is validated by our own dogfood.
2. **Facts need a vintage marker, not just "last seen."** "Last seen Sep 19" refers to ingest date. A fact quoting decision-time narration should be distinguishable from a fact describing shipped state — e.g., grade `is-current` against the repo at audit time.
3. **Line anchors are cosmetic; keep them but never trust them.** File-level pointers held perfectly across two weeks of drift. Consider rendering receipts as file-only by default.
4. **The value is effort and recall, not latency.** The pitch should say "half the exploration, instant access to pitfalls and decisions" — not "faster sessions."

## Verdict

**Value demonstrated, with an honest boundary.** A fresh session with `jevmory.md` answered all five questions correctly with half the exploration, and got instant access to the kind of knowledge (pitfalls, auth models, design decisions) that costs the most to rediscover. The same experiment also demonstrated, on our own store, the exact failure mode (stale vintage) that the audit layer must solve — which is both the strongest argument for the product's real differentiator and a reminder that an unverified memory file is a liability, not an asset.
