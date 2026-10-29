import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup


def script_string(source, name):
    match = re.search(r'\b' + re.escape(name) + r'\s*=\s*("(?:[^"\\]|\\.)*")\s*;', source)
    return json.loads(match[1]) if match else ''


def preview_statistics(source):
    text = BeautifulSoup(source, 'html.parser').get_text(' ', strip=True)
    match = re.search(r'阅读总时长\s*[:：]\s*(\d+(?:\.\d+)?)\s*分钟', text)
    return {
        'reported_minutes': float(match[1]) if match else None,
        'next_day_update': '次日更新' in text or '次日统计' in text,
    }


def resolve_book_reading(session, course, book, info):
    book_id = str(book.get('id') or '')
    if not book_id.isdecimal() or not book.get('enc'):
        raise ValueError('图书阅读缺少原生标识或访问签名')
    response = session.get(
        'https://mooc1.chaoxing.com/mooc-ans/readv2job/preview',
        params={'jobid': book.get('jobid', ''), 'djobid': book.get('source_jobid') or 'read-' + book_id,
                'type': 'read', 'knowledgeid': info['knowledgeid'], 'ut': 's', 'isphone': 'false',
                'clazzId': course['clazzId'], 'enc': book['enc'], 'courseid': course['courseId']},
    )
    response.raise_for_status()
    source = '\n'.join(element.get_text() for element in BeautifulSoup(response.text, 'html.parser').select('script:not([src])'))
    attribution = script_string(source, 'from')
    expected = f"{course['courseId']}_{course['clazzId']}_"
    if not attribution.startswith(expected):
        raise ValueError('阅读预览未返回匹配课程的归属信息')
    attribution_params = {'_from_': attribution, '_fromV2_': script_string(source, '_fromV2_'), 'rtag': script_string(source, 'rtag')}
    portal_id = script_string(source, 'id')
    if portal_id:
        if not re.fullmatch(r'[A-Za-z0-9_=+-]+', portal_id):
            raise ValueError('未知专题地址格式')
        path = '/mooc-ans/zt/portal/' + portal_id
    elif '"/course/" + id' in source:
        path = '/mooc-ans/course/' + book_id + '.html'
    else:
        raise ValueError('尚未适配的图书阅读入口')
    portal = session.get('https://mooc1.chaoxing.com' + path, params=attribution_params)
    portal.raise_for_status()
    for element in BeautifulSoup(portal.text, 'html.parser').select('a[href]'):
        target = urlsplit(urljoin(portal.url, element['href']))
        if target.hostname != 'mooc1.chaoxing.com' or target.path != '/mooc-ans/ztnodedetailcontroller/visitnodedetail':
            continue
        query = parse_qs(target.query)
        node_id = query.get('knowledgeId', [''])[0]
        if query.get('courseId', [''])[0] != book_id or not node_id.isdecimal():
            continue
        return {'course_id': book_id, 'knowledge_id': node_id, **attribution_params,
                'statistics': preview_statistics(response.text)}
    raise ValueError('专题没有可验证的阅读章节，可能已下架或需要登录')


def valid_read_log_response(response):
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    return all(payload[key] in (True, 1) for key in ('status', 'success') if key in payload)
