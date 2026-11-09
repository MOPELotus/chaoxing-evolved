import unittest

from web_dashboard.service import normalize_status, parse_assessment_html, parse_school, parse_task_cards


class DashboardParsingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
