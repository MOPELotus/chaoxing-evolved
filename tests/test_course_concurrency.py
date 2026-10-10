import threading
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from api.base import Chaoxing
from api.study_runner import _normalize_common_config, run_loaded_profile


class CourseConcurrencyTests(unittest.TestCase):
    def test_default_preserves_serial_courses(self):
        self.assertEqual(_normalize_common_config({})["course_jobs"], 1)
        self.assertEqual(_normalize_common_config({"course_jobs": 0})["course_jobs"], 1)

    def test_parallel_courses_are_bounded_and_failures_are_isolated(self):
        client = Chaoxing()
        client.login = Mock(return_value={"status": True})
        courses = [{"courseId": str(index), "clazzId": str(index), "cpi": "member", "title": str(index)} for index in range(4)]
        client.get_course_list = Mock(return_value=courses)
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        maximum = 0
        seen = []
        failure = ValueError("one course failed")

        def process(course_client, course, config):
            nonlocal active, maximum
            self.assertIsNot(course_client, client)
            self.assertIs(course_client._captcha_lock, client._captcha_lock)
            self.assertIs(course_client.video_log_limiter, client.video_log_limiter)
            self.assertEqual(config["jobs"], 1)
            with lock:
                active += 1
                maximum = max(maximum, active)
                seen.append(course["courseId"])
            try:
                barrier.wait(timeout=5)
                if course["courseId"] == "0":
                    course_client._fatal_task_error = "blocked"
                    raise failure
                self.assertEqual(course_client._fatal_task_error, "")
            finally:
                with lock:
                    active -= 1

        with ExitStack() as stack:
            stack.enter_context(patch("api.study_runner.build_runner_config", return_value=({"speed": 1, "course_list": [], "course_jobs": 2, "jobs": 1}, {}, {}, {"name": "test"})))
            stack.enter_context(patch("api.study_runner.configure_profile_runtime"))
            stack.enter_context(patch("api.study_runner.init_chaoxing", return_value=client))
            stack.enter_context(patch("api.study_runner.process_course", side_effect=process))
            report = stack.enter_context(patch("api.study_runner.log_course_report"))
            with self.assertRaises(RuntimeError) as raised:
                run_loaded_profile({})
        self.assertIs(raised.exception.__cause__, failure)
        self.assertEqual(maximum, 2)
        self.assertCountEqual(seen, ["0", "1", "2", "3"])
        self.assertEqual(report.call_args.args[2], ["执行失败", "执行结束", "执行结束", "执行结束"])


if __name__ == "__main__":
    unittest.main()
