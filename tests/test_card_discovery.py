import unittest
from unittest.mock import Mock, patch

from api.base import Chaoxing, SessionManager, StudyResult, chapter_card_count


class CardDiscoveryTests(unittest.TestCase):
    def test_native_card_count(self):
        for count in (0, 1, 7, 9, 12):
            self.assertEqual(chapter_card_count(f'<input id="cardcount" value="{count}">'), count)

    def test_invalid_directory_is_not_an_empty_chapter(self):
        for page in ("Login required", '<input id="cardcount" value="-1">', '<input id="cardcount" value="1001">'):
            self.assertIsNone(chapter_card_count(page))

    def test_ninth_card_is_discovered(self):
        client = Chaoxing()
        client.rate_limiter = Mock()
        client.study_emptypage = Mock()
        session = Mock()
        session.get.return_value = Mock(status_code=200, text='<input id="cardcount" value="9">')
        results = [([], {}) for position in range(8)] + [([{"type": "live", "jobid": "last-card"}], {})]
        with patch.object(SessionManager, "get_session", return_value=session), patch.object(client, "_is_captcha_response", return_value=False), patch("api.base.decode_course_card", side_effect=results):
            jobs, info = client.get_job_list({"courseId": "course", "clazzId": "class", "cpi": "member"}, {"id": "point"})
        self.assertEqual(jobs, [{"type": "live", "jobid": "last-card"}])
        self.assertEqual(session.get.call_count, 10)
        client.study_emptypage.assert_not_called()

    def test_missing_directory_does_not_mark_empty_page(self):
        client = Chaoxing()
        client.rate_limiter = Mock()
        client.study_emptypage = Mock()
        session = Mock()
        session.get.return_value = Mock(status_code=200, text="Login required")
        with patch.object(SessionManager, "get_session", return_value=session):
            jobs, info = client.get_job_list({"courseId": "course", "clazzId": "class", "cpi": "member"}, {"id": "point"})
        self.assertEqual(jobs, [])
        self.assertIn("fatal_error", info)
        client.study_emptypage.assert_not_called()

    def test_explicit_empty_directory(self):
        client = Chaoxing()
        client.rate_limiter = Mock()
        client.study_emptypage = Mock(return_value=StudyResult.SUCCESS)
        session = Mock()
        session.get.return_value = Mock(status_code=200, text='<input id="cardcount" value="0">')
        with patch.object(SessionManager, "get_session", return_value=session):
            self.assertEqual(client.get_job_list({"courseId": "course", "clazzId": "class", "cpi": "member"}, {"id": "point"}), ([], {}))
        self.assertEqual(session.get.call_count, 1)
        client.study_emptypage.assert_called_once()
