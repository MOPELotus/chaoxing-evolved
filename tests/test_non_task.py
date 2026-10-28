import json
import unittest
from unittest.mock import Mock

from api.decode import decode_course_card
from api.json_store import DEFAULT_PROFILE
from api.study_runner import ChapterResult, _normalize_common_config, process_chapter


class NonTaskTests(unittest.TestCase):
    def page(self, cards):
        return 'mArg=' + json.dumps({'attachments': cards, 'defaults': {'knowledgeid': 'point'}}) + ';'

    def test_switch_defaults_off_and_normalizes_strings(self):
        self.assertIs(DEFAULT_PROFILE['common']['include_non_task'], False)
        self.assertIs(_normalize_common_config({})['include_non_task'], False)
        self.assertIs(_normalize_common_config({'include_non_task': 'false'})['include_non_task'], False)
        self.assertIs(_normalize_common_config({'include_non_task': 'true'})['include_non_task'], True)

    def test_completed_chapter_only_scanned_when_enabled(self):
        client = Mock()
        client.get_job_list.return_value = ([], {})
        course = {'courseId': 'course', 'clazzId': 'class', 'cpi': 'member'}
        point = {'id': 'point', 'title': 'Chapter', 'has_finished': True}
        self.assertEqual(process_chapter(client, course, point, 1), ChapterResult.SUCCESS)
        client.get_job_list.assert_not_called()
        self.assertEqual(process_chapter(client, course, point, 1, include_non_task=True), ChapterResult.SUCCESS)
        client.get_job_list.assert_called_once_with(course, point, include_non_task=True)

    def test_non_task_work_keeps_identity_and_does_not_change_default(self):
        page = self.page([{'type': 'workid', 'mid': 'work-mid', 'property': {'module': 'work', '_jobid': 'work-source', 'title': 'Quiz'}}])
        self.assertEqual(decode_course_card(page)[0], [])
        jobs, info = decode_course_card(page, include_non_task=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['jobid'], 'work-source')
        self.assertIs(jobs[0]['non_task'], True)

    def test_passed_media_skipped_and_unpassed_non_task_discovered(self):
        cards = [
            {'type': 'video', 'mid': 'passed', 'isPassed': True, 'property': {'module': 'insertvideo'}},
            {'type': 'video', 'mid': 'pending', 'isPassed': False, 'property': {'module': 'insertaudio'}},
            {'type': 'video', 'job': True, 'jobid': 'required', 'mid': 'required', 'property': {'module': 'insertvideo'}},
        ]
        jobs, info = decode_course_card(self.page(cards), include_non_task=True)
        self.assertEqual([job['mid'] for job in jobs], ['pending', 'required'])
        self.assertTrue(jobs[0]['non_task'])
        self.assertNotIn('non_task', jobs[1])

    def test_discussion_without_attachment_is_reported(self):
        page = self.page([]) + '<iframe module="insertbbs" data=\'{"mid":"topic","title":"Discussion"}\'></iframe>'
        jobs, info = decode_course_card(page, include_non_task=True)
        self.assertEqual(jobs, [])
        self.assertEqual(info['unsupported_non_task'], [{'module': 'insertbbs', 'title': 'Discussion'}])


if __name__ == '__main__':
    unittest.main()
