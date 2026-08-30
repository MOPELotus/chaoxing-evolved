from __future__ import annotations

import enum
import math
import threading
import time
import traceback
from dataclasses import dataclass
from queue import PriorityQueue, ShutDown
from threading import RLock
from typing import Any

from tqdm import tqdm

from api.answer import Tiku
from api.base import Account, Chaoxing, StudyResult, is_expired_task_text
from api.course_selection import course_class_key, course_matches_selection
from api.exceptions import LoginError
from api.json_store import (
    build_config_sections,
    build_effective_profile,
    load_global_settings,
    load_json_profile,
    profile_json_path,
)
from api.live import Live
from api.live_process import LiveProcessor
from api.logger import logger
from api.runtime import configure_runtime


class ChapterResult(enum.Enum):
    SUCCESS = 0
    ERROR = 1
    NOT_OPEN = 2
    PENDING = 3
    FATAL = 4


def log_error(func):
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except BaseException as exc:
            logger.error(f"线程 {threading.current_thread().name} 发生异常: {exc}")
            traceback.print_exception(type(exc), exc, exc.__traceback__)
            raise

    return wrapper


def to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def is_challenge_point(point: dict[str, Any]) -> bool:
    challenge_values = {
        "1", "true", "yes", "challenge", "challenge_mode", "闯关", "挑战"
    }
    return any(
        str(point.get(key, "")).strip().lower() in challenge_values
        for key in ("challenge", "isChallenge", "challengeMode", "闯关", "挑战")
    )


_AUDIO_EXTENSIONS = {
    ".aac",
    ".amr",
    ".flac",
    ".m4a",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}


def is_audio_media_job(job: dict[str, Any]) -> bool:
    """Prefer the audio endpoint when the card metadata clearly describes audio."""
    property_data = job.get("property") if isinstance(job.get("property"), dict) else {}
    names = (
        job.get("name"),
        job.get("title"),
        job.get("filename"),
        property_data.get("name"),
        property_data.get("title"),
        property_data.get("filename"),
    )
    for value in names:
        normalized = str(value or "").split("?", 1)[0].split("#", 1)[0].strip().lower()
        if any(normalized.endswith(extension) for extension in _AUDIO_EXTENSIONS):
            return True

    metadata_keys = (
        "media_type",
        "mediaType",
        "mime",
        "mime_type",
        "mimeType",
        "contentType",
        "resource_type",
        "resourceType",
        "format",
        "suffix",
        "extension",
        "ext",
    )
    for source in (job, property_data):
        for key in metadata_keys:
            normalized = str(source.get(key) or "").strip().lower()
            if "audio" in normalized:
                return True
            if normalized and f".{normalized.lstrip('.')}" in _AUDIO_EXTENSIONS:
                return True
    return False


def _normalize_common_config(section: dict[str, Any]) -> dict[str, Any]:
    course_list = section.get("course_list", []) or []
    if isinstance(course_list, str):
        course_list = [item.strip() for item in course_list.split(",") if item.strip()]
    else:
        course_list = [str(item).strip() for item in course_list if str(item).strip()]

    return {
        "use_cookies": to_bool(section.get("use_cookies", False)),
        "cookies_path": str(section.get("cookies_path", "") or "").strip(),
        "cache_path": str(section.get("cache_path", "") or "").strip(),
        "username": str(section.get("username", "") or "").strip(),
        "password": str(section.get("password", "") or "").strip(),
        "course_list": course_list,
        "speed": float(section.get("speed", 1.0) or 1.0),
        "jobs": max(1, int(section.get("jobs", 4) or 4)),
        "notopen_action": str(section.get("notopen_action", "retry") or "retry").strip(),
        "challenge_retry_attempts": max(1, min(3, int(section.get("challenge_retry_attempts", 3) or 3))),
        "add_learning_count": to_bool(section.get("add_learning_count", False)),
        "target_count": max(0, int(section.get("target_count", 100) or 0)),
        "reading_duration_seconds": max(0, int(section.get("reading_duration_seconds", 0) or 0)),
    }


def build_runner_config(profile: dict, global_settings: dict | None = None) -> tuple[dict[str, Any], dict[str, str], dict[str, str], dict]:
    effective_profile = build_effective_profile(profile, global_settings)
    config_sections = build_config_sections(effective_profile, global_settings)
    common_config = _normalize_common_config(effective_profile.get("common", {}))
    # Notification settings are retained in profile files for forward/backward
    # compatibility, but are deliberately not part of the runner anymore.
    return common_config, config_sections["tiku"], {}, effective_profile


def configure_profile_runtime(profile_name: str, common_config: dict[str, Any]) -> None:
    configure_runtime(
        config_path=profile_json_path(profile_name),
        cookies_path=common_config.get("cookies_path") or None,
        cache_path=common_config.get("cache_path") or None,
    )


def init_chaoxing(common_config: dict[str, Any], tiku_config: dict[str, Any]) -> Chaoxing:
    username = common_config.get("username", "")
    password = common_config.get("password", "")
    use_cookies = common_config.get("use_cookies", False)

    if (not username or not password) and not use_cookies:
        raise ValueError("当前配置未填写账号密码，也未启用 Cookies 登录。")

    account = Account(username, password)

    tiku = Tiku()
    tiku.config_set(tiku_config)
    tiku = tiku.get_tiku_from_config()
    tiku.init_tiku()

    check_connection = to_bool(tiku_config.get("check_llm_connection", "true"))
    if check_connection:
        logger.info("正在验证题库连接配置...")
        if not tiku.check_llm_connection():
            raise RuntimeError("题库连接检查失败，请检查当前配置或关闭连接检查。")

    query_delay = float(tiku_config.get("delay", 0) or 0)
    return Chaoxing(account=account, tiku=tiku, query_delay=query_delay)


def process_job(
    chaoxing: Chaoxing,
    course: dict,
    job: dict,
    job_info: dict,
    speed: float,
    reading_duration_seconds: int = 0,
    challenge_attempt: int = 0,
) -> StudyResult:
    if job["type"] == "video":
        preferred_type = "Audio" if is_audio_media_job(job) else "Video"
        fallback_type = "Video" if preferred_type == "Audio" else "Audio"
        logger.trace(
            "识别到{}任务, 任务章节: {} 任务ID: {}",
            "音频" if preferred_type == "Audio" else "视频",
            course["title"],
            job["jobid"],
        )
        media_result = chaoxing.study_video(
            course, job, job_info, _speed=speed, _type=preferred_type
        )
        if media_result.is_failure():
            logger.warning(
                "{}模式处理失败，正在尝试{}模式: {}",
                preferred_type,
                fallback_type,
                job.get("name") or job.get("jobid", ""),
            )
            media_result = chaoxing.study_video(
                course, job, job_info, _speed=speed, _type=fallback_type
            )
        if media_result.is_failure():
            logger.warning(f"出现异常任务 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过")
        return media_result

    if job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        return chaoxing.study_document(course, job)

    if job["type"] == "workid":
        logger.trace(f"识别到章节检测任务, 任务章节: {course['title']}")
        if job.get("is_expired") or is_expired_task_text(job.get("status")) or is_expired_task_text(job.get("deadline")):
            logger.warning("章节检测任务已过期，按跳过处理: {}", job.get("jobid", ""))
            return StudyResult.SKIPPED
        return chaoxing.study_work(course, job, job_info, force_ai_refresh=challenge_attempt > 0)

    if job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
        result = chaoxing.study_read(course, job, job_info)
        if result.is_success() and reading_duration_seconds > 0:
            return chaoxing.study_read_duration(course, job_info, reading_duration_seconds)
        return result

    if job["type"] == "live":
        logger.trace(f"识别到直播任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        try:
            defaults = {
                "userid": chaoxing.get_uid(),
                "clazzId": course.get("clazzId"),
                "knowledgeid": job_info.get("knowledgeid"),
            }
            live = Live(
                attachment=job,
                defaults=defaults,
                course_id=course.get("courseId"),
            )
            live_result = {"ok": False}

            def run_live() -> None:
                live_result["ok"] = bool(LiveProcessor.run_live(live, 1.0))

            thread = threading.Thread(
                target=run_live,
                # Accelerated live playback does not satisfy the platform's
                # duration accounting, so live tasks always run at 1x.
                daemon=True,
            )
            thread.start()
            thread.join()
            return StudyResult.SUCCESS if live_result["ok"] else StudyResult.ERROR
        except Exception as exc:
            logger.error(f"处理直播任务时出错: {exc}")
            return StudyResult.ERROR

    logger.error(f"未知任务类型: {job['type']}")
    return StudyResult.ERROR


@dataclass(order=True)
class ChapterTask:
    index: int
    point: dict[str, Any]
    result: ChapterResult = ChapterResult.PENDING
    tries: int = 0


class JobProcessor:
    def __init__(self, chaoxing: Chaoxing, course: dict[str, Any], tasks: list[ChapterTask], config: dict[str, Any]):
        if "jobs" not in config or not config["jobs"]:
            config["jobs"] = 4

        self.chaoxing = chaoxing
        self.course = course
        self.speed = config["speed"]
        self.normal_max_tries = 5
        self.challenge_max_tries = max(
            1, min(3, int(config.get("challenge_retry_attempts", 3) or 3))
        )
        self.tasks = tasks
        self.failed_tasks: list[ChapterTask] = []
        self.abort_event = threading.Event()
        self.fatal_error = ""
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.worker_num = config["jobs"]
        self.config = config

    @staticmethod
    def _is_challenge(point: dict[str, Any]) -> bool:
        return is_challenge_point(point)

    def run(self) -> None:
        for task in self.tasks:
            self.task_queue.put(task)

        for _ in range(self.worker_num):
            thread = threading.Thread(target=self.worker_thread, daemon=True)
            self.threads.append(thread)
            thread.start()

        threading.Thread(target=self.retry_thread, daemon=True).start()

        self.task_queue.join()
        time.sleep(0.5)
        self.task_queue.shutdown()

    @log_error
    def worker_thread(self) -> None:
        tqdm.set_lock(tqdm.get_lock())
        while True:
            try:
                task = self.task_queue.get()
            except ShutDown:
                return

            if self.abort_event.is_set():
                self.task_queue.task_done()
                continue

            task.result = process_chapter(
                self.chaoxing,
                self.course,
                task.point,
                self.speed,
                reading_duration_seconds=self.config.get("reading_duration_seconds", 0),
                challenge_attempt=task.tries,
                abort_event=self.abort_event,
            )

            if self.abort_event.is_set() and task.result != ChapterResult.FATAL:
                self.task_queue.task_done()
                continue

            match task.result:
                case ChapterResult.SUCCESS:
                    logger.debug("Task success: {}", task.point["title"])
                    self.task_queue.task_done()
                    logger.debug(f"unfinished task: {self.task_queue.unfinished_tasks}")

                case ChapterResult.NOT_OPEN:
                    if self.config["notopen_action"] == "continue":
                        logger.warning("章节未开启: {}, 正在跳过", task.point["title"])
                        self.task_queue.task_done()
                        continue

                    if task.tries >= self.normal_max_tries:
                        logger.error(
                            "章节未开启: {} 可能由于上一章节的章节检测未完成，也可能由于章节已关闭，请手动检查后再试。",
                            task.point["title"],
                        )
                        self.task_queue.task_done()
                        continue

                    self.retry_queue.put(task)

                case ChapterResult.ERROR:
                    task.tries += 1
                    retry_limit = (
                        self.challenge_max_tries
                        if self._is_challenge(task.point)
                        else self.normal_max_tries
                    )
                    if task.tries >= retry_limit:
                        logger.error(
                            "任务失败，已达到重试上限: {} ({}/{} 次尝试)",
                            task.point["title"],
                            task.tries,
                            retry_limit,
                        )
                        self.failed_tasks.append(task)
                        self.task_queue.task_done()
                        continue
                    logger.warning(
                        "任务失败，准备重试: {} ({}/{} 次尝试)",
                        task.point["title"],
                        task.tries,
                        retry_limit,
                    )
                    self.retry_queue.put(task)

                case ChapterResult.FATAL:
                    self.fatal_error = str(
                        task.point.get("_fatal_error") or "任务执行遇到不可恢复的阻断"
                    )
                    self.abort_event.set()
                    self.failed_tasks.append(task)
                    logger.error(
                        "课程任务已停止: {} - {}",
                        task.point.get("title", ""),
                        self.fatal_error,
                    )
                    self.task_queue.task_done()

                case _:
                    logger.error("Invalid task state {} for task {}", task.result, task.point["title"])
                    self.failed_tasks.append(task)
                    self.task_queue.task_done()

    @log_error
    def retry_thread(self) -> None:
        try:
            while True:
                task = self.retry_queue.get()
                if self.abort_event.is_set():
                    self.retry_queue.task_done()
                    self.task_queue.task_done()
                    continue
                self.task_queue.put(task)
                self.retry_queue.task_done()
                self.task_queue.task_done()
                time.sleep(1)
        except ShutDown:
            return


def process_chapter(
    chaoxing: Chaoxing,
    course: dict[str, Any],
    point: dict[str, Any],
    speed: float,
    reading_duration_seconds: int = 0,
    challenge_attempt: int = 0,
    abort_event: threading.Event | None = None,
) -> ChapterResult:
    logger.info(f'当前章节: {point["title"]}')
    if abort_event is not None and abort_event.is_set():
        return ChapterResult.ERROR
    if is_expired_task_text(point.get("title")) or is_expired_task_text(point.get("status")):
        logger.warning("章节任务已过期，按完成处理: {}", point.get("title", ""))
        return ChapterResult.SUCCESS
    if point["has_finished"]:
        logger.info(f'章节：{point["title"]} 已完成所有任务点')
        return ChapterResult.SUCCESS

    chaoxing.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)
    if abort_event is not None and abort_event.is_set():
        return ChapterResult.ERROR
    jobs, job_info = chaoxing.get_job_list(course, point)

    if job_info.get("fatal_error"):
        point["_fatal_error"] = str(job_info["fatal_error"])
        if abort_event is not None:
            abort_event.set()
        return ChapterResult.FATAL

    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    job_results: list[StudyResult] = []
    for job in jobs:
        if abort_event is not None and abort_event.is_set():
            return ChapterResult.ERROR
        result = process_job(
            chaoxing,
            course,
            job,
            job_info,
            speed,
            reading_duration_seconds,
            challenge_attempt,
        )
        job_results.append(result)
        if result.is_failure():
            return ChapterResult.ERROR

    skipped_result = any(result == StudyResult.SKIPPED for result in job_results)
    # A successful work POST only means that Chaoxing accepted the request. It
    # may still contain empty native fields or receive a score below the task
    # point's pass line. Re-read ordinary homework points as well so those
    # cases enter the existing retry/AI-refresh path instead of being reported
    # as locally successful.
    requires_confirmation = is_challenge_point(point) or any(
        job.get("type") in {"live", "workid"} for job in jobs
    )
    if not requires_confirmation:
        return ChapterResult.SUCCESS

    try:
        fresh_points = chaoxing.get_course_point(
            course["courseId"], course["clazzId"], course["cpi"]
        ).get("points", [])
        fresh = next(
            (item for item in fresh_points if str(item.get("id")) == str(point.get("id"))),
            None,
        )
        if fresh and fresh.get("has_finished"):
            return ChapterResult.SUCCESS
        challenge = is_challenge_point(point)
        if skipped_result:
            logger.info(
                "章节包含已过期且不可提交的任务，平台不会将该卡片标记为完成；按跳过处理: {}",
                point["title"],
            )
            return ChapterResult.SUCCESS
        logger.warning(
            f"{'挑战' if challenge else '知识点'} {point['title']} 完成本地任务后仍未被平台确认"
        )
        return ChapterResult.ERROR
    except Exception as exc:
        logger.warning(f"重新读取章节完成状态失败: {exc}")
        return ChapterResult.ERROR

    return ChapterResult.ERROR


def process_course(chaoxing: Chaoxing, course: dict[str, Any], config: dict[str, Any]) -> None:
    logger.info(f"开始学习课程: {course['title']}")
    point_list = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])

    old_format_sizeof = tqdm.format_sizeof
    tqdm.format_sizeof = format_time
    tqdm.set_lock(RLock())

    tasks = []
    for index, point in enumerate(point_list["points"]):
        tasks.append(ChapterTask(point=point, index=index))

    processor = JobProcessor(chaoxing, course, tasks, config)
    processor.run()
    if processor.fatal_error:
        raise RuntimeError(f"课程 [{course['title']}] 已停止: {processor.fatal_error}")
    if processor.failed_tasks:
        failed_titles = ", ".join(task.point.get("title", "") for task in processor.failed_tasks)
        raise RuntimeError(f"课程 [{course['title']}] 存在未完成章节: {failed_titles}")
    if to_bool(config.get("add_learning_count", False)):
        target_count = max(0, int(config.get("target_count", 100) or 0))
        logger.info(f"开始增加课程章节学习次数，本次计划: {target_count}")
        result = chaoxing.increase_chapter_learning_count(course, point_list["points"], target_count)
        if result.is_failure():
            raise RuntimeError(f"课程 [{course['title']}] 章节学习次数增加失败")
    tqdm.format_sizeof = old_format_sizeof


def filter_courses(all_course: list[dict], course_list: list[str]) -> list[dict]:
    if not course_list:
        logger.info("当前未指定课程范围，默认处理全部课程。")
        return all_course

    course_task = []
    seen_classes: set[str] = set()
    for course in all_course:
        selection_key = course_class_key(course)
        if (
            selection_key
            and selection_key not in seen_classes
            and course_matches_selection(course, course_list)
        ):
            course_task.append(course)
            seen_classes.add(selection_key)

    if not course_task:
        raise ValueError("当前配置中的课程列表未匹配到任何有效课程，请先刷新课程列表后重新选择。")

    return course_task


def format_time(num, suffix="", divisor=""):
    total_time = round(num)
    sec = total_time % 60
    mins = (total_time % 3600) // 60
    hrs = total_time // 3600

    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{sec:02d}"
    return f"{mins:02d}:{sec:02d}"


def run_loaded_profile(profile: dict, global_settings: dict | None = None) -> None:
    try:
        common_config, tiku_config, _notification_config, effective_profile = build_runner_config(profile, global_settings)

        if common_config["speed"] <= 0 or not math.isfinite(common_config["speed"]):
            raise ValueError("倍速必须是大于 0 的有限数字")
        common_config["notopen_action"] = common_config.get("notopen_action", "retry") or "retry"

        configure_profile_runtime(effective_profile["name"], common_config)

        chaoxing = init_chaoxing(common_config, tiku_config)
        login_state = chaoxing.login(login_with_cookies=common_config.get("use_cookies", False))
        if not login_state["status"]:
            raise LoginError(login_state["msg"])

        all_course = chaoxing.get_course_list()
        course_task = filter_courses(all_course, common_config.get("course_list", []))

        logger.info(f"课程列表过滤完毕, 当前课程任务数量: {len(course_task)}")
        for course in course_task:
            process_course(chaoxing, course, common_config)

        logger.info("所有课程学习任务已完成")
    except KeyboardInterrupt as exc:
        logger.error(f"错误: 程序被用户手动中断, {exc}")
        raise
    except BaseException as exc:
        logger.error(f"错误: {type(exc).__name__}: {exc}")
        raise


def run_named_profile(profile_name: str) -> None:
    profile = load_json_profile(profile_name)
    global_settings = load_global_settings()
    run_loaded_profile(profile, global_settings)
