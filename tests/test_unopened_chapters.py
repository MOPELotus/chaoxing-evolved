import unittest
from queue import ShutDown
from unittest.mock import Mock, patch

from api.study_runner import ChapterResult, ChapterTask, JobProcessor


class UnopenedChapterTests(unittest.TestCase):
    def test_closed_chapter_retries_are_bounded_and_report_failure(self):
        for action, result, expected in (
            ('retry', ChapterResult.NOT_OPEN, 5),
            ('continue', ChapterResult.NOT_OPEN, 1),
            ('retry', ChapterResult.ERROR, 5),
            ('retry', ChapterResult.SUCCESS, 1),
        ):
            with self.subTest(action=action, result=result):
                task = ChapterTask(0, {'title': 'closed chapter'})
                processor = JobProcessor(Mock(), {'title': 'course'}, [task],
                                         {'speed': 1, 'jobs': 1, 'notopen_action': action})
                # Drive the real worker and its retry routing synchronously.
                # Fail fast if it tries to schedule an unbounded sixth attempt.
                processor.task_queue = Mock()
                processor.retry_queue = Mock()
                pending = [task]

                def get():
                    if not pending:
                        raise ShutDown
                    self.assertLess(process.call_count, expected)
                    return pending.pop()

                processor.task_queue.get.side_effect = get
                processor.retry_queue.put.side_effect = pending.append
                with patch('api.study_runner.process_chapter', return_value=result) as process:
                    processor.worker_thread()
                self.assertEqual(process.call_count, expected)
                processor.task_queue.task_done.assert_called_once()
                failed = action == 'retry' and result != ChapterResult.SUCCESS
                self.assertEqual(processor.failed_tasks, [task] if failed else [])
                self.assertEqual(task.tries, 5 if failed else 0)
