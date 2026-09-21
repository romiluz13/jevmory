"""Deterministic redaction at ingest (DOMAIN invariant #6: redaction at rest).

Applied to every statement BEFORE it is stored (``EventLog.append``) and
to captured context (context is built from already-redacted events;
``redact`` is idempotent, so double application is safe). Nothing
unredacted can ever leave the machine (privacy statement: only redacted
candidate quotes + minimal project context leave, and only during an
opted-in distill/audit).

Patterns (PLAN v2 decision 5), applied in order — most specific first:

======================  ==================================  ======================
shape                   example                             replacement
======================  ==================================  ======================
PEM key block           ``-----BEGIN ... PRIVATE KEY-----``  ``[redacted:private-key]``
Bearer token            ``Bearer eyJ...``                   ``[redacted:bearer]``
service key             ``sk-...`` (16+ body)               ``[redacted:secret-key]``
AWS access key id       ``AKIA``/``ASIA`` + 16 [0-9A-Z]    ``[redacted:aws-key]``
GitHub token            ``gh[pousr]_`` + 36 [A-Za-z0-9]    ``[redacted:github-token]``
JWT                     ``eyJ a.b.c``                       ``[redacted:jwt]``
credential assignment   ``password= / token: / api_key=``  ``<name>=[redacted:credential]``
hex run >= 20           sha1/sha256/md5-like               ``[redacted:token]``
base64-ish run >= 20    with at least one digit             ``[redacted:token]``
======================  ==================================  ======================

Stances, deliberate and corpus-test-pinned:

- Over-redaction is the safe side and accepted: bare 40-hex commit
  hashes are redacted too. Deterministic beats clever at rest.
- The base64-ish rule requires a digit: pure-alpha 20+ runs are English
  words or identifiers, not secrets.
- Unknown shapes are NOT redacted — no false confidence; the redaction
  corpus test (``tests/test_redact.py``) pins every behavior above.
- ``redact(redact(x)) == redact(x)`` (idempotent): replacements never
  re-match any rule.
"""

from __future__ import annotations

import re

REDACTED_PREFIX = "[redacted:"

# --- rules, most specific first ---------------------------------------------

_PEM_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
)

_BEARER_RE = re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]+=*")

_SERVICE_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")

_AWS_KEY_RE = re.compile(r"\bA(?:KIA|SIA)[0-9A-Z]{16}\b")

_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")

_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api_?key|secret|auth)\b[\"']?\s*[:=]\s*"
    r"(?:\"(?!\[redacted:)[^\"\n]{8,}\""
    r"|'(?!\[redacted:)[^'\n]{8,}'"
    r"|(?!\[redacted:)[^\s\"'&\[\]]{8,})"
)

# 20+ hex chars in a row are never innocent prose (also covers sha1/sha256).
_HEX_RUN_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{20,}(?![0-9A-Fa-f])")

# 20+ base64-ish chars containing at least one digit (pure-alpha runs are words).
_BASE64ISH_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{20,}={0,2}(?![A-Za-z0-9+/=])"
)


def _credential_assignment_repl(match: re.Match[str]) -> str:
    # Keep the credential's name, redact only its value.
    return f"{match.group(1)}=[redacted:credential]"


def _base64ish_repl(match: re.Match[str]) -> str:
    text = match.group(0)
    if re.search(r"\d", text):  # digit guard: pure-alpha runs are words
        return "[redacted:token]"
    return text


_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PEM_KEY_RE, "[redacted:private-key]"),
    (_BEARER_RE, "[redacted:bearer]"),
    (_SERVICE_KEY_RE, "[redacted:secret-key]"),
    (_AWS_KEY_RE, "[redacted:aws-key]"),
    (_GITHUB_TOKEN_RE, "[redacted:github-token]"),
    (_JWT_RE, "[redacted:jwt]"),
)


def _apply(text: str) -> tuple[str, int]:
    """Sequentially apply every rule; returns (redacted text, replacements done).

    Rules run in order on the evolving text (a JWT consumed by the
    bearer rule is not counted again by the JWT rule).
    """
    count = 0
    for pattern, replacement in _RULES:
        text, n = pattern.subn(replacement, text)
        count += n
    text, n = _CREDENTIAL_ASSIGNMENT_RE.subn(_credential_assignment_repl, text)
    count += n
    text, n = _HEX_RUN_RE.subn("[redacted:token]", text)
    count += n
    # Count only real base64-ish replacements (pure-alpha matches pass through).
    hits = [m for m in _BASE64ISH_RUN_RE.finditer(text) if re.search(r"\d", m.group(0))]
    if hits:
        text = _BASE64ISH_RUN_RE.sub(_base64ish_repl, text)
    count += len(hits)
    return text, count


def redact(text: str) -> str:
    """Deterministically scrub secrets from ``text`` (never raises)."""
    if not text:
        return text
    return _apply(text)[0]


def redactions_in(text: str) -> int:
    """How many replacements ``redact`` would apply to ``text`` (reporting aid)."""
    if not text:
        return 0
    return _apply(text)[1]
