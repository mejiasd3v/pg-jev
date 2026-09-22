import os
import unittest
from unittest.mock import patch

from test.http_fixture import HttpFixture, Response
from test.support import PgError, load_run


class TransportSecurityTests(unittest.TestCase):
    def test_redirects_are_never_followed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                with HttpFixture(lambda _request: Response.json({"unexpected": True})) as target:
                    with HttpFixture(lambda _request: Response(
                        status=status,
                        body=b"synthetic-secret prompt text",
                        headers={"Location": target.url + "/target"},
                    )) as source:
                        with patch.dict(os.environ, {
                            "TYPESAFE_API_KEY": "synthetic-secret",
                            "TYPESAFE_BASE_URL": source.url,
                            "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
                        }, clear=True):
                            with self.assertRaisesRegex(PgError, f"status={status}"):
                                load_run()("prompt text", "Question?")
                    self.assertEqual(len(target.requests), 0)

    def test_relative_redirect_is_not_followed(self):
        with HttpFixture(lambda _request: Response(
            status=302,
            headers={"Location": "/target"},
        )) as fixture:
            with patch.dict(os.environ, {
                "TYPESAFE_API_KEY": "key",
                "TYPESAFE_BASE_URL": fixture.url,
                "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
            }, clear=True):
                with self.assertRaisesRegex(PgError, "status=302"):
                    load_run()("text", "Question?")
        self.assertEqual([request["path"] for request in fixture.requests], ["/v1/systemone"])

    def test_provider_error_does_not_leak_body_key_or_prompt(self):
        secret = "synthetic-secret"
        prompt = "private prompt"
        with HttpFixture(lambda _request: Response(
            status=401,
            body=f"key={secret} input={prompt}".encode(),
            headers={
                "X-Request-ID": "safe-request-123",
                "Content-Length": "999999999",
            },
        )) as fixture:
            with patch.dict(os.environ, {
                "TYPESAFE_API_KEY": secret,
                "TYPESAFE_BASE_URL": fixture.url,
                "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
            }, clear=True):
                with self.assertRaises(PgError) as raised:
                    load_run()(prompt, "Question?")
        message = str(raised.exception)
        self.assertEqual(
            message,
            "prompt_jev API request failed: provider=typesafe category=authentication "
            "status=401 request_id=safe-request-123",
        )
        self.assertNotIn(secret, message)
        self.assertNotIn(prompt, message)

    def test_http_requires_admin_loopback_test_mode(self):
        cases = [
            ({"TYPESAFE_BASE_URL": "http://127.0.0.1:1"}, {}),
            (
                {"TYPESAFE_BASE_URL": "http://example.com", "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1"},
                {},
            ),
            (
                {"TYPESAFE_BASE_URL": "http://127.0.0.1:1"},
                {"jev.allow_insecure_loopback": "on"},
            ),
        ]
        for environment, settings in cases:
            with self.subTest(environment=environment, settings=settings):
                with patch.dict(os.environ, {"TYPESAFE_API_KEY": "key", **environment}, clear=True):
                    with self.assertRaisesRegex(PgError, "requires HTTPS"):
                        load_run(settings)("text", "Question?")

    def test_invalid_urls_and_header_settings_are_rejected(self):
        environments = [
            {"TYPESAFE_BASE_URL": "https://user:pass@example.com"},
            {"TYPESAFE_BASE_URL": "https://example.com/path#fragment"},
            {"TYPESAFE_BASE_URL": "ftp://example.com"},
            {"TYPESAFE_API_KEY": "key\nInjected: value"},
        ]
        for environment in environments:
            with self.subTest(environment=environment):
                values = {"TYPESAFE_API_KEY": "key", **environment}
                with patch.dict(os.environ, values, clear=True):
                    with self.assertRaises(PgError):
                        load_run()("text", "Question?")

    def test_cloudflare_path_and_gateway_values_are_validated(self):
        base = {
            "PROMPT_JEV_PROVIDER": "cloudflare",
            "CLOUDFLARE_API_TOKEN": "key",
        }
        cases = [
            {"CLOUDFLARE_ACCOUNT_ID": "../other"},
            {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_AI_GATEWAY_ID": "bad\rvalue"},
        ]
        for values in cases:
            with self.subTest(values=values), patch.dict(os.environ, {**base, **values}, clear=True):
                with self.assertRaises(PgError):
                    load_run()("text", "Question?")

    def test_administrator_provider_allowlist_is_enforced(self):
        with patch.dict(os.environ, {
            "PROMPT_JEV_PROVIDER": "vercel",
            "PROMPT_JEV_ALLOWED_PROVIDERS": "typesafe",
            "AI_GATEWAY_API_KEY": "key",
        }, clear=True):
            with self.assertRaisesRegex(PgError, "not allowed by administrator policy"):
                load_run()("text", "Question?")


if __name__ == "__main__":
    unittest.main()
