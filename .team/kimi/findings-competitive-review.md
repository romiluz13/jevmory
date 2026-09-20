# Kimi — competitive review: is jevmory's niche open?

Date: 2026-09-21. Reviewer: Kimi (devil's advocate).
Target claim (Lead's evidence digest, compiled 2026-02-05):
"The niche — persistent cross-session cross-agent coding-agent memory with
verbatim evidence-linked facts, graded by Jev — is open."

---

## Verdict: conditionally open — and the condition is brutal

The **narrow** niche is technically open: nothing in the market grades
verbatim-quote facts with calibrated confidence and attaches evidence
receipts. That specific mechanism has no direct competitor.

The **broad** niche it lives inside — persistent, cross-session, cross-agent
memory for coding agents — is **not open**. It is one of the most crowded
categories in agent tooling, with a ~94k-star incumbent, a funded cross-tool
MCP layer, and **two shipped native platform features** that did not exist
when the digest was written.

Which means: jevmory survives only if it positions as the **audit and
verification layer over everyone else's memory** — including the native
`MEMORY.md` files Claude Code and Codex now generate — not as yet another
memory store. The store is a commodity. The receipts are the moat.

### The meta-finding: the digest itself is stale

The digest's evidence window closed 2026-02-05. Nearly all the strongest
counter-evidence below shipped **after** that date:

- Claude Code Auto Memory: shipped v2.1.59, Feb 2026 (at/just after digest).
- Codex CLI Memories: documented/shipped May 2026.
- mem0 "Dream" background consolidation + staleness product line: Aug–Sep 2026.
- claude-mem's growth to 94.3k stars, marketplace distribution, cowork/cloud
  plugins, and 8+ agent integrations: bulk of it post-February.

A seven-month-old niche assessment in this category is not a minor caveat;
it is the finding. (There is an irony available here about a product whose
whole thesis is that memory rots. Use it in the marketing.)

---

## Angle 1 — Non-Jev competitors: the space is packed

### claude-mem (thedotmack) — the incumbent that owns the user

Source: github.com/thedotmack/claude-mem, fetched 2026-09-21.

- **94.3k stars, 8.3k forks, 162 contributors, 344 releases**, v13.25.2,
  commits landing hours before this review. Apache-2.0, TypeScript.
- Tagline: "Persistent Context Across Sessions for **Every Agent** —
  captures everything your agent does during sessions, compresses it with
  AI, injects relevant context back into future sessions." Works with
  Claude Code, Codex, Cursor, Gemini, Copilot, OpenCode, OpenClaw, Hermes,
  Grok, Windsurf. That is jevmory's entire "cross-agent" claim, shipped,
  with 8+ more agents than jevmory's two.
- Distribution jevmory does not have: Claude Code **Plugin Marketplace**
  one-click install, plus `.codex-plugin`, `.cursor-plugin`, `.grok-plugin`
  packages. ChromaDB-backed local worker; lifecycle hooks (SessionStart,
  PreCompact, PostToolUse). Cloud sync + a cowork plugin for Claude cloud
  sessions (ephemeral-container HTTP hook shims, secret redaction before
  envelope — they independently built jevmory's redaction-at-ingest too).
- What it lacks: calibrated confidence, verbatim-quote-only facts,
  contradiction/support scoring, receipts. Its memory is LLM-compressed
  summaries — exactly what jevmory's "can't hallucinate" pitch attacks.

### mem0 — the funded cross-tool layer, and it took the "dream" name

Sources: mem0.ai blog posts (May 8, Sep 3, Aug 6 2026), docs.mem0.ai.

- OSS + hosted, SOC 2, free tier. Claude Code plugin and a **Codex MCP
  integration**: one `[mcp_servers.mem0]` block in `config.toml` exposes
  nine memory tools. Cross-tool memory (Codex CLI session → surfaces in
  Cursor), semantic recall, per-user isolation, no size ceiling.
- **Mem0 Dream**: "background memory consolidation for AI agents"
  (Sep 3, 2026) and "Stale AI agent memory and how Mem0 Dream fixes it"
  (Aug 6, 2026). jevmory's most distinctive feature *name* — `jevmory
  dream`, grading "while you sleep" — is already a funded competitor's
  shipped product line, staleness-fixing included. Search for either and
  mem0 ranks first.
- mem0's pitch is LLM-summarized memory, no confidence calibration, no
  receipts. Different mechanism — but it owns the vocabulary.

### The rest of the field (each a credible "good enough")

- **agentmemory (rohitg00)** — flagged in the Lead's own community-brain
  answer: local memory layer via hooks/MCP/REST, shared across Claude
  Code, Cursor, Codex; hybrid search with KG-like structure; local
  embeddings, no server. Closest non-Jev match to the claimed niche.
- **Zep / Graphiti** — temporal knowledge graph whose edges carry "when
  each fact was true and where it came from" (episode provenance — the
  conceptual core of jevmory's receipts, generalized). 63.8% on
  LongMemEval vs mem0's 49.0% (particula.tech comparison, Jun 2026).
  Aimed at runtime agents, not coding agents — but the provenance idea
  is proven and portable.
- **Claude Command Center** — ingests all Claude Code + Codex transcripts,
  cross-session search. Transcript-ingest competitor.
- Also-rans with real traction: cognee, Letta/LangMem, Hindsight
  (Vectorize), basic-memory, supermemory, OpenMemory, mempalace, plus
  Ofer Blutrich's Obsidian-vault pattern (durable project-knowledge files
  predate jevmory's artifact shape).

**Bottom line for Angle 1:** "cross-session cross-agent memory for coding
agents" has at least a dozen credible entrants and one dominant incumbent.
Only the Jev-graded-verbatim-receipts mechanism is unclaimed.

---

## Angle 2 — Platform risk: the floor already moved

### Claude Code Auto Memory (native, default-on)

- Shipped in Claude Code v2.1.59 (Feb 2026). Writes its own `MEMORY.md`
  by default; ~200 lines / 25 KB cap; flat file; no semantic search.
- Note what this does to jevmory's viral one-liner: `jevmory audit
  MEMORY.md` is **auditing Anthropic's own auto-generated file**. That is
  either the best positioning luck possible (native memory is sloppy →
  audit catches it → demo writes itself) or a dependency on Anthropic's
  error rate. Both are true; see Risk 2.

### Codex CLI Memories (native, background)

Source: mem0 deep-dive (May 8, 2026) citing developers.openai.com/codex.

- Two layers: AGENTS.md (static, user-written, 32 KiB cap, silent
  truncation) + Memories (generated): background consolidation of sessions
  idle ≥6h, a two-model pipeline (extract → merge sub-agent), secret
  redaction before disk, 30-day pruning of unrecalled memories,
  rate-limit-aware, independent read/write toggles.
- Storage is plain markdown in `~/.codex/memories/` (`memory_summary.md`,
  `MEMORY.md`, `raw_memories.md`). Recall = read the summary whole, then
  `grep` the long file. No vectors.
- Today's limits: single-machine (no sync), single-tool, per-user,
  not user-editable, unavailable in EEA/UK/CH at launch.
- Tomorrow's risk: mem0's own blog enumerates those limits as its sales
  pitch — meaning the gap list is public, and OpenAI/Anthropic read the
  same lists. Cross-machine sync or cross-tool sharing is one good
  quarter away for either vendor.

**Bottom line for Angle 2:** the default user now gets *some* memory, free,
zero-install, from both platforms jevmory hooks into. "Your agent forgets
everything" — the category's founding pitch — is no longer true. External
tools must now justify themselves against native baselines, and both
vendors are visibly iterating.

---

## Angle 3 — Product objections that could kill it even if the niche is open

1. **Receipt rot.** Verbatim quotes with file:line provenance rot on every
   refactor, rename, or reformat. PLAN's "no decay" stance makes this
   *worse*, not better: facts never age out, so the store accumulates
   confidently-graded staleness. mem0 built an entire product line (Dream)
   around staleness because it is the dominant failure mode of persistent
   memory. jevmory's honest answer is that `audit` *detects* staleness
   rather than preventing it — which is a strong product, but it means
   the store alone is a liability and the audit is the product.
2. **The verbatim ceiling.** Quotes can only record what someone *typed*.
   "The user opened foo.py, ran the suite, and it failed" is not a quote.
   LLM-summarized memory (claude-mem, Codex Memories) captures synthesized
   observations jevmory architecturally cannot. The no-hallucination
   guarantee is real; so is the recall ceiling. Users will compare on
   coverage, not purity.
3. **Calibrated confidence is an unproven differentiator.** No independent
   benchmark shows Jev-graded memory beats LLM-summarized memory on
   fidelity. And the digest's own kev caveat cuts both ways: probabilities
   run overconfident out-of-domain (8.2% of new-source questions score
   ≥0.9 on wrong answers). "Calibrated" is a claim, not a demo.
4. **Receipts assume a user behavior that may not exist.** The viral audit
   assumes developers care about memory *correctness*. If users never
   verify what their agent remembered, the receipts answer a question
   nobody asks. (The social scan's HN/Reddit skepticism threads about
   LLM decision accuracy suggest the *skepticism* exists — whether it
   converts to *demand for verification tooling* is unproven.)
5. **Name collision.** `dream` is mem0's product line now. Shipping
   `jevmory dream` into that SEO landscape is a self-inflicted wound.

---

## Angle 4 — Adoption friction

- **Install:** jevmory = clone repo, run init, run `install --agent X`,
  paste hook JSON into settings by hand. claude-mem = one click in the
  Plugin Marketplace. mem0 = one MCP config block. Every manual step
  costs an order of magnitude of funnel.
- **Ecosystem shape:** the distribution channels (Claude Code plugins,
  Cursor plugins, Codex plugins, MCP registries) are all JS/TS/npm-shaped.
  Stdlib-only Python is operationally elegant and distributionally
  isolated. (Hooks being Python is fine; the *packaging* being outside
  every marketplace is the problem.)
- **Paid key for the core feature:** grading needs `$TYPESAFE_API_KEY`.
  claude-mem runs local/free; mem0 has a free tier. jevmory's
  differentiator sits behind a third-party paid API, while a verified
  wire-compatible free replica (kev, Apache-2.0, 4B-on-a-Mac) exists and
  jevmory doesn't ship the `--backend` escape hatch the digest already
  specced.
- **Coverage:** two agents (Claude Code, Codex) vs claude-mem's 8+. The
  digest's niche sentence says "Codex + Claude Code + Droid"; the README
  quickstart only installs claude/codex hooks.
- **Artifact placement:** `jevmory.md` at project root plus a `.gitignore`
  errand vs claude-mem hidden in `~/.claude-mem/`.

---

## Angle 5 — How I would kill jevmory next month

Two attacks, pick either:

**Attack A (stand on the incumbent).** Build on claude-mem's worker and
marketplace distribution. Keep LLM-compressed summaries for coverage.
Add a grading pass — Jev, or kev for free/local — that marks high-stakes
lines with confidence + verbatim evidence. Ship it as an optional
"verified memory" mode in the marketplace. Gets ~90% of jevmory's pitch
for ~10% of the build, on top of an installed base two orders of
magnitude larger. jevmory's stdlib-only/hook-JSON/manual-install shape
cannot win that fight.

**Attack B (platform squeeze).** Anthropic adds per-line confidence or
source citations to Auto Memory (the file format is theirs); OpenAI adds
cross-machine sync to Codex Memories. Either move shrinks the error
surface jevmory's audit monetizes. The audit demo — "1 stale, 1 wrong,
1 unsupported" — only sells while native memory is sloppy.

---

## Top 3 risks, ranked

1. **claude-mem already owns the user and the distribution.** 94.3k stars,
   marketplace install, 8+ agents, free, shipping daily. It solves the
   same felt pain ("agent forgets between sessions") with a worse
   mechanism and a vastly better funnel. Users do not buy mechanisms.
2. **Native memory improves and the audit's error surface shrinks.** Auto
   Memory and Codex Memories both shipped within the last ~7 months; both
   vendors are iterating. jevmory's headline demo depends on native
   memory being wrong. That is a dependency on a competitor's roadmap.
3. **The differentiator is unproven and paid-gated.** No public benchmark
   shows Jev-graded memory beats LLM summaries; the key costs money; a
   free wire-compatible replica (kev) is one `base_url` away from
   commoditizing even that.

## What jevmory must do to survive each

1. **vs claude-mem:** Don't fight for the store. Ship jevmory as the
   **audit layer over everything**: audit claude-mem's output, audit
   native `MEMORY.md` (both vendors'), audit `jevmory.md`. Get
   marketplace distribution (a thin TS plugin shim calling the Python
   CLI) — meet users where they install. "The linter for your agent's
   memory, whoever wrote it" is a bigger, more defensible market than
   "memory store #13."
2. **vs platform squeeze:** Stay deliberately platform-agnostic and bet
   on the durable gap: native memory is single-tool and single-machine
   *by business model* (Anthropic has no incentive to remember for
   Codex). Cross-tool is the moat native vendors won't cross. Publish
   audits of native memory quality as content — every Anthropic/OAI
   memory bug is jevmory marketing.
3. **vs the unproven differentiator:** Ship `--backend` with kev support
   (the digest specced it; it's a config change), so grading has a free
   local path. Publish an **independent fidelity benchmark**: Jev-graded
   verbatim facts vs LLM-summarized memory, measured against planted
   stale/wrong/unsupported lines (the demo fixture is half of this
   already). If Jev wins, that's the proof the pitch needs; if it
   doesn't, better to know now. Either way, make grading pluggable so
   the audit product survives a grader swap.

---

## What would collapse the value proposition even with the niche open

- Memory correctness never becomes a felt pain (users don't verify →
  receipts don't matter).
- Native memory gets good enough that audits routinely find nothing.
- Jev grading isn't measurably better than a cheap LLM judge, making the
  TypeSafe dependency cost without benefit.
- claude-mem (or a fork) adds confidence metadata, closing the mechanism
  gap while keeping the distribution gap.

## Sources

- github.com/thedotmack/claude-mem (fetched 2026-09-21): stars, forks,
  contributors, releases, cross-agent tagline, plugin dirs, cowork plugin,
  redaction commits.
- mem0.ai/blog/how-memory-works-in-codex-cli (May 8 2026, updated Jul 31
  2026): Codex two-layer memory, pipeline mechanics, limits, MCP tools.
- mem0.ai/blog (Aug 6 / Sep 3 / Sep 16 2026): Dream consolidation,
  staleness posts, Claude Code Auto Memory (v2.1.59) confirmation.
- particula.tech blog (Jun 2026): Zep Graphiti 63.8% vs mem0 49.0%
  LongMemEval.
- .team/lead/evidence-digest.md (2026-02-05): claim under attack, kev
  caveats, community-brain competitors (agentmemory, CCC, Gossip,
  Obsidian pattern), last30days scan.
- README.md (current): jevmory's own claims, quickstart, privacy, status.

Confidence notes: claude-mem facts are first-party (GitHub, today).
Codex Memories mechanics are second-party (mem0's detailed writeup citing
OpenAI docs; treat config specifics as directionally right). Auto Memory
details are second-party (mem0 blog + version changelog references).
Star counts and dates are as of today and will rot — which is, as noted,
the entire thesis.
