import json
import textwrap
import urllib.request
from pathlib import Path
from unittest.mock import patch


class PgError(Exception):
    def __init__(self, message, **kwargs):
        super().__init__(message)
        self.sqlstate = kwargs.get("sqlstate")


class Plpy:
    def __init__(self, settings=None):
        self.settings = settings or {}

    def error(self, message, **kwargs):
        raise PgError(message, **kwargs)

    def execute(self, query):
        marker = "current_setting('"
        if "current_user" in query.lower():
            return [{"value": self.settings.get("current_user", "test_role")}]
        if marker not in query:
            return [{"?column?": 1}]
        name = query.split(marker, 1)[1].split("'", 1)[0]
        return [{"value": self.settings.get(name)}]


def api_response(answers, **metadata):
    return {
        "model": "jev-test",
        "answers": answers,
        "usage": {"input_tokens": 1, "output_tokens": 1},
        **metadata,
    }


class StubResponse:
    def __init__(self, value):
        self.body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.headers = {"Content-Length": str(len(self.body))}
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        if self.offset >= len(self.body):
            return b""
        end = len(self.body) if size < 0 else self.offset + size
        chunk = self.body[self.offset:end]
        self.offset += len(chunk)
        return chunk


def load_run(settings=None, shared_data=None):
    source = (Path(__file__).parents[1] / "src" / "pg_prompt_jev.py").read_text()
    namespace = {"plpy": Plpy(settings), "SD": shared_data if shared_data is not None else {}}
    exec(
        "def run(input, instructions=None, noul=None, choice=None, score=None, questions=None):\n"
        + textwrap.indent(source, "    "),
        namespace,
    )
    return namespace["run"]


def call(run, response, **kwargs):
    captured = {}

    def urlopen(request, timeout):
        body = json.loads(request.data)
        captured.update({
            "url": request.full_url,
            "headers": dict(request.header_items()),
            "body": body,
            "timeout": timeout,
            **body,
        })
        return StubResponse(response)

    class Opener:
        open = staticmethod(urlopen)

    with (
        patch.object(urllib.request, "getproxies", return_value={"https": "http://proxy"}),
        patch.object(urllib.request, "proxy_bypass", return_value=False),
        patch.object(urllib.request, "build_opener", return_value=Opener()),
    ):
        result = run(**kwargs)
    return json.loads(result), captured
