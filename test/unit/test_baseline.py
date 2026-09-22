import json
import os
import unittest
from unittest.mock import patch

from test.support import PgError, api_response, call, load_run


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.run = load_run()

    def test_choice_output_and_request_shape(self):
        result, request = call(
            self.run,
            api_response({"answer": {
                "type": "choice",
                "choice": "billing",
                "probabilities": {"billing": 0.9, "technical": 0.1},
                "confidence": 0.8,
            }}),
            input="charged twice",
            instructions="Which team?",
            choice='["billing", "technical"]',
        )
        self.assertEqual(request["questions"]["answer"]["criteria"], {
            "billing": None,
            "technical": None,
        })
        self.assertEqual(result, {
            "choice": "billing",
            "probabilities": [
                {"value": "billing", "probability": 0.9},
                {"value": "technical", "probability": 0.1},
            ],
            "confidence": 0.8,
        })

    def test_provider_request_shapes(self):
        cases = [
            (
                {"PROMPT_JEV_PROVIDER": "vercel", "AI_GATEWAY_API_KEY": "vercel-key"},
                "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
                "vercel-key",
            ),
            (
                {
                    "PROMPT_JEV_PROVIDER": "cloudflare",
                    "CLOUDFLARE_API_TOKEN": "cloudflare-key",
                    "CLOUDFLARE_ACCOUNT_ID": "account-id",
                    "CLOUDFLARE_AI_GATEWAY_ID": "gateway-id",
                },
                "https://api.cloudflare.com/client/v4/accounts/account-id/ai/run",
                "cloudflare-key",
            ),
        ]
        for environment, url, key in cases:
            with self.subTest(url=url), patch.dict(os.environ, environment, clear=True):
                result, request = call(
                    load_run(),
                    api_response({"answer": {"type": "noul", "noul": 0.9}}),
                    input="urgent",
                    instructions="Is this urgent?",
                )
                self.assertEqual(result, 0.9)
                self.assertEqual(request["url"], url)
                self.assertEqual(request["headers"]["Authorization"], "Bearer " + key)

    def test_score_and_multi_question_output_shapes(self):
        score, _ = call(
            self.run,
            api_response({"answer": {
                "type": "score",
                "score": 1.75,
                "legend": {"0": "low", "1": "medium", "2": "high"},
                "probabilities": {"0": 0.05, "1": 0.15, "2": 0.8},
                "confidence": 0.9,
            }}),
            input="service is down",
            instructions="Severity?",
            score='["low", "medium", "high"]',
        )
        self.assertEqual(score["score"], 1.75)
        self.assertEqual(score["probabilities"][2]["value"], "high")

        multi, _ = call(
            self.run,
            api_response({
                "refund": {"type": "noul", "noul": 0.7},
                "team": {
                    "type": "choice",
                    "choice": "sales",
                    "probabilities": {"sales": 1.0, "support": 0.0},
                    "confidence": 1.0,
                },
            }),
            input="upgrade me",
            questions=json.dumps({
                "refund": {"type": "noul", "instructions": "Refund?"},
                "team": {
                    "type": "choice",
                    "instructions": "Team?",
                    "criteria": ["sales", "support"],
                },
            }),
        )
        self.assertEqual(multi["refund"], 0.7)
        self.assertEqual(multi["team"]["choice"], "sales")

    def test_duplicate_labels_are_rejected(self):
        with self.assertRaisesRegex(PgError, "labels must be unique"):
            self.run("text", "Pick", choice='["same", "same"]')

    def test_null_input_short_circuits_before_validation_or_network(self):
        self.assertIsNone(self.run(None, questions='{"invalid": true}'))


if __name__ == "__main__":
    unittest.main()
