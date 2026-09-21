# jevmory.md — domain model (ubiquitous language)

Single bounded context: **Memory Consolidation for coding agents**.
Everything below is one context; sub-domains are modules, not separate contexts.

## Ubiquitous language

| Term | Meaning |
|---|---|
| **Session** | One recorded coding-agent conversation (Claude Code or Codex transcript, JSONL on disk). |
| **Event** | One line of a session transcript, immutable, with a stable id (`sha256(file)+line`). |
| **Statement** | A verbatim text quote extracted from an event. Never rewritten, never generated. "Verbatim" means modulo outer whitespace (leading/trailing trim); the interior is byte-exact. |
| **Candidate** | A Statement that might deserve to become a Fact. Deterministically extracted, then judged. |
| **Fact** | An active, graded Statement in the memory store: verbatim claim + verbatim context (surrounding exchanges) + category + significance + confidence + provenance (source event ids) + lifecycle status. |
| **Judgment** | One typed Jev answer (Noul / Choice / Score) about a state. Judgments are evidence, stored verbatim in the judgments table — every number in jevmory.md is reproducible from stored receipts. |
| **Confidence** | A Fact's confidence is exactly `clamp01(2·|durable_noul − 0.5|)` — the certainty of durability. Choice/Score confidences drive their own rules and are never blended in. One formula, in `thresholds.py`. |
| **Significance** | How much the Fact matters for future work in this project (Score levels trivial→critical). |
| **Receipt** | The provenance + confidence record attached to every Fact: "who said it, when, how sure are we." |
| **Distill** | A consolidation run: grade pending candidates, dedupe against existing Facts, resolve conflicts, retire stale Facts, rewrite `jevmory.md`. |
| **Verdict** | The decision for a Fact: `keep`, `supersede`, `retire`, `ask` (low confidence → surface to human). |
| **Audit** | Grading an *external* memory file (e.g. Claude Code's `MEMORY.md`) line by line: still true / stale / wrong / unsupported, with receipts. |
| **jevmory.md** | The published memory file at project root — the only artifact agents read. Facts grouped by category, sorted by significance × confidence, each with a receipt line. |
| **Hook** | The integration that triggers ingest when an agent session ends (Claude Code `SessionEnd`, Codex `notify`) — and recall when a session starts (Claude Code `SessionStart`): the project's top active Facts printed as context, verbatim, confidence-ordered. |
| **Recall** | Read-only retrieval of what the store remembers: the SessionStart context injection, the `jevmory recall` rendering, and the MCP `recall` tool. Recall never grades, never writes, never leaves the machine. |
| **MCP server** | The agent-agnostic surface (`jevmory mcp`, stdio JSON-RPC): three read-only tools — `status`, `recall`, `fact` — so any MCP client (Claude Code, Codex CLI, Cursor) can query the store without agent-specific wiring. |

## Invariants (the domain's laws)

1. **No generation.** jevmory.md never writes a sentence that wasn't said by a human or
   agent in a session. Jev judges; code selects and composes. (System One cannot
   generate text — see `docs/reference/typesafe-api.md` constraint 1.)
2. **Every Fact has a Receipt.** Verbatim claim + source event ids + confidence.
   A fact without provenance is not stored.
3. **Destructive verdicts need high confidence.** Retiring/superseding a Fact requires
   confidence ≥ 0.8; low-confidence cases get Verdict `ask` and are surfaced in a
   "questions for you" section of jevmory.md.
4. **Hooks never break sessions, never grade, never die silently.** Ingest is
   synchronous, local-only, wrapped so it always exits 0; failures log where
   `status` shows them. Grading is never in the hook path.
5. **Per-project isolation.** Each project directory gets its own SQLite store;
   Facts never cross projects.
6. **Redaction at rest.** Secrets are scrubbed deterministically at ingest, before
   storage — the local store never holds raw secrets, and nothing unredacted can
   ever leave the machine.
7. **Grading is opt-in per project.** A marker file created by `jevmory init
   --enable-grading` is the only trigger for anything that calls the Jev API.
   No marker → events queue locally, forever if need be.
8. **No decay in v1.** Absence of mention is not evidence of staleness: confidence
   never decays, staleness is a visual badge, and only contradiction evidence
   changes a Fact's status.

## Sub-domain modules (one context, five modules)

- **ingestion** — session discovery, transcript format parsing (Claude/Codex), Event log, deterministic Statement/Candidate extraction.
- **judgment** — the Jev client: fan-out batching, budget math, retry/backoff, typed answers, fake client for tests.
- **memory** — the store: SQLite schema, FTS5 lookup, Fact lifecycle, dedupe/conflict bookkeeping.
- **distill** — the consolidation engine: the pipeline that composes judgments into verdicts and rewrites jevmory.md.
- **integration** — CLI + hooks (Claude Code `SessionEnd` ingest, `SessionStart` recall; Codex `notify`) + `install` command + the MCP server + the Claude Code plugin packaging (the repo is both marketplace and plugin; the vendored package runs off `${CLAUDE_PLUGIN_ROOT}`, no pip install).
