from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from copy import deepcopy
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
from api.runtime import configure_runtime
from tui.state import (
    build_run_log_path,
    get_run_record,
    list_run_records,
    load_runs_payload,
    process_exists,
    python_entry_command,
    refresh_runs_payload,
    save_runs_payload,
    terminate_process_tree,
    update_run_record,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BackendError(RuntimeError):
    pass


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def print_json(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
    sys.stdout.write("\n")
    return 0


def _read_json_stdin() -> Any:
    data = sys.stdin.read()
    if not data.strip():
        raise BackendError("未从标准输入读取到 JSON 内容。")
    return json.loads(data)


def _selected_course_ids(profile: dict) -> set[str]:
    return {str(item) for item in profile.get("common", {}).get("course_list", [])}


def _profile_view(name: str) -> dict[str, Any]:
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


def command_list_profiles(_args: argparse.Namespace) -> int:
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
    return print_json(payload)


def command_get_profile(args: argparse.Namespace) -> int:
    return print_json(load_json_profile(args.name))


def command_profile_view(args: argparse.Namespace) -> int:
    return print_json(_profile_view(args.name))


def command_save_profile(_args: argparse.Namespace) -> int:
    profile = _read_json_stdin()
    save_json_profile(profile)
    return print_json(_profile_view(profile["name"]))


def command_create_profile(args: argparse.Namespace) -> int:
    create_json_profile(args.name, force=False)
    return print_json(_profile_view(args.name))


def command_delete_profile(args: argparse.Namespace) -> int:
    run = get_run_record(args.name, refresh=True)
    if run and run.get("status") == "running" and not args.force:
        raise BackendError("目标配置仍在运行中，请先停止任务或使用 --force。")
    if run and run.get("status") == "running":
        command_stop_run(argparse.Namespace(name=args.name))
    deleted = delete_json_profile(args.name, remove_runtime_state=True)
    payload = load_runs_payload()
    payload.get("runs", {}).pop(args.name, None)
    save_runs_payload(payload)
    return print_json({"deleted": [str(path) for path in deleted]})


def command_get_global(_args: argparse.Namespace) -> int:
    return print_json(load_global_settings())


def command_save_global(_args: argparse.Namespace) -> int:
    settings = _read_json_stdin()
    save_global_settings(settings)
    return print_json(load_global_settings())


def command_fetch_courses(args: argparse.Namespace) -> int:
    profile = load_json_profile(args.name)
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
        raise BackendError(str(login_state.get("msg") or "课程列表获取失败。"))

    selected = _selected_course_ids(profile)
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
    return print_json(payload)


def _build_start_command(profile_name: str, run_id: str, log_path: Path) -> list[str]:
    return python_entry_command() + [
        "-m",
        "tui.worker_host",
        "--profile",
        profile_name,
        "--run-id",
        run_id,
        "--log-path",
        str(log_path),
    ]


def command_list_runs(_args: argparse.Namespace) -> int:
    return print_json(list_run_records(refresh=True))


def command_start_run(args: argparse.Namespace) -> int:
    ensure_desktop_state()
    profile = load_json_profile(args.name)
    current_run = get_run_record(profile["name"], refresh=True)
    if current_run and current_run.get("status") == "running" and process_exists(int(current_run.get("host_pid") or 0)):
        raise BackendError(f"{profile['name']} 已在运行中。")

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
    record = update_run_record(
        profile["name"],
        {
            "host_pid": process.pid,
            "status": "running",
        },
    )
    return print_json(record)


def command_stop_run(args: argparse.Namespace) -> int:
    record = get_run_record(args.name, refresh=True)
    if not record or record.get("status") != "running":
        raise BackendError(f"{args.name} 当前没有运行中的任务。")

    update_run_record(
        args.name,
        {
            "stop_requested": True,
            "status": "stopping",
            "note": "已请求停止。",
        },
    )
    terminate_process_tree(int(record.get("host_pid") or 0))
    record = update_run_record(
        args.name,
        {
            "status": "stopped",
            "ended_at": time.time(),
            "note": "任务已停止。",
        },
    )
    return print_json(record)


def _tail_lines(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf8", errors="replace") as fp:
        lines = fp.readlines()
    return [line.rstrip("\n") for line in lines[-limit:]]


def command_read_log(args: argparse.Namespace) -> int:
    record = get_run_record(args.name, refresh=True)
    if not record or not record.get("log_path"):
        return print_json({"profile_name": args.name, "lines": [], "path": ""})

    log_path = Path(str(record["log_path"]))
    return print_json(
        {
            "profile_name": args.name,
            "path": log_path,
            "status": record.get("status", "idle"),
            "lines": _tail_lines(log_path, max(1, args.lines)),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chaoxing PowerShell TUI backend bridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-profiles").set_defaults(func=command_list_profiles)

    get_profile = subparsers.add_parser("get-profile")
    get_profile.add_argument("--name", required=True)
    get_profile.set_defaults(func=command_get_profile)

    profile_view = subparsers.add_parser("profile-view")
    profile_view.add_argument("--name", required=True)
    profile_view.set_defaults(func=command_profile_view)

    subparsers.add_parser("save-profile").set_defaults(func=command_save_profile)

    create_profile = subparsers.add_parser("create-profile")
    create_profile.add_argument("--name", required=True)
    create_profile.set_defaults(func=command_create_profile)

    delete_profile = subparsers.add_parser("delete-profile")
    delete_profile.add_argument("--name", required=True)
    delete_profile.add_argument("--force", action="store_true")
    delete_profile.set_defaults(func=command_delete_profile)

    subparsers.add_parser("get-global").set_defaults(func=command_get_global)
    subparsers.add_parser("save-global").set_defaults(func=command_save_global)

    fetch_courses = subparsers.add_parser("fetch-courses")
    fetch_courses.add_argument("--name", required=True)
    fetch_courses.set_defaults(func=command_fetch_courses)

    subparsers.add_parser("list-runs").set_defaults(func=command_list_runs)

    start_run = subparsers.add_parser("start-run")
    start_run.add_argument("--name", required=True)
    start_run.set_defaults(func=command_start_run)

    stop_run = subparsers.add_parser("stop-run")
    stop_run.add_argument("--name", required=True)
    stop_run.set_defaults(func=command_stop_run)

    read_log = subparsers.add_parser("read-log")
    read_log.add_argument("--name", required=True)
    read_log.add_argument("--lines", type=int, default=30)
    read_log.set_defaults(func=command_read_log)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BackendError as exc:
        print_json({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
