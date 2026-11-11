import json
import unittest
from unittest.mock import Mock, patch

from api.decode import decode_course_card
from api.base import Chaoxing, SessionManager, StudyResult, decode_vote_form
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

    def test_required_survey_and_discussion_are_executable_jobs(self):
        cards = [
            {'type': 'vote', 'job': True, 'jobid': 'vote-job', 'jtoken': 'vote-token',
             'property': {'module': 'insertvote', 'id': 'active-1', 'title': 'Survey'}},
            {'job': True, 'jobid': 'bbs-job', 'mid': 'topic-1',
             'property': {'module': 'insertbbs', 'title': 'Discussion', 'replywordnum': '50'}},
        ]
        page = self.page(cards) + (
            '<iframe module="insertbbs" data=\'{"jobid":"bbs-job","detail":"Discuss this topic"}\'></iframe>'
        )
        jobs, _ = decode_course_card(page)
        self.assertEqual([job['type'] for job in jobs], ['vote', 'discussion'])
        self.assertEqual(jobs[0]['activeid'], 'active-1')
        self.assertEqual(jobs[1]['detail'], 'Discuss this topic')
        self.assertEqual(jobs[1]['replywordnum'], '50')

    def test_vote_form_keeps_native_values_and_marks_survey_prompt(self):
        html = '''<form id="formId">
          <div class="StudentTimu"><input name="questionId" value="1"><input name="questionType" value="0">
            <p class="Timup">Choose one</p><div class="TxAnswer"><input type="radio" name="answer1" value="X"><p>X：First</p></div>
            <div class="TxAnswer"><input type="radio" name="answer1" value="Y"><p>Y：Second</p></div></div>
          <div class="StudentTimu"><input name="questionId" value="2"><input name="questionType" value="4">
            <p class="Timup">Explain</p><textarea name="answer2"></textarea></div>
          <input name="activeId" value="99"><input name="doquescount" value="0">
        </form>'''
        questions, payload = decode_vote_form(html)
        self.assertEqual([question['type'] for question in questions], ['single', 'shortanswer'])
        self.assertEqual(questions[0]['option_values'], ['X', 'Y'])
        self.assertEqual(questions[0]['option_items'], ['First', 'Second'])
        self.assertTrue(all(question['purpose'] == 'survey' for question in questions))
        self.assertIn(('activeId', '99'), payload)

    def test_vote_submission_uses_ai_and_confirms_task_point(self):
        client = Chaoxing()
        client.tiku = Mock(DISABLE=False)
        client.tiku.query_all.return_value = ['B', 'A natural first-person response']
        page = Mock(status_code=200, url='https://mobilelearn.chaoxing.com/vote', text='''<form id="formId">
          <div class="StudentTimu"><input name="questionId" value="1"><input name="questionType" value="0">
            <p class="Timup">Preference</p><div class="TxAnswer"><input type="radio" name="answer1" value="A"><p>A：One</p></div>
            <div class="TxAnswer"><input type="radio" name="answer1" value="B"><p>B：Two</p></div></div>
          <div class="StudentTimu"><input name="questionId" value="2"><input name="questionType" value="4">
            <p class="Timup">Why</p><textarea name="answer2"></textarea></div>
          <input name="activeId" value="99"><input name="doquescount" value="0">
        </form>''')
        finished = Mock(status_code=200)
        finished.json.return_value = {'status': True}
        submitted = Mock(status_code=200)
        submitted.json.return_value = {'isresult': 1}
        session = Mock()
        session.get.side_effect = [page, finished]
        session.post.return_value = submitted
        course = {'courseId': 'course', 'clazzId': 'class'}
        job = {'activeid': '99', 'jobid': 'vote-job', 'jtoken': 'token', 'title': 'Survey'}
        info = {'knowledgeid': 'point'}
        with patch.object(SessionManager, 'get_session', return_value=session):
            self.assertEqual(client.study_vote(course, job, info), StudyResult.SUCCESS)
        sent = session.post.call_args.kwargs['data']
        self.assertIn(('answer1', 'B'), sent)
        self.assertIn(('answer2', 'A natural first-person response'), sent)
        self.assertTrue(client.tiku.query_all.call_args.kwargs['force_refresh'])

    def test_discussion_submission_uses_dedicated_prompt_and_minimum_length(self):
        client = Chaoxing()
        client.tiku = Mock(DISABLE=False)
        client.tiku.query_all.return_value = ['My view']
        chapter = Mock(status_code=200, url='https://mooc1.chaoxing.com/chapter',
                       text='<div id="topicMainDiv" data="https://groupweb.chaoxing.com/topic"></div>')
        topic = Mock(status_code=200, text='''<script>window.obj={topic:{"uuid":"topic-id","bbsid":"bbs-id"},course:{},urlToken:'token'};</script>''')
        posted = Mock(status_code=200)
        posted.json.return_value = {'status': True}
        session = Mock()
        session.get.side_effect = [chapter, topic]
        session.post.return_value = posted
        course = {'courseId': 'course', 'clazzId': 'class'}
        job = {'mid': 'mid', 'jobid': 'job', 'title': 'Discuss', 'detail': 'Details', 'replywordnum': 50}
        with patch.object(SessionManager, 'get_session', return_value=session):
            self.assertEqual(client.study_discussion(course, job, {'knowledgeid': 'point'}), StudyResult.SUCCESS)
        question = client.tiku.query_all.call_args.args[0][0]
        self.assertEqual(question['purpose'], 'discussion')
        self.assertEqual(question['minimum_length'], 50)
        self.assertGreaterEqual(len(session.post.call_args.kwargs['data']['topic_content']), 50)

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
