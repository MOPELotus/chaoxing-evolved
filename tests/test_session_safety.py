import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock, patch

from api.base import Chaoxing, SessionManager
from api.runtime import get_runtime_context


class SessionSafetyTests(unittest.TestCase):
    def test_concurrent_access_initializes_one_session(self):
        previous = SessionManager._instance
        self.addCleanup(setattr, SessionManager, "_instance", previous)
        SessionManager._instance = None
        with patch("api.base.use_cookies", return_value={}), patch("api.base.requests.Session") as factory:
            with ThreadPoolExecutor(max_workers=12) as executor:
                sessions = list(executor.map(lambda index: SessionManager.get_session(), range(100)))
            factory.assert_called_once()
            self.assertTrue(all(session is sessions[0] for session in sessions))

    def test_profile_change_rebuilds_session(self):
        previous = SessionManager._instance
        self.addCleanup(setattr, SessionManager, "_instance", previous)
        SessionManager._instance = None
        context = get_runtime_context()
        with patch("api.base.use_cookies", return_value={}), patch("api.base.requests.Session") as factory:
            SessionManager.get_session()
            with patch("api.base.get_runtime_context", return_value=replace(context, cookies_path=context.cookies_path.with_name("other.cookies.txt"))):
                SessionManager.get_session()
            self.assertEqual(factory.call_count, 2)

    def test_login_and_invalid_pages_are_not_empty_courses(self):
        for url, html in [("https://passport2.chaoxing.com/login", "用户登录"), ("https://mooc2-ans.chaoxing.com/studentcourse", "服务异常")]:
            with self.subTest(url=url):
                session = Mock()
                session.get.return_value = Mock(url=url, text=html)
                with patch.object(SessionManager, "get_session", return_value=session):
                    with self.assertRaises(RuntimeError):
                        Chaoxing().get_course_point("course", "class", "member")

    def test_explicit_empty_course_is_allowed(self):
        session = Mock()
        session.get.return_value = Mock(url="https://mooc2-ans.chaoxing.com/studentcourse", text="暂无章节内容")
        with patch.object(SessionManager, "get_session", return_value=session):
            self.assertEqual(Chaoxing().get_course_point("course", "class", "member")["points"], [])
