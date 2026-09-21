# Privacy — what leaves, what never does

jevmory is local by architecture. This page is the full detail behind
the three-line summary in the README.

## What leaves

Redacted candidate quotes (≤600 chars) + verbatim context (≤800 chars) +
minimal project context — only during `distill`/`audit` with
`$TYPESAFE_API_KEY` set **and** the project opted in
(`jevmory init --enable-grading`).

With `--backend kev` nothing leaves the machine at all: grading goes to
a local wire-compatible server (`$JEVMORY_KEV_ENDPOINT`, default
`127.0.0.1:8009`). The opt-in marker is still required — grading is
grading, wherever the model runs.

## Never

- Whole transcripts, or transcript metadata (the project context is
  just the project name — no file paths).
- Secrets, redacted at rest before any storage: `sk-*`, AWS keys,
  GitHub tokens, JWTs, PEM blocks, `password=`/`token=`/`api_key=`
  assignments, high-entropy hex/base64, bearer tokens →
  `[redacted:<kind>]`.

## Honest caveat

Quotes are verbatim conversation text. Anything non-secret you typed in
chat — a file path like `/Users/you/proj/main.py`, an internal hostname,
a person's name — stays inside the quote that leaves. The redactor
scrubs secrets, not paths; if that matters for your project, stay in
local mode.

## Fully local mode

`--offline` / no key / no opt-in — events queue, nothing leaves. No
marker → candidates queue; `jevmory status` says so. The recall hook and
the MCP server are read-only and local by construction: they cannot
grade or egress anything.
