import json
import unittest
from unittest.mock import Mock, patch

from api.base import Chaoxing, SessionManager, StudyResult
from api.book_reading import resolve_book_reading, valid_read_log_response
from api.decode import decode_course_card


class BookReadingTests(unittest.TestCase):
    def responses(self, attribution='12_34_56_signature', link=None):
        preview = Mock(text=f'<script>var from = "{attribution}"; var _fromV2_ = "v2"; var rtag = ""; id = "portal==";</script>')
        portal = Mock(url='https://mooc1.chaoxing.com/mooc-ans/zt/portal/portal==', text=link or '<a href="/mooc-ans/ztnodedetailcontroller/visitnodedetail?courseId=99&amp;knowledgeId=88">Text</a>')
        return [preview, portal]

    def test_resolver_keeps_attribution_and_uses_topic_not_source_chapter(self):
        session = Mock()
        session.get.side_effect = self.responses()
        context = resolve_book_reading(session, {'courseId': '12', 'clazzId': '34'}, {'id': '99', 'enc': 'signature'}, {'knowledgeid': '77'})
        self.assertEqual(context['course_id'], '99')
        self.assertEqual(context['knowledge_id'], '88')
        self.assertEqual(context['_from_'], '12_34_56_signature')
        self.assertEqual(session.get.call_args.kwargs['params']['_fromV2_'], 'v2')

    def test_resolver_rejects_wrong_course_and_external_reader(self):
        for responses in (self.responses(attribution='1_2_wrong'), self.responses(link='<a href="https://evil.example/mooc-ans/ztnodedetailcontroller/visitnodedetail?courseId=99&amp;knowledgeId=88">Text</a>')):
            session = Mock()
            session.get.side_effect = responses
            with self.assertRaises(ValueError):
                resolve_book_reading(session, {'courseId': '12', 'clazzId': '34'}, {'id': '99', 'enc': 'signature'}, {'knowledgeid': '77'})

    def test_discovery_is_opt_in_and_does_not_create_completion_job(self):
        properties = {'module': 'insertreadV2', 'mid': 'book-mid', 'id': '99', 'title': 'Book', '_jobid': 'read-99'}
        page = 'mArg=' + json.dumps({'defaults': {'knowledgeid': '77'}, 'attachments': [{'type': 'read', 'enc': 'signature', 'property': properties}]}) + ';'
        page += '<iframe module="insertreadV2" data=\'' + json.dumps(properties) + '\'></iframe>'
        self.assertNotIn('book_readings', decode_course_card(page)[1])
        jobs, info = decode_course_card(page, include_non_task=True)
        self.assertEqual(jobs, [])
        self.assertEqual(len(info['book_readings']), 1)
        self.assertEqual(info['unsupported_non_task'], [])

    def test_missing_signature_remains_pending(self):
        page = 'mArg=' + json.dumps({'defaults': {'knowledgeid': '77'}, 'attachments': [{'type': 'read', 'property': {'module': 'insertreadV2', 'id': '99', 'title': 'Book'}}]}) + ';'
        jobs, info = decode_course_card(page, include_non_task=True)
        self.assertEqual(info['book_readings'], [])
        self.assertEqual(len(info['unsupported_non_task']), 1)

    def test_readlog_requires_json_acknowledgement(self):
        for payload in ({}, {'status': True}, {'success': 1}):
            self.assertTrue(valid_read_log_response(Mock(status_code=200, json=Mock(return_value=payload))))
        for payload in ({'status': False}, {'success': 'false'}, [], None):
            self.assertFalse(valid_read_log_response(Mock(status_code=200, json=Mock(return_value=payload))))
        self.assertFalse(valid_read_log_response(Mock(status_code=200, json=Mock(side_effect=ValueError))))

    def test_timer_sends_topic_attribution_and_rejects_login_page(self):
        session = Mock()
        session.get.side_effect = [Mock(status_code=200, url='https://mooc1.chaoxing.com/reader', text='courseMainBox /multimedia/readlog'), Mock(status_code=200, json=Mock(return_value={})), Mock(status_code=200, json=Mock(return_value={}))]
        context = {'course_id': '99', 'knowledge_id': '88', '_from_': '12_34_56_signature'}
        with patch.object(SessionManager, 'get_session', return_value=session), patch('api.base.time.sleep'):
            result = Chaoxing().study_read_duration({'courseId': '12'}, {'reading_context': context}, 1)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(session.get.call_args.kwargs['params']['courseid'], '99')
        self.assertEqual(session.get.call_args.kwargs['params']['_from_'], context['_from_'])
        session.reset_mock()
        session.get.side_effect = [Mock(status_code=200, text='<title>登录</title>')]
        with patch.object(SessionManager, 'get_session', return_value=session):
            self.assertEqual(Chaoxing().study_read_duration({'courseId': '12'}, {'reading_context': context}, 1), StudyResult.ERROR)
        self.assertEqual(session.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
