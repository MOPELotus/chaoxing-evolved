from __future__ import annotations

import copy
import json
import re
import subprocess
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from api.base import Account, Chaoxing, SessionManager, chapter_card_count
from api.json_store import list_json_profiles, load_json_profile, profile_json_path
from api.logger import logger
from api.runtime import configure_runtime


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PROFILE_RE = re.compile(r"--desktop-worker\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))")
SKIP_PROFILE_PREFIXES = ("repair-",)
SCHOOL_FALLBACKS = {"170121": "\u5c71\u897f\u519c\u4e1a\u5927\u5b66"}
ACCEPTED_COURSE_GAPS = {
    "\u7ba1\u7406\u5b66\u539f\u7406\uff08\u4ea4\u53c9\u901a\u8bc6\u8bfe\uff09": {"platform_done": 26, "total": 29},
}


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    username: str
    password: str
    selections: tuple[str, ...]
    path: Path

    @property
    def selects_all(self) -> bool:
        return not self.selections


def course_key(course: dict[str, Any]) -> str:
    return f"{course.get('courseId', '')}::{course.get('clazzId', '')}"


def normalize_status(raw: str) -> str:
    text = re.sub(r"\s+", "", raw or "")
    if any(mark in text for mark in ("\u5f85\u6279", "\u5df2\u63d0\u4ea4", "\u5df2\u4ea4")):
        return "pending_review"
    if any(mark in text for mark in ("\u5df2\u5b8c\u6210", "\u5df2\u6279\u9605", "\u5df2\u8bc4\u9605", "\u5df2\u901a\u8fc7")):
        return "completed"
    if any(mark in text for mark in ("\u8fc7\u671f", "\u622a\u6b62", "\u5df2\u7ed3\u675f", "\u5df2\u5173\u95ed")):
        return "expired"
    if any(mark in text for mark in ("\u672a\u4ea4", "\u5f85\u505a", "\u672a\u5b8c\u6210", "\u672a\u5f00\u59cb")):
        return "incomplete"
    if any(mark in text for mark in ("\u8fdb\u884c\u4e2d", "\u4f5c\u7b54\u4e2d")):
        return "running"
    return "unknown"


def accepted_course_gap(title: str, done: int, total: int) -> int:
    """Return the number of explicitly accepted platform-only gaps."""
    rule = ACCEPTED_COURSE_GAPS.get(title)
    if not rule or total != rule["total"] or done < rule["platform_done"]:
        return 0
    return max(0, total - done)


def parse_assessment_html(html: str, kind: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "lxml")
    items: list[dict[str, str]] = []
    for row in soup.select(".bottomList li"):
        title_node = row.select_one(".overHidden2")
        status_node = row.select_one(".status")
        if not title_node or not status_node:
            continue
        title = " ".join(title_node.get_text(" ", strip=True).split())
        status = " ".join(status_node.get_text(" ", strip=True).split())
        deadline_node = row.select_one(".time")
        deadline = " ".join(deadline_node.get_text(" ", strip=True).split()) if deadline_node else ""
        items.append(
            {
                "title": title,
                "status": status,
                "state": normalize_status(status),
                "deadline": deadline,
                "kind": kind,
            }
        )
    return items


def parse_task_cards(html_pages: list[str]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for html in html_pages:
        match = re.findall(r"mArg=\{(.*?)\};", (html or "").replace(" ", ""), flags=re.S)
        if not match:
            continue
        try:
            payload = json.loads("{" + match[0] + "}")
        except (TypeError, ValueError):
            continue
        for index, card in enumerate(payload.get("attachments") or []):
            props = card.get("property") if isinstance(card.get("property"), dict) else {}
            card_type = str(card.get("type") or props.get("module") or "resource").lower()
            title = str(
                props.get("title")
                or props.get("name")
                or props.get("bookname")
                or card.get("title")
                or {
                    "video": "\u89c6\u9891",
                    "document": "\u6587\u6863",
                    "workid": "\u7ae0\u8282\u4f5c\u4e1a",
                    "read": "\u9605\u8bfb",
                }.get(card_type, "\u4efb\u52a1\u70b9")
            )
            passed = bool(card.get("isPassed"))
            is_task = bool(card.get("job") or card.get("jobid") or props.get("jobid"))
            tasks.append(
                {
                    "id": str(card.get("jobid") or props.get("jobid") or card.get("mid") or index),
                    "title": title,
                    "type": card_type,
                    "is_task": is_task,
                    "completed": passed,
                    "status": "completed" if passed else ("pending" if is_task else "resource"),
                }
            )
    return tasks


def parse_school(html: str, fid: str = "") -> str:
    soup = BeautifulSoup(html or "", "lxml")
    units = soup.select("#messageFid li")
    candidates: list[tuple[bool, str]] = []
    for unit in units:
        clone = BeautifulSoup(str(unit), "lxml").select_one("li")
        if clone is None:
            continue
        for node in clone.select("p, button, a, input"):
            node.decompose()
        name = " ".join(clone.get_text(" ", strip=True).split())
        if name:
            candidates.append(("\u8bbe\u7f6e\u4e3a\u663e\u793a\u5355\u4f4d" not in unit.get_text(" ", strip=True), name))
    preferred = [name for current, name in candidates if current]
    if preferred:
        result = preferred[0]
    elif candidates:
        result = candidates[0][1]
    else:
        result = SCHOOL_FALLBACKS.get(str(fid), f"\u673a\u6784 {fid}" if fid else "\u672a\u8bc6\u522b\u5b66\u6821")
    for canonical in ("\u5c71\u897f\u519c\u4e1a\u5927\u5b66", "\u95fd\u5357\u5e08\u8303\u5927\u5b66"):
        if canonical in result:
            return canonical
    return result


def profile_selects(profile: ProfileConfig, course: dict[str, Any]) -> bool:
    if profile.selects_all:
        return True
    key = course_key(course)
    title = str(course.get("title") or "")
    return key in profile.selections or title in profile.selections


def tail_text(path: Path, limit: int = 384 * 1024) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return ANSI_RE.sub("", stream.read().decode("utf-8", errors="replace"))
    except OSError:
        return ""


class DashboardService:
    def __init__(self, root: Path, refresh_seconds: int = 180):
        self.root = root.resolve()
        self.profile_dir = self.root / "desktop_state" / "profiles"
        self.hosted_dir = self.root / "desktop_state" / "hosted"
        self.cache_path = self.root / "desktop_state" / "dashboard-cache.json"
        self.refresh_seconds = max(30, refresh_seconds)
        self._platform_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._assessment_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._refreshing = False
        self._snapshot = self._load_cache()
        self._thread: threading.Thread | None = None

    def _load_cache(self) -> dict[str, Any]:
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._refresh_loop, name="dashboard-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def request_refresh(self) -> bool:
        with self._state_lock:
            already = self._refreshing
        self._wake.set()
        return not already

    def dashboard(self) -> dict[str, Any]:
        with self._state_lock:
            snapshot = copy.deepcopy(self._snapshot)
            snapshot["refreshing"] = self._refreshing
        snapshot.setdefault("schools", [])
        snapshot.setdefault("summary", {"accounts": 0, "running": 0, "selected_courses": 0})
        return snapshot

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            self._refresh_once()
            self._wake.wait(self.refresh_seconds)
            self._wake.clear()

    def _refresh_once(self) -> None:
        with self._state_lock:
            if self._refreshing:
                return
            self._refreshing = True
        try:
            groups = self._profile_groups()
            running = self._running_profiles()
            accounts: list[dict[str, Any]] = []
            for username, profiles in groups.items():
                if self._stop.is_set():
                    break
                previous = self._previous_account(username)
                try:
                    account = self._scan_account(username, profiles, running)
                except Exception as exc:
                    logger.warning("Dashboard scan failed for {}: {}", username, type(exc).__name__)
                    account = previous or {
                        "username": username,
                        "name": username,
                        "school": "\u672a\u8bc6\u522b\u5b66\u6821",
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "courses": [],
                        "progress": {"done": 0, "total": 0, "percent": 0},
                    }
                    account["status"] = "error"
                    account["error"] = f"{type(exc).__name__}: {exc}"
                accounts.append(account)
            if not self._stop.is_set() and len(accounts) == len(groups):
                self._publish(accounts)
        finally:
            with self._state_lock:
                self._refreshing = False

    def _previous_account(self, username: str) -> dict[str, Any] | None:
        with self._state_lock:
            for school in self._snapshot.get("schools", []):
                for account in school.get("accounts", []):
                    if account.get("username") == username:
                        return copy.deepcopy(account)
        return None

    def _publish(self, accounts: list[dict[str, Any]]) -> None:
        school_map: dict[str, list[dict[str, Any]]] = {}
        archived: list[dict[str, Any]] = []
        for account in accounts:
            if account.get("archived"):
                archived.append(account)
            else:
                school_map.setdefault(account.get("school") or "\u672a\u8bc6\u522b\u5b66\u6821", []).append(account)
        schools = [
            {"name": school, "accounts": sorted(rows, key=lambda row: row.get("name") or row.get("username"))}
            for school, rows in sorted(school_map.items(), key=lambda item: item[0])
        ]
        if archived:
            schools.append(
                {
                    "name": "\u5386\u53f2\u5f52\u6863",
                    "archived": True,
                    "accounts": sorted(archived, key=lambda row: row.get("name") or row.get("username")),
                }
            )
        selected = sum(
            sum(1 for c in account.get("courses", []) if c.get("selected"))
            for account in accounts
            if not account.get("archived")
        )
        running = sum(1 for account in accounts if account.get("status") == "running")
        snapshot = {
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "partial": False,
            "schools": schools,
            "summary": {
                "accounts": len(accounts),
                "running": running,
                "selected_courses": selected,
                "archived_accounts": len(archived),
            },
        }
        with self._state_lock:
            self._snapshot = snapshot
        try:
            self.cache_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _profile_groups(self) -> dict[str, list[ProfileConfig]]:
        grouped: dict[str, list[ProfileConfig]] = {}
        raw: list[ProfileConfig] = []
        for path in list_json_profiles():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            name = str(data.get("name") or path.stem)
            if name.startswith(SKIP_PROFILE_PREFIXES):
                continue
            common = data.get("common") if isinstance(data.get("common"), dict) else {}
            username = str(common.get("username") or "").strip()
            if not username:
                continue
            selections = common.get("course_list") or []
            if isinstance(selections, str):
                selections = [part.strip() for part in selections.split(",") if part.strip()]
            raw.append(
                ProfileConfig(
                    name=name,
                    username=username,
                    password=str(common.get("password") or ""),
                    selections=tuple(str(item).strip() for item in selections if str(item).strip()),
                    path=path,
                )
            )
        by_username: dict[str, list[ProfileConfig]] = {}
        for profile in raw:
            by_username.setdefault(profile.username, []).append(profile)
        for username, profiles in by_username.items():
            hosted = [profile for profile in profiles if profile.name.startswith("hosted-")]
            grouped[username] = hosted or profiles
        return dict(sorted(grouped.items()))

    @staticmethod
    def _running_profiles() -> set[str]:
        try:
            output = subprocess.run(
                ["ps", "-eo", "args="], capture_output=True, text=True, timeout=5, check=False
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return set()
        result: set[str] = set()
        for match in PROFILE_RE.finditer(output):
            result.add(next(group for group in match.groups() if group))
        return result

    def _preferred_profile(self, profiles: list[ProfileConfig], running: set[str] | None = None) -> ProfileConfig:
        running = running or set()
        return max(
            profiles,
            key=lambda profile: (
                profile.name in running,
                profile.path.with_suffix(".cookies.txt").exists(),
                profile.path.with_suffix(".cookies.txt").stat().st_mtime
                if profile.path.with_suffix(".cookies.txt").exists()
                else 0,
            ),
        )

    def _client(self, profile: ProfileConfig) -> Chaoxing:
        configure_runtime(config_path=profile_json_path(profile.name))
        client = Chaoxing(account=Account(profile.username, profile.password))
        state = client.login(login_with_cookies=True)
        if not state.get("status"):
            raise RuntimeError(state.get("msg") or "\u767b\u5f55\u5931\u8d25")
        return client

    def _profile_log_paths(self, profile: ProfileConfig) -> list[Path]:
        names = [profile.name]
        if profile.name.startswith("hosted-"):
            names.append(profile.name[len("hosted-") :])
        paths = [self.hosted_dir / f"{name}.log" for name in names]
        return [path for path in paths if path.exists()]

    def _signals(self, profiles: list[ProfileConfig]) -> dict[str, Any]:
        texts = [tail_text(path) for profile in profiles for path in self._profile_log_paths(profile)]
        text = "\n".join(texts)
        current = re.findall(r"\u5f53\u524d\u7ae0\u8282:\s*(.+)", text)
        return {"text": text, "current_chapters": set(current[-30:])}

    def _scan_account(
        self, username: str, profiles: list[ProfileConfig], running_profiles: set[str]
    ) -> dict[str, Any]:
        with self._platform_lock:
            preferred = self._preferred_profile(profiles, running_profiles)
            client = self._client(preferred)
            session = SessionManager.get_session()
            name = client.get_name() or username
            fid = str(client.get_fid() or "")
            manage = session.get("https://passport2.chaoxing.com/mooc/accountManage", timeout=12)
            school = parse_school(manage.text if manage.status_code == 200 else "", fid)
            all_courses = client.get_course_list()
            signals = self._signals(profiles)
            rows: list[dict[str, Any]] = []
            total_done = total_points = 0
            selected_count = 0
            for course in all_courses:
                key = course_key(course)
                selected_profiles = [profile for profile in profiles if profile_selects(profile, course)]
                selected = bool(selected_profiles)
                active_profiles = [profile for profile in selected_profiles if profile.name in running_profiles]
                row: dict[str, Any] = {
                    "key": key,
                    "title": str(course.get("title") or "\u672a\u547d\u540d\u8bfe\u7a0b"),
                    "teacher": str(course.get("teacher") or ""),
                    "course_id": str(course.get("courseId") or ""),
                    "class_id": str(course.get("clazzId") or ""),
                    "selected": selected,
                    "running": bool(active_profiles),
                    "runner_profiles": [profile.name for profile in selected_profiles],
                    "status": "not_selected",
                    "progress": None,
                    "chapters": [],
                }
                if selected:
                    selected_count += 1
                    try:
                        result = client.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
                        chapters = []
                        done = 0
                        for point in result.get("points") or []:
                            complete = bool(point.get("has_finished"))
                            done += int(complete)
                            point_title = str(point.get("title") or "\u672a\u547d\u540d\u7ae0\u8282")
                            if complete:
                                point_state = "completed"
                            elif point.get("is_expired"):
                                point_state = "failed"
                            elif point_title in signals["current_chapters"] and active_profiles:
                                point_state = "running"
                            elif point_title and point_title in signals["text"]:
                                point_state = "executed"
                            elif point.get("need_unlock"):
                                point_state = "locked"
                            else:
                                point_state = "pending"
                            chapters.append(
                                {
                                    "id": str(point.get("id") or ""),
                                    "title": point_title,
                                    "job_count": int(point.get("jobCount") or 0),
                                    "completed": complete,
                                    "locked": bool(point.get("need_unlock")),
                                    "challenge": bool(point.get("challenge")),
                                    "state": point_state,
                                }
                            )
                        total = len(chapters)
                        platform_done = done
                        accepted = accepted_course_gap(row["title"], platform_done, total)
                        done = min(total, platform_done + accepted)
                        percent = round(done * 100 / total, 1) if total else 0
                        row["progress"] = {"done": done, "total": total, "percent": percent}
                        if accepted:
                            row["platform_progress"] = {
                                "done": platform_done,
                                "total": total,
                                "percent": round(platform_done * 100 / total, 1),
                            }
                            row["accepted_incomplete"] = accepted
                        row["chapters"] = chapters
                        total_done += done
                        total_points += total
                        failed_pattern = re.escape(row["title"]) + r".*(?:\u6267\u884c\u5931\u8d25|ERROR|\u9519\u8bef)"
                        if not total:
                            row["status"] = "unavailable"
                        elif done == total:
                            row["status"] = "completed"
                        elif re.search(failed_pattern, signals["text"], re.I):
                            row["status"] = "failed"
                        elif active_profiles:
                            row["status"] = "running"
                        elif f"\u5f00\u59cb\u5b66\u4e60\u8bfe\u7a0b: {row['title']}" in signals["text"]:
                            row["status"] = "executed"
                        else:
                            row["status"] = "queued"
                    except Exception as exc:
                        row["status"] = "failed"
                        row["error"] = f"{type(exc).__name__}: {exc}"
                rows.append(row)
            rows.sort(key=lambda item: (not item["selected"], item["status"] == "completed", item["title"]))
            percent = round(total_done * 100 / total_points, 1) if total_points else 0
            is_running = any(profile.name in running_profiles for profile in profiles)
            archived = not any(profile.name.startswith("hosted-") for profile in profiles)
            return {
                "username": username,
                "name": name,
                "school": school,
                "archived": archived,
                "fid": fid,
                "status": "archived" if archived else ("running" if is_running else ("completed" if total_points and total_done == total_points else "idle")),
                "profiles": [profile.name for profile in profiles],
                "selected_courses": selected_count,
                "progress": {"done": total_done, "total": total_points, "percent": percent},
                "courses": rows,
                "error": "",
            }

    def _find_group(self, username: str) -> list[ProfileConfig]:
        profiles = self._profile_groups().get(username)
        if not profiles:
            raise KeyError("\u672a\u627e\u5230\u8d26\u53f7")
        return profiles

    def _find_course(self, client: Chaoxing, key: str) -> dict[str, Any]:
        for course in client.get_course_list():
            if course_key(course) == key:
                return course
        raise KeyError("\u672a\u627e\u5230\u8bfe\u7a0b")

    def chapter_tasks(self, username: str, key: str, point_id: str) -> dict[str, Any]:
        with self._platform_lock:
            profiles = self._find_group(username)
            client = self._client(self._preferred_profile(profiles))
            course = self._find_course(client, key)
            session = SessionManager.get_session()
            chapter = session.get(
                "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
                params={
                    "courseId": course["courseId"],
                    "clazzid": course["clazzId"],
                    "chapterId": point_id,
                    "cpi": course["cpi"],
                    "mooc2": 1,
                },
                timeout=25,
            )
            chapter.raise_for_status()
            count = chapter_card_count(chapter.text)
            if count is None:
                raise RuntimeError("\u65e0\u6cd5\u786e\u8ba4\u4efb\u52a1\u5361\u6570\u91cf")
            pages: list[str] = []
            for number in range(count):
                response = session.get(
                    "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards",
                    params={
                        "clazzid": course["clazzId"],
                        "courseid": course["courseId"],
                        "knowledgeid": point_id,
                        "ut": "s",
                        "cpi": course["cpi"],
                        "v": "2025-0424-1038-3",
                        "mooc2": 1,
                        "num": number,
                    },
                    timeout=25,
                )
                response.raise_for_status()
                pages.append(response.text)
            tasks = parse_task_cards(pages)
            return {"course": course.get("title", ""), "chapter_id": point_id, "tasks": tasks}

    def assessments(self, username: str, kind: str) -> dict[str, Any]:
        if kind not in {"homework", "exam"}:
            raise ValueError("kind must be homework or exam")
        cache_key = (username, kind)
        cached = self._assessment_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 120:
            return copy.deepcopy(cached[1])
        with self._platform_lock:
            profiles = self._find_group(username)
            client = self._client(self._preferred_profile(profiles))
            selected_courses = [
                course
                for course in client.get_course_list()
                if any(profile_selects(profile, course) for profile in profiles)
            ]
            rows: list[dict[str, Any]] = []
            for course in selected_courses:
                try:
                    items = self._assessment_course(client, course, kind)
                    rows.append({"course_key": course_key(course), "course": course["title"], "items": items})
                except Exception as exc:
                    rows.append(
                        {
                            "course_key": course_key(course),
                            "course": course.get("title", ""),
                            "items": [],
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            states = Counter(item["state"] for row in rows for item in row["items"])
            result = {
                "username": username,
                "kind": kind,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "summary": {"total": sum(states.values()), **states},
                "courses": rows,
            }
            self._assessment_cache[cache_key] = (time.monotonic(), result)
            return copy.deepcopy(result)

    def _assessment_course(self, client: Chaoxing, course: dict[str, Any], kind: str) -> list[dict[str, str]]:
        session = SessionManager.get_session()
        middle = session.get(
            "https://mooc1.chaoxing.com/visit/stucoursemiddle",
            params={
                "courseid": course["courseId"],
                "clazzid": course["clazzId"],
                "cpi": course["cpi"],
                "ismooc2": 1,
                "v": 2,
            },
            timeout=25,
        )
        middle.raise_for_status()
        soup = BeautifulSoup(middle.text, "lxml")

        def value(identifier: str) -> str:
            node = soup.select_one(f"#{identifier}")
            return str(node.get("value") or "") if node else ""

        if kind == "homework":
            url = "https://mooc1.chaoxing.com/mooc2/work/list"
            params = {
                "courseId": course["courseId"],
                "classId": course["clazzId"],
                "cpi": course["cpi"],
                "ut": "s",
                "enc": value("workEnc"),
            }
        else:
            url = "https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list"
            params = {
                "courseid": course["courseId"],
                "clazzid": course["clazzId"],
                "cpi": course["cpi"],
                "ut": "s",
                "t": value("t"),
                "enc": value("examEnc"),
                "openc": value("openc"),
            }
        response = session.get(url, params=params, timeout=25)
        response.raise_for_status()
        return parse_assessment_html(response.text, kind)
