import threading
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from api.base import Chaoxing, StudyResult
from api.study_runner import ChapterResult, process_chapter, run_followup_phases, run_loaded_profile


class RunPhaseTests(unittest.TestCase):
    def test_reading_timer_is_deferred_and_deduplicated(self):
        client = Mock()
        course = {'courseId': 'course', 'clazzId': 'class', 'cpi': 'member'}
        point = {'id': 'point', 'title': 'Reading', 'has_finished': False}
        client.get_job_list.return_value = ([{'type': 'read', 'jobid': 'reading'}], {'knowledgeid': 'point'})
        client.get_course_point.return_value = {'points': [dict(point, has_finished=True)]}
        targets = {}
        with patch('api.study_runner.process_job', return_value=StudyResult.SUCCESS) as process:
            for attempt in range(2):
                result = process_chapter(client, course, point, 1, reading_duration_seconds=9999, deferred_readings=targets)
                self.assertEqual(result, ChapterResult.SUCCESS)
            self.assertEqual(process.call_args.args[5], 0)
        self.assertEqual(targets, {'point': {'knowledgeid': 'point'}})
        client.study_read_duration.assert_not_called()

    def test_tasks_then_counts_then_reading_with_isolated_failures(self):
        client = Chaoxing()
        client.login = Mock(return_value={'status': True})
        courses = [{'courseId': str(index), 'clazzId': str(index), 'cpi': 'member', 'title': str(index)} for index in range(3)]
        client.get_course_list = Mock(return_value=courses)
        client.get_course_point = Mock(return_value={'points': [{'id': 'point'}]})
        config = {'speed': 1, 'jobs': 4, 'course_jobs': 2, 'course_list': [], 'reading_duration_seconds': 9999, 'add_learning_count': True, 'target_count': 300}
        lock = threading.Lock()
        tasks_finished = set()
        counts_finished = set()
        calls = []

        def task(course_client, course, common):
            course['_reading_targets'] = {'point': {'knowledgeid': 'point'}}
            with lock:
                tasks_finished.add(course['courseId'])
            if course['courseId'] == '0':
                raise ValueError('task failure')

        def count(course, points, target):
            self.assertEqual(len(tasks_finished), 3)
            self.assertEqual(target, 300)
            with lock:
                calls.append(('count', course['courseId']))
                counts_finished.add(course['courseId'])
            return StudyResult.SUCCESS

        def read(course, info, duration):
            self.assertEqual(len(counts_finished), 3)
            self.assertEqual(duration, 9999)
            with lock:
                calls.append(('read', course['courseId']))
            return StudyResult.ERROR if course['courseId'] == '1' else StudyResult.SUCCESS

        client.increase_chapter_learning_count = Mock(side_effect=count)
        client.study_read_duration = Mock(side_effect=read)
        with ExitStack() as stack:
            stack.enter_context(patch('api.study_runner.build_runner_config', return_value=(config, {}, {}, {'name': 'test'})))
            stack.enter_context(patch('api.study_runner.configure_profile_runtime'))
            stack.enter_context(patch('api.study_runner.init_chaoxing', return_value=client))
            stack.enter_context(patch('api.study_runner.process_course', side_effect=task))
            report = stack.enter_context(patch('api.study_runner.log_course_report'))
            with self.assertRaises(RuntimeError):
                run_loaded_profile({})
        self.assertEqual([phase for phase, identity in calls], ['count'] * 3 + ['read'] * 3)
        self.assertEqual(report.call_count, 1)
        self.assertEqual(report.call_args.args[2], ['执行失败', '执行结束', '执行结束'])
        self.assertEqual(courses[1]['_reading_outcome'], '执行失败')
        self.assertTrue(all(course['_count_outcome'] == '执行成功' for course in courses))

    def test_disabled_auxiliary_phases_do_not_fetch_or_run(self):
        client = Mock()
        run_followup_phases(client, [{'title': 'course'}], {}, 1)
        client.get_course_point.assert_not_called()
        client.increase_chapter_learning_count.assert_not_called()
        client.study_read_duration.assert_not_called()

    def test_empty_course_does_not_claim_count_success(self):
        client = Mock()
        client.get_course_point.return_value = {'points': []}
        course = {'courseId': 'course', 'clazzId': 'class', 'cpi': 'member', 'title': 'course'}
        run_followup_phases(client, [course], {'add_learning_count': True, 'target_count': 300}, 1)
        self.assertEqual(course['_count_outcome'], '执行失败')
        client.increase_chapter_learning_count.assert_not_called()

    def test_book_reading_is_reported_in_reading_phase_without_wrong_endpoint(self):
        client = Mock()
        course = {'title': 'course', '_non_task_pending': {
            ('point', 'insertreadV2', 'Book'): {'module': 'insertreadV2', 'title': 'Book'},
            ('vote', 'insertvote', 'Survey'): {'module': 'insertvote', 'title': 'Survey'},
        }}
        run_followup_phases(client, [course], {'reading_duration_seconds': 9999}, 1)
        self.assertEqual(course['_reading_outcome'], '待处理图书阅读 1 项；已执行计时 0 项')
        client.study_read_duration.assert_not_called()

    def test_book_pending_does_not_block_supported_reading(self):
        client = Mock()
        client.study_read_duration.return_value = StudyResult.SUCCESS
        course = {'title': 'course', '_reading_targets': {'point': {'knowledgeid': 'point'}},
                  '_non_task_pending': {('book', 'insertreadV2', 'Book'): {'module': 'insertreadV2', 'title': 'Book'}}}
        run_followup_phases(client, [course], {'reading_duration_seconds': 9999}, 1)
        client.study_read_duration.assert_called_once_with(course, {'knowledgeid': 'point'}, 9999)
        self.assertEqual(course['_reading_outcome'], '待处理图书阅读 1 项；已执行计时 1 项')

    def test_survey_does_not_count_as_pending_reading(self):
        client = Mock()
        course = {'title': 'course', '_non_task_pending': {
            ('point', 'insertvote', 'Survey'): {'module': 'insertvote', 'title': 'Survey'},
        }}
        run_followup_phases(client, [course], {'reading_duration_seconds': 9999}, 1)
        self.assertEqual(course['_reading_outcome'], '无待执行计时')
        client.study_read_duration.assert_not_called()

    def test_discovered_book_runs_only_in_independent_reading_phase(self):
        client = Mock()
        book = {'id': '99', 'title': 'Book', 'knowledgeid': 'point'}
        course = {'title': 'course', 'courseId': '12', 'clazzId': '34', 'cpi': 'member'}
        point = {'id': 'point', 'title': 'Reading', 'has_finished': True}
        client.get_job_list.return_value = ([], {'book_readings': [book]})
        with patch('api.study_runner.process_job') as process:
            process_chapter(client, course, point, 1, include_non_task=True)
        process.assert_not_called()
        client.study_book_read_duration.assert_not_called()
        client.study_book_read_duration.return_value = StudyResult.SUCCESS
        run_followup_phases(client, [course], {'reading_duration_seconds': 9999}, 1)
        client.study_book_read_duration.assert_called_once_with(course, book, 9999)
        self.assertEqual(course['_reading_outcome'], '执行成功')


if __name__ == '__main__':
    unittest.main()
