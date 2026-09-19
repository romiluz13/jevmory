# Contributing to jev-md

Thanks for your interest. jev-md is deliberately small: a local,
zero-dependency CLI that turns coding-agent transcripts into
receipts-backed memory. Keep it that way.

## Setup

There is none. The project runs on the Python 3 standard library alone
(3.10+); no venv, no requirements file, no pins. Check out and run the
suite:

```sh
python3 -m unittest discover
```

If that prints `OK`, you have a working development environment.

## Ground rules

1. **Stdlib only, forever.** No runtime dependencies — not even "small"
   ones. This is a mission invariant, enforced by
   `tests/test_packaging.py`: it parses every module under `jev_md/`
   with `ast` and fails on any non-stdlib import, and it pins that
   pyproject declares no dependencies. If a change seems to need a
   library, the change is wrong.
2. **No LLM generation.** Jev judges; code selects and composes. Facts
   are verbatim quotes with receipts, never model-written prose.
3. **Privacy by architecture.** Hooks ingest transcripts locally;
   anything that leaves the machine must be a redacted quote, sent only
   after the per-project opt-in (`jev-md init --enable-grading`). Every
   new egress path needs a test proving it stays behind that gate.
4. **Hooks never break sessions and never die silently.** Ingest always
   exits 0; failures surface in `jev-md status`, never in the agent's
   face.

## Tests

- The whole suite is offline: no network, no API key. `FakeJev`
  simulates the judgment API, including adversarial modes.
- While iterating, run the narrowest relevant module, e.g.
  `python3 -m unittest tests.test_redact -v`.
- Before proposing a change, run the full suite:
  `python3 -m unittest discover`.

## Pull requests

- One concern per PR: a fix, a feature, or a refactor — not several.
- Suite green, and new behavior pinned by a test that fails without
  the change.
- No new runtime dependencies (see rule 1; the packaging test and CI
  reject them anyway).
- Agent-authored contributions follow the same bar. Whether a human or
  an AI wrote the code makes no difference to tests, receipts, and
  review.

## Finding your way around

- `docs/PLAN.md` — the product/engineering plan (what and why)
- `docs/DOMAIN.md` — domain invariants (the non-negotiables)
- `jev_md/` — the package: `ingestion/` (parsers, redaction, scan),
  `judgment/` (Jev client, FakeJev), `memory/` (SQLite store, facts,
  receipts), `audit/` (memory-file grading, reports), `dream/`
  (grading engine, `jev.md` writer), plus `cli.py` and `hook.py`
- `.ddd/notes/` — the running decision log
- `demo/` — the offline planted-error demo; `scripts/live_smoke.py` —
  the budget-capped live-API smoke
