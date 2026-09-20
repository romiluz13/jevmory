# Competitive landscape: Jev-based coding-agent memory (and the market around it)

Research date: 2026-09-21. Compiled by the lead from five independent streams: a full close-read of the compaction wave (GLM), a close-read of six dev-tool repos (subagent A), a census of the entire awesome-jev ecosystem — 959 deduplicated entries (subagent B), an open-replica compatibility assessment (subagent C), and a devil's-advocate market review including non-Jev competitors (Kimi). Raw evidence: `.team/glm/research-compaction-wave.md`, `.team/kimi/findings-competitive-review.md`, `.team/lead/evidence-digest.md`, `.team/lead/rombot-answer-2026-09-21.md`.

## Verdict

**The narrow niche is open. The broad category is a battlefield, and the window is short.**

- Inside the Jev ecosystem (959 entries): **zero projects** combine persistent memory + cross-session state + cross-agent support + transcript ingestion. Five partial neighbors exist; none is local, zero-dependency, agent-agnostic, and coding-session-specific at once.
- Outside the Jev ecosystem: the broad category — "your agent remembers between sessions" — is one of the most crowded in agent tooling. claude-mem (94.3k stars, 8+ agents, marketplace distribution) owns the funnel; Anthropic and OpenAI both ship native memory (`MEMORY.md` in Claude Code v2.1.59, Codex Memories in `~/.codex/memories/`); mem0 owns the vocabulary, including the word "Dream."
- jevmory's unclaimed ground is the **mechanism**: Jev-graded verbatim facts with calibrated confidence, evidence receipts (file:line), conflict resolution, and a never-pruned source transcript — in a local, stdlib-only, agent-agnostic package.
- Strategic conclusion (Kimi's, adopted): **the receipts are the moat, not the store.** Position jevmory as the verification/audit layer over agent memory — including everyone else's — with the store as the reference implementation.

## 1. The Jev ecosystem at a glance (census, 959 unique entries)

| Bucket | Count | Saturation |
|---|---|---|
| Eval / calibration / open reproductions | 158 | Saturated (~40 open-weight Jev replicas) |
| Coding-agent tooling & guardrails | 149 | Saturated (stop hooks, gates, routers) |
| Discussions / field notes / meta-lists | 126 | — |
| SDKs & language clients | 116 | Saturated (15+ languages, 8 Go clients alone) |
| Browser / computer / mobile agents | 76 | Crowded |
| Games, robotics & simulation | 71 | Crowded |
| Routing / classification / scoring | 52 | Crowded |
| Demos | 50 | — |
| Apps, media & creative | 43 | — |
| Framework & infra integrations | 35 | Crowded |
| Official resources | 41 | — |
| Context & compaction | 7 | Small but high-mindshare; **all per-session** |
| MCP servers (pure) | 6 (+26 touching) | Thin |
| Finance, search, data, moderation | ~13 | Thin |
| **Persistent cross-session cross-agent coding memory** | **0 complete, 5 partial** | **Empty** |

Pattern: the ecosystem built routers, gates, compactors, and judges for the *inside* of a session, plus 40 replicas of the model itself — but nobody built the memory layer sessions could share. Week-one build stats (madewithjev): 239 builds, 216 authors, 198 of whom shipped exactly one build. Wide, shallow, fast-copying. If the gap gets demoed on X, it fills within days.

## 2. Closest competitors inside the Jev ecosystem

### 2a. The compaction wave — validates the substrate, does not compete

| Project | What it does | Scope | Kept content | Cross-session | Cross-agent |
|---|---|---|---|---|---|
| tamaratran/fast-jev-compaction (~5k★) | Claude Code compaction via 2 Noul questions per tool call (keepCall/keepResult, threshold 0.5) | one live session | verbatim, delete-only | no | no |
| zaycruz/fast-jev-compaction-pi | pi port + tombstones, error-tail retention | one session file | verbatim + deterministic tombstones | same-session-file only | no |
| KamilPostrozny/pi-fast-jev-compaction | non-destructive: filters rendered prompt, raw transcript intact; pressure/hysteresis machine | one session | verbatim + bounded deterministic excerpts | reload/branch-restore only | no |
| willfish/pi-observational-memory-jev | **nearest neighbor**: Jev-graded verbatim observations (keep Noul + kind Choice) → kind-typed topic files in `.memory/<session-id>/` | per-session-id dir | verbatim single-line excerpts | topic files survive session end + fork inheritance; **new sessions start empty** | no (pi only) |
| odradekk/pi-square #409 | research issue: verbatim pruning as complement to their Context Memory | n/a | n/a | n/a | no |
| Kylejeong2/nanocode PR #1 | stdlib-Python port; prunes `history.md`, keeps never-pruned `history.full.md` | one session | verbatim | no | no |

**Verdict: open.** Every wave member manages one runtime's context window. Cross-session durability appears only as same-session-file persistence, fork inheritance, or an unpruned companion transcript. The wave *validates* Jev as a grading backend (adopted by 4+ runtimes in four days) — the differentiation is what you do with the grades.

### 2b. The five persistent-memory neighbors (from the 959-entry census)

| Project | Claim (verbatim) | What it lacks vs jevmory |
|---|---|---|
| **jev-carryforward** (Dharundp6) | "What your last session knew, scored against what this one is doing. MCP server: a per-project ledger written as things happen, recalled per task with Jev" | Closest neighbor. Ledger written by hooks (not transcript ingestion); no verbatim-quote facts, receipts, or conflict resolution; MCP+Gateway dependency |
| **Cheshi** (CheshiAI) | "Jev-powered conversation memory: find past sessions and revisit decisions with original sources. macOS workspace for OpenAI Codex" | Codex-only, macOS Electron app, not a library; search-style recall, not graded fact store |
| **dgui-hypermem** (ctaxnagomi) | "Self-hosted hybrid memory MCP server on Cloudflare Workers with a JEV reasoning layer" | Cloud-hosted, generic agent memory, not coding-session-specific, not local |
| **invalidate** (chopratejas) | "Gives every remembered fact a lease and asks Jev whether new evidence ends it" | A TTL layer over someone else's store — not a memory system. **Idea worth adopting: staleness-by-design** |
| **jev-recall** (samdotmak) | "Retrieve by relevance, not resemblance: filter an AI assistant's memories with Jev" | A retrieval filter over an external store |

## 3. Non-Jev competitive reality (Kimi's review — the hard part)

- **claude-mem (thedotmack)** — 94.3k stars, 344 releases, Apache-2.0. "Persistent Context Across Sessions for **Every Agent**": Claude Code, Codex, Cursor, Gemini, Copilot, OpenCode, Grok, Windsurf. One-click Plugin Marketplace install. ChromaDB local worker. Cloud sync. Secret redaction at ingest (they built it independently too). It solves the same felt pain with a worse mechanism (LLM-compressed summaries — exactly what jevmory's no-hallucination pitch attacks) and a vastly better funnel. **Users do not buy mechanisms.**
- **Native platform memory** — Claude Code Auto Memory (v2.1.59, Feb 2026) writes `MEMORY.md` by default; Codex Memories consolidates idle sessions in the background into `~/.codex/memories/`. The category's founding pitch — "your agent forgets everything" — is no longer true by default. But: both are single-tool and single-machine *by business model* (Anthropic has no incentive to remember for Codex). Cross-tool is the moat native vendors won't cross.
- **mem0** — funded, SOC 2, Claude Code plugin + Codex MCP integration, free tier. "Mem0 Dream" is a shipped background-consolidation product line: **mem0 owns the word "Dream" in this market.** Shipping `jevmory dream` into that SEO landscape is a self-inflicted wound.
- **agentmemory (rohitg00)** — local, hooks/MCP/REST, cross Claude Code/Cursor/Codex, hybrid search, local embeddings, no server. The closest non-Jev mechanism match.
- **Zep/Graphiti** — temporal knowledge graph with episode provenance (the conceptual core of receipts, generalized); 63.8% LongMemEval vs mem0's 49.0%. Runtime-agent aimed.
- **CCC, Gossip, Obsidian-vault patterns, simple state files** — the community's existing answers; none graded, none verbatim-receipted.

## 4. Differentiator matrix

| | Session scope | Cross-session | Cross-agent | Verbatim kept | Receipts (file:line) | Calibrated confidence | Conflict resolution | Local + zero-dep |
|---|---|---|---|---|---|---|---|---|
| fast-jev-compaction + wave | ✓ | — | — | ✓ | — | ✓ (keep/drop) | — | varies |
| willfish observational | ✓ | fork-only | — | ✓ | sourceEntryId | keep score | — | pi-only |
| jev-carryforward | ✓ | ✓ | ~ (MCP) | — | — | ✓ | — | — (MCP+Gateway) |
| Cheshi | ✓ | ✓ | — | ~ | ~ sources | — | — | — (Electron app) |
| claude-mem | ✓ | ✓ | ✓ (8+) | — (LLM summaries) | — | — | — | — (ChromaDB worker) |
| Native MEMORY.md / Codex Memories | ✓ | ✓ | — | — | — | — | — | ✓ (but single-tool) |
| mem0 / agentmemory / Zep | ✓ | ✓ | ✓/partial | — | Zep provenance | — | — | agentmemory ~local |
| **jevmory** | ✓ | ✓ | ✓ (Codex+Claude+Droid) | ✓ | ✓ | ✓ (Jev-graded) | ✓ (supersede/contradict) | ✓ |

## 5. Ranked threats

1. **claude-mem's distribution + a grading pass.** If claude-mem (or a fork) adds confidence metadata, it closes the mechanism gap while keeping a two-orders-of-magnitude funnel lead. Kimi's Attack A: a "verified memory" mode on claude-mem's worker gets ~90% of jevmory's pitch for ~10% of the build.
2. **Native memory improving.** The audit demo ("1 stale, 1 wrong, 1 unsupported") only sells while native memory is sloppy. Both vendors iterate; per-line confidence in Auto Memory would shrink the error surface. Dependency on a competitor's roadmap.
3. **The window itself.** The census shows a fast-copying ecosystem (198 one-build authors). The community brain's explicit conclusion: "the gap between Jev-as-compactor and Jev-as-cross-session-memory-backbone is exactly where a new tool could emerge." jev-carryforward already gestures at it. First credible mover takes the "Jev memory" slot.
4. **Unproven differentiator, paid-gated.** No public benchmark shows Jev-graded memory beats LLM-summarized memory on fidelity. kev's out-of-domain overconfidence (8.2% of new-source questions ≥0.9 on wrong answers) shows even "calibrated" claims need evidence.
5. **willfish** moving to a project-shared `.memory/` root with receipts; **pi-square #215** Context Memory adding a verbatim stage.

## 6. Positioning recommendations

1. **Lead with the audit, not the store.** "The linter for your agent's memory, whoever wrote it": `jevmory audit` works on native `MEMORY.md` (both vendors'), claude-mem output, and `jevmory.md` itself. Native memory shipping sloppy files is the best positioning luck available — every Anthropic/OpenAI memory bug is jevmory marketing.
2. **Keep cross-agent as the durable moat.** Native vendors won't remember for each other; that's a business-model constraint, not a feature gap. jevmory reads Codex + Claude Code + Droid today; more parsers later.
3. **Rename the `dream` command.** mem0 Dream owns the word. Candidates: `jevmory distill`, `jevmory grade`, `jevmory sleep` (keep the metaphor, lose the collision). Cheap now, expensive after launch.
4. **Ship `--backend` with kev support before PyPI.** Verified wire-compatible (`base_url` swap only), Apache-2.0, free, local. Removes the paid-key objection for evaluation; kev's calibration caveat is documented and kev ships benchmark tooling to tune thresholds.
5. **Publish a fidelity benchmark.** Jev-graded verbatim facts vs LLM-summarized memory, measured against planted stale/wrong/unsupported lines (the audit demo fixture is half the benchmark already). If Jev wins, that's the proof the pitch needs; if not, better to know before launch. This is the publish-gate experiment the fresh-session value test feeds into.
6. **State the design advantages the wave proved for free:** jevmory never mutates live context (no prompt-cache invalidation, unlike pruning — pi-square #409's cost analysis applies to them, not us); the SQLite event log is a never-pruned transcript (nanocode's `history.full.md` invariant, formalized).
7. **Distribution:** a thin TS plugin shim calling the Python CLI for the Claude Code marketplace (Kimi: every manual install step costs an order of magnitude of funnel). Post-launch, not pre-launch.

## 7. Steal-list from the research (engineering, ranked by GLM)

1. KamilPostrozny's pressure/hysteresis gating → dream-engine arming (margin-cleared, validated, never oscillating).
2. Kamil's deferred-destructive pattern → defer `resolve()` supersede/contradict to batch end.
3. willfish's tombstone-after-write ordering as a stated, tested invariant.
4. Kamil's bounded deterministic status-doc excerpts ("re-read the source" caveat) for context around facts.
5. zaycruz's error-tail preservation → head + tail for long evidence strings.
6. tamaratran's honesty pattern → "confidence is not proof; the agent can re-read the source" in `jevmory audit` output.
7. chopratejas/invalidate's memory-TTL concept → an optional lease/staleness pass (directly answers Kimi's receipt-rot objection).
8. pg-jev's ~20-items-per-state batching limit and content-hash answer cache; foreman's batched-Noul battery with clamped validation; jev-review's `noMatch`/`noIssue` fallback + confidence gates.

## 8. Honest open questions (not resolved by this research)

- Does a fresh session with `jevmory.md` actually perform better? (Fresh-session value test on memongo — next step, user's gate for publishing.)
- Is Jev grading measurably better than a cheap LLM judge for fact-worth-remembering? (Fidelity benchmark, recommendation 5.)
- Do developers care enough about memory *correctness* to adopt a verification tool? (Unproven demand; the audit demo is the test.)
- Receipt rot rate on real refactoring workloads. (Instrument during the memongo dogfood.)
