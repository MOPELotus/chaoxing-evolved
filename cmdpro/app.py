from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from api.json_store import GLOBAL_SETTINGS_PATH, build_effective_profile, profile_json_path, profile_override_enabled
from api.provider_catalog import (
    COLLAB_PROVIDER_OPTIONS,
    DECISION_PROVIDER_OPTIONS,
    PROVIDER_OPTIONS,
    provider_items,
    provider_label,
)
from cmdpro import service
from cmdpro.worker_host import main as worker_host_main


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CMD_PRO_ENTRY = PROJECT_ROOT / "cmd_pro.py"
STATUS_LABELS = {
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "stopped": "已停止",
    "stopping": "停止中",
    "starting": "启动中",
    "idle": "未启动",
}
NOTOPEN_ACTIONS = [("retry", "重试"), ("continue", "继续"), ("ask", "人工确认")]
NOTIFICATION_PROVIDER_ITEMS = [
    ("", "不启用"),
    ("ServerChan", "ServerChan"),
    ("Qmsg", "Qmsg"),
    ("Bark", "Bark"),
    ("Telegram", "Telegram"),
    ("OneBotV11", "OneBotV11"),
]
NOTIFICATION_TARGET_OPTIONS = [("private", "QQ 私聊"), ("group", "QQ群")]

TIKU_OVERRIDE_SPECS = [
    {"key": "tokens", "label": "言溪 Token 列表", "type": "str"},
    {"key": "url", "label": "荷花题库地址", "type": "str"},
    {"key": "endpoint", "label": "AI 接口地址", "type": "str"},
    {"key": "key", "label": "AI 密钥", "type": "secret"},
    {"key": "model", "label": "AI 模型", "type": "str"},
    {"key": "http_proxy", "label": "HTTP 代理", "type": "str"},
    {"key": "min_interval_seconds", "label": "最小请求间隔（秒）", "type": "int", "minimum": 0},
    {"key": "request_timeout_seconds", "label": "请求超时（秒）", "type": "int", "minimum": 10},
    {"key": "siliconflow_key", "label": "硅基密钥", "type": "secret"},
    {"key": "siliconflow_model", "label": "硅基模型", "type": "str"},
    {"key": "siliconflow_endpoint", "label": "硅基接口地址", "type": "str"},
    {"key": "likeapi_search", "label": "LIKE 联网搜索", "type": "bool"},
    {"key": "likeapi_vision", "label": "LIKE 视觉识别", "type": "bool"},
    {"key": "likeapi_model", "label": "LIKE 模型", "type": "str"},
    {"key": "likeapi_retry", "label": "LIKE 自动重试", "type": "bool"},
    {"key": "likeapi_retry_times", "label": "LIKE 重试次数", "type": "int", "minimum": 0},
]
NOTIFICATION_OVERRIDE_SPECS = [
    {"key": "provider", "label": "通知提供方", "type": "enum", "options": NOTIFICATION_PROVIDER_ITEMS},
    {"key": "url", "label": "通知地址", "type": "str"},
    {"key": "tg_chat_id", "label": "Telegram Chat ID", "type": "str"},
    {"key": "onebot_host", "label": "OneBot 主机", "type": "str"},
    {"key": "onebot_port", "label": "OneBot 端口", "type": "int", "minimum": 1},
    {"key": "onebot_path", "label": "OneBot 路径", "type": "str"},
    {"key": "onebot_access_token", "label": "OneBot Access Token", "type": "secret"},
    {"key": "onebot_target_type", "label": "OneBot 目标", "type": "enum", "options": NOTIFICATION_TARGET_OPTIONS},
    {"key": "onebot_user_id", "label": "QQ 号", "type": "str"},
    {"key": "onebot_group_id", "label": "QQ群号", "type": "str"},
    {"key": "notify_on_start", "label": "启动时通知", "type": "bool"},
    {"key": "notify_on_success", "label": "成功时通知", "type": "bool"},
    {"key": "notify_on_failure", "label": "失败时通知", "type": "bool"},
    {"key": "notify_on_stop", "label": "停止时通知", "type": "bool"},
    {"key": "attach_log_file", "label": "附带日志文件", "type": "bool"},
    {"key": "include_log_excerpt", "label": "附带日志摘要", "type": "bool"},
]
GLOBAL_TIKU_SPECS = [{"section": ("defaults", "tiku"), **spec} for spec in TIKU_OVERRIDE_SPECS]
GLOBAL_NOTIFICATION_SPECS = [{"section": ("defaults", "notification"), **spec} for spec in NOTIFICATION_OVERRIDE_SPECS]
GLOBAL_DESKTOP_SPECS = [
    {"section": ("desktop",), "key": "system_notifications", "label": "系统通知", "type": "bool"},
    {"section": ("desktop",), "key": "in_app_notifications", "label": "应用内提示", "type": "bool"},
    {"section": ("desktop",), "key": "notify_on_completed", "label": "成功提醒", "type": "bool"},
    {"section": ("desktop",), "key": "notify_on_failed", "label": "失败提醒", "type": "bool"},
    {"section": ("desktop",), "key": "notify_on_stopped", "label": "停止提醒", "type": "bool"},
]


def configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(str(status or "idle").strip(), str(status or "未启动"))


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def set_console_title(title: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(str(title))
            return
        except Exception:
            pass
    sys.stdout.write(f"\x1b]2;{title}\x07")
    sys.stdout.flush()


def pause(message: str = "按回车继续...") -> None:
    input(message)


def prompt_text(label: str, default: str = "", allow_empty: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    if value == "":
        return default if allow_empty else value
    return value


def prompt_bool(label: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{label} ({hint}): ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true", "on"}


def prompt_int(label: str, default: int, minimum: int | None = None) -> int:
    while True:
        raw = prompt_text(label, str(default))
        try:
            value = int(raw)
        except ValueError:
            print("请输入整数。")
            continue
        if minimum is not None and value < minimum:
            print(f"该值不能小于 {minimum}。")
            continue
        return value


def prompt_float(label: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    while True:
        raw = prompt_text(label, str(default))
        try:
            value = float(raw)
        except ValueError:
            print("请输入数字。")
            continue
        if minimum is not None and value < minimum:
            print(f"该值不能小于 {minimum}。")
            continue
        if maximum is not None and value > maximum:
            print(f"该值不能大于 {maximum}。")
            continue
        return value


def render_options(options: list[tuple[str, str]], selected: str | None = None) -> None:
    for index, (value, label) in enumerate(options, start=1):
        mark = "当前" if str(selected or "").strip() == value else "    "
        print(f" {index:>2}. {label} [{value}] {mark}")


def prompt_option(label: str, options: list[tuple[str, str]], selected: str | None = None, allow_keep: bool = True) -> str:
    while True:
        print(label)
        render_options(options, selected)
        suffix = "，直接回车保持不变" if allow_keep else ""
        raw = input(f"请输入编号{suffix}: ").strip()
        if raw == "" and allow_keep and selected is not None:
            return selected
        try:
            index = int(raw)
        except ValueError:
            print("请输入有效编号。")
            continue
        if 1 <= index <= len(options):
            return options[index - 1][0]
        print("编号超出范围。")


def prompt_multi_options(label: str, options: list[tuple[str, str]], selected: list[str] | None = None) -> list[str]:
    current = list(selected or [])
    while True:
        print(label)
        render_options(options)
        current_text = ", ".join(provider_label(item) for item in current) if current else "未设置"
        raw = input(f"输入多个编号并用逗号分隔；留空保持当前（{current_text}）；输入 0 清空: ").strip()
        if raw == "":
            return current
        if raw == "0":
            return []

        values: list[str] = []
        ok = True
        for part in raw.split(","):
            piece = part.strip()
            if not piece:
                continue
            try:
                index = int(piece)
            except ValueError:
                ok = False
                break
            if index < 1 or index > len(options):
                ok = False
                break
            value = options[index - 1][0]
            if value not in values:
                values.append(value)
        if ok:
            return values
        print("输入格式不正确，请重新输入。")


def format_provider_summary(summary: dict[str, Any]) -> str:
    providers = list(summary.get("providers", []) or [])
    if len(providers) > 1:
        return " + ".join(provider_label(item) for item in providers)
    return provider_label(summary.get("provider", ""))


def print_profile_lines(profiles: list[dict[str, Any]]) -> None:
    if not profiles:
        print("当前还没有任何配置。")
        return

    for index, item in enumerate(profiles, start=1):
        summary = item.get("summary", {})
        run = item.get("run") or {}
        status = status_label(run.get("status"))
        provider = format_provider_summary(summary)
        username = summary.get("username", "") or "-"
        course_count = summary.get("course_count", 0)
        print(f"{index:>2}. {item['name']} | {status} | {provider} | 课程 {course_count} | 账号 {username}")


def select_profile_by_index(profiles: list[dict[str, Any]], raw: str) -> dict[str, Any] | None:
    try:
        index = int(raw)
    except ValueError:
        return None
    if 1 <= index <= len(profiles):
        return profiles[index - 1]
    return None


def open_new_console(mode: str, name: str | None = None) -> None:
    args = [sys.executable, str(CMD_PRO_ENTRY), "--mode", mode]
    if name:
        args += ["--name", name]

    if os.name == "nt":
        subprocess.Popen(
            args,
            cwd=PROJECT_ROOT,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return

    subprocess.Popen(args, cwd=PROJECT_ROOT)


def open_file_in_editor(path: Path) -> None:
    resolved = path.resolve()
    if os.name == "nt":
        subprocess.Popen(["notepad.exe", str(resolved)])
        return

    editor = os.environ.get("EDITOR")
    if editor:
        subprocess.Popen(shlex.split(editor) + [str(resolved)])
        return

    print(f"请手动打开文件：{resolved}")


def print_header(title: str, note: str | None = None) -> None:
    clear_screen()
    print(f"=== {title} ===")
    if note:
        print(note)
    print()


def _bool_text(value: object) -> str:
    return "是" if bool(value) else "否"


def _display_value(spec: dict[str, Any], value: object) -> str:
    if spec["type"] == "bool":
        return _bool_text(value)
    if spec["type"] == "secret":
        return "已填写" if str(value or "").strip() else "未填写"
    if spec["type"] == "enum":
        options = dict(spec["options"])
        return options.get(str(value or "").strip(), str(value or "未设置"))
    text = str(value or "").strip()
    return text if text else "未设置"


def _ensure_profile_override_maps(profile: dict, section: str) -> tuple[dict, dict]:
    section_data = profile.setdefault(section, {})
    overrides = profile.setdefault("overrides", {}).setdefault(section, {})
    return section_data, overrides


def _set_profile_override(profile: dict, section: str, key: str, value: object, enabled: bool) -> None:
    section_data, overrides = _ensure_profile_override_maps(profile, section)
    if enabled:
        section_data[key] = value
        overrides[key] = True
    else:
        section_data.pop(key, None)
        overrides.pop(key, None)
        if not overrides:
            profile.setdefault("overrides", {}).pop(section, None)
            if not profile.get("overrides"):
                profile.pop("overrides", None)


def launcher_mode() -> int:
    set_console_title("超星助手 命令行 Pro")
    while True:
        profiles = service.list_profiles_view()
        running = sum(1 for item in profiles if (item.get("run") or {}).get("status") == "running")
        attention = sum(1 for item in profiles if (item.get("run") or {}).get("status") in {"failed", "stopped"})

        print_header("超星助手 命令行 Pro", "主控台")
        print(f"配置总数：{len(profiles)}")
        print(f"运行中：{running}")
        print(f"需关注：{attention}")
        print()
        print("当前配置：")
        print_profile_lines(profiles[:8])
        if len(profiles) > 8:
            print(f"... 其余 {len(profiles) - 8} 个配置请在“配置管理窗口”中查看。")
        print()
        print("快捷操作：")
        print("  p   打开配置管理窗口")
        print("  r   打开运行监控窗口")
        print("  g   打开全局设置窗口")
        print("  n   新建配置并立即打开编辑窗口")
        print("  e 3 打开第 3 个配置的编辑窗口")
        print("  s 3 启动第 3 个配置并打开实时日志")
        print("  x 3 停止第 3 个配置")
        print("  l 3 打开第 3 个配置的日志窗口")
        print("  q   退出")
        print()

        command = input("请输入命令: ").strip()
        if not command:
            continue
        lower = command.lower()
        if lower == "q":
            return 0
        if lower == "p":
            open_new_console("profiles")
            continue
        if lower == "r":
            open_new_console("runs")
            continue
        if lower == "g":
            open_new_console("global")
            continue
        if lower == "n":
            name = prompt_text("输入新配置名称", allow_empty=False).strip()
            if not name:
                continue
            try:
                view = service.create_profile(name)
            except Exception as exc:
                print(f"创建失败：{exc}")
                pause()
                continue
            open_new_console("edit-profile", view["profile"]["name"])
            continue

        parts = lower.split(maxsplit=1)
        if len(parts) == 2 and parts[0] in {"e", "s", "x", "l"}:
            target = select_profile_by_index(profiles, parts[1])
            if not target:
                print("未找到对应编号。")
                pause()
                continue
            name = target["name"]
            try:
                if parts[0] == "e":
                    open_new_console("edit-profile", name)
                elif parts[0] == "s":
                    service.start_run(name)
                    open_new_console("log", name)
                elif parts[0] == "x":
                    service.stop_run(name)
                elif parts[0] == "l":
                    open_new_console("log", name)
            except Exception as exc:
                print(f"操作失败：{exc}")
                pause()


def profiles_mode() -> int:
    set_console_title("超星助手 命令行 Pro - 配置管理")
    while True:
        profiles = service.list_profiles_view()
        print_header("配置管理", "可在这里创建、编辑、启动、停止和删除配置。")
        print_profile_lines(profiles)
        print()
        print("命令：")
        print("  n             新建配置")
        print("  e 2           编辑第 2 个配置")
        print("  s 2           启动第 2 个配置并打开日志")
        print("  x 2           停止第 2 个配置")
        print("  l 2           打开第 2 个配置的实时日志")
        print("  d 2           删除第 2 个配置")
        print("  o             打开全局设置窗口")
        print("  r             刷新列表")
        print("  q             关闭窗口")
        print()

        command = input("请输入命令: ").strip().lower()
        if command in {"q", "quit", "exit"}:
            return 0
        if command == "r":
            continue
        if command == "o":
            open_new_console("global")
            continue
        if command == "n":
            name = prompt_text("输入新配置名称", allow_empty=False).strip()
            if not name:
                continue
            try:
                view = service.create_profile(name)
            except Exception as exc:
                print(f"创建失败：{exc}")
                pause()
                continue
            open_new_console("edit-profile", view["profile"]["name"])
            continue

        parts = command.split(maxsplit=1)
        if len(parts) != 2 or parts[0] not in {"e", "s", "x", "l", "d"}:
            print("命令格式不正确。")
            pause()
            continue

        target = select_profile_by_index(profiles, parts[1])
        if not target:
            print("未找到对应编号。")
            pause()
            continue
        name = target["name"]

        try:
            if parts[0] == "e":
                open_new_console("edit-profile", name)
            elif parts[0] == "s":
                service.start_run(name)
                open_new_console("log", name)
            elif parts[0] == "x":
                service.stop_run(name)
            elif parts[0] == "l":
                open_new_console("log", name)
            elif parts[0] == "d":
                if prompt_bool(f"确认删除配置 {name}", False):
                    service.remove_profile(name, force=True)
        except Exception as exc:
            print(f"操作失败：{exc}")
            pause()


def edit_profile_basic(profile: dict) -> None:
    common = profile.setdefault("common", {})
    print_header("编辑基本设置", f"档案：{profile['name']}")
    common["username"] = prompt_text("账号", str(common.get("username", "")))
    common["password"] = prompt_text("密码", str(common.get("password", "")))
    common["use_cookies"] = prompt_bool("启用 Cookies 登录", bool(common.get("use_cookies", False)))
    common["speed"] = prompt_float("倍速", float(common.get("speed", 1.0) or 1.0), 1.0, 2.0)
    common["jobs"] = prompt_int("并发章节数", int(common.get("jobs", 4) or 4), 1)
    common["notopen_action"] = prompt_option(
        "关闭章节处理策略",
        NOTOPEN_ACTIONS,
        selected=str(common.get("notopen_action", "retry") or "retry"),
    )
    common["cookies_path"] = prompt_text("Cookies 文件路径", str(common.get("cookies_path", "")))
    common["cache_path"] = prompt_text("题库缓存路径", str(common.get("cache_path", "")))
    service.save_profile(profile)


def edit_profile_tiku(profile: dict) -> None:
    tiku = profile.setdefault("tiku", {})
    print_header("编辑题库模式", f"档案：{profile['name']}")
    primary_provider = prompt_option(
        "选择主题库",
        provider_items(PROVIDER_OPTIONS),
        selected=str(tiku.get("provider", "TikuYanxi") or "TikuYanxi"),
    )
    selected_providers = prompt_multi_options(
        "选择协同题库（可多选）",
        provider_items(COLLAB_PROVIDER_OPTIONS),
        selected=list(tiku.get("providers", []) or []),
    )
    if len(selected_providers) > 1:
        tiku["provider"] = "MultiTiku"
        tiku["providers"] = selected_providers
    elif len(selected_providers) == 1:
        tiku["provider"] = selected_providers[0]
        tiku["providers"] = selected_providers
    else:
        tiku["provider"] = primary_provider
        tiku["providers"] = []

    tiku["decision_provider"] = prompt_option(
        "选择冲突仲裁题库",
        provider_items(DECISION_PROVIDER_OPTIONS),
        selected=str(tiku.get("decision_provider", "SiliconFlow") or "SiliconFlow"),
    )
    tiku["check_llm_connection"] = prompt_bool("启动前检查题库连接", bool(tiku.get("check_llm_connection", True)))
    tiku["submit"] = prompt_bool("答题后直接提交", bool(tiku.get("submit", False)))
    tiku["cover_rate"] = prompt_float("最低覆盖率（0-1）", float(tiku.get("cover_rate", 0.9) or 0.9), 0.0, 1.0)
    tiku["delay"] = prompt_float("单题间隔（秒）", float(tiku.get("delay", 1.0) or 1.0), 0.0, None)
    service.save_profile(profile)


def edit_profile_override_section(profile: dict, section: str, specs: list[dict[str, Any]], title: str) -> None:
    settings = service.load_global_view()
    while True:
        effective = build_effective_profile(profile, settings)
        print_header(title, f"档案：{profile['name']}")
        for index, spec in enumerate(specs, start=1):
            enabled = profile_override_enabled(profile, section, spec["key"])
            current_value = effective.get(section, {}).get(spec["key"])
            state_text = "单独设置" if enabled else "继承全局"
            print(f"{index:>2}. {spec['label']}：{_display_value(spec, current_value)} [{state_text}]")
        print()
        print("输入编号编辑；输入 0 返回。")
        raw = input("请选择: ").strip()
        if raw in {"0", ""}:
            return

        try:
            index = int(raw)
        except ValueError:
            continue
        if not (1 <= index <= len(specs)):
            continue

        spec = specs[index - 1]
        current_enabled = profile_override_enabled(profile, section, spec["key"])
        current_effective = effective.get(section, {}).get(spec["key"])
        print()
        print(f"{spec['label']}")
        print(f"当前生效值：{_display_value(spec, current_effective)}")
        print(f"当前状态：{'单独设置' if current_enabled else '继承全局'}")
        print("输入新值后保存；输入 / 恢复继承；直接回车取消。")

        if spec["type"] == "enum":
            options = list(spec["options"])
            print(" 0. 恢复继承")
            render_options(options, str(current_effective or ""))
            choice = input("请输入编号: ").strip()
            if choice == "":
                continue
            if choice == "0":
                _set_profile_override(profile, section, spec["key"], None, False)
                service.save_profile(profile)
                continue
            try:
                option_index = int(choice)
            except ValueError:
                continue
            if 1 <= option_index <= len(options):
                _set_profile_override(profile, section, spec["key"], options[option_index - 1][0], True)
                service.save_profile(profile)
            continue

        raw_value = input("请输入: ").strip()
        if raw_value == "":
            continue
        if raw_value == "/":
            _set_profile_override(profile, section, spec["key"], None, False)
            service.save_profile(profile)
            continue

        try:
            if spec["type"] == "bool":
                value = raw_value.lower() in {"y", "yes", "1", "true", "on", "是"}
            elif spec["type"] == "int":
                value = int(raw_value)
                minimum = spec.get("minimum")
                if minimum is not None and value < int(minimum):
                    raise ValueError(f"{spec['label']} 不能小于 {minimum}")
            else:
                value = raw_value
        except Exception as exc:
            print(f"输入无效：{exc}")
            pause()
            continue

        _set_profile_override(profile, section, spec["key"], value, True)
        service.save_profile(profile)


def select_courses_for_profile(profile: dict) -> None:
    print_header("刷新课程列表", f"档案：{profile['name']}，正在请求课程数据...")
    try:
        courses = service.fetch_courses(profile["name"])
    except Exception as exc:
        print(f"课程列表获取失败：{exc}")
        pause()
        return

    clear_screen()
    print(f"=== 课程选择：{profile['name']} ===")
    print("输入课程编号，使用英文逗号分隔。留空表示清空。")
    print()
    for index, course in enumerate(courses, start=1):
        mark = "[x]" if course.get("selected") else "[ ]"
        teacher = str(course.get("teacher", "") or "").strip()
        teacher_text = f" | {teacher}" if teacher else ""
        print(f"{index:>2}. {mark} {course['title']}{teacher_text}")
    print()
    raw = input("请选择课程: ").strip()
    selected_ids: list[str] = []
    if raw:
        for part in raw.split(","):
            piece = part.strip()
            if not piece:
                continue
            try:
                index = int(piece)
            except ValueError:
                continue
            if 1 <= index <= len(courses):
                course_id = str(courses[index - 1]["courseId"])
                if course_id not in selected_ids:
                    selected_ids.append(course_id)
    profile.setdefault("common", {})["course_list"] = selected_ids
    service.save_profile(profile)


def edit_profile_window(name: str) -> int:
    set_console_title(f"超星助手 命令行 Pro - 编辑配置 - {name}")
    while True:
        view = service.profile_view(name)
        profile = view["profile"]
        effective = view["effective_profile"]
        summary = view["summary"]
        run = view.get("run") or {}

        print_header("编辑配置", f"档案：{name}")
        print(f"当前状态：{status_label(run.get('status'))}")
        print(f"账号：{profile.get('common', {}).get('username', '') or '未填写'}")
        print(f"课程数量：{summary.get('course_count', 0)}")
        print(f"当前题库：{format_provider_summary(summary)}")
        print(f"冲突仲裁题库：{provider_label(summary.get('decision_provider', ''))}")
        print(f"荷花题库地址：{effective.get('tiku', {}).get('url', '') or '未设置'}")
        print()
        print(" 1  编辑基本设置")
        print(" 2  编辑题库模式")
        print(" 3  编辑题库覆盖项")
        print(" 4  编辑通知覆盖项")
        print(" 5  刷新并选择课程")
        print(" 6  打开配置 JSON")
        print(" 7  启动当前配置并打开日志")
        print(" 8  停止当前配置")
        print(" 9  打开实时日志窗口")
        print(" 0  关闭窗口")
        print()

        command = input("请选择操作: ").strip()
        try:
            if command == "0":
                return 0
            if command == "1":
                edit_profile_basic(profile)
            elif command == "2":
                edit_profile_tiku(profile)
            elif command == "3":
                edit_profile_override_section(profile, "tiku", TIKU_OVERRIDE_SPECS, "题库覆盖项")
            elif command == "4":
                edit_profile_override_section(profile, "notification", NOTIFICATION_OVERRIDE_SPECS, "通知覆盖项")
            elif command == "5":
                select_courses_for_profile(profile)
            elif command == "6":
                open_file_in_editor(profile_json_path(name))
            elif command == "7":
                service.start_run(name)
                open_new_console("log", name)
            elif command == "8":
                service.stop_run(name)
            elif command == "9":
                open_new_console("log", name)
        except Exception as exc:
            print(f"操作失败：{exc}")
            pause()


def _get_nested(mapping: dict[str, Any], section: tuple[str, ...], key: str) -> Any:
    current: Any = mapping
    for part in section:
        current = current.setdefault(part, {})
    return current.get(key)


def _set_nested(mapping: dict[str, Any], section: tuple[str, ...], key: str, value: Any) -> None:
    current: Any = mapping
    for part in section:
        current = current.setdefault(part, {})
    current[key] = value


def edit_global_section(settings: dict[str, Any], specs: list[dict[str, Any]], title: str) -> None:
    while True:
        print_header(title)
        for index, spec in enumerate(specs, start=1):
            value = _get_nested(settings, tuple(spec["section"]), spec["key"])
            print(f"{index:>2}. {spec['label']}：{_display_value(spec, value)}")
        print()
        print("输入编号编辑；输入 0 返回。")
        raw = input("请选择: ").strip()
        if raw in {"0", ""}:
            return
        try:
            index = int(raw)
        except ValueError:
            continue
        if not (1 <= index <= len(specs)):
            continue

        spec = specs[index - 1]
        current = _get_nested(settings, tuple(spec["section"]), spec["key"])
        if spec["type"] == "bool":
            value = prompt_bool(spec["label"], bool(current))
        elif spec["type"] == "int":
            value = prompt_int(spec["label"], int(current or 0), spec.get("minimum"))
        elif spec["type"] == "enum":
            value = prompt_option(spec["label"], list(spec["options"]), selected=str(current or ""))
        else:
            value = prompt_text(spec["label"], str(current or ""))
        _set_nested(settings, tuple(spec["section"]), spec["key"], value)
        service.save_global_view(settings)


def global_mode() -> int:
    set_console_title("超星助手 命令行 Pro - 全局设置")
    while True:
        settings = service.load_global_view()
        print_header("全局设置")
        print(f"荷花题库地址：{settings.get('defaults', {}).get('tiku', {}).get('url', '') or '未设置'}")
        print(f"默认 AI 模型：{settings.get('defaults', {}).get('tiku', {}).get('model', '') or '未设置'}")
        print(f"默认硅基模型：{settings.get('defaults', {}).get('tiku', {}).get('siliconflow_model', '') or '未设置'}")
        print(f"OneBot 提供方：{settings.get('defaults', {}).get('notification', {}).get('provider', '') or '不启用'}")
        print()
        print(" 1  编辑题库默认值")
        print(" 2  编辑通知默认值")
        print(" 3  编辑桌面提醒")
        print(" 4  打开全局 JSON")
        print(" 0  关闭窗口")
        print()
        command = input("请选择操作: ").strip()
        if command == "0":
            return 0
        if command == "1":
            edit_global_section(settings, GLOBAL_TIKU_SPECS, "题库默认值")
        elif command == "2":
            edit_global_section(settings, GLOBAL_NOTIFICATION_SPECS, "通知默认值")
        elif command == "3":
            edit_global_section(settings, GLOBAL_DESKTOP_SPECS, "桌面提醒")
        elif command == "4":
            open_file_in_editor(GLOBAL_SETTINGS_PATH)


def runs_mode() -> int:
    set_console_title("超星助手 命令行 Pro - 运行监控")
    while True:
        runs = service.list_runs_view()
        print_header("运行监控", "按回车或输入 r 刷新。")
        if not runs:
            print("当前没有任何运行记录。")
        else:
            for index, record in enumerate(runs, start=1):
                started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(record.get("started_at") or time.time())))
                print(
                    f"{index:>2}. {record.get('profile_name', '')} | {status_label(record.get('status'))} | "
                    f"开始于 {started} | 退出码 {record.get('exit_code', '-')}"
                )
        print()
        print("命令：")
        print("  l 2   打开第 2 条记录的实时日志")
        print("  x 2   停止第 2 条记录")
        print("  e 2   打开第 2 条记录对应配置")
        print("  q     关闭窗口")
        print()
        command = input("请输入命令: ").strip().lower()
        if command in {"", "r"}:
            continue
        if command == "q":
            return 0
        parts = command.split(maxsplit=1)
        if len(parts) != 2 or parts[0] not in {"l", "x", "e"}:
            continue
        try:
            index = int(parts[1])
        except ValueError:
            continue
        if index < 1 or index > len(runs):
            continue
        name = str(runs[index - 1].get("profile_name", "") or "")
        if not name:
            continue
        try:
            if parts[0] == "l":
                open_new_console("log", name)
            elif parts[0] == "x":
                service.stop_run(name)
            elif parts[0] == "e":
                open_new_console("edit-profile", name)
        except Exception as exc:
            print(f"操作失败：{exc}")
            pause()


def log_mode(name: str) -> int:
    set_console_title(f"超星助手 命令行 Pro - 实时日志 - {name}")
    last_snapshot: list[str] = []
    while True:
        log_view = service.read_log(name, lines=80)
        lines = list(log_view.get("lines", []) or [])
        status = status_label(log_view.get("status"))
        if lines != last_snapshot:
            print_header("实时日志", f"档案：{name} | 状态：{status}")
            print(f"日志文件：{log_view.get('path', '') or '尚未生成'}")
            print("-" * 72)
            if lines:
                for line in lines:
                    print(line)
            else:
                print("当前还没有日志输出。")
            print("-" * 72)
            print("本窗口每 1 秒自动刷新。按 Ctrl+C 关闭。")
            last_snapshot = lines

        if log_view.get("status") in {"completed", "failed", "stopped"}:
            print()
            print("任务已经结束。按 Ctrl+C 关闭窗口，或保留窗口查看日志。")
            time.sleep(2)
        else:
            time.sleep(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chaoxing command line Pro.")
    parser.add_argument(
        "--mode",
        default="launcher",
        choices=["launcher", "profiles", "global", "runs", "edit-profile", "log", "worker-host"],
        help="Window mode",
    )
    parser.add_argument("--name", help="Profile name for profile-specific modes")
    parser.add_argument("--run-id", help="Run identifier for worker host")
    parser.add_argument("--log-path", help="Log path for worker host")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.mode == "worker-host":
        worker_args = ["--profile", args.name or "", "--run-id", args.run_id or "", "--log-path", args.log_path or ""]
        return worker_host_main(worker_args)
    if args.mode == "launcher":
        return launcher_mode()
    if args.mode == "profiles":
        return profiles_mode()
    if args.mode == "global":
        return global_mode()
    if args.mode == "runs":
        return runs_mode()
    if args.mode == "edit-profile":
        if not args.name:
            parser.error("--name is required for --mode edit-profile")
        return edit_profile_window(args.name)
    if args.mode == "log":
        if not args.name:
            parser.error("--name is required for --mode log")
        return log_mode(args.name)
    return 0
