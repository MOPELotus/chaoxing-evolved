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

    def test_replays_rejected_full_progress_when_doublespeed_is_disabled(self):
        chaoxing = object.__new__(Chaoxing)
        session = MagicMock()
        session.get.return_value.json.return_value = {
            "status": "success",
            "dtoken": "token",
            "crc": "crc",
            "key": "key",
            "duration": 1,
        }
        chaoxing.get_fid = MagicMock(return_value="1")
        chaoxing.video_progress_log = MagicMock(side_effect=[(False, 200), (True, 200)])
        clock = iter(range(1000))

        job = {
            "objectid": "object",
            "playTime": 1_000,
            "name": "normal-speed-only.mp4",
            "property": {"doublespeed": 0},
        }

        with (
            patch("api.base.SessionManager.get_session", return_value=session),
            patch("api.base.time.time", side_effect=lambda: next(clock)),
            patch("api.base.time.sleep"),
            patch("api.base.tqdm"),
        ):
            result = chaoxing.study_video({}, job, {}, _speed=1.0)

        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(chaoxing.video_progress_log.call_count, 2)
        # The second report is the valid replay completion, not another stale
        # end-position poll inherited from the rejected accelerated run.
        self.assertEqual(chaoxing.video_progress_log.call_args_list[1].args[6], 1)


if __name__ == "__main__":
    unittest.main()
