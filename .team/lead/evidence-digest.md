# Evidence digest — Jev competitive landscape (for Kimi's devil's-advocate review)

Compiled by the Lead from four independent research streams, 2026-09-21.
Status at compile time: Subagent A (dev-tool repos) and Subagent C (open replicas) reports complete; Lead's personal close-read of fast-jev-compaction complete; awesome-jev census (Subagent B) and GLM compaction-wave report still pending. Kimi attacks what is here.

## jevmory's claimed niche

Persistent, cross-session, cross-agent project memory for coding agents: ingests Codex + Claude Code + Droid transcripts, Jev grades verbatim facts with calibrated confidence, keeps them in a per-project `jevmory.md` with file:line evidence receipts, local-only, stdlib-only Python.

## Evidence in hand

### 1. fast-jev-compaction (tamaratran, ~5k stars) — Lead's personal source read

- Claude Code plugin replacing the compaction summary with Jev decisions. For every non-pinned tool call, two Noul questions (keepCall, keepResult), batched under 30k request tokens, threshold 0.5.
- Purely within-session. Nothing survives the session; nothing is written to disk. It deletes tool calls/results from the live conversation; kept content stays verbatim, in order.
- Integration: Claude Code function-hook plugin (`session.compact` event, early-access flag), falls back to built-in summary on failure or <25% reduction.
- Key design ideas: whole-conversation state with results omitted (`ok, 4213 chars` notes); staged state shrinking (inputs truncated 1000→200→60 chars, texts abridged, old messages collapsed) to fit 25k tokens; `JevAsker` injectable transport; pinned first + newest 6 messages.
- Position: complements jevmory (in-session pruning vs cross-session memory), does not compete.

### 2. Dev-tool repos (Subagent A: foreman, jev-review, jev-search, pg-jev, jev-trader, jev-ultrafast)

- None competes. Closest is thruwire/foreman: supervises Codex/OpenCode runs with a fixed battery of 10 Noul questions per request, persists per-run state to `.foreman/runs/` — but run-scoped recovery state only; run N learns nothing from run N−1; one agent at a time.
- All others: stateless (jev-search, jev-trader, jev-ultrafast), session-scoped cache (pg-jev), or single-latest artifact (jev-review). None stores verbatim content. None is cross-agent memory.
- Steal-worthy: foreman's all-questions-in-one-request batching + clamped validation; jev-review's explicit `noMatch`/`noIssue` fallback + confidence gates; pg-jev's measured ~20-items-per-state batching limit and content-hash answer cache.

### 3. Open replicas (Subagent C: kev, SemIf, NanoJev, jevlike, mini-jev)

- kev (jaredpalmer) is verified wire-compatible with `api.typesafe.ai/v1/systemone` (endpoint paths, request/response schemas, all three question types; own tests run the official typesafe-sdk against it with only a base_url change). Apache-2.0. 0.6B/4B/8B Qwen3 + LoRA; 4B fits a 32 GB Mac.
- Caveats: probabilities overconfident out-of-domain (8.2% of new-source questions get ≥0.9 on wrong answers); Kev-4B trails hosted Jev by ~6–7 accuracy points; single-request server, no auth.
- Others: SemIf (no HTTP server, custom JSONL), NanoJev (game-specialized, own audit admits contract gaps), jevlike (training starter), mini-jev (measurement study).
- Implication: jevmory could ship a `--backend` escape hatch today via base_url config against kev.

### 4. last30days social scan (82 items, 7 sources)

- Ecosystem week one: ~239 builds, 126 repos, 17.5k stars. Compaction wave is the largest coding-agent-adjacent cluster. Open replicas forming on r/LocalLLaMA. No social-source sighting of a persistent cross-session cross-agent Jev memory tool.

### 5. Community brain query (AI Agents community, ~4,000 members)

Lead asked directly: any persistent cross-session Jev memory tools? Verbatim answer recorded in `.team/lead/rombot-answer-2026-09-21.md`. Key points:

- **No Jev-based cross-session memory tool exists in the community corpus.** "Nobody has built a cross-session memory tool on top of it yet — the model is 3 days old. The gap between Jev-as-compactor and Jev-as-cross-session-memory-backbone is exactly where a new tool could emerge." First-mover window is real but time-boxed.
- **Non-Jev adjacent tools that DO exist** (Kimi: weigh these as counter-evidence):
  - **agentmemory (rohitg00)** — local memory layer via hooks/MCP/REST, shared across Claude Code, Cursor, Codex; hybrid search with KG-like structure; local embeddings, no server; viewer UI. Closest non-Jev competitor to the claimed niche.
  - **Claude Command Center (CCC)** — ingests all Claude Code + Codex session transcripts, keyword search across history, sessions reference each other's discoveries.
  - **Gossip (Yogev)** — inter-agent messaging/observation between live sessions (different problem: coordination, not memory).
  - **Ofer Blutrich's Obsidian vault system** — daily background skill scans repos/WhatsApp/calendar/gmail into project memory files in a git-synced vault. A "durable project knowledge file" pattern that predates jevmory's artifact shape.
  - **Simple state files** — agents write state to shared files; community's "old simple way."
  - **Mem0 / Letta / Zep** — established but runtime-agent memory, not coding-agent memory (community consensus, including Rom's own take).
  - **Enterprise emerging pattern**: "Context Lakes" / "shared brain for agent fleets" discussions.
- Community caveat on Jev-as-compactor from Alex Greenshpun: selective deletion can cause loops when the model must redo tool calls to recover lost data.

## The claim under attack

"The niche — persistent cross-session cross-agent coding-agent memory with verbatim evidence-linked facts, graded by Jev — is open."

Kimi: attack this. Strongest counter-evidence? What did we miss (non-Jev tools like mem0/letta/khoj-style memory, Claude Code native memory features, OpenAI/Codex roadmap signals)? What would make jevmory's value proposition collapse even if the niche is technically open?
