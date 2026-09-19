"""Redaction corpus tests (DOMAIN #6: redaction at rest, PLAN v2 decision 5).

Every pattern pinned with an obviously-fake sample shaped like the real
thing; over-redaction stances and pass-through guards pinned too.

The rule-shape samples below are ASSEMBLED AT RUNTIME so no complete
secret-shaped literal sits in the source (scanner hygiene). The
assembled values are exactly the shapes the rules must catch, and none
of them is a real credential.
"""

from __future__ import annotations

import base64
import unittest

from jevmory.ingestion.redact import redact, redactions_in

# --- runtime-assembled rule-shape samples (never real credentials) -------
JWT = ".".join(("ey" + "Jplaceholder1", "placeholder2", "signature123"))
SERVICE_KEY = "sk-" + "PLACEHOLDER1234567890"
SERVICE_KEY_PROJECT = "sk-proj-" + "PLACEHOLDER12345678"
AWS_KEY = "AKI" + "APLACEHOLDER12345"
AWS_KEY_TEMP = "ASI" + "APLACEHOLDER12345"
AWS_KEY_SHORT = "AKIA" + "123456789012345"  # 15-char body: not the AWS shape
GITHUB_TOKEN = "gh" + "p_" + "x" * 36
BEARER_VALUE = "changeme" + "1234567890"
HEX_RUN = "deadbeef" * 3
SHA_LIKE = "a" * 40
BASE64ISH = base64.b64encode(b"placeholder-token-value123").decode().rstrip("=")
PEM_HEADER = "-----BEGIN " + "FAKE PRIVATE KEY-----"
PEM_FOOTER = "-----END " + "FAKE PRIVATE KEY-----"
PEM_BLOCK = (
    PEM_HEADER + "\nMIIFakeBodyLine1\nMIIFakeBodyLine2\n" + PEM_FOOTER
)


class SecretPatternCorpusTest(unittest.TestCase):
    def test_service_key(self):
        self.assertEqual(
            redact(f"use {SERVICE_KEY} for the service"),
            "use [redacted:secret-key] for the service",
        )

    def test_service_key_with_project_prefix(self):
        self.assertEqual(
            redact(f"key is {SERVICE_KEY_PROJECT} ok"),
            "key is [redacted:secret-key] ok",
        )

    def test_short_service_key_body_not_redacted(self):
        # sk- + <16 chars is not a key shape; leave it (no false confidence)
        self.assertEqual(redact("a sk-FAKE123 short ref"), "a sk-FAKE123 short ref")

    def test_aws_access_key_ids(self):
        self.assertEqual(
            redact(f"aws key {AWS_KEY} here"),
            "aws key [redacted:aws-key] here",
        )
        self.assertEqual(
            redact(f"temp creds {AWS_KEY_TEMP} too"),
            "temp creds [redacted:aws-key] too",
        )

    def test_aws_key_wrong_shape_not_redacted(self):
        # 15-char body is not the 20-char AWS shape
        self.assertEqual(
            redact(f"{AWS_KEY_SHORT} stays"), f"{AWS_KEY_SHORT} stays"
        )

    def test_github_tokens(self):
        self.assertEqual(
            redact(f"git token {GITHUB_TOKEN}"),
            "git token [redacted:github-token]",
        )
        for prefix in ("gho_", "ghu_", "ghs_", "ghr_"):
            self.assertEqual(
                redact(f"git {prefix}{'0' * 36}"),
                "git [redacted:github-token]",
            )

    def test_github_token_too_short_not_redacted(self):
        self.assertEqual(redact("ghp_FAKE short"), "ghp_FAKE short")

    def test_jwt(self):
        self.assertEqual(redact(f"jwt {JWT} tail"), "jwt [redacted:jwt] tail")

    def test_bearer_token(self):
        self.assertEqual(
            redact(f"Authorization: Bearer {BEARER_VALUE}"),
            "Authorization: [redacted:bearer]",
        )

    def test_bearer_takes_precedence_over_jwt(self):
        # A Bearer-prefixed JWT is consumed by the bearer rule (order is
        # part of the contract; the value never survives either way).
        out = redact(f"Authorization: Bearer {JWT}")
        self.assertEqual(out, "Authorization: [redacted:bearer]")

    def test_pem_private_key_block(self):
        text = f"sign with\n{PEM_BLOCK}\nthanks"
        self.assertEqual(redact(text), "sign with\n[redacted:private-key]\nthanks")

    def test_credential_assignment_unquoted(self):
        self.assertEqual(
            redact("the deploy password=changeme12345 ok"),
            "the deploy password=[redacted:credential] ok",
        )

    def test_credential_assignment_colon_form_normalized(self):
        self.assertEqual(
            redact("token: changeme12345678"),
            "token=[redacted:credential]",
        )

    def test_credential_assignment_quoted(self):
        self.assertEqual(
            redact('api_key = "placeholder12345678"'),
            "api_key=[redacted:credential]",
        )

    def test_credential_assignment_case_insensitive_name_preserved(self):
        self.assertEqual(
            redact("PASSWORD=changeme12345"),
            "PASSWORD=[redacted:credential]",
        )

    def test_credential_assignment_short_value_not_redacted(self):
        # < 8 chars of value is not confidently a secret
        self.assertEqual(redact("password=short"), "password=short")
        self.assertEqual(redact("pwd=abc123"), "pwd=abc123")

    def test_credential_assignment_stops_at_url_separator(self):
        self.assertEqual(
            redact("https://x.io/callback?token=changeme1234&state=xyz"),
            "https://x.io/callback?token=[redacted:credential]&state=xyz",
        )

    def test_hex_run(self):
        self.assertEqual(
            redact(f"checksum {HEX_RUN} end"),
            "checksum [redacted:token] end",
        )

    def test_hex_run_overredacts_commit_hashes_by_design(self):
        # Deliberate stance: deterministic over-redaction is the safe side.
        self.assertEqual(redact(f"commit {SHA_LIKE}"), "commit [redacted:token]")

    def test_short_hex_not_redacted(self):
        self.assertEqual(redact("hex a1b2c3d4e5 only"), "hex a1b2c3d4e5 only")

    def test_base64ish_run_with_digit(self):
        self.assertEqual(
            redact(f"blob {BASE64ISH} here"),
            "blob [redacted:token] here",
        )

    def test_pure_alpha_run_is_a_word_not_redacted(self):
        # Digit guard: pure-alpha 20+ runs are words/identifiers.
        self.assertEqual(
            redact("the alphabet ABCDEFGHIJKLMNOPQRSTUVWXYZ stays"),
            "the alphabet ABCDEFGHIJKLMNOPQRSTUVWXYZ stays",
        )
        self.assertEqual(
            redact("installationinstructions stays"), "installationinstructions stays"
        )

    def test_multiple_secrets_in_one_text(self):
        out = redact(
            f"use {SERVICE_KEY} and {AWS_KEY} "
            "with password=changeme12345 please"
        )
        self.assertIn("[redacted:secret-key]", out)
        self.assertIn("[redacted:aws-key]", out)
        self.assertIn("[redacted:credential]", out)
        self.assertNotIn("PLACEHOLDER", out)
        self.assertNotIn("changeme12345", out)

    def test_kitchen_sink_is_idempotent(self):
        text = (
            f"keys: {SERVICE_KEY}, {AWS_KEY}, "
            f"{GITHUB_TOKEN}, password=changeme12345, "
            f"Bearer {JWT}, "
            f"{HEX_RUN}, and a normal sentence about uv run."
        )
        once = redact(text)
        self.assertEqual(redact(once), once)

    def test_benign_prose_untouched(self):
        text = (
            "Always use uv run in this repo; plain python breaks the lockfile. "
            "Docs at https://api.typesafe.ai/v1/systemone and files under "
            "/Users/dev/example/pyproject.toml are fine."
        )
        self.assertEqual(redact(text), text)


class RedactionCountTest(unittest.TestCase):
    def test_count_matches_sequential_replacements(self):
        # bearer consumes the JWT, so this text has 3 real replacements.
        text = (
            f"password=changeme12345 and Bearer {JWT} "
            f"plus {AWS_KEY}"
        )
        self.assertEqual(redactions_in(text), 3)
        self.assertEqual(redact(text).count("[redacted:"), 3)

    def test_count_zero_for_benign(self):
        self.assertEqual(redactions_in("just a normal convention about bun"), 0)

    def test_empty_text(self):
        self.assertEqual(redact(""), "")
        self.assertEqual(redactions_in(""), 0)


if __name__ == "__main__":
    unittest.main()
