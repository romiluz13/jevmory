# Fidelity benchmark — v0.2 baseline

`scripts/bench_fidelity.py` measures the audit pipeline against a
planted-truth fixture (3 verbatim truths, a stale line quoting a
superseded fact, a one-word mutation of an active fact, an unsupported
claim). Numbers below are from the offline run (`python3
scripts/bench_fidelity.py`) on v0.2.0; regenerate with `--json` for
machine-readable output.

## Stage 1 — deterministic anchors (the guaranteed floor, no model)

| metric | value |
|---|---|
| verbatim recall | 3/3 (100%) |
| false anchors | 0/3 (0%) |
| routed to model | 3 |

Zero API spend for everything the store can prove by string equality;
planted errors never anchor (a superseded fact is not active, a
one-word mutation is not equality, an unstored claim has nothing to
match). This floor is CI-able and deterministic.

## Full pipeline — harness self-check (ideal scripted model)

The same fixture through the complete engine with a model that answers
every line with its planted disposition: the confusion matrix is
perfectly diagonal (3 VERIFIED, 1 STALE, 1 WRONG, 1 UNSUPPORTED;
accuracy 100%, 1 api call, 360 tokens). This proves the measurement
path (engine → rules → report), not any real model.

## Full pipeline — live model (measurement, not a gate)

Pending: run `scripts/bench_fidelity.py --live` (or `--live --backend
kev`) and record the confusion matrix here. Positioning rec 5: honest
benchmarks are the category's currency — record the numbers you get,
not the numbers you want.
