# dream.md — DDD task note

Outcome: viral-ready local tool `dream-md` — Jev-graded, verbatim-provenance memory for coding agents.
Method: DDD. Plan: `docs/PLAN.md`. Domain: `docs/DOMAIN.md`. Team: `.team/MISSION.md`.
Status: plan approved by lead; implementation delegated to GLM (M0→M7), review by Kimi.

## Documentation basis

| Question | Source and applicable version/section | Rule and implementation decision | Check/result or open question |
|---|---|---|---|
| Jev API request/response contract | docs.typesafe.ai/api.md (fetched 2026-09-19; distilled at `docs/reference/typesafe-api.md`) | POST /v1/systemone, Bearer auth, model `jev-latest`, questions map with typed answers under same ids | pending live smoke test |
| Question types + answer shapes | docs.typesafe.ai/api.md + /primitives.md | Noul→probability (no confidence); Choice→choice+probabilities+confidence; Score→score+legend+probabilities+confidence | pending |
| Can Jev generate/summarize text? | docs.typesafe.ai/concepts/how-to-build-with-system-one.md — "does not generate code or choose its own next action" | **No generation in dream.md.** Facts = verbatim quotes, selected+graded by judgments. Invariant #1 | shapes design; pending code |
| Batching/cost | docs.typesafe.ai/patterns/fan-out.md + /primitives.md (parallel questions section) | Speculative fan-out: all questions in one request, parallel eval, ~no latency cost; ~32k token budget shared state+questions (~150k chars) | batcher needs budget estimator + tests |
| Confidence semantics + gating | docs.typesafe.ai/confidence.md | Three ranges (act/caution/don't); thresholds scale with risk; destructive ops gated higher | thresholds in `thresholds.py`, Kimi to attack values |
| Retry policy | docs.typesafe.ai/api.md errors section | 401/422 fail fast; 429/529 exponential backoff; hook must never block a session | pending |
| Claude Code transcript format | LOCAL ground truth: `~/.claude/projects/**/*.jsonl` | parser built from observed real files, not docs | **pending — GLM must inspect real files before finalizing parser** |
| Codex transcript format | LOCAL ground truth: `~/.codex/sessions/**/*.jsonl` | same | **pending — GLM must inspect** |
| Claude Code hook surface | Claude Code hooks docs (SessionEnd, stdin JSON with session_id/transcript_path) — verify against local `~/.claude/settings.json` examples | `install` emits hook JSON, never auto-patches without `--yes` | pending — verify real hook payload shape |
| Codex notify surface | Codex config.toml notify (session/turn events) | emit TOML snippet | pending |
| Competitor gap | GitHub/web search 2026-09-17/18 (lead session): Claude Code built-in auto-memory (Feb 2026, no confidence/provenance), agentmemory (5k★, local, no consolidation), eidetic/ai-memory/msync (none use Jev, none calibrated) | differentiation is receipts + calibration, not storage | one more fresh search before launch |

## Progress log

- 2026-09-19 00:56 — repo created, plan + domain + API reference written, team dispatched (GLM: M0+M1 first; Kimi: plan attack). Implementation/checks pending.
