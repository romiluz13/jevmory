"""Jev HTTP client — stdlib only (docs/reference/typesafe-api.md).

``POST {endpoint}`` with ``Authorization: Bearer`` and a JSON body
``{"state", "model", "questions"}``; the response envelope carries
typed ``answers`` keyed by question id plus ``usage``.

Error handling, verbatim from the reference:

- 401 → fail fast (``JevAuthError``), no retry.
- 422 → fail fast surfacing field detail (``JevValidationError``).
- 429/529 → retry on the backoff schedule + jitter, then give up
  gracefully (``JevRetryExhausted``) — a memory hook must never crash
  a session, so exhaustion is a typed error the caller turns into
  "leave candidates queued", never an unhandled exception.
- anything else (other HTTP codes, URLError, timeout) → fail fast
  (``JevTransportError``).

Transport, sleep, and jitter are injectable: every behavior above is
unit-tested offline with a fake transport, a sleep collector, and a
zero/constant jitter — no network, no API key, no real waiting.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from dream_md.judgment.answers import parse_answer
from dream_md.judgment.errors import (
    JevAuthError,
    JevProtocolError,
    JevRetryExhausted,
    JevTransportError,
    JevValidationError,
)
from dream_md.judgment.questions import Question
from dream_md.judgment.tokens import questions_wire
from dream_md.thresholds import (
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    RETRY_JITTER_MAX,
    RETRY_MAX_ATTEMPTS,
)

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

# (url, headers, body, timeout) -> response bytes; may raise
# urllib.error.HTTPError / URLError exactly like the real transport.
Transport = Callable[[str, dict[str, str], bytes, float], bytes]


@dataclass(frozen=True)
class Usage:
    """Token usage from the response envelope (runs.stats receipts)."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class JevResponse:
    """Parsed envelope: typed answers keyed by question id + usage."""

    answers: dict[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    model: str = DEFAULT_MODEL


def _urllib_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> bytes:
    """The default stdlib POST transport."""
    request = urllib.request.Request(
        url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _http_detail(error: urllib.error.HTTPError) -> str:
    """Best-effort body detail for fail-fast errors (truncated)."""
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:  # pragma: no cover - defensive
        body = ""
    detail = " ".join(body.split())
    if len(detail) > 300:
        detail = detail[:300] + "..."
    return detail or str(error.reason)


class JevClient:
    """Thin, fully-injectable Jev API client."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be non-empty")
        self._api_key = api_key
        self._endpoint = endpoint
        self._model = model
        self._timeout = timeout
        self._transport = transport or _urllib_transport
        self._sleep = sleep
        self._jitter = jitter or (lambda: random.uniform(0.0, RETRY_JITTER_MAX))

    def ask(
        self, state: Any, questions: Mapping[str, Question]
    ) -> JevResponse:
        """Ask every question against one shared state; one response."""
        if not questions:
            raise ValueError("at least one question is required")
        body = {
            "state": state,
            "model": self._model,
            "questions": questions_wire(questions),
        }
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_retry_error: Exception | None = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                raw = self._transport(
                    self._endpoint, headers, payload, self._timeout
                )
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    raise JevAuthError(
                        f"Jev API rejected the key (401): {_http_detail(error)}"
                    ) from error
                if error.code == 422:
                    raise JevValidationError(
                        f"Jev API rejected the request body (422): "
                        f"{_http_detail(error)}"
                    ) from error
                if error.code in (429, 529):
                    last_retry_error = error
                    if attempt >= RETRY_MAX_ATTEMPTS:
                        break  # give up gracefully below
                    delay = RETRY_BACKOFF_SECONDS[attempt - 1] + self._jitter()
                    self._sleep(delay)
                    continue
                raise JevTransportError(
                    f"Jev API HTTP {error.code}: {_http_detail(error)}"
                ) from error
            except (urllib.error.URLError, OSError) as error:
                raise JevTransportError(
                    f"Jev API unreachable: {error}"
                ) from error
            return self._parse_response(raw, questions)

        raise JevRetryExhausted(
            f"Jev API still rate-limited/overloaded after "
            f"{RETRY_MAX_ATTEMPTS} attempts (429/529); giving up gracefully",
            last_error=last_retry_error,
        )

    def _parse_response(
        self, raw: bytes, questions: Mapping[str, Question]
    ) -> JevResponse:
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise JevProtocolError(f"response is not JSON: {error}") from error
        if not isinstance(envelope, Mapping):
            raise JevProtocolError(
                f"response envelope is not an object: {envelope!r}"
            )

        answers_raw = envelope.get("answers")
        if not isinstance(answers_raw, Mapping):
            raise JevProtocolError("envelope missing 'answers' object")

        parsed: dict[str, Any] = {}
        for qid, question in questions.items():
            if qid not in answers_raw:
                raise JevProtocolError(f"no answer for question {qid!r}")
            parsed[qid] = parse_answer(question.type, answers_raw[qid])

        usage = self._parse_usage(envelope.get("usage"))
        model = envelope.get("model", DEFAULT_MODEL)
        if not isinstance(model, str) or not model:
            raise JevProtocolError(f"invalid model echo: {model!r}")
        return JevResponse(answers=parsed, usage=usage, model=model)

    @staticmethod
    def _parse_usage(raw: Any) -> Usage:
        if not isinstance(raw, Mapping):
            raise JevProtocolError("envelope missing 'usage' object")
        input_tokens = raw.get("input_tokens")
        output_tokens = raw.get("output_tokens")
        for name, value in (
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise JevProtocolError(
                    f"usage.{name} is not an integer: {value!r}"
                )
        return Usage(input_tokens=input_tokens, output_tokens=output_tokens)
