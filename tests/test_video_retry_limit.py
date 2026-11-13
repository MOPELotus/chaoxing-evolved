import unittest
from unittest.mock import MagicMock, patch

from api.base import Chaoxing, StudyResult


class VideoRetryLimitTests(unittest.TestCase):
    def test_full_video_without_platform_pass_is_bounded(self):
        chaoxing = object.__new__(Chaoxing)
        session = MagicMock()
        session.get.return_value.json.return_value = {
            "status": "success",
            "dtoken": "token",
            "crc": "crc",
            "key": "key",
            "duration": 100,
        }
        chaoxing.get_fid = MagicMock(return_value="1")
        chaoxing.video_progress_log = MagicMock(return_value=(False, 200))

        job = {
            "objectid": "object",
            "playTime": 100_000,
            "name": "stuck.mp4",
        }

        with (
            patch("api.base.SessionManager.get_session", return_value=session),
            patch("api.base.time.sleep"),
            patch("api.base.tqdm"),
        ):
            result = chaoxing.study_video({}, job, {}, _speed=2.0)

        self.assertEqual(result, StudyResult.ERROR)
        # One instant-completion attempt plus thirty bounded final reports.
        self.assertEqual(chaoxing.video_progress_log.call_count, 31)


if __name__ == "__main__":
    unittest.main()
