import json
import urllib.error
import urllib.request
import unittest

from test.http_fixture import HttpFixture, Response


class HttpFixtureTests(unittest.TestCase):
    def test_counts_requests_and_connections(self):
        with HttpFixture(lambda _request: Response.json({"ok": True})) as fixture:
            for _ in range(2):
                request = urllib.request.Request(fixture.url, data=b"{}", method="POST")
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(json.load(response), {"ok": True})
        self.assertEqual(len(fixture.requests), 2)
        self.assertEqual(fixture.connection_count, 2)

    def test_can_emit_redirect_status_and_malformed_body(self):
        responses = iter([
            Response(status=302, headers={"Location": "/elsewhere"}),
            Response(body=b"not-json"),
        ])
        with HttpFixture(lambda _request: next(responses)) as fixture:
            opener = urllib.request.build_opener(_NoRedirect())
            request = urllib.request.Request(fixture.url, data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(request)
            self.assertEqual(error.exception.code, 302)
            error.exception.close()
            with urllib.request.urlopen(request) as response:
                with self.assertRaises(json.JSONDecodeError):
                    json.load(response)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


if __name__ == "__main__":
    unittest.main()
