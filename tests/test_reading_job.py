import unittest
from unittest.mock import Mock, patch

from api.base import Chaoxing, SessionManager, StudyResult
from api.decode import _process_attachment_cards
from api.study_runner import ChapterResult, process_chapter


class ReadingJobTests(unittest.TestCase):
    def card(self, **changes):
        return {"type": "read", "job": True, "jobid": "read-demo", "jtoken": "token", "property": {"read": True, "module": "insertreadV2", "title": "Reading"}, **changes}

    def test_read_true_does_not_mean_finished(self):
        jobs = _process_attachment_cards([self.card()])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["jobid"], "read-demo")

    def test_completed_reading_is_not_resubmitted(self):
        for card in (self.card(job=False), self.card(job=None), self.card(isPassed=True)):
            self.assertEqual(_process_attachment_cards([card]), [])
        card = self.card()
        del card["job"]
        self.assertEqual(_process_attachment_cards([card]), [])

    def test_nested_job_keeps_required_tokens(self):
        card = self.card(job={"jobid": "read-nested", "jtoken": "nested"}, jobid="", jtoken="")
        job = _process_attachment_cards([card])[0]
        self.assertEqual((job["jobid"], job["jtoken"]), ("read-nested", "nested"))

    def run_read(self, body, readtime=0):
        session = Mock()
        session.get.return_value = Mock(status_code=200, json=Mock(return_value=body))
        with patch.object(SessionManager, "get_session", return_value=session):
            result = Chaoxing().study_read({"courseId": "course", "clazzId": "class"}, {"jobid": "read-demo", "jtoken": "token", "readtime": readtime}, {"knowledgeid": "point"})
        return result, session

    def test_uses_official_route_and_checks_status(self):
        result, session = self.run_read({"status": True})
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(session.get.call_args.kwargs["url"], "https://mooc1.chaoxing.com/mooc-ans/job/readv2")
        for body in ({"status": False}, {"status": "false"}, {}, [], None):
            result, session = self.run_read(body)
            self.assertEqual(result, StudyResult.ERROR)

    def test_timed_reading_is_not_marked_complete_without_reading(self):
        for duration in (60, "120", "unknown", -1, "nan"):
            result, session = self.run_read({"status": True}, readtime=duration)
            self.assertEqual(result, StudyResult.ERROR)
            session.get.assert_not_called()

    def test_reading_requires_platform_confirmation(self):
        client = Mock()
        point = {"id": "point", "title": "Reading", "has_finished": False}
        client.get_job_list.return_value = ([{"type": "read", "jobid": "read-demo"}], {})
        client.get_course_point.return_value = {"points": [point]}
        course = {"courseId": "course", "clazzId": "class", "cpi": "user"}
        with patch("api.study_runner.process_job", return_value=StudyResult.SUCCESS):
            self.assertEqual(process_chapter(client, course, point, 1), ChapterResult.ERROR)
            client.get_course_point.return_value = {"points": [dict(point, has_finished=True)]}
            self.assertEqual(process_chapter(client, course, point, 1), ChapterResult.SUCCESS)


if __name__ == "__main__":
    unittest.main()
