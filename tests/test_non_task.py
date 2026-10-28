import json
import unittest
from unittest.mock import Mock, patch

from api.decode import decode_course_card
from api.base import Chaoxing, SessionManager, StudyResult
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

    def test_native_jobid_without_job_flag_still_identifies_required_work(self):
        page = self.page([{'type': 'workid', 'jobid': 'work-required', 'mid': 'work-mid', 'property': {'module': 'work', 'jobid': 'work-required', '_jobid': 'work-required'}}])
        jobs, info = decode_course_card(page, include_non_task=True)
        self.assertEqual(jobs[0]['jobid'], 'work-required')
        self.assertNotIn('non_task', jobs[0])

    def test_completed_chapter_does_not_rerun_required_work(self):
        client = Chaoxing()
        client.rate_limiter = Mock()
        client.study_emptypage = Mock()
        session = Mock()
        session.get.side_effect = [Mock(status_code=200, text='<input id="cardcount" value="1">'), Mock(status_code=200, text=self.page([
            {'type': 'workid', 'jobid': 'work-required', 'property': {'module': 'work', 'jobid': 'work-required'}},
            {'type': 'workid', 'property': {'module': 'work', '_jobid': 'work-optional'}},
        ]))]
        with patch.object(SessionManager, 'get_session', return_value=session), patch.object(client, '_is_captcha_response', return_value=False):
            jobs, info = client.get_job_list({'courseId': 'course', 'clazzId': 'class', 'cpi': 'member'}, {'id': 'point', 'has_finished': True}, include_non_task=True)
        self.assertEqual([job['jobid'] for job in jobs], ['work-optional'])

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

    def test_non_task_document_previews_without_claiming_completion(self):
        client = Chaoxing()
        session = Mock()
        session.get.return_value = Mock(status_code=200, text='preview', url='https://mooc1.chaoxing.com/ananas/modules/pdf/index.html')
        with patch.object(SessionManager, 'get_session', return_value=session), patch.object(client, '_is_captcha_response', return_value=False):
            result = client.study_document({'courseId': 'course', 'clazzId': 'class'}, {'non_task': True, 'objectid': 'object', 'mid': 'mid'})
        self.assertEqual(result, StudyResult.SKIPPED)
        self.assertEqual(session.get.call_args.args[0], 'https://mooc1.chaoxing.com/ananas/modules/pdf/index.html')

    def test_completed_review_page_does_not_query_or_submit_again(self):
        client = Chaoxing()
        client.tiku = Mock(DISABLE=False)
        session = Mock()
        session.get.return_value = Mock(status_code=200, text='<title>查看已批阅作业</title><span class="testTit_status">已完成</span><form></form>')
        with patch.object(SessionManager, 'get_session', return_value=session):
            result = client._study_work_once(
                {'courseId': 'course', 'clazzId': 'class'},
                {'jobid': 'work-source', 'enc': 'test-enc', 'non_task': True},
                {'knowledgeid': 'point', 'ktoken': 'test-token', 'cpi': 'member'},
            )
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(session.get.call_count, 1)
        client.tiku.query_all.assert_not_called()
        session.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
