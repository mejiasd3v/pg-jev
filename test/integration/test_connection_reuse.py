import json
import os
import time
import unittest
from unittest.mock import patch

from test.http_fixture import HttpFixture, Response
from test.support import PgError, api_response, load_run


class ConnectionReuseTests(unittest.TestCase):
    def response(self):
        return Response.json(api_response({
            "answer": {"type": "noul", "noul": 0.5}
        }))

    def environment(self, fixture, **values):
        return {
            "PROMPT_JEV_PROVIDER": "typesafe",
            "TYPESAFE_API_KEY": "key",
            "TYPESAFE_BASE_URL": fixture.url,
            "PROMPT_JEV_ALLOW_INSECURE_LOOPBACK": "1",
            **values,
        }

    def close_cache(self, shared_data):
        cached = shared_data.get("http_connection")
        if cached:
            cached["connection"].close()

    def test_five_same_context_calls_reuse_one_connection(self):
        shared_data = {}
        with HttpFixture(lambda _request: self.response()) as fixture:
            with patch.dict(os.environ, self.environment(fixture), clear=True):
                run = load_run(shared_data=shared_data)
                for _ in range(5):
                    self.assertEqual(json.loads(run("text", "Question?")), 0.5)
            self.close_cache(shared_data)
        self.assertEqual(len(fixture.requests), 5)
        self.assertEqual(fixture.connection_count, 1)

    def test_context_changes_replace_the_connection(self):
        shared_data = {}
        with HttpFixture(lambda _request: self.response()) as fixture:
            contexts = [
                (self.environment(fixture), {"current_user": "role_one"}),
                (self.environment(fixture), {"current_user": "role_one"}),
                (self.environment(fixture), {"current_user": "role_two"}),
                (self.environment(fixture, TYPESAFE_API_KEY="other"), {"current_user": "role_two"}),
                (self.environment(fixture, TYPESAFE_DEFAULT_MODEL="other-model"), {"current_user": "role_two"}),
            ]
            for environment, settings in contexts:
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(
                        json.loads(load_run(settings, shared_data)("text", "Question?")),
                        0.5,
                    )
            self.close_cache(shared_data)
        self.assertEqual(fixture.connection_count, 4)

    def test_endpoint_change_uses_a_new_connection(self):
        shared_data = {}
        with HttpFixture(lambda _request: self.response()) as first:
            with HttpFixture(lambda _request: self.response()) as second:
                for fixture in (first, second):
                    with patch.dict(os.environ, self.environment(fixture), clear=True):
                        self.assertEqual(
                            json.loads(load_run(shared_data=shared_data)("text", "Question?")),
                            0.5,
                        )
                self.close_cache(shared_data)
        self.assertEqual(first.connection_count, 1)
        self.assertEqual(second.connection_count, 1)

    def test_invalid_response_drops_connection_before_next_call(self):
        shared_data = {}

        def respond(_request):
            if len(fixture.requests) == 2:
                return Response(body=b"not-json")
            return self.response()

        with HttpFixture(respond) as fixture:
            with patch.dict(os.environ, self.environment(fixture), clear=True):
                run = load_run(shared_data=shared_data)
                self.assertEqual(json.loads(run("text", "Question?")), 0.5)
                with self.assertRaises(PgError):
                    run("text", "Question?")
                self.assertEqual(json.loads(run("text", "Question?")), 0.5)
            self.close_cache(shared_data)
        self.assertEqual(fixture.connection_count, 2)

    def test_server_close_and_connection_age_prevent_reuse(self):
        shared_data = {}
        with HttpFixture(lambda _request: Response.json(
            api_response({"answer": {"type": "noul", "noul": 0.5}}),
            headers={"Connection": "close"},
        )) as fixture:
            with patch.dict(os.environ, self.environment(fixture), clear=True):
                run = load_run(shared_data=shared_data)
                run("text", "Question?")
                run("text", "Question?")
            self.close_cache(shared_data)
        self.assertEqual(fixture.connection_count, 2)

        shared_data = {}
        with HttpFixture(lambda _request: self.response()) as fixture:
            with patch.dict(os.environ, self.environment(fixture), clear=True):
                run = load_run(
                    {"jev.connection_age": "0.001"}, shared_data=shared_data
                )
                run("text", "Question?")
                time.sleep(0.01)
                run("text", "Question?")
            self.close_cache(shared_data)
        self.assertEqual(fixture.connection_count, 2)


if __name__ == "__main__":
    unittest.main()
