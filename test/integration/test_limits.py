import json
import os
import time
import unittest
import urllib.request
from unittest.mock import patch

from test.http_fixture import HttpFixture, Response
from test.support import PgError, api_response, load_run


class LimitTests(unittest.TestCase):
    def response_body(self, **metadata):
        return json.dumps(api_response(
            {"answer": {"type": "noul", "noul": 0.5}},
            **metadata,
        ), separators=(",", ":")).encode()

    def run_against(self, response, settings=None, **arguments):
        limits = {
            name: value for name, value in os.environ.items()
            if name.startswith("PROMPT_JEV_MAX_")
        }
        shared_data = {}
        with HttpFixture(lambda _request: response) as fixture:
            with patch.dict(os.environ, {
                "PROMPT_JEV_PROVIDER": "typesafe",
                "TYPESAFE_API_KEY": "key",
                "TYPESAFE_BASE_URL": fixture.url,
                "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
                **limits,
            }, clear=True):
                try:
                    return load_run(settings, shared_data)(
                        arguments.pop("input", "text"),
                        arguments.pop("instructions", "Question?"),
                        **arguments,
                    )
                finally:
                    cached = shared_data.get("http_connection")
                    if cached:
                        cached["connection"].close()

    def test_input_limit_counts_utf8_bytes_before_network(self):
        with patch.dict(os.environ, {
            "TYPESAFE_API_KEY": "key",
            "PROMPT_JEV_MAX_INPUT_BYTES": "4",
        }, clear=True), patch.object(urllib.request, "build_opener") as opener:
            with self.assertRaises(PgError) as raised:
                load_run()("ééx", "Question?")
        self.assertEqual(raised.exception.sqlstate, "22023")
        opener.assert_not_called()

    def test_request_byte_cap_and_question_count_cap_precede_network(self):
        cases = [
            (
                {"PROMPT_JEV_MAX_REQUEST_BYTES": "100"},
                {"instructions": "x" * 80},
                "byte limit",
            ),
            (
                {"PROMPT_JEV_MAX_QUESTIONS": "2"},
                {"instructions": None, "questions": {
                    str(index): {"type": "noul", "instructions": "Q"}
                    for index in range(3)
                }},
                "count limit",
            ),
        ]
        for environment, arguments, message in cases:
            with self.subTest(message=message), patch.dict(os.environ, {
                "TYPESAFE_API_KEY": "key", **environment
            }, clear=True), patch.object(urllib.request, "build_opener") as opener:
                with self.assertRaisesRegex(PgError, message) as raised:
                    load_run()(input="text", **arguments)
                self.assertEqual(raised.exception.sqlstate, "22023")
                opener.assert_not_called()

    def test_json_nesting_is_bounded_for_requests_and_responses(self):
        nested = "value"
        for _ in range(65):
            nested = {"next": nested}
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "key"}, clear=True), patch.object(
            urllib.request, "build_opener"
        ) as opener:
            with self.assertRaisesRegex(PgError, "nesting limit") as raised:
                load_run()(
                    "text",
                    questions={"q": {"type": "noul", "instructions": nested}},
                )
        self.assertEqual(raised.exception.sqlstate, "22023")
        opener.assert_not_called()

        body = self.response_body(extra=nested)
        with self.assertRaises(PgError) as raised:
            self.run_against(Response(body=body))
        self.assertEqual(raised.exception.sqlstate, "JV006")

    def test_response_cap_applies_with_and_without_content_length(self):
        body = self.response_body(padding="x" * 300)
        with patch.dict(os.environ, {"PROMPT_JEV_MAX_RESPONSE_BYTES": "100"}, clear=False):
            for response in (
                Response(body=body, headers={"Content-Length": str(len(body))}),
                Response(chunks=[(0, body)], headers={"Connection": "close"}),
            ):
                with self.subTest(headers=response.headers):
                    with self.assertRaises(PgError) as raised:
                        self.run_against(response)
                    self.assertEqual(raised.exception.sqlstate, "JV006")

    def test_exact_response_cap_is_accepted_and_cap_plus_one_is_rejected(self):
        body = self.response_body()
        with patch.dict(os.environ, {
            "PROMPT_JEV_MAX_RESPONSE_BYTES": str(len(body)),
        }, clear=False):
            self.assertEqual(json.loads(self.run_against(Response(body=body))), 0.5)
        with patch.dict(os.environ, {
            "PROMPT_JEV_MAX_RESPONSE_BYTES": str(len(body) - 1),
        }, clear=False):
            with self.assertRaises(PgError) as raised:
                self.run_against(Response(body=body))
            self.assertEqual(raised.exception.sqlstate, "JV006")

    def test_false_length_encoding_and_disconnect_fail_closed(self):
        body = self.response_body()
        responses = [
            Response(body=body, headers={
                "Content-Length": str(len(body) + 1),
                "Connection": "close",
            }),
            Response(body=body, headers={"Content-Encoding": "gzip"}),
            Response(headers={"Content-Length": "10"}, close_early=True),
        ]
        for response in responses:
            with self.subTest(headers=response.headers):
                with self.assertRaises(PgError) as raised:
                    self.run_against(response)
                self.assertEqual(raised.exception.sqlstate, "JV006")

    def test_slow_headers_and_slow_drip_obey_configured_budgets(self):
        body = self.response_body()
        cases = [
            (
                Response(body=body, header_delay=0.2),
                {"jev.connect_timeout": "0.05", "jev.total_timeout": "0.15"},
            ),
            (
                Response(
                    chunks=[(0.06, body[:10]), (0.06, body[10:20]), (0.06, body[20:])],
                    headers={"Content-Length": str(len(body))},
                ),
                {"jev.read_timeout": "0.1", "jev.total_timeout": "0.12"},
            ),
        ]
        for response, settings in cases:
            with self.subTest(settings=settings):
                started = time.monotonic()
                with self.assertRaises(PgError) as raised:
                    self.run_against(response, settings=settings)
                elapsed = time.monotonic() - started
                self.assertEqual(raised.exception.sqlstate, "JV002")
                self.assertLess(elapsed, 1.0)

    def test_invalid_configuration_and_attempts_to_raise_ceiling_are_rejected(self):
        for value in ("0", "-1", "nan", "infinity", "not-a-number"):
            with self.subTest(value=value), patch.dict(os.environ, {
                "TYPESAFE_API_KEY": "key",
                "PROMPT_JEV_MAX_TOTAL_SECONDS": value,
            }, clear=True):
                with self.assertRaises(PgError) as raised:
                    load_run()("text", "Question?")
                self.assertEqual(raised.exception.sqlstate, "JV001")

        with patch.dict(os.environ, {
            "TYPESAFE_API_KEY": "key",
            "PROMPT_JEV_MAX_RESPONSE_BYTES": "100",
        }, clear=True):
            with self.assertRaises(PgError) as raised:
                load_run({"jev.response_bytes": "101"})("text", "Question?")
        self.assertEqual(raised.exception.sqlstate, "JV001")


if __name__ == "__main__":
    unittest.main()
