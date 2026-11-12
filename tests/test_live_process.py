import unittest
from unittest.mock import Mock, patch

from api.live import Live
from api.live_process import LiveProcessor


class FakeLive:
    name = "测试直播"

    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def get_status(self):
        return {"temp": {"data": {"duration": 60}}}

    def do_finish(self):
        self.calls += 1
        return next(self.results)


class LiveProcessTests(unittest.TestCase):
    def test_stops_after_two_consecutive_save_failures(self):
        live = FakeLive([False, False])
        with patch("api.live_process.time.sleep") as sleep:
            self.assertFalse(LiveProcessor.run_live(live))
        self.assertEqual(live.calls, 2)
        sleep.assert_called_once_with(5)

    def test_continues_after_one_transient_save_failure(self):
        live = FakeLive([False, True])
        with patch("api.live_process.time.sleep") as sleep:
            self.assertTrue(LiveProcessor.run_live(live))
        self.assertEqual(live.calls, 2)
        self.assertEqual([call.args for call in sleep.call_args_list], [(5,), (59,)])

    def test_decodes_legacy_string_wrapped_live_data(self):
        response = Mock()
        response.text = '{"temp":{"data":"{\\"duration\\":120}"}}'
        response.raise_for_status.return_value = None
        with patch("api.live.SessionManager.get_session") as get_session:
            get_session.return_value.get.return_value = response
            live = Live(
                {"property": {"liveId": "live", "_jobid": "job"}},
                {"userid": "user", "clazzId": "class", "knowledgeid": "point"},
                "course",
            )
            status = live.get_status()
        self.assertEqual(status["temp"]["data"], {"duration": 120})

    def test_legacy_empty_data_is_reported_without_submitting(self):
        live = FakeLive([])
        live.get_status = lambda: {
            "status": True,
            "temp": {"data": "", "message": "faile", "status": 0},
        }
        with patch("api.live_process.time.sleep") as sleep:
            self.assertFalse(LiveProcessor.run_live(live))
        self.assertEqual(live.calls, 0)
        sleep.assert_not_called()

    def test_uses_legacy_card_duration_when_status_shape_has_no_duration(self):
        live = FakeLive([True])
        live.attachment = {"property": {"liveDuration": "60"}}
        live.get_status = lambda: {"temp": {"data": {}}}
        with patch("api.live_process.time.sleep") as sleep:
            self.assertTrue(LiveProcessor.run_live(live))
        self.assertEqual(live.calls, 1)
        sleep.assert_called_once_with(59)


if __name__ == "__main__":
    unittest.main()
