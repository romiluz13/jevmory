# dream-md — team mission

## Outcome

Ship a viral-ready, local, zero-dependency developer tool: **dream-md** — coding-agent
memory where every fact is a verbatim quote graded by TypeSafe Jev's calibrated
confidence. Target: jev.directory submission-quality demo by morning.

## Finish condition (pass/fail — user asleep, autonomy granted)

PASS requires ALL of:
1. `python3 -m unittest discover` green with no network and no API key.
2. Live smoke: real Jev API call via `TYPESAFE_API_KEY` returns typed answers (3+ candidates graded).
3. Real `dream.md` file generated from real transcripts on this machine, containing graded verbatim facts with receipts.
4. `dream-md audit` produces a graded report on a real or realistic MEMORY.md.
5. README with pitch, quickstart, privacy note, demo section. MIT LICENSE.
6. All work committed to git on `main` with clean history.

NOT required (human-reserved): pushing to GitHub, jev.directory submission, tweeting.
The repo stays local; the user flips it public in the morning.

## Roster

- **Lead** (w1S:p3): decisions, plan, verification, packaging. Writes docs/, decisions.
- **GLM** (w1S:p4): implementation. Owns all code under `dream_md/` + `tests/` + `scripts/`.
- **Kimi** (w1S:p5): devil's advocate + code review. Read-only on code; writes `.team/kimi/` notes.

## Working agreement

- Method: DDD (`.ddd/notes/dream-md.md` is the single task note; documentation basis must stay updated).
- User is asleep; full autonomy for reversible work inside this repo. No pushes, no publishing, no installs outside the repo, no API spend beyond smoke tests.
- GLM: after each milestone, run tests, update the task note's progress log, report to lead with claims + checks. Escape hatch: after 3 failed attempts at anything, stop and write up why.
- Kimi: attack the plan and each milestone's diff. Findings → `.team/kimi/findings-<topic>.md` + summary reply. A clean review gate (≤3 iterations per milestone) before lead marks a milestone verified.
- Reviews are gates, not verdicts: lead resolves each finding by fixing or rebutting with stated grounds.
- One writer per artifact: GLM owns code; lead owns docs/ + .ddd/; Kimi owns .team/kimi/.

## Authority decisions pre-answered (lead, so work never blocks)

- Python 3 stdlib only; no third-party deps, no virtualenv needed.
- Name: `dream-md` (package/CLI), output file `dream.md`.
- MIT license. Version 0.1.0.
- Per-project SQLite store under `~/.dream-md/`.
- No LLM generation anywhere in the core pipeline (domain invariant).
- Privacy: README must state transcripts are sent to the Jev API for grading; add `--offline` mode that only queues events.
- Commit style: short imperative subject, `Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>` trailer (per harness rules), committed by GLM or lead in-repo.
