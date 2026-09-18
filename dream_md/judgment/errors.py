"""Error hierarchy for the Jev judgment transport (M2).

One base class so callers can catch anything Jev-related with a single
``except JevError``. Fail-fast errors (auth, validation, protocol) are
distinct from retry-exhausted: the caller (dream engine, hooks) treats
``JevRetryExhausted`` as "leave the candidates queued and report",
never as a crash (API reference: a memory hook must never crash a
session).
"""

from __future__ import annotations


class JevError(Exception):
    """Base class for every Jev transport/parsing failure."""


class JevAuthError(JevError):
    """HTTP 401: missing or invalid API key. Fail fast, no retry."""


class JevValidationError(JevError):
    """HTTP 422: request body rejected. Fail fast, surface field detail."""


class JevRetryExhausted(JevError):
    """HTTP 429/529 persisted past the retry schedule. Give up gracefully.

    Candidates stay queued; ``status`` reports the failure. Never crash
    a session over grading (working agreement).
    """

    def __init__(self, message: str, last_error: Exception | None = None):
        super().__init__(message)
        self.last_error = last_error


class JevTransportError(JevError):
    """Network-level failure (URLError, timeout) or unexpected HTTP status.

    Only 429/529 are retried per the API reference; everything else
    fails fast through here.
    """


class JevProtocolError(JevError):
    """Response envelope or answer shape violates the documented API.

    Missing answers, type mismatches, out-of-range values, malformed
    JSON. The stored receipts contract requires strict parsing: never
    guess what an off-spec answer meant.
    """
