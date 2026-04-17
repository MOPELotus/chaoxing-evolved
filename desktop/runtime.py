from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

from api.base import Account, Chaoxing
from api.json_store import build_runtime_sections, load_global_settings, load_json_profile, write_runtime_ini
from api.runtime import configure_runtime


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANSI_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
PROFILE_IO_LOCK = threading.RLock()


def strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


def _runtime_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class DesktopRunState:
    id: str
    profile_name: str
    runtime_config_path: Path
    command: list[str]
    started_at: float
    status: str = "running"
    exit_code: int | None = None
    logs: list[str] = field(default_factory=list)
    process: subprocess.Popen | None = field(default=None, repr=False)


class RunManager(QObject):
    runs_changed = pyqtSignal()
    log_received = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._runs: dict[str, DesktopRunState] = {}
        self._lock = threading.RLock()

    def _build_command(self, runtime_config_path: Path) -> list[str]:
        return [sys.executable, str(PROJECT_ROOT / "main.py"), "-c", str(runtime_config_path)]

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
            runtime_config_path = write_runtime_ini(profile, global_settings)
            command = self._build_command(runtime_config_path)

            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf8",
                errors="replace",
                bufsize=1,
            )
            run_state = DesktopRunState(
                id=uuid.uuid4().hex[:8],
                profile_name=profile_name,
                runtime_config_path=runtime_config_path,
                command=command,
                started_at=time.time(),
                process=process,
            )
            self._runs[profile_name] = run_state

        reader = threading.Thread(
            target=self._pump_output,
            args=(profile_name,),
            daemon=True,
            name=f"DesktopRun-{profile_name}",
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
            self.log_received.emit(profile_name, clean_line)

        run_state.process.wait()
        with self._lock:
            current = self._runs.get(profile_name)
            if not current:
                return
            current.exit_code = run_state.process.returncode
            if current.status == "running":
                current.status = "completed" if current.exit_code == 0 else "failed"
            current.process = None
        self.runs_changed.emit()

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
            run_state.process = None
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


def fetch_courses_for_profile(profile_name: str) -> list[dict]:
    with PROFILE_IO_LOCK:
        profile = load_json_profile(profile_name)
        global_settings = load_global_settings()
        runtime_sections = build_runtime_sections(profile, global_settings)
        runtime_config_path = write_runtime_ini(profile, global_settings)
        common = runtime_sections["common"]

        configure_runtime(
            config_path=runtime_config_path,
            cookies_path=common.get("cookies_path"),
            cache_path=common.get("cache_path"),
        )

        account = Account(common.get("username", ""), common.get("password", ""))
        chaoxing = Chaoxing(account=account, tiku=None, query_delay=0)
        login_state = chaoxing.login(login_with_cookies=_runtime_bool(common.get("use_cookies", "false")))
        if not login_state["status"]:
            raise ValueError(login_state["msg"])

        selected_course_ids = set(profile.get("common", {}).get("course_list", []))
        courses = []
        for course in chaoxing.get_course_list():
            courses.append(
                {
                    "courseId": course["courseId"],
                    "clazzId": course["clazzId"],
                    "title": course["title"],
                    "teacher": course.get("teacher", ""),
                    "desc": course.get("desc", ""),
                    "selected": course["courseId"] in selected_course_ids,
                }
            )
        return courses
