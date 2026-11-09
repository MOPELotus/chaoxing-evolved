import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
