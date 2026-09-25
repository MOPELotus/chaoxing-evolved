from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.answer import DummyTiku, answer_cache_key, normalize_question_title
from api.answer_check import check_answer
from api.base import Chaoxing, SessionManager, StudyResult, map_choice_answer, prepare_submission_answer
from api.decode import decode_questions_info
from api.response_ai import ResponsesAnswerService


class AnswerSafetyTests(unittest.TestCase):
    def test_numeric_content_is_not_an_option_prefix(self):
        for answer, options, expected in [
            ("1.5", ["A. 0.5", "B. 1.5"], "B"),
            ("-1", ["A. 1", "B. -1"], "B"),
            ("2", ["A. 2", "B. 3"], "A"),
            ("1/2", ["A. 12", "B. 1/2"], "B"),
        ]:
            with self.subTest(answer=answer):
                self.assertEqual(map_choice_answer(answer, options), expected)

    def test_ambiguous_or_partial_answers_are_rejected(self):
        options = ["A. alpha", "B. beta", "C. gamma", "D. delta"]
        self.assertEqual(map_choice_answer("AC", options), "")
        self.assertEqual(map_choice_answer("A,Z", options, multiple=True), "")
        self.assertEqual(map_choice_answer("A,unknown", options, multiple=True), "")
        self.assertEqual(map_choice_answer("not necessary", ["A. necessary", "B. not necessary at all"]), "")
        self.assertEqual(map_choice_answer("same", ["A. same", "B. same"]), "")

    def test_judgement_words_are_valid_choice_text(self):
        provider = DummyTiku()
        for answer in ("Yes", "No", "True", "False", "F", "T"):
            self.assertTrue(check_answer(answer, "single", provider))

    def test_native_subjective_editors(self):
        for kind in ("shortanswer", "calculation", "completion"):
            answer, fields = prepare_submission_answer("response", {"type": kind, "answer_fields": ["answerEditor71"]})
            self.assertEqual(answer, "response")
            self.assertEqual(fields, {"answerEditor71": "<p>response</p>"})

    def test_empty_and_incomplete_editors_rejected(self):
        question = {"type": "completion", "answer_fields": ["answerEditor71"]}
        for value in (["<p><br></p>"], [None], ["\u200b"], ["&nbsp;"], [{}], ["", "answer"]):
            with self.subTest(value=value):
                self.assertEqual(prepare_submission_answer(value, question), ("", {}))

    def test_unknown_native_protocol_rejected(self):
        for kind in ("cloze", "reading", "ordering", "unknown", "oral", "composite"):
            self.assertEqual(prepare_submission_answer(["A", "B"], {"type": kind}), ("", {}))

    def test_numeric_stems_preserved(self):
        for title in ("2024 + 1 = ?", "1.5 + 2 = ?", "2024年发生了什么？"):
            self.assertEqual(normalize_question_title(title), title)
        self.assertEqual(normalize_question_title("12、选择答案"), "选择答案")

    def test_cache_includes_option_order_and_material(self):
        original = {"title": "Choose", "type": "single", "options": "A. alpha\nB. beta"}
        reordered = dict(original, options="A. beta\nB. alpha")
        self.assertNotEqual(answer_cache_key(original), answer_cache_key(reordered))
        self.assertNotEqual(answer_cache_key(original), answer_cache_key(dict(original, material="other")))
        provider = DummyTiku()
        provider.DISABLE = False
        with patch("api.answer.CacheDAO") as cache:
            cache.return_value.get_cache.side_effect = lambda key: {original["title"]: "A", answer_cache_key(original): "A"}.get(key)
            with patch.object(provider, "_query_all", return_value=["B"]) as backend:
                self.assertEqual(provider.query_all([reordered]), ["B"])
                backend.assert_called_once()

    def run_work(self, kind, answers, *, manual=False, submit=True, controls=None):
        if controls is None:
            controls = '<textarea name="answerEditor71"></textarea>'
        page = f'<form><div class="singleQuesId" data="71"><div class="TiMu" data="{kind}"><div class="Zy_TItle">Question</div>{controls}<input name="answertype71" value="{kind}"></div></div></form>'
        session = Mock()
        session.get.side_effect = [
            Mock(status_code=200, text=page, url="https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew"),
            Mock(status_code=200, json=lambda: {"status": 2}),
        ]
        session.post.return_value = Mock(status_code=200, json=lambda: {"status": True, "msg": "mock"})
        provider = SimpleNamespace(
            DISABLE=False, is_manual=manual, COVER_RATE=0.8, true_list=[], false_list=[],
            query_all=lambda *args, **kwargs: answers, get_submit_params=lambda: "" if submit else "1",
        )
        with patch.object(SessionManager, "get_session", return_value=session):
            result = Chaoxing(tiku=provider).study_work(
                {"courseId": "course", "clazzId": "class"},
                {"jobid": "work-demo", "enc": "fake"},
                {"knowledgeid": "knowledge", "ktoken": "fake", "cpi": "fake"},
            )
        return result, session

    def test_invalid_answers_never_post_even_in_manual_or_save_mode(self):
        for kind, answer in [("2", ["<p><br></p>"]), ("2", None), ("14", ["A", "B"]), ("3", "unclear"), ("4", " ")]:
            for manual, submit in ((False, True), (True, True), (False, False)):
                with self.subTest(kind=kind, answer=answer, manual=manual, submit=submit):
                    result, session = self.run_work(kind, [answer], manual=manual, submit=submit)
                    self.assertEqual(result, StudyResult.ERROR)
                    session.post.assert_not_called()

    def test_subjective_submission_uses_native_field(self):
        result, session = self.run_work("4", ["response"])
        self.assertEqual(result, StudyResult.SUCCESS)
        payload = session.post.call_args.kwargs["data"]
        self.assertEqual(payload["answerEditor71"], "<p>response</p>")
        self.assertNotIn("answer71", payload)

    def test_mismatched_batch_never_posts(self):
        for answers in ([], ["response", "extra"], {"answer": "response"}):
            result, session = self.run_work("4", answers)
            self.assertEqual(result, StudyResult.ERROR)
            session.post.assert_not_called()

    def test_provider_batch_mismatch_discards_entire_batch(self):
        provider = DummyTiku()
        provider.DISABLE = False
        with patch("api.answer.CacheDAO") as cache:
            cache.return_value.get_cache.return_value = None
            with patch.object(provider, "_query_all", return_value=["A", "B"]):
                self.assertEqual(provider.query_all([{"title": "Choose", "type": "single"}]), [None])
            cache.return_value.add_cache.assert_not_called()

    def test_unstructured_text_is_not_split_into_arbitrary_blanks(self):
        question = {"type": "completion", "answer_fields": ["answerEditor71", "answerEditor72"]}
        self.assertEqual(prepare_submission_answer("北京", question), ("", {}))

    def test_invalid_native_choice_values_never_post(self):
        controls = '<ul><li><input type="radio" value="invalid">A. alpha</li><li>B. beta</li></ul>'
        result, session = self.run_work("0", ["A"], controls=controls)
        self.assertEqual(result, StudyResult.ERROR)
        session.post.assert_not_called()

    def test_explicit_save_mode_is_preserved(self):
        result, session = self.run_work("4", ["response"], submit=False)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(session.post.call_args.kwargs["data"]["pyFlag"], "1")

    def test_native_choice_value_is_preserved(self):
        controls = '<ul><li><input type="radio" value="D">A. alpha</li><li><input type="radio" value="A">B. beta</li></ul>'
        result, session = self.run_work("0", ["A"], controls=controls)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(session.post.call_args.kwargs["data"]["answer71"], "D")

    def test_native_multiple_span_values_are_preserved(self):
        controls = '<ul><li><span class="num_option_dx" data="D">A.</span>alpha</li><li><span class="num_option_dx" data="A">B.</span>beta</li><li><span class="num_option_dx" data="B">C.</span>gamma</li></ul>'
        result, session = self.run_work("1", ["AC"], controls=controls)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(session.post.call_args.kwargs["data"]["answer71"], "DB")

    def test_native_span_choice_value_is_preserved(self):
        controls = '<ul><li><span class="num_option" data="D">A.</span>alpha</li><li><span class="num_option" data="A">B.</span>beta</li></ul>'
        result, session = self.run_work("0", ["A"], controls=controls)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(session.post.call_args.kwargs["data"]["answer71"], "D")

    def test_choice_request_preserves_bare_decimal(self):
        service = ResponsesAnswerService({"base_url": "https://example.invalid", "api_key": "fake", "model": "fake"})
        request = service.build_request({"type": "single", "title": "Choose", "options": ["0.5", "1.5"]})
        content = request["input"][0]["content"][0]["text"]
        self.assertIn("A. 0.5", content)
        self.assertIn("B. 1.5", content)

    def test_subjective_type_codes(self):
        for kind in ("7", "8", "10"):
            question = decode_questions_info(f'<form><div class="singleQuesId" data="71"><div class="TiMu" data="{kind}"></div></div></form>')["questions"][0]
            self.assertEqual(question["type"], "shortanswer")

    def test_live_choice_markup_excludes_label_from_content(self):
        page = '<form><div class="singleQuesId" data="71"><div class="TiMu" data="0"><div class="Zy_TItle">1\n\n【单选题】Choose</div><ul><li><label class="before"><span class="num_option" data="A">A</span></label><a class="after"><p>True</p></a></li><li><label class="before"><span class="num_option" data="B">B</span></label><a class="after"><p>False</p></a></li></ul></div></div></form>'
        question = decode_questions_info(page)["questions"][0]
        self.assertEqual(question["option_items"], ["True", "False"])
        self.assertEqual(map_choice_answer("True", question["option_items"]), "A")
        self.assertEqual(normalize_question_title(question["title"]), "【单选题】Choose")

    def test_chat_completions_preserves_image_blocks(self):
        service = ResponsesAnswerService({"base_url": "https://example.invalid", "api_key": "fake", "model": "fake", "protocol": "chat_completions"})
        with patch.object(service, "_media_blocks", return_value=[{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]):
            request = service.build_request({"type": "single", "title": "image question"})
        content = request["messages"][1]["content"]
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,AAAA")

    def test_json_mode_instruction_is_in_user_input(self):
        service = ResponsesAnswerService({"base_url": "https://example.invalid", "api_key": "fake", "model": "fake", "retry_attempts": 0})
        request = service.build_request({"type": "single", "title": "Choose"})
        self.assertIn("JSON", request["input"][0]["content"][0]["text"])
        self.assertEqual(service.retry_attempts, 0)


if __name__ == "__main__":
    unittest.main()
