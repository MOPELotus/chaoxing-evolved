import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from api.base import Chaoxing, SessionManager, chapter_card_count
from api.decode import decode_course_card
from api.json_store import profile_json_path
from api.runtime import configure_runtime


def scan_readings(client):
    session = SessionManager.get_session()
    targets = {}
    pending = []
    for course in client.get_course_list():
        points = client.get_course_point(course['courseId'], course['clazzId'], course['cpi'])['points']
        for point in points:
            directory = session.get('https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax', params={
                'courseId': course['courseId'], 'clazzid': course['clazzId'], 'chapterId': point['id'],
                'cpi': course['cpi'], 'mooc2': 1,
            })
            directory.raise_for_status()
            count = chapter_card_count(directory.text)
            if count is None:
                raise ValueError('无法确认阅读任务卡页数')
            for page in range(count):
                response = session.get('https://mooc1.chaoxing.com/mooc-ans/knowledge/cards', params={
                    'courseid': course['courseId'], 'clazzid': course['clazzId'], 'knowledgeid': point['id'],
                    'cpi': course['cpi'], 'ut': 's', 'num': page, 'mooc2': 1,
                })
                response.raise_for_status()
                jobs, info = decode_course_card(response.text, include_non_task=True)
                if not info.get('knowledgeid'):
                    raise ValueError('阅读任务卡无有效章节信息')
                for book in info.get('book_readings', []):
                    key = f"{course['courseId']}:{course['clazzId']}:{book['id']}"
                    targets[key] = (course, book)
                for item in info.get('unsupported_non_task', []):
                    if 'read' in item.get('module', '').lower():
                        pending.append({'course': course['title'], 'chapter': point['title'], **item})
                for job in jobs:
                    if job.get('type') == 'read' and not info.get('book_readings'):
                        pending.append({'course': course['title'], 'chapter': point['title'], 'module': 'read', 'title': job.get('title', '')})
        logger.info('[阅读扫描] {} | 累计可处理 {} 项 | 待处理 {} 项', course['title'], len(targets), len(pending))
    return targets, pending


def save_state(path, state):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def run_readings(client, targets, pending, duration, state_path, scan_only=False):
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'items': {}}
    state['pending'] = pending
    state['scan_finished_at'] = datetime.now(timezone.utc).isoformat()
    for key, (course, book) in targets.items():
        item = state['items'].setdefault(key, {'acknowledged_seconds': 0})
        item.update(course=course['title'], title=book['title'], requested_seconds=duration)
    save_state(state_path, state)
    if scan_only:
        return
    failures = 0
    for key, (course, book) in targets.items():
        item = state['items'][key]
        previous = int(item.get('acknowledged_seconds') or 0)
        if 'baseline_reported_seconds' not in item:
            try:
                statistics = client.get_book_reading_statistics(course, book)
                minutes = statistics.get('reported_minutes')
                if minutes is None or not math.isfinite(float(minutes)) or float(minutes) < 0:
                    raise ValueError('平台阅读统计不可用，不能确定还需补多少时长')
                item['baseline_reported_seconds'] = math.floor(float(minutes) * 60)
                item['baseline_checked_at'] = datetime.now(timezone.utc).isoformat()
                item['statistics_next_day'] = statistics.get('next_day_update', False)
                save_state(state_path, state)
            except Exception as error:
                item['status'] = 'planning_failed'
                save_state(state_path, state)
                failures += 1
                logger.warning('[阅读计划待处理] {} | {} | {}', course['title'], book['title'], type(error).__name__)
                continue
        baseline = int(item['baseline_reported_seconds'])
        remaining = max(0, duration - baseline - previous)
        if remaining == 0:
            item['status'] = 'platform_target_already_met' if baseline >= duration else 'logs_sent_unverified'
            save_state(state_path, state)
            logger.info('[阅读跳过] {} | {} | 平台基线 {} 秒，已发送 {} 秒，目标 {} 秒', course['title'], book['title'], baseline, previous, duration)
            continue
        item['status'] = 'running'
        save_state(state_path, state)

        def record_progress(elapsed):
            item['acknowledged_seconds'] = previous + elapsed
            item['updated_at'] = datetime.now(timezone.utc).isoformat()
            save_state(state_path, state)
            if elapsed % 60 == 0 or elapsed == remaining:
                logger.info('[阅读进度] {} | {} | 已接受日志 {} 秒，平台历史 {} 秒，目标 {} 秒（新增时长待次日核验）', course['title'], book['title'], previous + elapsed, baseline, duration)

        logger.info('[阅读开始] {} | {} | 剩余 {} 秒', course['title'], book['title'], remaining)
        try:
            result = client.study_book_read_duration(course, book, remaining, progress_callback=record_progress)
            item['status'] = 'logs_sent_unverified' if result.is_success() else 'failed'
        except Exception as error:
            item['status'] = 'failed'
            logger.warning('[阅读异常] {} | {} | {}', course['title'], book['title'], type(error).__name__)
        save_state(state_path, state)
        failures += item['status'] == 'failed'
    if failures or pending:
        raise RuntimeError(f'阅读仍有待处理项：执行失败 {failures}，未适配 {len(pending)}')


def main():
    parser = argparse.ArgumentParser(description='仅扫描和执行独立图书阅读，不执行答题、视频或学习访问次数')
    parser.add_argument('--profile', required=True)
    parser.add_argument('--expected-name', required=True)
    parser.add_argument('--duration', type=int, default=9999)
    parser.add_argument('--scan-only', action='store_true')
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error('duration 必须为正数')
    logger.remove()
    logger.add(sys.stderr, level='INFO')
    context = configure_runtime(config_path=profile_json_path(args.profile))
    state_path = context.config_path.with_suffix('.reading-progress.json')
    lock_path = state_path.with_suffix('.lock')
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError('已有阅读锁，请确认旧进程退出后再处理锁文件') from None
    try:
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(str(os.getpid()))
        client = Chaoxing()
        if client.get_name() != args.expected_name:
            raise ValueError('阅读账号身份不匹配，停止')
        targets, pending = scan_readings(client)
        logger.info('[阅读清单] 可处理 {} 项，待处理 {} 项；不会运行其他学习阶段', len(targets), len(pending))
        run_readings(client, targets, pending, args.duration, state_path, args.scan_only)
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
