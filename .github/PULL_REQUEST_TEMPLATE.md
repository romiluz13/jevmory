## What

One concern per PR. What does this change do, in one or two sentences?

## Why

The problem being solved, or a plan reference (docs/PLAN.md section,
issue number).

## How

Key design points a reviewer should check, and where the new tests
live.

## Checks

- [ ] `python3 -m unittest discover` prints OK
- [ ] New behavior is pinned by a test that fails without this change
- [ ] No new runtime dependencies (stdlib only)
- [ ] No new egress path, or it is gated behind the per-project opt-in
      marker and covered by a test
- [ ] Hooks still always exit 0
