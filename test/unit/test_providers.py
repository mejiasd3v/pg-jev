import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test.http_fixture import HttpFixture, Response
from test.support import PgError, call, load_run

FIXTURES = Path(__file__).parents[1] / "fixtures" / "providers"


class ProviderTests(unittest.TestCase):
    def fixture(self, provider):
        return json.loads((FIXTURES / f"{provider}-success.json").read_text())

    def test_provider_contracts_and_documented_defaults(self):
        cases = [
            (
                "typesafe",
                {"TYPESAFE_API_KEY": "typesafe-key"},
                {},
                "https://api.typesafe.ai/v1/systemone",
                "jev-latest",
            ),
            (
                "vercel",
                {"PROMPT_JEV_PROVIDER": "vercel", "AI_GATEWAY_API_KEY": "vercel-key"},
                {},
                "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
                "typesafe-ai/jev",
            ),
            (
                "cloudflare",
                {
                    "PROMPT_JEV_PROVIDER": "cloudflare",
                    "CLOUDFLARE_API_TOKEN": "cloudflare-key",
                    "CLOUDFLARE_ACCOUNT_ID": "account-id",
                },
                {},
                "https://api.cloudflare.com/client/v4/accounts/account-id/ai/run",
                "typesafe/jev",
            ),
        ]
        for provider, environment, settings, url, model in cases:
            with self.subTest(provider=provider), patch.dict(os.environ, environment, clear=True):
                result, request = call(
                    load_run(settings),
                    self.fixture(provider),
                    input="text",
                    instructions="Question?",
                )
                self.assertEqual(result, 0.75)
                self.assertEqual(request["url"], url)
                self.assertEqual(request["body"]["model"], model)

    def test_provider_specific_model_and_credential_precedence(self):
        cases = [
            (
                {
                    "TYPESAFE_API_KEY": "environment-key",
                    "TYPESAFE_DEFAULT_MODEL": "environment-model",
                },
                {"jev.api_key": "session-key", "jev.typesafe_model": "session-model"},
                "environment-key",
                "environment-model",
            ),
            (
                {
                    "PROMPT_JEV_PROVIDER": "vercel",
                    "AI_GATEWAY_API_KEY": "environment-key",
                    "VERCEL_JEV_MODEL": "vercel-model",
                    "TYPESAFE_DEFAULT_MODEL": "legacy-model",
                },
                {"jev.api_key": "session-key", "jev.vercel_model": "session-model"},
                "environment-key",
                "vercel-model",
            ),
            (
                {
                    "PROMPT_JEV_PROVIDER": "vercel",
                    "AI_GATEWAY_API_KEY": "key",
                    "TYPESAFE_DEFAULT_MODEL": "legacy-model",
                },
                {"jev.vercel_model": "session-model"},
                "key",
                "legacy-model",
            ),
            (
                {"PROMPT_JEV_PROVIDER": "vercel", "AI_GATEWAY_API_KEY": "key"},
                {"jev.vercel_model": "session-model"},
                "key",
                "session-model",
            ),
        ]
        for environment, settings, key, model in cases:
            provider = environment.get("PROMPT_JEV_PROVIDER", "typesafe")
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                _, request = call(
                    load_run(settings),
                    self.fixture(provider),
                    input="text",
                    instructions="Question?",
                )
                self.assertEqual(request["headers"]["Authorization"], "Bearer " + key)
                self.assertEqual(request["body"]["model"], model)

    def test_http_statuses_have_stable_sqlstates(self):
        cases = {
            301: "JV007",
            401: "JV003",
            403: "JV003",
            429: "JV004",
            500: "JV005",
            529: "JV005",
        }
        for status, sqlstate in cases.items():
            with self.subTest(status=status):
                with HttpFixture(lambda _request: Response(status=status)) as fixture:
                    with patch.dict(os.environ, {
                        "TYPESAFE_API_KEY": "key",
                        "TYPESAFE_BASE_URL": fixture.url,
                        "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
                    }, clear=True):
                        with self.assertRaises(PgError) as raised:
                            load_run()("text", "Question?")
                self.assertEqual(raised.exception.sqlstate, sqlstate)

    def test_retries_are_off_by_default(self):
        with HttpFixture(lambda _request: Response(status=529)) as fixture:
            with patch.dict(os.environ, {
                "TYPESAFE_API_KEY": "key",
                "TYPESAFE_BASE_URL": fixture.url,
                "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
            }, clear=True):
                with self.assertRaises(PgError) as raised:
                    load_run()("text", "Question?")
        self.assertEqual(raised.exception.sqlstate, "JV005")
        self.assertEqual(len(fixture.requests), 1)

    def test_explicit_retry_is_bounded_and_honors_retry_after_ceiling(self):
        successful = json.dumps(self.fixture("typesafe")).encode()

        def respond(_request):
            if len(fixture.requests) == 1:
                return Response(status=429, headers={"Retry-After": "100"})
            return Response(body=successful)

        with HttpFixture(respond) as fixture:
            with patch.dict(os.environ, {
                "TYPESAFE_API_KEY": "key",
                "TYPESAFE_BASE_URL": fixture.url,
                "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
                "PROMPT_JEV_MAX_RETRIES": "1",
                "PROMPT_JEV_MAX_RETRY_AFTER_SECONDS": "0.01",
            }, clear=True):
                started = time.monotonic()
                result = load_run({"jev.retries": "1"})("text", "Question?")
                elapsed = time.monotonic() - started
        self.assertEqual(json.loads(result), 0.75)
        self.assertEqual(len(fixture.requests), 2)
        self.assertLess(elapsed, 1)

    def test_permanent_errors_are_not_retried_when_enabled(self):
        with HttpFixture(lambda _request: Response(status=401)) as fixture:
            with patch.dict(os.environ, {
                "TYPESAFE_API_KEY": "key",
                "TYPESAFE_BASE_URL": fixture.url,
                "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
                "PROMPT_JEV_MAX_RETRIES": "1",
            }, clear=True):
                with self.assertRaises(PgError) as raised:
                    load_run({"jev.retries": "1"})("text", "Question?")
        self.assertEqual(raised.exception.sqlstate, "JV003")
        self.assertEqual(len(fixture.requests), 1)

    def test_more_than_one_retry_is_rejected_as_configuration(self):
        with patch.dict(os.environ, {
            "TYPESAFE_API_KEY": "key",
            "PROMPT_JEV_MAX_RETRIES": "2",
        }, clear=True):
            with self.assertRaises(PgError) as raised:
                load_run()("text", "Question?")
        self.assertEqual(raised.exception.sqlstate, "JV001")


if __name__ == "__main__":
    unittest.main()
