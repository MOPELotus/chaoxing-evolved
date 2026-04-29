from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from api.base import Account, Chaoxing
from api.json_store import (
    build_effective_profile,
    create_json_profile,
    delete_json_profile,
    ensure_desktop_state,
    list_json_profiles,
    load_global_settings,
    load_json_profile,
    profile_json_path,
    profile_summary,
    save_global_settings,
    save_json_profile,
)
from api.provider_catalog import (
    COLLAB_PROVIDER_OPTIONS,
    DECISION_PROVIDER_OPTIONS,
    PROVIDER_OPTIONS,
    provider_items,
)
from api.runtime import configure_runtime
from cmdpro.state import (
    build_run_log_path,
    get_run_record,
    list_run_records,
    load_runs_payload,
    process_exists,
    python_entry_command,
    save_runs_payload,
    terminate_process_tree,
    update_run_record,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CMD_PRO_ENTRY = PROJECT_ROOT / "cmd_pro.py"


class ServiceError(RuntimeError):
    pass


def available_provider_catalog() -> dict[str, Any]:
    return {
        "providers": provider_items(PROVIDER_OPTIONS),
        "collab_providers": provider_items(COLLAB_PROVIDER_OPTIONS),
        "decision_providers": provider_items(DECISION_PROVIDER_OPTIONS),
    }


def selected_course_ids(profile: dict) -> set[str]:
    return {str(item) for item in profile.get("common", {}).get("course_list", [])}


def profile_view(name: str) -> dict[str, Any]:
    profile = load_json_profile(name)
    global_settings = load_global_settings()
    effective_profile = build_effective_profile(profile, global_settings)
    return {
        "profile": profile,
        "effective_profile": effective_profile,
        "summary": profile_summary(profile, global_settings),
        "run": get_run_record(name, refresh=True),
        "path": profile_json_path(name),
    }


def list_profiles_view() -> list[dict[str, Any]]:
    global_settings = load_global_settings()
    payload = []
    runs_by_name = {record["profile_name"]: record for record in list_run_records(refresh=True)}
    for path in list_json_profiles():
        profile = load_json_profile(path.stem)
        payload.append(
            {
                "name": path.stem,
                "path": path,
                "summary": profile_summary(profile, global_settings),
                "run": runs_by_name.get(path.stem),
            }
        )
    return payload


def create_profile(name: str) -> dict[str, Any]:
    create_json_profile(name, force=False)
    return profile_view(name)


def save_profile(profile: dict) -> dict[str, Any]:
    save_json_profile(profile)
    return profile_view(profile["name"])


def remove_profile(name: str, force: bool = False) -> dict[str, Any]:
    run = get_run_record(name, refresh=True)
    if run and run.get("status") == "running" and not force:
        raise ServiceError("目标配置仍在运行中，请先停止任务或使用强制删除。")
    if run and run.get("status") == "running":
        stop_run(name)
    deleted = delete_json_profile(name, remove_runtime_state=True)
    payload = load_runs_payload()
    payload.get("runs", {}).pop(name, None)
    save_runs_payload(payload)
    return {"deleted": [str(path) for path in deleted]}


def load_global_view() -> dict:
    return load_global_settings()


def save_global_view(settings: dict) -> dict:
    save_global_settings(settings)
    return load_global_settings()


def fetch_courses(name: str) -> list[dict[str, Any]]:
    profile = load_json_profile(name)
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
    login_state = chaoxing.login(login_with_cookies=bool(common.get("use_cookies", False)))
    if not login_state.get("status"):
        raise ServiceError(str(login_state.get("msg") or "课程列表获取失败。"))

    selected = selected_course_ids(profile)
    payload = []
    for course in chaoxing.get_course_list():
        payload.append(
            {
                "courseId": course["courseId"],
                "clazzId": course["clazzId"],
                "title": course["title"],
                "teacher": course.get("teacher", ""),
                "desc": course.get("desc", ""),
                "selected": str(course["courseId"]) in selected,
            }
        )
    return payload


def _build_start_command(profile_name: str, run_id: str, log_path: Path) -> list[str]:
    return python_entry_command() + [
        str(CMD_PRO_ENTRY),
        "--mode",
        "worker-host",
        "--name",
        profile_name,
        "--run-id",
        run_id,
        "--log-path",
        str(log_path),
    ]


def list_runs_view() -> list[dict[str, Any]]:
    return list_run_records(refresh=True)


def start_run(name: str) -> dict[str, Any]:
    ensure_desktop_state()
    profile = load_json_profile(name)
    current_run = get_run_record(profile["name"], refresh=True)
    if current_run and current_run.get("status") == "running" and process_exists(int(current_run.get("host_pid") or 0)):
        raise ServiceError(f"{profile['name']} 已在运行中。")

    started_at = time.time()
    run_id = uuid.uuid4().hex[:8]
    log_path = build_run_log_path(profile["name"], started_at, run_id)
    command = _build_start_command(profile["name"], run_id, log_path)

    update_run_record(
        profile["name"],
        {
            "run_id": run_id,
            "profile_name": profile["name"],
            "profile_path": str(profile_json_path(profile["name"])),
            "command": command,
            "started_at": started_at,
            "ended_at": None,
            "status": "starting",
            "exit_code": None,
            "log_path": str(log_path),
            "stop_requested": False,
            "note": "",
        },
    )

    env = os.environ.copy()
    env["DESKTOP_MANAGED_RUN"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    popen_kwargs: dict[str, Any] = {
        "cwd": PROJECT_ROOT,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    return update_run_record(
        profile["name"],
        {
            "host_pid": process.pid,
            "status": "running",
        },
    )


def stop_run(name: str) -> dict[str, Any]:
    record = get_run_record(name, refresh=True)
    if not record or record.get("status") != "running":
        raise ServiceError(f"{name} 当前没有运行中的任务。")

    update_run_record(
        name,
        {
            "stop_requested": True,
            "status": "stopping",
            "note": "已请求停止。",
        },
    )
    terminate_process_tree(int(record.get("host_pid") or 0))
    return update_run_record(
        name,
        {
            "status": "stopped",
            "ended_at": time.time(),
            "note": "任务已停止。",
        },
    )


def _tail_lines(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf8", errors="replace") as fp:
        lines = fp.readlines()
    return [line.rstrip("\n") for line in lines[-limit:]]


def read_log(name: str, lines: int = 30) -> dict[str, Any]:
    record = get_run_record(name, refresh=True)
    if not record or not record.get("log_path"):
        return {"profile_name": name, "lines": [], "path": "", "status": "idle"}

    log_path = Path(str(record["log_path"]))
    return {
        "profile_name": name,
        "path": log_path,
        "status": record.get("status", "idle"),
        "lines": _tail_lines(log_path, max(1, lines)),
    }

