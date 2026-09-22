import copy
import os
import unittest
from unittest.mock import patch

from test.support import PgError, api_response, call, load_run


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.run = load_run()

    def assert_response_error(self, response, message="invalid response", **arguments):
        defaults = {"input": "text", "instructions": "Question?"}
        with self.assertRaisesRegex(PgError, message) as raised:
            call(self.run, response, **(defaults | arguments))
        self.assertEqual(raised.exception.sqlstate, "JV006")

    def choice_response(self):
        return api_response({"answer": {
            "type": "choice",
            "choice": "a",
            "probabilities": {"a": 0.75, "b": 0.25},
            "confidence": 0.5,
        }})

    def score_response(self):
        return api_response({"answer": {
            "type": "score",
            "score": 1.25,
            "legend": {"0": "low", "1": "medium", "2": "high"},
            "probabilities": {"0": 0.1, "1": 0.55, "2": 0.35},
            "confidence": 0.6,
        }})

    def test_v01_top_level_array_is_rejected(self):
        self.assert_response_error([])

    def test_v02_to_v04_missing_and_partial_answers_are_rejected(self):
        self.assert_response_error(api_response({}))
        questions = {
            "one": {"type": "noul", "instructions": "One?"},
            "two": {"type": "noul", "instructions": "Two?"},
        }
        for answers in ({}, {"one": {"type": "noul", "noul": 0.5}}):
            with self.subTest(answers=answers):
                self.assert_response_error(
                    api_response(answers),
                    instructions=None,
                    questions=questions,
                )

    def test_v05_and_v06_wrong_or_unknown_types_are_rejected(self):
        for returned_type in ("choice", "unknown"):
            with self.subTest(returned_type=returned_type):
                self.assert_response_error(api_response({"answer": {"type": returned_type}}))

    def test_v07_missing_probabilities_are_rejected(self):
        response = self.choice_response()
        del response["answers"]["answer"]["probabilities"]
        self.assert_response_error(
            response,
            instructions="Pick",
            choice=["a", "b"],
        )

    def test_v08_choice_fields_are_validated_independently(self):
        mutations = [
            ("choice", "other"),
            ("probabilities", {"a": -1, "b": 2}),
            ("confidence", 7),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                response = self.choice_response()
                response["answers"]["answer"][field] = value
                self.assert_response_error(
                    response,
                    instructions="Pick",
                    choice=["a", "b"],
                )

    def test_v09_to_v11_noul_wrong_types_and_ranges_are_rejected(self):
        for value in ("yes", 7, True, -0.1, 1.1):
            with self.subTest(value=value):
                self.assert_response_error(
                    api_response({"answer": {"type": "noul", "noul": value}})
                )

    def test_v15_extra_answer_is_rejected(self):
        self.assert_response_error(api_response({
            "answer": {"type": "noul", "noul": 0.5},
            "extra": {"type": "noul", "noul": 0.5},
        }))

    def test_v16_null_answer_is_rejected(self):
        self.assert_response_error(api_response({"answer": None}))

    def test_v17_malformed_json_and_invalid_utf8_are_controlled(self):
        for body in (b"<html>bad</html>", b'\xff{"answers": {}}'):
            with self.subTest(body=body):
                self.assert_response_error(body)

    def test_nonstandard_and_nonfinite_numbers_are_rejected(self):
        bodies = [
            b'{"model":"m","usage":{"input_tokens":1,"output_tokens":1},"answers":{"answer":{"type":"noul","noul":NaN}}}',
            b'{"model":"m","usage":{"input_tokens":1,"output_tokens":1},"answers":{"answer":{"type":"noul","noul":Infinity}}}',
            b'{"model":"m","usage":{"input_tokens":1,"output_tokens":1},"answers":{"answer":{"type":"noul","noul":1e999}}}',
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.assert_response_error(body)

    def test_duplicate_json_keys_are_rejected(self):
        self.assert_response_error(
            b'{"model":"m","model":"other","usage":{"input_tokens":1,"output_tokens":1},"answers":{"answer":{"type":"noul","noul":0.5}}}'
        )

    def test_required_metadata_is_validated_but_extensions_are_allowed(self):
        valid = api_response(
            {"answer": {"type": "noul", "noul": 0.5}},
            future_metadata={"value": True},
        )
        result, _ = call(self.run, valid, input="text", instructions="Question?")
        self.assertEqual(result, 0.5)

        invalid = [
            {"answers": valid["answers"], "usage": valid["usage"]},
            {**valid, "usage": {"input_tokens": True, "output_tokens": 1}},
            {**valid, "usage": {"input_tokens": 1}},
        ]
        for response in invalid:
            with self.subTest(response=response):
                self.assert_response_error(response)

    def test_choice_requires_exact_normalized_distribution_and_maximum(self):
        invalid_probabilities = [
            {"a": 1.0},
            {"a": 0.5, "b": 0.4},
            {"a": 0.4, "b": 0.6},
            {"a": False, "b": 1.0},
        ]
        for probabilities in invalid_probabilities:
            with self.subTest(probabilities=probabilities):
                response = self.choice_response()
                response["answers"]["answer"]["probabilities"] = probabilities
                self.assert_response_error(
                    response,
                    instructions="Pick",
                    choice=["a", "b"],
                )

    def test_choice_ties_and_probability_rounding_tolerance_are_accepted(self):
        response = self.choice_response()
        response["answers"]["answer"].update({
            "probabilities": {"a": 0.5000004, "b": 0.5},
            "choice": "b",
        })
        result, _ = call(
            self.run,
            response,
            input="text",
            instructions="Pick",
            choice=["a", "b"],
        )
        self.assertEqual(result["choice"], "b")

    def test_score_validates_index_set_legend_and_weighted_value(self):
        mutations = [
            ("probabilities", {"0": 0.1, "1": 0.55, "3": 0.35}),
            ("legend", {"0": "low", "1": "medium"}),
            ("legend", {"0": "low", "1": 7, "2": "high"}),
            ("score", 2.5),
            ("score", True),
            ("score", 1.5),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                response = self.score_response()
                response["answers"]["answer"][field] = value
                self.assert_response_error(
                    response,
                    instructions="Rate",
                    score=["low", "medium", "high"],
                )

    def test_valid_score_preserves_public_output_shape(self):
        result, _ = call(
            self.run,
            self.score_response(),
            input="text",
            instructions="Rate",
            score=["low", "medium", "high"],
        )
        self.assertEqual(result, {
            "score": 1.25,
            "probabilities": [
                {"index": 0, "value": "low", "probability": 0.1},
                {"index": 1, "value": "medium", "probability": 0.55},
                {"index": 2, "value": "high", "probability": 0.35},
            ],
            "confidence": 0.6,
        })


if __name__ == "__main__":
    unittest.main()
