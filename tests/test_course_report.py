from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from tqdm import tqdm

from api.course_report import log_course_report, summarize_course_points
from api.study_runner import process_course, run_loaded_profile


class CourseReportTests(unittest.TestCase):
    def setUp(self):
        self.courses = [
            {"title": "课程甲", "courseId": "course", "clazzId": "class1", "cpi": "user"},
            {"title": "课程甲", "courseId": "course", "clazzId": "class2", "cpi": "user"},
        ]
        self.client = Mock()

    def report(self, responses, outcomes=None):
        self.client.get_course_point.side_effect = responses
        lines = []
        def capture(template, *args):
            lines.append(template.format(*args))
        with patch("api.course_report.logger") as logger:
            logger.info.side_effect = capture
            logger.warning.side_effect = capture
            log_course_report(self.client, self.courses, outcomes or ["执行结束", "执行结束"], 12.3)
        return "\n".join(lines)

    def test_counts_platform_confirmation_not_skipped_or_job_count(self):
        progress = summarize_course_points({"points": [
            {"has_finished": True, "jobCount": 8},
            {"has_finished": False, "title": "过期作业", "is_expired": True},
            {"has_finished": False, "title": "未解锁", "need_unlock": True},
            {"has_finished": "false"},
        ]})
        self.assertEqual((progress.completed, progress.total, progress.remaining, progress.unknown), (1, 4, 2, 1))
        self.assertEqual((progress.expired, progress.locked, progress.percentage), (1, 1, "25.0%"))
        self.assertEqual(progress.unfinished_titles, ("过期作业", "未解锁"))

    def test_same_title_classes_stay_separate_and_overall_is_weighted(self):
        output = self.report([
            {"points": [{"has_finished": True}]},
            {"points": [{"has_finished": True}, {"has_finished": False}, {"has_finished": False}]},
        ])
        self.assertIn("课程甲 [班级 class1] | 知识点 1/1 | 完成率 100.0%", output)
        self.assertIn("课程甲 [班级 class2] | 知识点 1/3 | 完成率 33.3%", output)
        self.assertIn("课程确认全部完成 1/2", output)
        self.assertIn("已获取知识点 2/4 | 完成率 50.0%", output)
        self.assertEqual(self.client.get_course_point.call_count, 2)

    def test_read_failure_does_not_hide_other_courses(self):
        output = self.report([RuntimeError("network"), {"points": [{"has_finished": False}]}], ["执行失败", "未执行"])
        self.assertIn("知识点 --/-- | 完成率 -- | 执行失败 | 状态读取失败：RuntimeError", output)
        self.assertIn("知识点 0/1 | 完成率 0.0% | 未完成 1 | 状态未知 0 | 未执行", output)
        self.assertIn("状态读取成功 1/2", output)
        self.assertIn("不能代表所有课程的最终完成率", output)

    def test_empty_or_invalid_data_is_not_complete(self):
        output = self.report([{"points": []}, {}])
        self.assertIn("知识点 0/0 | 完成率 --", output)
        self.assertIn("课程确认全部完成 0/2", output)
        self.assertNotIn("100.0%", output)
        for data in (None, {}, {"points": None}, {"points": [None]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                summarize_course_points(data)

    def test_unknown_state_is_not_assumed_finished(self):
        output = self.report([{"points": [{}]}, {"points": [{"has_finished": True}]}])
        self.assertIn("未完成 0 | 状态未知 1", output)
        self.assertIn("存在缺失或未知状态", output)

    def test_unfinished_titles_are_bounded(self):
        output = self.report([
            {"points": [{"title": f"章节{index}", "has_finished": False} for index in range(8)]},
            {"points": [{"has_finished": True}]},
        ])
        self.assertIn("章节0、章节1、章节2、章节3、章节4 等 8 个", output)
        self.assertNotIn("章节5", output)

    def runner_context(self, process_effect=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.client.login.return_value = {"status": True}
        self.client.get_course_list.return_value = self.courses
        stack.enter_context(patch("api.study_runner.build_runner_config", return_value=({"speed": 1, "course_list": []}, {}, {}, {"name": "test"})))
        stack.enter_context(patch("api.study_runner.configure_profile_runtime"))
        stack.enter_context(patch("api.study_runner.init_chaoxing", return_value=self.client))
        process = stack.enter_context(patch("api.study_runner.process_course", side_effect=process_effect))
        report = stack.enter_context(patch("api.study_runner.log_course_report"))
        return process, report

    def test_runner_reports_all_courses_after_success(self):
        process, report = self.runner_context()
        run_loaded_profile({})
        self.assertEqual(process.call_count, 2)
        report.assert_called_once()
        self.assertEqual(report.call_args.args[2], ["执行结束", "执行结束"])

    def test_runner_failure_continues_next_course_and_preserves_cause(self):
        error = RuntimeError("original failure")
        process, report = self.runner_context([error, None])
        with self.assertRaises(RuntimeError) as raised:
            run_loaded_profile({})
        self.assertIs(raised.exception.__cause__, error)
        self.assertEqual(process.call_count, 2)
        self.assertIn("其中 1 门执行失败", str(raised.exception))
        self.assertEqual(report.call_args.args[1], self.courses)
        self.assertEqual(report.call_args.args[2], ["执行失败", "执行结束"])

    def test_every_failed_course_is_attempted_and_reported(self):
        process, report = self.runner_context([ValueError("first"), RuntimeError("second")])
        with self.assertRaisesRegex(RuntimeError, "其中 2 门执行失败") as raised:
            run_loaded_profile({})
        self.assertEqual(process.call_count, 2)
        self.assertIn("class1", str(raised.exception))
        self.assertIn("class2", str(raised.exception))
        self.assertEqual(report.call_args.args[2], ["执行失败", "执行失败"])

    def test_keyboard_interrupt_still_reports(self):
        process, report = self.runner_context(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            run_loaded_profile({})
        self.assertEqual(process.call_count, 1)
        self.assertEqual(report.call_args.args[2], ["已中断", "未执行"])

    def test_report_error_does_not_replace_original_error(self):
        error = RuntimeError("original failure")
        process, report = self.runner_context(error)
        report.side_effect = ValueError("report failure")
        with self.assertRaises(RuntimeError) as raised:
            run_loaded_profile({})
        self.assertIs(raised.exception.__cause__, error)

    def test_system_exit_does_not_start_next_course(self):
        process, report = self.runner_context(SystemExit(2))
        with self.assertRaises(SystemExit):
            run_loaded_profile({})
        self.assertEqual(process.call_count, 1)
        self.assertEqual(report.call_args.args[2], ["已中断", "未执行"])

    def test_previous_course_block_does_not_poison_next_course(self):
        self.client._fatal_task_error = "previous course blocked"
        self.client.get_course_point.return_value = {"points": []}
        with patch("api.study_runner.JobProcessor") as processor:
            processor.return_value.fatal_error = ""
            processor.return_value.failed_tasks = []
            process_course(self.client, self.courses[1], {})
        self.assertEqual(self.client._fatal_task_error, "")
        processor.return_value.run.assert_called_once()

    def test_progress_formatter_restored_on_course_failure(self):
        original = tqdm.format_sizeof
        self.client.get_course_point.return_value = {"points": []}
        with patch("api.study_runner.JobProcessor") as processor:
            processor.return_value.run.side_effect = RuntimeError("failed")
            with self.assertRaises(RuntimeError):
                process_course(self.client, self.courses[0], {})
        self.assertIs(tqdm.format_sizeof, original)


if __name__ == "__main__":
    unittest.main()
