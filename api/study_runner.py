from __future__ import annotations

import enum
import os
import threading
import time
import traceback
from concurrent.futures.thread import ThreadPoolExecutor
from dataclasses import dataclass
from queue import PriorityQueue, ShutDown
from threading import RLock
from typing import Any

from tqdm import tqdm

from api.answer import Tiku
from api.base import Account, Chaoxing, StudyResult
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
from api.notification import Notification
from api.runtime import configure_runtime


class ChapterResult(enum.Enum):
    SUCCESS = 0
    ERROR = 1
    NOT_OPEN = 2
    PENDING = 3


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


def should_send_internal_notifications() -> bool:
    return os.environ.get("DESKTOP_MANAGED_RUN", "").strip().lower() not in {"1", "true", "yes", "on"}


def should_initialize_internal_notifications() -> bool:
    return should_send_internal_notifications()


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
        "jobs": int(section.get("jobs", 4) or 4),
        "notopen_action": str(section.get("notopen_action", "retry") or "retry").strip(),
    }


def build_runner_config(profile: dict, global_settings: dict | None = None) -> tuple[dict[str, Any], dict[str, str], dict[str, str], dict]:
    effective_profile = build_effective_profile(profile, global_settings)
    config_sections = build_config_sections(effective_profile, global_settings)
    common_config = _normalize_common_config(effective_profile.get("common", {}))
    return common_config, config_sections["tiku"], config_sections["notification"], effective_profile


def configure_profile_runtime(profile_name: str, common_config: dict[str, Any]) -> None:
    configure_runtime(
        config_path=profile_json_path(profile_name),
        cookies_path=common_config.get("cookies_path") or None,
        cache_path=common_config.get("cache_path") or None,
    )


def init_chaoxing(common_config: dict[str, Any], tiku_config: dict[str, str]) -> Chaoxing:
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


def process_job(chaoxing: Chaoxing, course: dict, job: dict, job_info: dict, speed: float) -> StudyResult:
    if job["type"] == "video":
        logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        video_result = chaoxing.study_video(course, job, job_info, _speed=speed, _type="Video")
        if video_result.is_failure():
            logger.warning("当前任务非视频任务, 正在尝试音频任务解码")
            video_result = chaoxing.study_video(course, job, job_info, _speed=speed, _type="Audio")
        if video_result.is_failure():
            logger.warning(f"出现异常任务 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过")
        return video_result

    if job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        return chaoxing.study_document(course, job)

    if job["type"] == "workid":
        logger.trace(f"识别到章节检测任务, 任务章节: {course['title']}")
        return chaoxing.study_work(course, job, job_info)

    if job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
        return chaoxing.study_read(course, job, job_info)

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
            thread = threading.Thread(
                target=LiveProcessor.run_live,
                args=(live, speed),
                daemon=True,
            )
            thread.start()
            thread.join()
            return StudyResult.SUCCESS
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
        self.max_tries = 5
        self.tasks = tasks
        self.failed_tasks: list[ChapterTask] = []
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.worker_num = config["jobs"]
        self.config = config

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
                logger.info("任务队列已关闭")
                return

            task.result = process_chapter(self.chaoxing, self.course, task.point, self.speed)

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

                    if task.tries >= self.max_tries:
                        logger.error(
                            "章节未开启: {} 可能由于上一章节的章节检测未完成，也可能由于章节已关闭，请手动检查后再试。",
                            task.point["title"],
                        )
                        self.task_queue.task_done()
                        continue

                    self.retry_queue.put(task)

                case ChapterResult.ERROR:
                    task.tries += 1
                    logger.warning(
                        "Retrying task {} ({}/{} attempts)",
                        task.point["title"],
                        task.tries,
                        self.max_tries,
                    )
                    if task.tries >= self.max_tries:
                        logger.error("Max retries reached for task: {}", task.point["title"])
                        self.failed_tasks.append(task)
                        self.task_queue.task_done()
                        continue
                    self.retry_queue.put(task)

                case _:
                    logger.error("Invalid task state {} for task {}", task.result, task.point["title"])
                    self.failed_tasks.append(task)
                    self.task_queue.task_done()

    @log_error
    def retry_thread(self) -> None:
        try:
            while True:
                task = self.retry_queue.get()
                self.task_queue.put(task)
                self.retry_queue.task_done()
                self.task_queue.task_done()
                time.sleep(1)
        except ShutDown:
            return


def process_chapter(chaoxing: Chaoxing, course: dict[str, Any], point: dict[str, Any], speed: float) -> ChapterResult:
    logger.info(f'当前章节: {point["title"]}')
    if point["has_finished"]:
        logger.info(f'章节：{point["title"]} 已完成所有任务点')
        return ChapterResult.SUCCESS

    chaoxing.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)
    jobs, job_info = chaoxing.get_job_list(course, point)

    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    job_results: list[StudyResult] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for result in executor.map(lambda job: process_job(chaoxing, course, job, job_info, speed), jobs):
            job_results.append(result)

    for result in job_results:
        if result.is_failure():
            return ChapterResult.ERROR

    return ChapterResult.SUCCESS


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
    tqdm.format_sizeof = old_format_sizeof


def filter_courses(all_course: list[dict], course_list: list[str]) -> list[dict]:
    if not course_list:
        logger.info("当前未指定课程范围，默认处理全部课程。")
        return all_course

    selected_ids = {str(course_id).strip() for course_id in course_list if str(course_id).strip()}
    course_task = []
    seen_course_ids = set()
    for course in all_course:
        course_id = course["courseId"]
        if course_id in selected_ids and course_id not in seen_course_ids:
            course_task.append(course)
            seen_course_ids.add(course_id)

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
    notification = Notification()

    try:
        common_config, tiku_config, notification_config, effective_profile = build_runner_config(profile, global_settings)

        common_config["speed"] = min(2.0, max(1.0, common_config.get("speed", 1.0)))
        common_config["notopen_action"] = common_config.get("notopen_action", "retry") or "retry"

        configure_profile_runtime(effective_profile["name"], common_config)

        if should_initialize_internal_notifications():
            notification.config_set(notification_config)
            notification = notification.get_notification_from_config()
            notification.init_notification()

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
        if should_send_internal_notifications():
            notification.send("chaoxing : 所有课程学习任务已完成")
    except KeyboardInterrupt as exc:
        logger.error(f"错误: 程序被用户手动中断, {exc}")
        raise
    except BaseException as exc:
        logger.error(f"错误: {type(exc).__name__}: {exc}")
        logger.error(traceback.format_exc())
        if should_send_internal_notifications():
            try:
                notification.send(f"chaoxing : 出现错误 {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            except Exception:
                pass
        raise


def run_named_profile(profile_name: str) -> None:
    profile = load_json_profile(profile_name)
    global_settings = load_global_settings()
    run_loaded_profile(profile, global_settings)
