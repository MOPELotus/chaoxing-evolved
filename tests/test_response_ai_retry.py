from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import httpx

from api.answer import AI
from api.response_ai import ResponsesAnswerService


class ResponseAIRetryTests(unittest.TestCase):
    def service(self, **config):
        return ResponsesAnswerService({
            "base_url": "https://example.invalid", "api_key": "test-key", "model": "test-model",
            **config,
        })

    def response(self, status=200, answer="A", headers=None, body=None):
        if body is None:
            body = {"output_text": json.dumps({"answer": answer, "confidence": 1})}
        return httpx.Response(status, json=body, headers=headers, request=httpx.Request("POST", "https://example.invalid/v1/responses"))

    def run_answers(self, service, replies, questions=None):
        with patch("api.response_ai.httpx.Client") as client_class, patch("api.response_ai.time.sleep") as sleep:
            post = client_class.return_value.__enter__.return_value.post
            post.side_effect = replies
            answers = [service.answer(question) for question in questions or [{"type": "single", "title": "Choose"}]]
        return answers, post, sleep

    def test_transient_statuses_retry_current_question(self):
        for status in (408, 409, 425, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                answers, post, sleep = self.run_answers(self.service(), [self.response(status), self.response()])
                self.assertEqual(answers, ["A"])
                self.assertEqual(post.call_count, 2)
                sleep.assert_called_once_with(2.0)

    def test_permanent_statuses_do_not_retry(self):
        for status in (400, 401, 403, 404, 422):
            with self.subTest(status=status):
                answers, post, sleep = self.run_answers(self.service(), [self.response(status)])
                self.assertEqual(answers, [None])
                self.assertEqual(post.call_count, 1)
                sleep.assert_not_called()

    def test_network_errors_retry(self):
        for error in (httpx.ReadTimeout("timeout"), httpx.ConnectError("connection"), httpx.RemoteProtocolError("disconnected")):
            with self.subTest(error=type(error).__name__):
                answers, post, sleep = self.run_answers(self.service(), [error, self.response()])
                self.assertEqual(answers, ["A"])
                self.assertEqual(post.call_count, 2)

    def test_empty_answers_retry(self):
        for answer in (None, "", " ", [], [""], [None], {}, {"text": " "}):
            with self.subTest(answer=answer):
                answers, post, sleep = self.run_answers(self.service(), [self.response(answer=answer), self.response()])
                self.assertEqual(answers, ["A"])
                self.assertEqual(post.call_count, 2)

    def test_malformed_and_incomplete_responses_retry(self):
        for body in ([], {"choices": [None]}, {"choices": [{"message": None}]}, {"output_text": "invalid JSON"}, {"output_text": "{}"}, {"error": {"message": "upstream failed"}}, {"status": "incomplete", "output_text": '{"answer":"A"}'}):
            with self.subTest(body=body):
                answers, post, sleep = self.run_answers(self.service(), [self.response(body=body), self.response()])
                self.assertEqual(answers, ["A"])
                self.assertEqual(post.call_count, 2)

    def test_exhaustion_is_bounded_and_returns_no_answer(self):
        answers, post, sleep = self.run_answers(self.service(), [self.response(503) for attempt in range(5)])
        self.assertEqual(answers, [None])
        self.assertEqual(post.call_count, 5)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4, 8, 16])

    def test_zero_retries_is_respected(self):
        answers, post, sleep = self.run_answers(self.service(retry_attempts=0), [self.response(503)])
        self.assertEqual(answers, [None])
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_retry_after_header_is_respected(self):
        answers, post, sleep = self.run_answers(self.service(), [self.response(429, headers={"Retry-After": "7"}), self.response()])
        self.assertEqual(answers, ["A"])
        sleep.assert_called_once_with(7.0)

    def test_invalid_retry_after_falls_back_to_backoff(self):
        for header in ("invalid", "NaN", "-10"):
            with self.subTest(header=header):
                answers, post, sleep = self.run_answers(self.service(), [self.response(503, headers={"Retry-After": header}), self.response()])
                self.assertEqual(answers, ["A"])
                sleep.assert_called_once_with(2.0)

    def test_successful_batch_items_are_not_repeated(self):
        service = self.service()
        provider = AI()
        provider._response_service = service
        questions = [{"type": "single", "title": "first"}, {"type": "single", "title": "second"}]
        with patch("api.response_ai.httpx.Client") as client_class, patch("api.response_ai.time.sleep"):
            post = client_class.return_value.__enter__.return_value.post
            post.side_effect = [self.response(answer="A"), self.response(503), self.response(answer="B")]
            self.assertEqual(provider._query_all(questions), ["A", "B"])
        titles = [json.loads(call.kwargs["json"]["input"][0]["content"][0]["text"])["question"]["title"] for call in post.call_args_list]
        self.assertEqual(titles, ["first", "second", "second"])

    def test_failed_attempts_are_not_cached(self):
        cache = Mock()
        cache.get_cache.return_value = None
        service = self.service(semantic_cache_enabled=True, retry_attempts=1)
        service.cache = cache
        self.run_answers(service, [self.response(503), self.response(503)])
        cache.add_cache.assert_not_called()
        self.run_answers(service, [self.response(503), self.response()])
        cache.add_cache.assert_called_once()


if __name__ == "__main__":
    unittest.main()
