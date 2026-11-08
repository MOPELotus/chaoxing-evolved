import unittest
from unittest.mock import Mock, patch

from api.study_runner import (
    ChapterResult, ChapterTask, JobProcessor, _normalize_common_config,
)


class ChapterRetryLimitTests(unittest.TestCase):
    def run_processor(self, results, limit=5, challenge=False):
        task = ChapterTask(index=0, point={"id": "1", "title": "chapter", "challenge": challenge})
        processor = JobProcessor(Mock(), {}, [task], {
            "speed": 1, "jobs": 1, "notopen_action": "retry", "chapter_retry_limit": limit,
        })
        processor.retry_delay_seconds = 0
        with patch("api.study_runner.process_chapter", side_effect=results) as execute, patch("api.study_runner.time.sleep"):
            try:
                processor.run()
            finally:
                processor.retry_queue.shutdown()
        return processor, task, execute.call_count

    def test_zero_is_preserved_and_default_remains_five(self):
        self.assertEqual(_normalize_common_config({})["chapter_retry_limit"], 5)
        self.assertEqual(_normalize_common_config({"chapter_retry_limit": 0})["chapter_retry_limit"], 0)

    def test_default_still_stops_after_five_failures(self):
        processor, task, calls = self.run_processor([ChapterResult.ERROR] * 5 + [ChapterResult.SUCCESS])
        self.assertEqual(calls, 5)
        self.assertEqual(processor.failed_tasks, [task])

    def test_unlimited_can_recover_after_more_than_five_failures(self):
        for challenge in [False, True]:
            with self.subTest(challenge=challenge):
                processor, task, calls = self.run_processor(
                    [ChapterResult.ERROR] * 6 + [ChapterResult.SUCCESS], limit=0, challenge=challenge)
                self.assertEqual(calls, 7)
                self.assertEqual(task.result, ChapterResult.SUCCESS)
                self.assertEqual(processor.failed_tasks, [])

    def test_unlimited_does_not_retry_fatal_blocks(self):
        processor, task, calls = self.run_processor([ChapterResult.FATAL], limit=0)
        self.assertEqual(calls, 1)
        self.assertTrue(processor.abort_event.is_set())
        self.assertEqual(processor.failed_tasks, [task])

    def test_unopened_chapters_remain_bounded(self):
        processor, task, calls = self.run_processor([ChapterResult.NOT_OPEN] * 6, limit=0)
        self.assertEqual(calls, 5)
        self.assertEqual(processor.failed_tasks, [task])
