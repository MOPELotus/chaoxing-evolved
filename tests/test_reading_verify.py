import unittest
from unittest.mock import Mock, patch

from api.reading_verify import verify_readings


class ReadingVerificationTests(unittest.TestCase):
    def test_zero_next_day_statistic_is_not_treated_as_failure_or_completion(self):
        client = Mock()
        progress = {'items': {'key': {'acknowledged_seconds': 9999, 'status': 'logs_sent_unverified'}}}
        with patch('api.reading_verify.resolve_book_reading', return_value={
            'statistics': {'reported_minutes': 0.0, 'next_day_update': True},
        }):
            rows = verify_readings(client, {'key': ({'title': 'Course'}, {'title': 'Book'})}, progress)
        self.assertEqual(rows[0]['reported_minutes'], 0.0)
        self.assertEqual(rows[0]['status'], 'platform_statistics_visible')
        self.assertEqual(rows[0]['local_status'], 'logs_sent_unverified')
        self.assertEqual(progress['items']['key']['acknowledged_seconds'], 9999)
        client.study_book_read_duration.assert_not_called()

    def test_failed_verification_does_not_rewrite_progress(self):
        progress = {'items': {'key': {'acknowledged_seconds': 60}}}
        with patch('api.reading_verify.resolve_book_reading', side_effect=ValueError('invalid')):
            rows = verify_readings(Mock(), {'key': ({'title': 'Course'}, {'title': 'Book'})}, progress)
        self.assertEqual(rows[0]['status'], 'verification_failed')
        self.assertNotIn('reported_minutes', rows[0])
        self.assertEqual(progress['items']['key']['acknowledged_seconds'], 60)


if __name__ == '__main__':
    unittest.main()
