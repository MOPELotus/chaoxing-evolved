from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from requests import RequestException

from api.base import Account, Chaoxing, SessionManager
from api.json_store import build_effective_profile, load_global_settings, load_json_profile, profile_json_path
from api.logger import logger
from api.runtime import configure_runtime


HOMEWORK_LIST_URL = "https://mooc1-api.chaoxing.com/work/stu-work"
EXAM_MOBILE_LIST_URL = "https://mooc1-api.chaoxing.com/exam-ans/exam/phone/examcode"
EXAM_WEB_LIST_URL = "https://mooc1.chaoxing.com/exam-ans/exam/test/examcode/examlist"
COURSE_VISIT_URL = "https://mooc1.chaoxing.com/visit/stucoursemiddle?ismooc2=1"
ACTIVITY_LIST_URL = "https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ProfileTaskContext:
    profile_name: str
    common: dict[str, Any]
    session: Any
    chaoxing: Chaoxing


def _normalize_text(value: str | None) -> str:
    return WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _tag_text(tag: Tag | None) -> str:
    if not tag:
        return ""
    return _normalize_text(tag.get_text(" ", strip=True))


def _boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_profile_context(profile_name: str) -> ProfileTaskContext:
    profile = load_json_profile(profile_name)
    global_settings = load_global_settings()
    effective = build_effective_profile(profile, global_settings)
    common = effective.get("common", {})

    configure_runtime(
        config_path=profile_json_path(effective["name"]),
        cookies_path=common.get("cookies_path") or None,
        cache_path=common.get("cache_path") or None,
    )

    use_cookies = _boolish(common.get("use_cookies", False))
    username = str(common.get("username", "") or "").strip()
    password = str(common.get("password", "") or "").strip()
    if (not username or not password) and not use_cookies:
        raise ValueError("当前配置未填写账号密码，也未启用 Cookies 登录。")

    chaoxing = Chaoxing(account=Account(username, password), tiku=None, query_delay=0)
    login_state = chaoxing.login(login_with_cookies=use_cookies)
    if not login_state.get("status"):
        raise ValueError(str(login_state.get("msg") or "登录失败。"))

    session = SessionManager.get_session()
    return ProfileTaskContext(profile_name=effective["name"], common=common, session=session, chaoxing=chaoxing)


def _parse_query_params(raw_url: str) -> dict[str, str]:
    parsed = urlparse(raw_url)
    payload: dict[str, str] = {}
    for key, values in parse_qs(parsed.query).items():
        if values:
            payload[key] = values[0]
    return payload


def _build_course_open_url(course_id: str, clazz_id: str) -> str:
    return f"{COURSE_VISIT_URL}&courseid={course_id}&clazzid={clazz_id}&pageHeader=8"


def _extract_status_from_text(text: str) -> tuple[str, bool, bool]:
    normalized = _normalize_text(text)
    finished = any(keyword in normalized for keyword in ("已完成", "待批阅", "已批阅", "已交", "已交卷"))
    expired = any(keyword in normalized for keyword in ("已结束", "已过期", "已截止"))
    if finished:
        status = "已完成"
    elif expired:
        status = "已结束"
    elif any(keyword in normalized for keyword in ("待提交", "未交", "未完成", "进行中", "待做", "待完成")):
        status = "待处理"
    else:
        status = normalized or "未知"
    return status, finished, expired


def _extract_info_line(text: str) -> str:
    segments = [segment.strip() for segment in re.split(r"[\r\n]+", text) if segment.strip()]
    for segment in segments:
        if any(keyword in segment for keyword in ("剩余", "截止", "结束", "时间", "有效期", "时长")):
            return _normalize_text(segment)
    if len(segments) > 1:
        return _normalize_text(" | ".join(segments[1:3]))
    return ""


def _extract_title(tag: Tag | None, fallback: str = "") -> str:
    if not tag:
        return fallback
    for selector in ("h1", "h2", "h3", "h4", ".title", ".tit", ".name", "a"):
        child = tag.select_one(selector)
        text = _tag_text(child)
        if text:
            return text
    text = _tag_text(tag)
    return text or fallback


def _candidate_container(tag: Tag) -> Tag:
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"li", "tr", "article", "section"}:
            return parent
        classes = " ".join(parent.get("class", []))
        if any(token in classes for token in ("list", "item", "task", "work", "exam")):
            return parent
    return tag


def _resolve_candidate_url(tag: Tag, base_url: str) -> str:
    href = str(tag.get("href", "") or "").strip()
    if href and href != "#":
        return urljoin(base_url, href)

    for attr_name in ("data", "dataurl"):
        raw = str(tag.get(attr_name, "") or "").strip()
        if raw:
            return urljoin(base_url, raw)

    onclick = str(tag.get("onclick", "") or "").strip()
    for pattern in (
        r"go\(['\"]([^'\"]+)['\"]\)",
        r"setUrl\([^,]+,['\"]([^'\"]+)['\"]",
        r"window\.open\(['\"]([^'\"]+)['\"]",
        r"location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
    ):
        match = re.search(pattern, onclick)
        if match:
            return urljoin(base_url, match.group(1))
    return ""


def _parse_homework_items(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    tasks: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    candidates = list(soup.select("li[data], li[dataurl], a[href], a[onclick], [dataurl]"))
    for element in candidates:
        if not isinstance(element, Tag):
            continue
        raw_url = _resolve_candidate_url(element, base_url)
        if not raw_url:
            continue
        lowered = raw_url.lower()
        if not any(token in lowered for token in ("workid=", "taskrefid=", "/work/", "dohomework")):
            continue

        params = _parse_query_params(raw_url)
        work_id = params.get("workId") or params.get("taskrefId") or params.get("workAnswerId") or ""
        course_id = params.get("courseId") or params.get("courseid") or ""
        clazz_id = params.get("classId") or params.get("clazzId") or params.get("clazzid") or ""
        if not work_id and not course_id and "stu-work" in lowered:
            continue

        key = work_id or raw_url
        if key in seen_keys:
            continue
        seen_keys.add(key)

        container = _candidate_container(element)
        blob = _tag_text(container)
        title = _extract_title(container, fallback=_tag_text(element))
        status, finished, expired = _extract_status_from_text(blob)
        info = _extract_info_line(container.get_text("\n", strip=True))

        tasks.append(
            {
                "task_kind": "homework",
                "task_type": "作业",
                "task_id": work_id,
                "title": title or "未命名作业",
                "course": "",
                "status": status,
                "info": info,
                "finished": finished,
                "expired": expired,
                "course_id": course_id,
                "clazz_id": clazz_id,
                "open_url": raw_url,
                "course_open_url": _build_course_open_url(course_id, clazz_id) if course_id and clazz_id else "",
            }
        )

    return tasks


def _parse_exam_table_rows(soup: BeautifulSoup, page_base: str) -> list[dict[str, Any]]:
    exams: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in soup.select("table tr.dataTr"):
        cells = row.select("td")
        if len(cells) < 5:
            continue

        title = _tag_text(cells[1] if len(cells) > 1 else row)
        time_range = _tag_text(cells[2] if len(cells) > 2 else None)
        exam_status = _tag_text(cells[4] if len(cells) > 4 else None)
        answer_status = _tag_text(cells[5] if len(cells) > 5 else None)
        action_link = cells[-1].select_one("a")
        raw_url = _resolve_candidate_url(action_link or row, page_base)
        onclick = str(action_link.get("onclick", "") if action_link else row.get("onclick", "") or "")

        params = _parse_query_params(raw_url)
        course_id = params.get("moocId") or params.get("courseId") or ""
        clazz_id = params.get("clazzid") or params.get("classId") or ""
        exam_id = params.get("examId") or ""
        if not exam_id:
            for pattern, key_name in ((r"examId=(\d+)", "exam_id"), (r"moocId=(\d+)", "course_id"), (r"clazzid=(\d+)", "clazz_id")):
                match = re.search(pattern, onclick)
                if match:
                    if key_name == "exam_id":
                        exam_id = match.group(1)
                    elif key_name == "course_id" and not course_id:
                        course_id = match.group(1)
                    elif key_name == "clazz_id" and not clazz_id:
                        clazz_id = match.group(1)

        key = exam_id or title
        if key in seen_keys:
            continue
        seen_keys.add(key)

        status_blob = f"{exam_status} {answer_status}".strip()
        status, finished, expired = _extract_status_from_text(status_blob)
        exams.append(
            {
                "task_kind": "exam",
                "task_type": "考试",
                "task_id": exam_id,
                "title": title or "未命名考试",
                "course": "",
                "status": status,
                "info": time_range,
                "finished": finished,
                "expired": expired,
                "course_id": course_id,
                "clazz_id": clazz_id,
                "open_url": raw_url,
                "course_open_url": _build_course_open_url(course_id, clazz_id) if course_id and clazz_id else "",
            }
        )
    return exams


def _parse_exam_cards(soup: BeautifulSoup, page_base: str) -> list[dict[str, Any]]:
    exams: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for node in soup.select(".examList li, .exam-list li, li, .list-item"):
        tag = node if isinstance(node, Tag) else None
        if not tag:
            continue
        raw_text = _tag_text(tag)
        if "考试" not in raw_text and "测验" not in raw_text and "试卷" not in raw_text:
            continue

        link = tag.select_one("a[href], a[onclick]") or tag
        raw_url = _resolve_candidate_url(link, page_base)
        params = _parse_query_params(raw_url)
        exam_id = params.get("examId") or params.get("taskrefId") or ""
        course_id = params.get("courseId") or params.get("moocId") or ""
        clazz_id = params.get("classId") or params.get("clazzid") or ""
        key = exam_id or raw_url or raw_text
        if key in seen_keys:
            continue
        seen_keys.add(key)

        title = _extract_title(tag, fallback=raw_text)
        status, finished, expired = _extract_status_from_text(raw_text)
        info = _extract_info_line(tag.get_text("\n", strip=True))
        exams.append(
            {
                "task_kind": "exam",
                "task_type": "考试",
                "task_id": exam_id,
                "title": title or "未命名考试",
                "course": "",
                "status": status,
                "info": info,
                "finished": finished,
                "expired": expired,
                "course_id": course_id,
                "clazz_id": clazz_id,
                "open_url": raw_url,
                "course_open_url": _build_course_open_url(course_id, clazz_id) if course_id and clazz_id else "",
            }
        )
    return exams


def _fetch_activity_tasks(context: ProfileTaskContext) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    type_map = {
        0: "签到",
        2: "签到",
        4: "抢答",
        5: "主题讨论",
        6: "投票",
        14: "问卷",
        17: "直播",
        23: "随堂练习",
        35: "分组任务",
        42: "随堂练习",
        43: "评分",
        45: "拍照",
        47: "作业",
        64: "笔记",
    }
    try:
        courses = context.chaoxing.get_course_list()
    except Exception as exc:
        logger.error(f"获取课程任务列表失败：{exc}")
        return tasks

    seen_keys: set[str] = set()
    for course in courses:
        course_id = str(course.get("courseId", "") or "").strip()
        clazz_id = str(course.get("clazzId", "") or "").strip()
        if not course_id or not clazz_id:
            continue
        params = {
            "fid": 0,
            "courseId": course_id,
            "classId": clazz_id,
            "showNotStartedActive": 0,
            "_": str(int(__import__("time").time() * 1000)),
        }
        try:
            response = context.session.get(ACTIVITY_LIST_URL, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.debug(f"读取课程任务失败 {course.get('title', course_id)}: {exc}")
            continue

        active_list = None
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), dict):
                active_list = payload["data"].get("activeList")
            if active_list is None:
                active_list = payload.get("activeList")
            if active_list is None and isinstance(payload.get("data"), list):
                active_list = payload.get("data")
        if active_list is None and isinstance(payload, list):
            active_list = payload
        if not isinstance(active_list, list):
            continue

        for item in active_list:
            if not isinstance(item, dict):
                continue
            active_id = str(item.get("id") or item.get("activeId") or "").strip()
            key = f"{course_id}:{clazz_id}:{active_id}"
            if key in seen_keys:
                continue
            seen_keys.add(key)

            active_type = item.get("activeType", item.get("type"))
            status_code = item.get("status")
            is_ongoing = status_code == 1
            is_ended = status_code == 2
            title = _normalize_text(item.get("nameOne") or item.get("name") or item.get("title") or "未知任务")
            info = _normalize_text(item.get("endTimeText") or item.get("endTime") or item.get("startTime") or "")
            tasks.append(
                {
                    "task_kind": "activity",
                    "task_type": type_map.get(active_type, f"类型{active_type}"),
                    "task_id": active_id,
                    "title": title,
                    "course": _normalize_text(course.get("title") or course.get("courseName") or ""),
                    "status": "进行中" if is_ongoing else ("已结束" if is_ended else "未开始"),
                    "info": info,
                    "finished": False,
                    "expired": bool(is_ended),
                    "course_id": course_id,
                    "clazz_id": clazz_id,
                    "open_url": _build_course_open_url(course_id, clazz_id),
                    "course_open_url": _build_course_open_url(course_id, clazz_id),
                    "ongoing": bool(is_ongoing),
                }
            )
    return tasks


def list_profile_tasks(profile_name: str, include_finished: bool = False) -> dict[str, Any]:
    context = _build_profile_context(profile_name)
    result: dict[str, Any] = {
        "profile_name": context.profile_name,
        "homeworks": [],
        "exams": [],
        "activities": [],
        "pending": [],
    }

    try:
        response = context.session.get(HOMEWORK_LIST_URL, timeout=12)
        response.raise_for_status()
        result["homeworks"] = _parse_homework_items(response.text, response.url)
    except RequestException as exc:
        logger.error(f"获取作业列表失败：{exc}")

    exam_items: list[dict[str, Any]] = []
    try:
        response = context.session.get(EXAM_MOBILE_LIST_URL, timeout=12)
        response.raise_for_status()
        exam_items.extend(_parse_exam_cards(BeautifulSoup(response.text, "lxml"), response.url))
    except RequestException as exc:
        logger.error(f"获取考试列表失败：{exc}")

    try:
        fid = context.chaoxing.get_fid()
        response = context.session.get(EXAM_WEB_LIST_URL, params={"edition": 1, "nohead": 0, "fid": fid}, timeout=12)
        response.raise_for_status()
        exam_items.extend(_parse_exam_table_rows(BeautifulSoup(response.text, "lxml"), response.url))
    except RequestException as exc:
        logger.debug(f"获取桌面考试列表失败：{exc}")

    deduped_exams: list[dict[str, Any]] = []
    seen_exam_keys: set[str] = set()
    for item in exam_items:
        key = str(item.get("task_id") or item.get("title") or item.get("open_url") or "")
        if key in seen_exam_keys:
            continue
        seen_exam_keys.add(key)
        deduped_exams.append(item)
    result["exams"] = deduped_exams
    result["activities"] = _fetch_activity_tasks(context)

    pending: list[dict[str, Any]] = []
    for group_name in ("homeworks", "exams"):
        for item in result[group_name]:
            if include_finished or (not item.get("finished") and not item.get("expired")):
                pending.append(item)
    for item in result["activities"]:
        if include_finished or item.get("ongoing"):
            pending.append(item)

    pending.sort(key=lambda item: (item.get("task_kind", ""), item.get("course", ""), item.get("title", "")))
    result["pending"] = pending
    result["summary"] = {
        "homework_count": len(result["homeworks"]),
        "exam_count": len(result["exams"]),
        "activity_count": len(result["activities"]),
        "pending_count": len(result["pending"]),
    }
    return result
