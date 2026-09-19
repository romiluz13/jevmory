"""JevClient tests — everything driven by an injected transport, no network.

Pins the transport contract: 401/422/transport failures fail fast with
no retries, 429/529 retry with the 1s/2s/4s backoff schedule plus
jitter, and the response envelope is strictly validated.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from email.message import Message

from jev_md.judgment.client import DEFAULT_ENDPOINT, JevClient
from jev_md.judgment.errors import (
    JevAuthError,
    JevProtocolError,
    JevRetryExhausted,
    JevTransportError,
    JevValidationError,
)
from jev_md.judgment.questions import phase_a_questions, phase_a_state


class RecordingTransport:
    """Scriptable transport: pops one response/error per call."""

    def __init__(self, script):
        # each item: dict {"response": obj} | {"raw": bytes} |
        # {"error": Exception}
        self.script = list(script)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, dict(headers), body, timeout))
        item = self.script.pop(0)
        if "error" in item:
            raise item["error"]
        if "raw" in item:
            return item["raw"]
        return json.dumps(item["response"]).encode()

    @property
    def called(self):
        return len(self.calls)


def http_error(code, detail="detail"):
    return urllib.error.HTTPError(
        DEFAULT_ENDPOINT, code, "reason", Message(), io.BytesIO(detail.encode())
    )


def envelope(answers, model="jev-fake", usage=None):
    return {
        "model": model,
        "answers": answers,
        "usage": usage or {"input_tokens": 120, "output_tokens": 30},
    }


def good_answer():
    return {"type": "noul", "noul": 0.9}


STATE = {"project_context": {"slug": "demo"}, "candidates": [
    {"id": "ev0001", "role": "user", "text": "Run `make lint`.",
     "context": "[assistant] ok"},
]}

# a single-question request: most tests below script one noul answer
ONE = {"c0_durable": phase_a_questions(1)["c0_durable"]}


class RequestShapeTest(unittest.TestCase):
    def test_wire_request_shape(self):
        transport = RecordingTransport([
            {"response": envelope({"c0_durable": good_answer()})}
        ])
        sleeps = []
        client = JevClient(
            "key-123", transport=transport, sleep=sleeps.append,
            jitter=lambda: 0.0, timeout=7.5,
        )
        response = client.ask(STATE, ONE)
        self.assertEqual(response.answers["c0_durable"].noul, 0.9)
        url, headers, body, timeout = transport.calls[0]
        self.assertEqual(url, DEFAULT_ENDPOINT)
        self.assertEqual(headers["Authorization"], "Bearer key-123")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(timeout, 7.5)
        payload = json.loads(body)
        self.assertEqual(
            sorted(payload), ["model", "questions", "state"]
        )
        self.assertEqual(sorted(payload["questions"]), ["c0_durable"])
        self.assertEqual(payload["state"], STATE)
        self.assertEqual(sleeps, [])


class EnvelopeTest(unittest.TestCase):
    def client(self, script):
        return JevClient(
            "k", transport=RecordingTransport(script),
            sleep=lambda s: None, jitter=lambda: 0.0,
        )

    def test_extra_answer_ids_ignored(self):
        answers = {"c0_durable": good_answer(), "c9_ghost": good_answer()}
        response = self.client(
            [{"response": envelope(answers)}]
        ).ask(STATE, ONE)
        self.assertEqual(sorted(response.answers), ["c0_durable"])

    def test_missing_answer_raises_protocol_error(self):
        with self.assertRaises(JevProtocolError):
            self.client([{"response": envelope({})}]).ask(STATE, ONE)

    def test_answer_type_mismatch_raises_protocol_error(self):
        raw = {"type": "choice", "choice": "a",
               "probabilities": {"a": 1.0}, "confidence": 0.5}
        with self.assertRaises(JevProtocolError):
            self.client(
                [{"response": envelope({"c0_durable": raw})}]
            ).ask(STATE, ONE)

    def test_malformed_json_raises_protocol_error(self):
        with self.assertRaises(JevProtocolError):
            self.client([{"raw": b"not-json"}]).ask(STATE, ONE)

    def test_missing_usage_raises_protocol_error(self):
        response = {"model": "m", "answers": {"c0_durable": good_answer()}}
        with self.assertRaises(JevProtocolError):
            self.client([{"response": response}]).ask(STATE, ONE)

    def test_non_integer_usage_rejected(self):
        usage = {"input_tokens": "120", "output_tokens": 30}
        with self.assertRaises(JevProtocolError):
            self.client(
                [{"response": envelope({"c0_durable": good_answer()}, usage=usage)}]
            ).ask(STATE, ONE)


class FailFastTest(unittest.TestCase):
    def test_401_fails_fast_without_retry(self):
        transport = RecordingTransport([{"error": http_error(401)}])
        sleeps = []
        client = JevClient(
            "k", transport=transport, sleep=sleeps.append, jitter=lambda: 0.0
        )
        with self.assertRaises(JevAuthError):
            client.ask(STATE, ONE)
        self.assertEqual(transport.called, 1)
        self.assertEqual(sleeps, [])

    def test_422_fails_fast_with_detail(self):
        transport = RecordingTransport([{"error": http_error(422)}])
        client = JevClient(
            "k", transport=transport, sleep=lambda s: None, jitter=lambda: 0.0
        )
        with self.assertRaises(JevValidationError) as ctx:
            client.ask(STATE, ONE)
        self.assertIn("detail", str(ctx.exception))
        self.assertEqual(transport.called, 1)

    def test_500_fails_fast_without_retry(self):
        transport = RecordingTransport([{"error": http_error(500)}])
        client = JevClient(
            "k", transport=transport, sleep=lambda s: None, jitter=lambda: 0.0
        )
        with self.assertRaises(JevTransportError):
            client.ask(STATE, ONE)
        self.assertEqual(transport.called, 1)

    def test_urlerror_fails_fast(self):
        transport = RecordingTransport([
            {"error": OSError("connection refused")}
        ])
        client = JevClient(
            "k", transport=transport, sleep=lambda s: None, jitter=lambda: 0.0
        )
        with self.assertRaises(JevTransportError):
            client.ask(STATE, ONE)
        self.assertEqual(transport.called, 1)


class RetryTest(unittest.TestCase):
    def ask_with(self, script):
        transport = RecordingTransport(script)
        sleeps = []
        client = JevClient(
            "k", transport=transport, sleep=sleeps.append, jitter=lambda: 0.0
        )
        return client, transport, sleeps

    def test_429_then_success_retries_once(self):
        client, transport, sleeps = self.ask_with([
            {"error": http_error(429)},
            {"response": envelope({"c0_durable": good_answer()})},
        ])
        response = client.ask(STATE, ONE)
        self.assertEqual(response.answers["c0_durable"].noul, 0.9)
        self.assertEqual(transport.called, 2)
        self.assertEqual(sleeps, [1.0])

    def test_429_529_then_success(self):
        client, transport, sleeps = self.ask_with([
            {"error": http_error(429)},
            {"error": http_error(529)},
            {"response": envelope({"c0_durable": good_answer()})},
        ])
        client.ask(STATE, ONE)
        self.assertEqual(transport.called, 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_retry_exhausted_after_three_retries(self):
        client, transport, sleeps = self.ask_with([
            {"error": http_error(429)},
            {"error": http_error(429)},
            {"error": http_error(529)},
        ])
        with self.assertRaises(JevRetryExhausted):
            client.ask(STATE, ONE)
        self.assertEqual(transport.called, 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_jitter_added_to_backoff(self):
        sleeps = []
        jittered = JevClient(
            "k", transport=RecordingTransport([
                {"error": http_error(429)},
                {"response": envelope({"c0_durable": good_answer()})},
            ]),
            sleep=sleeps.append, jitter=lambda: 0.5,
        )
        jittered.ask(STATE, ONE)
        self.assertEqual(sleeps, [1.5])


class ConstructionTest(unittest.TestCase):
    def test_empty_api_key_rejected(self):
        with self.assertRaises(ValueError):
            JevClient("", transport=lambda *a: None)

    def test_empty_questions_rejected(self):
        client = JevClient(
            "k", transport=lambda *a: None, sleep=lambda s: None,
            jitter=lambda: 0.0,
        )
        with self.assertRaises(ValueError):
            client.ask(STATE, {})


if __name__ == "__main__":
    unittest.main()
