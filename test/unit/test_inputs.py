import json
import os
import unittest
import urllib.request
from unittest.mock import patch

from test.support import PgError, api_response, call, load_run


class InputTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.run = load_run()

    def assert_invalid_without_request(self, message, **arguments):
        with patch.object(urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(PgError, message) as raised:
                self.run(input="text", **arguments)
        self.assertEqual(raised.exception.sqlstate, "22023")
        opener.assert_not_called()

    def test_v12_invalid_choice_object_is_rejected(self):
        self.assert_invalid_without_request(
            "labels must be non-empty",
            instructions="Pick",
            choice={"": None, "b": 42},
        )

    def test_v13_score_object_requests_an_ordered_array(self):
        self.assert_invalid_without_request(
            "ordered JSON array",
            instructions="Score",
            score={"low": None, "high": None},
        )

    def test_v14_numeric_multi_question_instructions_are_rejected(self):
        self.assert_invalid_without_request(
            "requires non-empty",
            questions={"q": {"type": "noul", "instructions": 42}},
        )

    def test_json_looking_nested_strings_are_not_decoded(self):
        instructions = '{"keep": "as text"}'
        result, request = call(
            self.run,
            api_response({"q": {"type": "noul", "noul": 0.4}}),
            input="text",
            questions={"q": {"type": "noul", "instructions": instructions}},
        )
        self.assertEqual(result, {"q": 0.4})
        self.assertEqual(request["questions"]["q"]["instructions"], instructions)

    def test_structured_instructions_and_descriptions_are_preserved(self):
        questions = {
            "选择": {
                "type": "choice",
                "instructions": {"task": ["选择", "one"]},
                "criteria": {
                    "甲": {"detail": ["first"]},
                    "乙": ["second"],
                },
            },
            "score": {
                "type": "score",
                "instructions": ["rate", {"language": "日本語"}],
                "criteria": [
                    {"label": "低", "description": {"rank": 1}},
                    {"label": "高", "description": ["rank", 2]},
                ],
            },
        }
        response = api_response({
            "选择": {
                "type": "choice",
                "choice": "甲",
                "probabilities": {"甲": 1.0, "乙": 0.0},
                "confidence": 1.0,
            },
            "score": {
                "type": "score",
                "score": 1,
                "legend": {"0": "low", "1": "high"},
                "probabilities": {"0": 0.0, "1": 1.0},
                "confidence": 1.0,
            },
        })
        _, request = call(self.run, response, input="unicode ✓", questions=questions)
        self.assertEqual(request["questions"], questions)

    def test_whitespace_empty_and_unknown_fields_are_rejected(self):
        cases = [
            {"instructions": "   "},
            {"questions": {}},
            {"questions": {"": {"type": "noul", "instructions": "Q"}}},
            {"questions": {"q": {"type": "noul", "instructions": "Q", "instruction": "typo"}}},
            {"questions": {"q": {"type": "noul", "instructions": []}}},
            {"questions": {"q": {"type": "noul", "instructions": None}}},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assert_invalid_without_request("prompt_jev", **arguments)

    def test_conflicting_question_arguments_are_rejected(self):
        self.assert_invalid_without_request(
            "cannot be combined",
            instructions="Q",
            choice=["a", "b"],
            questions={"q": {"type": "noul", "instructions": "Q"}},
        )
        self.assert_invalid_without_request(
            "cannot be combined",
            instructions="Q",
            choice=["a", "b"],
            score=["low", "high"],
        )

    def test_criteria_labels_and_descriptions_are_validated_consistently(self):
        cases = [
            {"choice": ["a", "a"]},
            {"choice": [" ", "b"]},
            {"choice": [{"label": "a", "description": 7}, "b"]},
            {"choice": [{"label": "a", "extra": "typo"}, "b"]},
            {"noul": ["true", "maybe"]},
        ]
        for modes in cases:
            with self.subTest(modes=modes):
                self.assert_invalid_without_request("prompt_jev", instructions="Q", **modes)

    def test_choice_and_score_count_boundaries(self):
        accepted = [
            ("choice", [f"v{i}" for i in range(2)]),
            ("choice", [f"v{i}" for i in range(255)]),
            ("score", [f"v{i}" for i in range(2)]),
            ("score", [f"v{i}" for i in range(10)]),
        ]
        for kind, criteria in accepted:
            with self.subTest(kind=kind, count=len(criteria)):
                probabilities = {
                    str(index) if kind == "score" else label: 1.0 if index == 0 else 0.0
                    for index, label in enumerate(criteria)
                }
                answer = {
                    "type": kind,
                    kind: 0 if kind == "score" else criteria[0],
                    "probabilities": probabilities,
                    "confidence": 1.0,
                }
                if kind == "score":
                    answer["legend"] = {
                        str(index): label for index, label in enumerate(criteria)
                    }
                result, _ = call(
                    self.run,
                    api_response({"answer": answer}),
                    input="text",
                    instructions="Q",
                    **{kind: criteria},
                )
                self.assertIsNotNone(result)

        for kind, criteria in (
            ("choice", ["only"]),
            ("choice", [f"v{i}" for i in range(256)]),
            ("score", ["only"]),
            ("score", [f"v{i}" for i in range(11)]),
        ):
            with self.subTest(kind=kind, count=len(criteria)):
                self.assert_invalid_without_request(
                    "requires between",
                    instructions="Q",
                    **{kind: criteria},
                )

    def test_sql_null_and_json_null_modes_remain_distinct(self):
        self.assertIsNone(self.run(None, instructions=None, questions="not json"))
        self.assert_invalid_without_request("non-empty JSON object", questions="null")
        self.assert_invalid_without_request(
            "requires criteria", instructions="Q", choice="null"
        )
        result, request = call(
            self.run,
            api_response({"answer": {"type": "noul", "noul": 0.2}}),
            input="text",
            instructions="Q",
            noul="null",
        )
        self.assertEqual(result, 0.2)
        self.assertNotIn("criteria", request["questions"]["answer"])

    def test_invalid_outer_json_is_controlled(self):
        self.assert_invalid_without_request("must be valid JSON", questions="{")


if __name__ == "__main__":
    unittest.main()
