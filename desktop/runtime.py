from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from api.base import Account, Chaoxing
from api.course_selection import course_class_key, course_matches_selection
from api.json_store import (
    build_effective_profile,
    load_global_settings,
    load_json_profile,
    profile_json_path,
)
from api.runtime import configure_runtime
from desktop.worker import WORKER_FLAG


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_APP_ENTRY = PROJECT_ROOT / "desktop_app.py"
ANSI_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
PROFILE_IO_LOCK = threading.RLock()
RUN_LOG_DIR = PROJECT_ROOT / "desktop_state" / "logs"


def strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


def _runtime_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _format_time(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _build_run_log_path(profile_name: str, started_at: float, run_id: str) -> Path:
    date_prefix = datetime.fromtimestamp(started_at).strftime("%Y%m%d-%H%M%S")
    profile_dir = RUN_LOG_DIR / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir / f"{date_prefix}-{run_id}.log"


def _is_compiled_desktop_app() -> bool:
    if getattr(sys, "frozen", False):
        return True
    if "__compiled__" in globals():
        return True

    try:
        return Path(sys.executable).resolve() == Path(sys.argv[0]).resolve()
    except OSError:
        return False


@dataclass
class DesktopRunState:
    id: str
    profile_name: str
    profile_path: Path
    command: list[str]
    started_at: float
    log_path: Path | None = None
    status: str = "running"
    exit_code: int | None = None
    ended_at: float | None = None
    logs: list[str] = field(default_factory=list)
    process: subprocess.Popen | None = field(default=None, repr=False)


class RunManager(QObject):
    runs_changed = pyqtSignal()
    log_received = pyqtSignal(str, str)
    run_finished = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._runs: dict[str, DesktopRunState] = {}
        self._lock = threading.RLock()

    def _build_command(self, profile_name: str) -> list[str]:
        if _is_compiled_desktop_app():
            return [sys.executable, WORKER_FLAG, profile_name]
        return [sys.executable, str(DESKTOP_APP_ENTRY), WORKER_FLAG, profile_name]

    def list_runs(self) -> list[DesktopRunState]:
        with self._lock:
            return sorted(self._runs.values(), key=lambda item: item.started_at, reverse=True)

    def get_run(self, profile_name: str) -> DesktopRunState | None:
        with self._lock:
            return self._runs.get(profile_name)

    def start_profile(self, profile_name: str) -> DesktopRunState:
        with self._lock:
            current_run = self._runs.get(profile_name)
            if current_run and current_run.status == "running":
                raise ValueError(f"{profile_name} 已在运行中")

            profile = load_json_profile(profile_name)
            global_settings = load_global_settings()
            profile_path = profile_json_path(profile["name"])
            if not profile_path.exists():
                raise FileNotFoundError(f"{profile_name} 的配置文件不存在")
            command = self._build_command(profile["name"])
            started_at = time.time()
            log_path = _build_run_log_path(profile["name"], started_at, uuid.uuid4().hex[:8])
            env = os.environ.copy()
            env["DESKTOP_MANAGED_RUN"] = "1"
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf8",
                errors="replace",
                bufsize=1,
                env=env,
            )
            run_state = DesktopRunState(
                id=uuid.uuid4().hex[:8],
                profile_name=profile["name"],
                profile_path=profile_path,
                command=command,
                started_at=started_at,
                log_path=log_path,
                process=process,
            )
            self._runs[profile["name"]] = run_state
            self._write_log_line(run_state, f"[系统] {_format_time(started_at)} 任务已启动")
            self._write_log_line(run_state, f"[系统] 配置文件: {profile_path}")
            self._write_log_line(run_state, f"[系统] 运行命令: {' '.join(command)}")

        reader = threading.Thread(
            target=self._pump_output,
            args=(profile["name"],),
            daemon=True,
            name=f"DesktopRun-{profile['name']}",
        )
        reader.start()
        self.runs_changed.emit()
        return run_state

    def _pump_output(self, profile_name: str) -> None:
        run_state = self.get_run(profile_name)
        if not run_state or not run_state.process or not run_state.process.stdout:
            return

        for line in run_state.process.stdout:
            clean_line = strip_ansi(line.rstrip())
            if not clean_line:
                continue
            with self._lock:
                current = self._runs.get(profile_name)
                if not current:
                    return
                current.logs.append(clean_line)
                if len(current.logs) > 2000:
                    current.logs = current.logs[-2000:]
                self._write_log_line(current, clean_line)
            self.log_received.emit(profile_name, clean_line)

        run_state.process.wait()
        notify_run: DesktopRunState | None = None
        with self._lock:
            current = self._runs.get(profile_name)
            if not current:
                return
            current.exit_code = run_state.process.returncode
            if current.status == "running":
                current.status = "completed" if current.exit_code == 0 else "failed"
            current.ended_at = time.time()
            current.process = None
            notify_run = current
            self._write_log_line(
                current,
                f"[系统] {_format_time(current.ended_at)} 任务结束，状态: {current.status}，退出码: {current.exit_code}",
            )
        self.runs_changed.emit()
        if notify_run:
            self.run_finished.emit(notify_run.profile_name, notify_run.status)

    def stop_profile(self, profile_name: str) -> None:
        with self._lock:
            run_state = self._runs.get(profile_name)
            if not run_state or run_state.status != "running" or not run_state.process:
                raise ValueError(f"{profile_name} 当前没有运行中的任务")

            run_state.process.terminate()
            try:
                run_state.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                run_state.process.kill()
                run_state.process.wait(timeout=5)

            run_state.exit_code = run_state.process.returncode
            run_state.status = "stopped"
            run_state.ended_at = time.time()
            self._write_log_line(
                run_state,
                f"[系统] {_format_time(run_state.ended_at)} 任务已停止，退出码: {run_state.exit_code}",
            )
        self.runs_changed.emit()

    def logs_for_profile(self, profile_name: str) -> str:
        run_state = self.get_run(profile_name)
        if not run_state:
            return ""
        return "\n".join(run_state.logs)

    def remove_profile_state(self, profile_name: str, stop_running: bool = True) -> None:
        run_state = self.get_run(profile_name)
        if run_state and run_state.status == "running":
            if not stop_running:
                raise ValueError(f"{profile_name} 仍在运行中")
            self.stop_profile(profile_name)

        removed = False
        with self._lock:
            if profile_name in self._runs:
                self._runs.pop(profile_name, None)
                removed = True

        if removed:
            self.runs_changed.emit()

    def _write_log_line(self, run_state: DesktopRunState, line: str) -> None:
        if not run_state.log_path:
            return
        run_state.log_path.parent.mkdir(parents=True, exist_ok=True)
        with run_state.log_path.open("a", encoding="utf8") as fp:
            fp.write(line.rstrip() + "\n")

def fetch_courses_for_profile(profile_name: str) -> list[dict]:
    with PROFILE_IO_LOCK:
        profile = load_json_profile(profile_name)
        global_settings = load_global_settings()
        effective_profile = build_effective_profile(profile, global_settings)
        common = effective_profile.get("common", {})

        configure_runtime(
            config_path=profile_json_path(effective_profile["name"]),
            cookies_path=common.get("cookies_path") or None,
            cache_path=common.get("cache_path") or None,
        )

        account = Account(str(common.get("username", "") or ""), str(common.get("password", "") or ""))
        chaoxing = Chaoxing(account=account, tiku=None, query_delay=0)
        login_state = chaoxing.login(login_with_cookies=_runtime_bool(common.get("use_cookies", False)))
        if not login_state["status"]:
            raise ValueError(login_state["msg"])

        selected_courses = list(effective_profile.get("common", {}).get("course_list", []))
        courses = []
        for course in chaoxing.get_course_list():
            courses.append(
                {
                    "courseId": course["courseId"],
                    "clazzId": course["clazzId"],
                    "selectionKey": course_class_key(course),
                    "title": course["title"],
                    "teacher": course.get("teacher", ""),
                    "desc": course.get("desc", ""),
                    "selected": course_matches_selection(course, selected_courses),
                }
            )
        return courses
