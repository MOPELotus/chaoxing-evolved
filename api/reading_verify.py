import argparse
import json
import sys
from datetime import datetime, timezone

from loguru import logger

from api.base import Chaoxing, SessionManager
from api.book_reading import resolve_book_reading
from api.json_store import profile_json_path
from api.reading_worker import save_state, scan_readings
from api.runtime import configure_runtime


def verify_readings(client, targets, progress):
    rows = []
    session = SessionManager.get_session()
    for key, (course, book) in targets.items():
        item = progress.get('items', {}).get(key, {})
        row = {'course': course['title'], 'title': book['title'],
               'acknowledged_seconds': item.get('acknowledged_seconds', 0),
               'local_status': item.get('status', 'not_started'),
               'local_updated_at': item.get('updated_at'),
               'checked_at': datetime.now(timezone.utc).isoformat()}
        try:
            context = resolve_book_reading(session, course, book, book)
            row.update(context['statistics'])
            row['status'] = 'platform_statistics_visible' if row['reported_minutes'] is not None else 'platform_statistics_unavailable'
        except Exception as error:
            row.update(status='verification_failed', error_type=type(error).__name__)
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description='只读核验平台阅读统计；不执行阅读计时或修改阅读进度')
    parser.add_argument('--profile', required=True)
    parser.add_argument('--expected-name', required=True)
    args = parser.parse_args()
    logger.remove()
    logger.add(sys.stderr, level='INFO')
    context = configure_runtime(config_path=profile_json_path(args.profile))
    client = Chaoxing()
    if client.get_name() != args.expected_name:
        raise ValueError('核验账号身份不匹配')
    targets, pending = scan_readings(client)
    progress_path = context.config_path.with_suffix('.reading-progress.json')
    progress = json.loads(progress_path.read_text(encoding='utf-8')) if progress_path.exists() else {}
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'readings': verify_readings(client, targets, progress), 'pending': pending,
              'note': '平台显示值与本地发送值分别记录；当天未更新不代表失败，跨日统计不能直接当作累计总数。'}
    save_state(context.config_path.with_suffix('.reading-verification.json'), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
