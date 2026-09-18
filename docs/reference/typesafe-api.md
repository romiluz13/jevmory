# TypeSafe System One (Jev) — distilled API reference

> Ground truth for all Jev integration code. Retrieved 2026-09-19 from
> https://docs.typesafe.ai/api.md, /primitives.md, /confidence.md,
> /patterns/fan-out.md, /concepts/how-to-build-with-system-one.md.
> This is a distillation of exact request/response shapes; the source pages are authoritative.

## Endpoint

```
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer $TYPESAFE_API_KEY
Content-Type: application/json
```

## Request body

```json
{
  "state": "<string or structured JSON object>",
  "model": "jev-latest",
  "questions": {
    "<id>": { "type": "noul|choice|score", "instructions": "...", "criteria": "..." }
  }
}
```

- `state`: the content to evaluate. Plain string, or structured JSON (object/array) for
  chat logs, records, etc. Structure it; point questions at parts with backticked
  dot-and-index paths, e.g. ``Does `candidates[3].text` state a durable preference?``
- Question IDs are for your code only — not sent to the model. Write the complete
  question in `instructions`.
- All questions in one request see the same state and are evaluated **independently
  and in parallel**. Batching many questions adds ~no latency; each extra question
  costs only its own tokens.

## Question types and answer shapes

### Noul — yes/no, returns probability

Question: `{ "type": "noul", "instructions": "...", "criteria": { "true": "...", "false": "..." } }`
(criteria optional; `true`/`false` describe what yes/no mean.)

Answer: `{ "type": "noul", "noul": 0.92 }` — probability of yes. Near 1 strong yes,
near 0 strong no, near 0.5 uncertain. **Noul has no `confidence` field** — the noul
value itself is the signal (distance from 0.5 is the certainty).

### Choice — pick one option, returns distribution + confidence

Question: `{ "type": "choice", "instructions": "...", "criteria": { "<option>": "<rubric or null>", ... } }`

Answer: `{ "type": "choice", "choice": "technical", "probabilities": { "billing": 0.08, "technical": 0.85, "sales": 0.07 }, "confidence": 0.82 }`
- `probabilities` floats sum to 1, across every option you defined.
- `confidence` 0..1 derived from how peaked the distribution is.

### Score — position on ordered levels, returns weighted value + confidence

Question: `{ "type": "score", "instructions": "...", "criteria": ["level0", "level1", "level2"] }`
(at least 2 levels; use structured objects per level when guidance is rich.)

Answer: `{ "type": "score", "score": 1.6, "legend": { "0": "Calm", "1": "Frustrated", "2": "Very angry" }, "probabilities": { "0": 0.05, "1": 0.3, "2": 0.65 }, "confidence": 0.78 }`
- `score` is probability-weighted and can land between levels.

## Response envelope

```json
{
  "model": "jev-latest",
  "answers": { "<id>": { ...answer... } },
  "usage": { "input_tokens": 312, "output_tokens": 48 }
}
```

One answer per question, keyed by the same ids.

## Budget

The state and questions share a request budget of ~32,000 tokens
(~150,000 characters of English text). Chunk work into multiple requests when
a batch exceeds it.

## Confidence semantics

- `confidence` = statistic over `probabilities`. Flat distribution = uncertain.
- Three-range pattern: high → act automatically; medium → flag/confirm; low → don't act.
- Thresholds scale with risk of the action. For memory writes (reversible, low-stakes)
  moderate thresholds are fine; for *deleting/retiring* a fact (destructive) require
  higher confidence.

## Errors

| Status | Meaning | Handling |
|---|---|---|
| 401 | Missing/invalid API key | fail fast, clear message |
| 422 | Body validation failed | fail fast, surface field detail |
| 429 | Rate limited | retry with exponential backoff |
| 529 | Overloaded | retry with exponential backoff |

Official SDKs do backoff automatically; we use stdlib HTTP so we implement it
(3 attempts, 1s/2s/4s + jitter, then give up gracefully — a memory hook must
never crash a session).

## Critical design constraints (from the official docs)

1. **System One does not generate text.** It never writes prose/code. It only
   returns typed judgments. Consequence: dream.md cannot ask Jev to "summarize a
   session". Facts must be **verbatim quotes** from transcripts, selected and
   graded by judgments. Provenance is exact by construction.
2. Ask **narrow, atomic questions** — one judgment each. Composite judgments are
   split into multiple questions combined with weights in code.
3. **Speculative fan-out**: include questions that only matter for some inputs;
   ignore unneeded answers in code. No cost to asking.
4. Two requests only when the second's state depends on the first's answer.
5. Answers are stable across repeated evaluations (self-consistency); probabilities
   are calibrated (RLCD), not overconfident — this is why confidence-gating memory
   is sound.
