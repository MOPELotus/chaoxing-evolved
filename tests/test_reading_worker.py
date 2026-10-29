import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from api.base import StudyResult
from api.reading_worker import run_readings


class ReadingWorkerTests(unittest.TestCase):
    def test_resume_uses_acknowledged_seconds_and_retains_unverified_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            path.write_text(json.dumps({'items': {'key': {'acknowledged_seconds': 5}}}))
            client = Mock()
            client.get_book_reading_statistics.return_value = {'reported_minutes': 0, 'next_day_update': True}

            def read(course, book, seconds, progress_callback):
                self.assertEqual(seconds, 5)
                progress_callback(5)
                return StudyResult.SUCCESS

            client.study_book_read_duration.side_effect = read
            targets = {'key': ({'title': 'Course'}, {'title': 'Book'})}
            run_readings(client, targets, [], 10, path)
            item = json.loads(path.read_text())['items']['key']
            self.assertEqual(item['acknowledged_seconds'], 10)
            self.assertEqual(item['status'], 'logs_sent_unverified')
            run_readings(client, targets, [], 10, path)
            self.assertEqual(client.study_book_read_duration.call_count, 1)

    def test_scan_only_never_sends_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            client = Mock()
            run_readings(client, {'key': ({'title': 'Course'}, {'title': 'Book'})}, [], 10, path, scan_only=True)
            client.study_book_read_duration.assert_not_called()

    def test_failure_preserves_progress_and_continues_next_book(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            client = Mock()
            client.study_book_read_duration.side_effect = [StudyResult.ERROR, StudyResult.SUCCESS]
            client.get_book_reading_statistics.return_value = {'reported_minutes': 0, 'next_day_update': True}
            targets = {key: ({'title': 'Course'}, {'title': key}) for key in ['first', 'second']}
            with self.assertRaises(RuntimeError):
                run_readings(client, targets, [], 10, path)
            self.assertEqual(client.study_book_read_duration.call_count, 2)
            self.assertEqual(json.loads(path.read_text())['items']['first']['acknowledged_seconds'], 0)

    def test_existing_platform_credit_is_skipped_and_partial_credit_is_topped_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            path.write_text(json.dumps({'items': {'partial': {'acknowledged_seconds': 2}}}))
            client = Mock()
            client.get_book_reading_statistics.side_effect = [
                {'reported_minutes': 1, 'next_day_update': True},
                {'reported_minutes': 0.1, 'next_day_update': True},
            ]

            def read(course, book, seconds, progress_callback):
                self.assertEqual(seconds, 2)
                progress_callback(seconds)
                return StudyResult.SUCCESS

            client.study_book_read_duration.side_effect = read
            targets = {key: ({'title': 'Course'}, {'title': key}) for key in ['complete', 'partial']}
            run_readings(client, targets, [], 10, path)
            self.assertEqual(client.study_book_read_duration.call_count, 1)
            items = json.loads(path.read_text())['items']
            self.assertEqual(items['complete']['status'], 'platform_target_already_met')
            self.assertEqual(items['complete']['acknowledged_seconds'], 0)
            self.assertEqual(items['partial']['baseline_reported_seconds'], 6)
            self.assertEqual(items['partial']['acknowledged_seconds'], 4)

    def test_missing_platform_statistics_does_not_start_blind_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            client = Mock()
            client.get_book_reading_statistics.return_value = {'reported_minutes': None}
            with self.assertRaises(RuntimeError):
                run_readings(client, {'key': ({'title': 'Course'}, {'title': 'Book'})}, [], 10, path)
            client.study_book_read_duration.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['items']['key']['status'], 'planning_failed')

    def test_resume_does_not_add_new_day_statistics_to_existing_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            path.write_text(json.dumps({'items': {'key': {'acknowledged_seconds': 2, 'baseline_reported_seconds': 6}}}))
            client = Mock()
            client.study_book_read_duration.return_value = StudyResult.SUCCESS
            run_readings(client, {'key': ({'title': 'Course'}, {'title': 'Book'})}, [], 10, path)
            self.assertEqual(client.study_book_read_duration.call_args.args[2], 2)
            client.get_book_reading_statistics.assert_not_called()


if __name__ == '__main__':
    unittest.main()
