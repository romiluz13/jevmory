# dream.md — domain model (ubiquitous language)

Single bounded context: **Memory Consolidation for coding agents**.
Everything below is one context; sub-domains are modules, not separate contexts.

## Ubiquitous language

| Term | Meaning |
|---|---|
| **Session** | One recorded coding-agent conversation (Claude Code or Codex transcript, JSONL on disk). |
| **Event** | One line of a session transcript, immutable, with a stable id (`sha256(file)+line`). |
| **Statement** | A verbatim text quote extracted from an event. Never rewritten, never generated. |
| **Candidate** | A Statement that might deserve to become a Fact. Deterministically extracted, then judged. |
| **Fact** | An active, graded Statement in the memory store: verbatim claim + category + significance + confidence + provenance (source event ids) + lifecycle status. |
| **Judgment** | One typed Jev answer (Noul / Choice / Score) about a state. Judgments are evidence, recorded in the run log. |
| **Confidence** | 0..1 certainty from Jev's probability distribution (Choice/Score) or the Noul value's distance from 0.5. |
| **Significance** | How much the Fact matters for future work in this project (Score levels trivial→critical). |
| **Receipt** | The provenance + confidence record attached to every Fact: "who said it, when, how sure are we." |
| **Dream** | A consolidation run: grade pending candidates, dedupe against existing Facts, resolve conflicts, retire stale Facts, rewrite `dream.md`. |
| **Verdict** | The decision for a Fact: `keep`, `supersede`, `retire`, `ask` (low confidence → surface to human). |
| **Audit** | Grading an *external* memory file (e.g. Claude Code's `MEMORY.md`) line by line: still true / stale / wrong / unsupported, with receipts. |
| **dream.md** | The published memory file at project root — the only artifact agents read. Facts grouped by category, sorted by significance × confidence, each with a receipt line. |
| **Hook** | The integration that triggers ingest when an agent session ends (Claude Code `SessionEnd`, Codex `notify`). |

## Invariants (the domain's laws)

1. **No generation.** dream.md never writes a sentence that wasn't said by a human or
   agent in a session. Jev judges; code selects and composes. (System One cannot
   generate text — see `docs/reference/typesafe-api.md` constraint 1.)
2. **Every Fact has a Receipt.** Verbatim claim + source event ids + confidence.
   A fact without provenance is not stored.
3. **Destructive verdicts need high confidence.** Retiring/superseding a Fact requires
   confidence ≥ 0.8; low-confidence cases get Verdict `ask` and are surfaced in a
   "questions for you" section of dream.md.
4. **Hooks never break sessions.** Ingest is async, time-boxed, offline-tolerant:
   if the Jev API is unreachable, candidates queue as ungraded Events and wait for
   the next Dream.
5. **Per-project isolation.** Each project directory gets its own SQLite store;
   Facts never cross projects.

## Sub-domain modules (one context, five modules)

- **ingestion** — session discovery, transcript format parsing (Claude/Codex), Event log, deterministic Statement/Candidate extraction.
- **judgment** — the Jev client: fan-out batching, budget math, retry/backoff, typed answers, fake client for tests.
- **memory** — the store: SQLite schema, FTS5 lookup, Fact lifecycle, dedupe/conflict bookkeeping.
- **dream** — the consolidation engine: the pipeline that composes judgments into verdicts and rewrites dream.md.
- **integration** — CLI + hooks (Claude Code `SessionEnd`, Codex `notify`) + `install` command.
