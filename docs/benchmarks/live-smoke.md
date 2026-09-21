# Live smoke and fidelity benchmark

The network paths are pinned by budget-capped scripts. This page is the
full detail; the README links here.

## Live smoke

```sh
TYPESAFE_API_KEY=… python3 scripts/live_smoke.py --probe   # 3 synthetic candidates, 1 request
TYPESAFE_API_KEY=… python3 scripts/live_smoke.py --distill   # 1 real distill run, capped
```

The smoke is hard-capped on requests (`--cap`, default 40, PLAN's smoke
ceiling); `BudgetExhausted` is a `JevError`, so the run row closes and
events stay queued — the smoke cannot overspend. Live-verified against
the real API: probe (auth, envelope, strict parse, usage accounting), a
capped distill over real transcripts, and a live audit that caught a
planted wrong line with receipts in the store's `runs`/`judgments`
tables.

## Fidelity benchmark

```sh
python3 scripts/bench_fidelity.py                           # offline: anchor floor + self-check
TYPESAFE_API_KEY=… python3 scripts/bench_fidelity.py --live  # real model confusion matrix
```

The fidelity benchmark runs a planted-truth fixture (3 verbatim truths,
a stale line quoting a superseded fact, a one-word mutation, an
unsupported claim): offline it proves the deterministic anchor stage —
3/3 verbatim recall, zero false anchors — and self-checks the full
pipeline against an ideal scripted model; `--live` measures the real
model's end-to-end confusion matrix on the same fixture. Results:
[fidelity.md](fidelity.md).
