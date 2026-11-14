import unittest
from unittest.mock import MagicMock, Mock, patch

from api.base import Chaoxing, SessionManager, StudyResult
from api.decode import decode_course_card
from api.json_store import DEFAULT_PROFILE
from api.study_runner import (
    ChapterResult,
    _normalize_common_config,
    process_chapter,
    process_course,
)


class ReviewModeConfigTests(unittest.TestCase):
    def test_review_mode_defaults_off_and_normalizes_strings(self):
        self.assertIs(DEFAULT_PROFILE["common"]["review_mode"], False)
        self.assertIs(_normalize_common_config({})["review_mode"], False)
        self.assertIs(_normalize_common_config({"review_mode": "true"})["review_mode"], True)


class ReviewModeDiscoveryTests(unittest.TestCase):
    @staticmethod
    def page(cards):
        return "mArg=" + __import__("json").dumps(
            {"attachments": cards, "defaults": {"knowledgeid": "point"}}
        ) + ";"

    def test_review_discovers_passed_media_only(self):
        cards = [
            {
                "type": "video",
                "job": True,
                "jobid": "video-job",
                "mid": "video-mid",
                "isPassed": True,
                "property": {"module": "insertvideo", "name": "lesson.mp4"},
            },
            {
                "type": "workid",
                "job": True,
                "jobid": "work-job",
                "mid": "work-mid",
                "isPassed": False,
                "property": {"module": "work"},
            },
        ]
        normal_jobs, _ = decode_course_card(self.page(cards))
        self.assertEqual([job["jobid"] for job in normal_jobs], ["work-job"])
        jobs, _ = decode_course_card(self.page(cards), review_mode=True)
        self.assertEqual([job["jobid"] for job in jobs], ["video-job"])
        self.assertTrue(jobs[0]["review"])

    def test_completed_chapter_runs_in_review_mode(self):
        client = Mock()
        client.get_job_list.return_value = ([], {})
        course = {"courseId": "course", "clazzId": "class", "cpi": "member"}
        point = {"id": "point", "title": "Chapter", "has_finished": True}
        self.assertEqual(
            process_chapter(client, course, point, 1, review_mode=True),
            ChapterResult.SUCCESS,
        )
        client.get_job_list.assert_called_once_with(course, point, review_mode=True)


class ReviewModePhaseTests(unittest.TestCase):
    def test_normal_phase_precedes_full_review_phase(self):
        client = Mock()
        point = {"id": "point", "title": "Chapter", "has_finished": True}
        client.get_course_point.side_effect = [
            {"points": [point]},
            {"points": [point]},
        ]
        course = {"courseId": "course", "clazzId": "class", "cpi": "member", "title": "Course"}
        configs = []

        class Processor:
            def __init__(self, _client, _course, tasks, config):
                configs.append((dict(config), [task.point["id"] for task in tasks]))
                self.fatal_error = ""
                self.failed_tasks = []

            def run(self):
                return None

        with patch("api.study_runner.JobProcessor", Processor):
            process_course(
                client,
                course,
                {"speed": 2, "jobs": 3, "review_mode": True, "include_non_task": True},
            )

        self.assertEqual(len(configs), 2)
        self.assertNotIn("review_phase", configs[0][0])
        self.assertTrue(configs[1][0]["review_phase"])
        self.assertEqual(configs[1][0]["jobs"], 3)
        self.assertFalse(configs[1][0]["include_non_task"])
        self.assertEqual(configs[0][1], ["point"])
        self.assertEqual(configs[1][1], ["point"])
        self.assertEqual(course["_review_outcome"], "执行结束")


class ReviewModePlaybackTests(unittest.TestCase):
    def test_passed_review_media_replays_from_zero_to_duration(self):
        client = Chaoxing()
        session = MagicMock()
        session.get.return_value.json.return_value = {
            "status": "success",
            "duration": 2,
            "dtoken": "token",
            "crc": "crc",
            "key": "key",
        }
        client.video_progress_log = Mock(return_value=(True, 200))
        job = {
            "review": True,
            "name": "lesson.mp4",
            "objectid": "object",
            "playTime": 2000,
            "property": {},
            "videoFaceCaptureEnc": "",
            "attDuration": "",
            "attDurationEnc": "",
            "rt": "1",
            "otherinfo": "",
            "jobid": "job",
        }
        course = {"cpi": "member"}
        with (
            patch.object(SessionManager, "get_session", return_value=session),
            patch("api.base.time.time", side_effect=range(20)),
            patch("api.base.time.sleep"),
        ):
            result = client.study_video(course, job, {}, _speed=1.0)

        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(client.video_progress_log.call_count, 1)
        self.assertEqual(client.video_progress_log.call_args.args[6], 2)


if __name__ == "__main__":
    unittest.main()
