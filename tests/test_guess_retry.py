from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from api.base import Chaoxing, SessionManager, StudyResult
from api.guess_retry import GuessRetryLedger, combination_key, guess_retry_settings, next_guess, work_feedback


def work_page(status="待完成", *, title="Choose", values=("D", "A"), blank=False):
    options = "".join(f'<li><input type="radio" value="{value}">{label}. {text}</li>' for value, label, text in zip(values, ("A", "B"), ("alpha", "beta")))
    extra = '<div class="singleQuesId" data="72"><div class="TiMu" data="2"><div class="Zy_TItle">Fill</div><textarea name="answerEditor721"></textarea></div>' if blank else ""
    return f'<div class="testTit_status">{status}</div><form action="/mooc-ans/work/addStudentWorkNew"><div class="singleQuesId" data="71"><div class="TiMu" data="0"><div class="Zy_TItle">{title}</div><ul>{options}</ul></div>{extra}<button type="submit">提交</button></form>'


def response(text="", body=None):
    return Mock(status_code=200, text=text, url="https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew", json=lambda: body)


class GuessRetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = GuessRetryLedger(Path(self.directory.name) / "retry.json")
        self.session = Mock()
        self.session.post.return_value = response(body={"status": True, "msg": "ok", "backUrl": "/mooc-ans/work/result"})
        self.provider = SimpleNamespace(
            _conf={"guess_retry_enabled": True, "guess_retry_limit": 3},
            DISABLE=False, COVER_RATE=1.0, true_list=[], false_list=[],
            query_all=Mock(return_value=["A"]), get_submit_params=lambda: "",
        )
        self.client = Chaoxing(tiku=self.provider)
        self.course = {"courseId": "course", "clazzId": "class"}
        self.job = {"jobid": "work-demo", "enc": "fake"}
        self.info = {"knowledgeid": "knowledge", "ktoken": "fake", "cpi": "fake"}
        self.retry_page = work_page("未达到及格线，请重做")
        self.complete = '<div class="testTit_status testTit_status_complete">已完成</div>'

    def run_work(self):
        with patch.object(SessionManager, "get_session", return_value=self.session), patch("api.base.GuessRetryLedger", return_value=self.ledger), patch("api.base.time.sleep"):
            return self.client.study_work(self.course, self.job, self.info)

    def attempt(self, page, feedback):
        return [response(page), response(body={"status": 2}), response(feedback)]

    def test_default_off_and_limits(self):
        self.assertEqual(guess_retry_settings({}), (False, 3))
        self.assertEqual(guess_retry_settings({"guess_retry_enabled": "false"}), (False, 3))
        self.assertEqual(guess_retry_settings({"guess_retry_enabled": "true", "guess_retry_limit": 50}), (True, 10))
        self.assertEqual(guess_retry_settings({"guess_retry_enabled": True, "guess_retry_limit": 0}), (False, 0))

    def test_hidden_generic_failure_dialog_does_not_trigger_retry(self):
        self.assertEqual(work_feedback(work_page() + '<div hidden>未达到及格线，请重做</div>'), "unknown")
        self.assertEqual(work_feedback('<div class="testTit_status">未达到及格线，请重做</div>'), "unknown")
        self.assertEqual(work_feedback(self.retry_page), "retryable")

    def test_disabled_submit_is_not_permission_to_retry(self):
        for attribute in ('disabled', 'aria-disabled="true"', 'hidden', 'style="display: none"'):
            with self.subTest(attribute=attribute):
                page = self.retry_page.replace('<button type="submit">', f'<button type="submit" {attribute}>')
                self.assertEqual(work_feedback(page), "unknown")

    def test_candidate_exhaustion_and_uniqueness(self):
        domains = [["A", "B"], ["true", "false"]]
        seen = set()
        for attempt in range(4):
            candidate = next_guess(domains, seen)
            self.assertIsNotNone(candidate)
            signature = combination_key(candidate)
            self.assertNotIn(signature, seen)
            seen.add(signature)
        self.assertIsNone(next_guess(domains, seen))
        self.assertIsNone(next_guess([], set()))

    def test_full_coverage_then_failed_submission_can_guess(self):
        self.session.get.side_effect = self.attempt(work_page(), self.retry_page) + self.attempt(self.retry_page, self.complete)
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.provider.query_all.assert_called_once()
        submitted = [call.kwargs["data"]["answer71"] for call in self.session.post.call_args_list]
        self.assertEqual(submitted, ["D", "A"])
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.assertEqual(self.session.post.call_count, 2)

    def test_missing_answer_never_guessed_or_posted(self):
        self.provider.query_all.return_value = [None]
        self.session.get.return_value = response(work_page())
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_not_called()
        self.assertFalse(self.ledger.path.exists())

    def test_completion_stops_without_guessing(self):
        self.session.get.side_effect = self.attempt(work_page(), self.complete)
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.session.post.assert_called_once()

    def test_judgement_retries_with_other_value(self):
        self.provider.query_all.return_value = ["true"]
        initial = work_page().replace('data="0"', 'data="3"')
        retry = self.retry_page.replace('data="0"', 'data="3"')
        self.session.get.side_effect = self.attempt(initial, retry) + self.attempt(retry, self.complete)
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.assertEqual([call.kwargs["data"]["answer71"] for call in self.session.post.call_args_list], ["true", "false"])

    def test_multiple_choice_is_not_guessed(self):
        initial = work_page().replace('data="0"', 'data="1"')
        retry = self.retry_page.replace('data="0"', 'data="1"')
        self.session.get.side_effect = self.attempt(initial, retry) + [response(retry)]
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_called_once()

    def test_out_of_range_answer_cannot_activate_guessing(self):
        self.provider.query_all.return_value = ["Z"]
        self.session.get.return_value = response(work_page())
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_not_called()

    def test_rejected_submission_is_not_a_wrong_answer_retry(self):
        self.session.get.side_effect = [response(work_page()), response(body={"status": 2})]
        self.session.post.return_value = response(body={"status": False, "msg": "次数已用完"})
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_called_once()

    def test_only_total_score_is_not_permission_to_guess(self):
        self.session.get.side_effect = self.attempt(work_page(), '<div>60分</div>')
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_called_once()

    def test_timeout_after_post_does_not_repeat_on_new_client(self):
        self.session.get.side_effect = [response(work_page()), response(body={"status": 2})]
        self.session.post.side_effect = requests.Timeout()
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.client = Chaoxing(tiku=self.provider)
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_called_once()

    def test_unknown_feedback_blocks_restart(self):
        self.session.get.side_effect = self.attempt(work_page(), "")
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.ledger = GuessRetryLedger(self.ledger.path)
        self.client = Chaoxing(tiku=self.provider)
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_called_once()

    def test_limit_is_extra_attempts_and_not_reset(self):
        self.provider._conf["guess_retry_limit"] = 1
        self.session.get.side_effect = self.attempt(work_page(), self.retry_page) + self.attempt(self.retry_page, self.retry_page)
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.assertEqual(self.session.post.call_count, 2)
        entry = self.ledger.get(self.ledger.work_key(self.course, self.job, self.info))
        self.assertEqual((entry["status"], entry["attempts"]), ("exhausted", 2))

    def test_exhausted_combinations_do_not_repeat(self):
        self.session.get.side_effect = self.attempt(work_page(), self.retry_page) + self.attempt(self.retry_page, self.retry_page) + [response(self.retry_page)]
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.assertEqual(self.session.post.call_count, 2)

    def test_changed_question_stops_guessing(self):
        self.session.get.side_effect = self.attempt(work_page(), self.retry_page) + [response(work_page("未达到及格线，请重做", title="Changed"))]
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_called_once()

    def test_other_question_types_are_preserved(self):
        retry = work_page("未达到及格线，请重做", blank=True)
        self.provider.query_all.return_value = ["A", "filled"]
        self.session.get.side_effect = self.attempt(work_page(blank=True), retry) + self.attempt(retry, self.complete)
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        for call in self.session.post.call_args_list:
            self.assertEqual(call.kwargs["data"]["answerEditor721"], "<p>filled</p>")
        self.provider.query_all.assert_called_once()

    def test_save_mode_does_not_activate_guessing(self):
        self.provider.get_submit_params = lambda: "1"
        self.session.get.side_effect = [response(work_page()), response(body={"status": 2})]
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.assertEqual(self.session.post.call_args.kwargs["data"]["pyFlag"], "1")
        self.assertFalse(self.ledger.path.exists())

    def test_unlabelled_native_values_work_without_guessing(self):
        self.provider._conf["guess_retry_enabled"] = False
        self.session.get.side_effect = self.attempt(work_page(values=("", "")), self.complete)
        self.assertEqual(self.run_work(), StudyResult.SUCCESS)
        self.assertEqual(self.session.post.call_args.kwargs["data"]["answer71"], "A")

    def test_ledger_failure_prevents_submission(self):
        self.session.get.side_effect = [response(work_page()), response(body={"status": 2})]
        with patch.object(self.ledger, "record", side_effect=OSError("disk full")):
            self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_not_called()

    def test_invalid_ledger_does_not_reset_history(self):
        self.ledger.path.write_text("invalid", encoding="utf-8")
        self.assertEqual(self.run_work(), StudyResult.ERROR)
        self.session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
