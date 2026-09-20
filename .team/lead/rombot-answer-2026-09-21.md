# RomBot community answer — Jev cross-session memory tools (2026-02-05)

Question asked: "Are there any persistent cross-session memory tools for coding agents built on TypeSafe Jev - tools that ingest Claude Code / Codex transcripts and accumulate durable project knowledge across sessions and agents (not in-session context compaction like fast-jev-compaction)? If any exist, which repos? If not, what do people use for cross-session agent memory today?"

Verbatim answer (citations inline, preserved):

---

Nothing in the community corpus on a cross-session memory tool built on TypeSafe Jev. Jev itself only launched 2-3 days ago — the community is still experimenting with it in-session (compaction, skill routing, typed decisions), not as a cross-session memory foundation.

Here's what people actually use for cross-session agent memory today, from the corpus:

**1. Claude Command Center (CCC) — The Real Amir**
Open-source. Ingests all Claude Code + Codex session transcripts. Full keyword search across all history. Sessions can "talk" to each other — one session can reference what another session discovered. Works as a layer on top of Claude Code rather than replacing it. Overlaps with Superset but more customizable. *(community-chunk:94853f8f, May 2026)*

**2. Gossip — Yogev**
Inter-agent messaging with `send` (message between sessions) and `observe` (read another session's real tool calls and output from its transcript, consuming zero tokens from the target, no context interruption). Lets you check if a session is stuck vs actually working. "Two sessions in the same repo, and the only way one learns what the other found is you, copying a paragraph between windows." *(community-chunk:54c936b1, Aug 2026)* Yogev described a "supervisor agent" pattern: a watcher observes all orchestrators and sessions, finds high-level problems, and resolves them. He builds everything using Gossip as the backbone — agents supervise other agents without manual session management. *(community-chunk:1b3155fb, Aug 2026)*

**3. CCC × Gossip × Switchboard Summit**
The three major inter-session tools are converging — joint webinar scheduled for Sept 27, 2026. Cross-session communication is emerging as its own infrastructure category. *(community-chunk:d0faf08d, Sept 2026)*

**4. Ofer Blutrich's Obsidian Vault System**
A Claude Code skill runs in the background daily, scanning git repos, WhatsApp (via MCP), calendar, and Gmail. Produces daily notes and project memory files in an Obsidian vault synced as a git repo across two machines. "Never loses state." The vault accumulates project knowledge: scoreboard, work board, content seeds, commit counts per project. *(community-chunk:d471db47, March 2026)*

**5. agentmemory — rohitg00**
Local memory layer via hooks/MCP/REST. Shares memory across Claude Code, Cursor, Codex. Hybrid search with KG-like structure. Local embeddings, no server. Has a viewer UI. The community verdict: good for personal coding agent memory but not production-multi-agent scale. *(community-chunk:ce7f6f62, May 2026)*

**6. Memongo — Rom Iluz**
MongoDB-backed memory framework with hybrid retrieval on a single collection and "Dreaming" consolidation (auto-distills repeated patterns from raw logs into structured facts). Runs local via MongoDB Atlas Local. Benchmarked at 14.29x cheaper than filesystem memory at scale. Not designed to replace coding agent memory specifically — targets runtime agent memory. *(community-chunk:662a85e7, June 2026)* github.com/romiluz13/Memongo

**7. Daniel's Simple State Files**
"The old simple way. When the machines finish a task they write a state file to a shared place. It's also good when context of agent is nearly full and you want a fresh conversation or /reset." Cross-machine state persistence via shared filesystem. *(community-chunk:4057b131, Sept 2026)*

**8. Instinct — Cross-Agent Scheduling**
Omer Itach (Sept 16, 2026): "From now on I can coordinate directly with other people's Instinct — close dates, share tasks, plan together, without ping-pong through you." Agents negotiate with each other directly. *(community-chunk:4057b131)*

**9. Context Lakes / Shared Brains — Emerging Enterprise Pattern**
Community meetup with Zohar Eini (Port CEO) on Context Lakes ("Smarter, Cheaper, Safer") and Tomer Greenwald (Cyera) on "A Shared Brain for a Fleet of Agents." *(community-chunk:bd68d13b, July 2026)*. The community daily summary from May 2026 captured the consensus: a good agent needs continuity — "a memory system that connects transcripts, client requirements, decisions, files, and insights... not just chunks without soul." *(community-chunk:a91a8aba)*

**10. Mem0 / Letta / Zep / Graphiti**
The established memory framework landscape. Mem0 is the biggest with published benchmark results. Nir's educational GitHub project covers all of them plus memory evaluation and benchmarks. *(community-chunk:4395e035, May 2026)*. But Rom Iluz noted Mem0 "is great, but not for coding agent memory" — it targets runtime agent memory for deployed agents, not developer coding agent workflows. *(community-chunk:9e487ef7)*

---

**Bottom line on Jev:** Jev is brand new (Sept 17-18, 2026 launch from TypeSafe AI at typesafe.ai). The community is using it for compaction, skill selection routing, CLI automation, and as a cheap/fast decision model. Alex Greenshpun flagged that it deletes selectively and can cause loops when the model has to redo tool calls to recover lost data. Nobody has built a cross-session memory tool on top of it yet — the model is 3 days old. If someone were to build one, Jev's "typed decisions" primitive (structured output instead of free text) would lend itself to extracting structured facts from agent transcripts — but nobody has done it yet. The gap between Jev-as-compactor and Jev-as-cross-session-memory-backbone is exactly where a new tool could emerge.
