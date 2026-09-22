import json
import os
import re
import textwrap
import urllib.request
from pathlib import Path
from unittest.mock import patch


class PgError(Exception):
    pass


class Plpy:
    def error(self, message):
        raise PgError(message)

    def execute(self, _query):
        return [{"value": None}]


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return json.dumps(self.value).encode()


sql = (Path(__file__).parents[1] / "sql" / "pg_prompt_jev--0.1.0.sql").read_text()
body = re.search(r"AS \$PY\$\n(.*?)\n\$PY\$;", sql, re.S).group(1)
namespace = {"plpy": Plpy()}
exec(
    "def run(input, instructions=None, noul=None, choice=None, score=None, questions=None):\n"
    + textwrap.indent(body, "    "),
    namespace,
)
run = namespace["run"]
os.environ["TYPESAFE_API_KEY"] = "test-key"


def call(response, **kwargs):
    captured = {}

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        assert timeout == 10
        return Response(response)

    with patch.object(urllib.request, "urlopen", urlopen):
        result = run(**kwargs)
    captured.update(captured["body"])
    return json.loads(result), captured


choice_result, choice_request = call(
    {
        "answers": {
            "answer": {
                "type": "choice",
                "choice": "billing",
                "probabilities": {"billing": 0.9, "technical": 0.1},
                "confidence": 0.8,
            }
        }
    },
    input="charged twice",
    instructions="Which team?",
    choice='["billing", "technical"]',
)
assert choice_request["questions"]["answer"]["criteria"] == {
    "billing": None,
    "technical": None,
}
assert choice_result["choice"] == "billing"
assert choice_result["probabilities"][1] == {
    "value": "technical",
    "probability": 0.1,
}

with patch.dict(
    os.environ,
    {"PROMPT_JEV_PROVIDER": "vercel", "AI_GATEWAY_API_KEY": "vercel-key"},
):
    _, vercel_request = call(
        {"answers": {"answer": {"type": "noul", "noul": 0.9}}},
        input="urgent",
        instructions="Is this urgent?",
    )
assert vercel_request["url"] == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
assert vercel_request["headers"]["Authorization"] == "Bearer vercel-key"
assert vercel_request["body"]["state"] == "urgent"

with patch.dict(
    os.environ,
    {
        "PROMPT_JEV_PROVIDER": "cloudflare",
        "CLOUDFLARE_API_TOKEN": "cloudflare-key",
        "CLOUDFLARE_ACCOUNT_ID": "account-id",
        "CLOUDFLARE_AI_GATEWAY_ID": "gateway-id",
    },
):
    _, cloudflare_request = call(
        {"answers": {"answer": {"type": "noul", "noul": 0.9}}},
        input="urgent",
        instructions="Is this urgent?",
    )
assert cloudflare_request["url"] == (
    "https://api.cloudflare.com/client/v4/accounts/account-id/ai/run"
)
assert cloudflare_request["headers"]["Authorization"] == "Bearer cloudflare-key"
assert cloudflare_request["headers"]["Cf-aig-gateway-id"] == "gateway-id"
assert cloudflare_request["body"] == {
    "model": "typesafe/jev",
    "input": {
        "state": "urgent",
        "questions": {
            "answer": {"type": "noul", "instructions": "Is this urgent?"}
        },
    },
}

score_result, _ = call(
    {
        "answers": {
            "answer": {
                "type": "score",
                "score": 1.75,
                "probabilities": {"0": 0.05, "1": 0.15, "2": 0.8},
                "confidence": 0.9,
            }
        }
    },
    input="service is down",
    instructions="Severity?",
    score='["low", "medium", "high"]',
)
assert score_result["score"] == 1.75
assert score_result["probabilities"][2]["value"] == "high"

multi_result, _ = call(
    {
        "answers": {
            "refund": {"type": "noul", "noul": 0.7},
            "team": {
                "type": "choice",
                "choice": "sales",
                "probabilities": {"sales": 1.0, "support": 0.0},
                "confidence": 1.0,
            },
        }
    },
    input="upgrade me",
    questions=json.dumps(
        {
            "refund": {"type": "noul", "instructions": "Refund?"},
            "team": {
                "type": "choice",
                "instructions": "Team?",
                "criteria": ["sales", "support"],
            },
        }
    ),
)
assert multi_result["refund"] == 0.7
assert multi_result["team"]["choice"] == "sales"

try:
    run("text", "Pick", choice='["same", "same"]')
except PgError as error:
    assert "unique" in str(error)
else:
    raise AssertionError("duplicate labels were accepted")

assert run(None, "Question?") is None
print("ok")
