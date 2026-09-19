# Security Policy

## Reporting a vulnerability

Please do not open public issues for security problems. Use GitHub's
private security advisory:

1. Open <https://github.com/romiluz13/jev-md/security/advisories/new>
2. Submit a description, reproduction steps, and impact.

Expect a response within a few days. If the advisory system is
unreachable, contact the maintainer via their GitHub profile
(@romiluz13).

## Privacy model (read before reporting data-leak bugs)

- Session transcripts never leave your machine. Hooks ingest locally
  into a per-project SQLite store under `~/.jev-md/`.
- Grading (`dream`, `audit` with a key set) sends redacted candidate
  quotes (≤600 chars), verbatim context (≤800 chars), and the project
  name — nothing else — and only when the project opted in
  (`jev-md init --enable-grading`) and `$TYPESAFE_API_KEY` is set.
- Redaction is deterministic and pattern-based (API keys, tokens, PEM
  blocks, credential assignments, high-entropy strings). It is
  best-effort, not a guarantee: non-secret content that merely looks
  sensitive (file paths, hostnames, people's names) is not scrubbed.
  A redaction pattern that misses a real class of secrets is a
  vulnerability — report it.

## Scope

In scope: everything under `jev_md/`, the hook entry points, and this
repository's scripts. The Jev API service itself is out of scope here;
report service issues to TypeSafe.
