import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from web_dashboard.service import (
    AssessmentRateLimitError,
    DashboardService,
    accepted_course_gap,
    assessment_response_is_limited,
    normalize_status,
    parse_assessment_html,
    parse_school,
    parse_task_cards,
)


class DashboardParsingTests(unittest.TestCase):
    def test_detects_assessment_verification_redirects(self):
        response = type(
            "Response",
            (),
            {"url": "https://mooc1.chaoxing.com/antispiderShowVerify.ac", "status_code": 404, "text": ""},
        )()
        self.assertTrue(assessment_response_is_limited(response))

    def test_retries_rate_limited_assessment_reads_after_backoff(self):
        with TemporaryDirectory() as directory:
            service = DashboardService(Path(directory))
            with (
                patch.object(
                    service,
                    "_assessment_course_once",
                    side_effect=[AssessmentRateLimitError("limited"), [{"title": "done"}]],
                ) as read,
                patch("web_dashboard.service.time.sleep") as sleep,
            ):
                result = service._assessment_course(object(), {"title": "Course"}, "homework")
            self.assertEqual(result, [{"title": "done"}])
            self.assertEqual(read.call_count, 2)
            sleep.assert_called_once_with(30.0)

    def test_accepts_only_the_three_known_management_gaps(self):
        title = "\u7ba1\u7406\u5b66\u539f\u7406\uff08\u4ea4\u53c9\u901a\u8bc6\u8bfe\uff09"
        self.assertEqual(accepted_course_gap(title, 26, 29), 3)
        self.assertEqual(accepted_course_gap(title, 25, 29), 0)
        self.assertEqual(accepted_course_gap("\u5176\u4ed6\u8bfe\u7a0b", 26, 29), 0)

    def test_accepts_liuxing_review_mode_gaps_only_for_his_account(self):
        title = "\u82f1\u8bed\u542c\u8bf42\uff082026\u5e74\u6625\u5b66\u671f\uff09"
        self.assertEqual(accepted_course_gap(title, 76, 97, "17536308868"), 21)
        self.assertEqual(accepted_course_gap(title, 76, 97, "15525142626"), 0)
        self.assertEqual(accepted_course_gap(title, 75, 97, "17536308868"), 0)

    def test_normalize_assessment_states(self):
        self.assertEqual(normalize_status("已完成"), "completed")
        self.assertEqual(normalize_status("已提交，待批阅"), "pending_review")
        self.assertEqual(normalize_status("未交"), "incomplete")
        self.assertEqual(normalize_status("已过期"), "expired")

    def test_parse_assessment_rows(self):
        html = """
        <div class="bottomList"><ul>
          <li><p class="overHidden2">第一章作业</p><p class="status">已完成</p><div class="time">剩余10小时</div></li>
          <li><p class="overHidden2">第二章作业</p><p class="status">未交</p></li>
        </ul></div>
        """
        rows = parse_assessment_html(html, "homework")
        self.assertEqual([row["state"] for row in rows], ["completed", "incomplete"])
        self.assertEqual(rows[0]["deadline"], "剩余10小时")

    def test_parse_task_cards_keeps_completed_and_pending(self):
        html = '''<script>mArg={"attachments":[
          {"type":"video","jobid":"1","job":true,"isPassed":true,"property":{"name":"A.mp4"}},
          {"type":"workid","jobid":"2","job":true,"property":{"title":"Quiz"}}
        ]};</script>'''
        rows = parse_task_cards([html])
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["completed"])
        self.assertEqual(rows[1]["status"], "pending")

    def test_parse_display_school(self):
        html = '''<ul id="messageFid">
          <li>Old School <a>设置为显示单位</a><p>学号:1</p></li>
          <li>Current School<p>学号:2</p></li>
        </ul>'''
        self.assertEqual(parse_school(html), "Current School")

    def test_normalizes_affiliated_school_systems(self):
        html = '<ul id="messageFid"><li>\u5c71\u897f\u519c\u4e1a\u5927\u5b66\u5b9e\u9a8c\u5ba4\u51c6\u5165\u7cfb\u7edf<p>\u5b66\u53f7:2</p></li></ul>'
        self.assertEqual(parse_school(html), "\u5c71\u897f\u519c\u4e1a\u5927\u5b66")

    def test_refresh_keeps_last_complete_snapshot_until_atomic_publish(self):
        with TemporaryDirectory() as directory:
            service = DashboardService(Path(directory))
            service._snapshot = {
                "updated_at": "old",
                "partial": False,
                "summary": {"accounts": 2, "running": 0, "selected_courses": 2},
                "schools": [],
            }
            observed = []

            def scan(username, _profiles, _running):
                observed.append(service.dashboard()["summary"]["accounts"])
                return {
                    "username": username,
                    "name": username,
                    "school": "School",
                    "status": "idle",
                    "selected_courses": 1,
                    "progress": {"done": 0, "total": 1, "percent": 0},
                    "courses": [],
                }

            with (
                patch.object(service, "_profile_groups", return_value={"one": [object()], "two": [object()]}),
                patch.object(service, "_running_profiles", return_value=set()),
                patch.object(service, "_scan_account", side_effect=scan),
            ):
                service._refresh_once()

            self.assertEqual(observed, [2, 2])
            self.assertEqual(service.dashboard()["summary"]["accounts"], 2)
            self.assertFalse(service.dashboard()["partial"])

    def test_publish_moves_legacy_profiles_into_archive_group(self):
        with TemporaryDirectory() as directory:
            service = DashboardService(Path(directory))
            base = {
                "status": "idle",
                "selected_courses": 1,
                "progress": {"done": 0, "total": 1, "percent": 0},
                "courses": [{"selected": True}],
            }
            service._publish(
                [
                    {**base, "username": "1", "name": "Current", "school": "School", "archived": False},
                    {**base, "username": "2", "name": "Legacy", "school": "Old School", "archived": True},
                ]
            )
            snapshot = service.dashboard()
            self.assertEqual(snapshot["schools"][-1]["name"], "历史归档")
            self.assertEqual(snapshot["schools"][-1]["accounts"][0]["name"], "Legacy")
            self.assertEqual(snapshot["summary"]["selected_courses"], 1)
            self.assertEqual(snapshot["summary"]["archived_accounts"], 1)


if __name__ == "__main__":
    unittest.main()
