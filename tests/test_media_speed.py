import unittest
from unittest.mock import MagicMock

from api.base import StudyResult
from api.study_runner import process_job


class MediaSpeedTests(unittest.TestCase):
    def _run(self, property_data):
        chaoxing = MagicMock()
        chaoxing.study_video.return_value = StudyResult.SUCCESS
        job = {
            "type": "video",
            "jobid": "job-1",
            "name": "lesson.mp4",
            "property": property_data,
        }
        result = process_job(
            chaoxing,
            {"title": "course"},
            job,
            {},
            speed=2.0,
        )
        return result, chaoxing

    def test_explicit_platform_doublespeed_ban_forces_normal_speed(self):
        result, chaoxing = self._run({"doublespeed": 0})

        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(chaoxing.study_video.call_args.kwargs["_speed"], 1.0)

    def test_missing_doublespeed_flag_preserves_requested_speed(self):
        result, chaoxing = self._run({})

        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(chaoxing.study_video.call_args.kwargs["_speed"], 2.0)


if __name__ == "__main__":
    unittest.main()
