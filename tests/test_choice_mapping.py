from __future__ import annotations

import json
import unittest

from api.base import (
    assemble_work_submission,
    clean_res,
    map_choice_answer,
    prepare_submission_answer,
    random_answer,
    resolve_work_submit_url,
)


class ChoiceMappingTests(unittest.TestCase):
    def setUp(self):
        self.options = [
            "Some benefit of a working holiday",
            "foo bar is not a benefit",
            "Travel around the country",
            "Meet new people",
        ]

    def test_english_option_initial_is_not_treated_as_choice_letter(self):
        self.assertEqual(clean_res("Some benefit of a working holiday"), ["Some benefit of a working holiday"])

    def test_option_text_maps_by_index_instead_of_first_character(self):
        self.assertEqual(map_choice_answer(self.options[0], self.options), "A")
        self.assertEqual(map_choice_answer(self.options[1], self.options), "B")

    def test_explicit_letters_and_numbers_map_directly(self):
        self.assertEqual(map_choice_answer("B", self.options), "B")
        self.assertEqual(map_choice_answer("2", self.options), "B")
        self.assertEqual(map_choice_answer("A, C", self.options, multiple=True), "AC")

    def test_random_fallback_never_uses_option_text_initials(self):
        answers = {random_answer(self.options, "single") for _ in range(50)}
        self.assertTrue(answers)
        self.assertTrue(answers <= {"A", "B", "C", "D"})
        self.assertNotIn("S", answers)
        self.assertNotIn("f", answers)

    def test_completion_answers_fill_each_native_editor(self):
        answer, fields = prepare_submission_answer(
            ["F", "D", "I"],
            {"type": "completion", "answer_fields": ["answerEditor91", "answerEditor92", "answerEditor93"]},
        )
        self.assertEqual(answer, "FDI")
        self.assertEqual(
            fields,
            {
                "answerEditor91": "<p>F</p>",
                "answerEditor92": "<p>D</p>",
                "answerEditor93": "<p>I</p>",
            },
        )

    def test_compact_completion_answer_is_split_by_blank_count(self):
        answer, fields = prepare_submission_answer(
            "CEB",
            {"type": "completion", "answer_fields": ["answerEditor71", "answerEditor72", "answerEditor73"]},
        )
        self.assertEqual(answer, "CEB")
        self.assertEqual(list(fields.values()), ["<p>C</p>", "<p>E</p>", "<p>B</p>"])

    def test_native_completion_form_omits_synthetic_combined_answer(self):
        form = {
            "pyFlag": "",
            "questions": [{
                "id": "71",
                "answerSource71": "cover",
                "answerField": {"answer71": "CEB", "answertype71": "2"},
                "submission_fields": {
                    "answerEditor711": "<p>C</p>",
                    "answerEditor712": "<p>E</p>",
                    "answerEditor713": "<p>B</p>",
                },
            }],
        }
        payload = assemble_work_submission(form)
        self.assertNotIn("answer71", payload)
        self.assertEqual(payload["answertype71"], "2")
        self.assertEqual(payload["answerEditor712"], "<p>E</p>")

    def test_matching_answer_uses_chaoxing_native_shape(self):
        question = {
            "type": "matching",
            "matching_groups": {
                "left": ["1、Sun Simiao", "2、Bian Que"],
                "right": ["A、pestilential diseases", "B、Great Medical Sincerity"],
            },
        }
        answer, fields = prepare_submission_answer(
            {"pairs": [
                {"left": "1、Sun Simiao", "right": "B、Great Medical Sincerity"},
                {"left": "2", "right": "A"},
            ]},
            question,
        )
        self.assertEqual(fields, {})
        self.assertEqual(
            json.loads(answer),
            [{"name": "1", "content": "B"}, {"name": "2", "content": "A"}],
        )

    def test_current_work_form_action_and_token_are_used(self):
        html = '<form action="addStudentWorkNewWeb?token=fresh-token&amp;workid=7"></form>'
        self.assertEqual(
            resolve_work_submit_url(
                html,
                "https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew?workid=7",
            ),
            "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNewWeb?token=fresh-token&workid=7",
        )


if __name__ == "__main__":
    unittest.main()
