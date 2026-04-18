from __future__ import annotations

import base64
import json
import platform
import queue
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from api.json_store import (
    DEFAULT_GLOBAL_SETTINGS,
    DEFAULT_PROFILE,
    build_effective_profile,
    create_json_profile,
    delete_json_profile,
    ensure_desktop_state,
    list_json_profiles,
    load_global_settings,
    load_json_profile,
    profile_summary,
    save_global_settings,
    save_json_profile,
)
from lightweight.runtime import RunManager, fetch_courses_for_profile


APP_TITLE = "超星助手轻量版"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "desktop_state"
PROVIDER_OPTIONS = ["TikuYanxi", "SiliconFlow", "AI", "TikuLike", "TikuAdapter", "MultiTiku"]
COLLAB_PROVIDER_OPTIONS = ["TikuYanxi", "SiliconFlow", "AI", "TikuLike", "TikuAdapter"]
DECISION_PROVIDER_OPTIONS = ["SiliconFlow", "AI", "TikuYanxi", "TikuLike", "TikuAdapter"]
NOTOPEN_ACTION_OPTIONS = ["retry", "continue", "ask"]
NOTIFICATION_PROVIDER_OPTIONS = ["", "ServerChan", "Qmsg", "Bark", "Telegram", "OneBotV11"]
NOTIFICATION_TARGET_OPTIONS = ["private", "group"]
STATUS_LABELS = {
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "stopped": "已停止",
    "idle": "未启动",
}


def _bool_label(value: bool) -> str:
    return "是" if value else "否"


def _to_optional_str(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _to_int(value: str, fallback: int) -> int:
    stripped = value.strip()
    if not stripped:
        return fallback
    return int(stripped)


def _to_float(value: str, fallback: float) -> float:
    stripped = value.strip()
    if not stripped:
        return fallback
    return float(stripped)


class ScrollableFrame(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.container = ttk.Frame(self.canvas)

        self.container.bind(
            "<Configure>",
            lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.window_id, width=event.width),
        )

        self.window_id = self.canvas.create_window((0, 0), window=self.container, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")


class LightweightApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        ensure_desktop_state()

        self.title(APP_TITLE)
        self.geometry("1360x900")
        self.minsize(1180, 760)

        self.style = ttk.Style(self)
        if "vista" in self.style.theme_names():
            self.style.theme_use("vista")
        elif "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        self.run_manager = RunManager()
        self.ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.profiles: dict[str, dict] = {}
        self.current_profile_name: str | None = None
        self.course_items: list[dict] = []
        self._last_log_payload = ""
        self._known_run_statuses: dict[str, str] = {}
        self._toast_windows: list[tk.Toplevel] = []

        self._build_variables()
        self._build_layout()
        self._load_global_settings()
        self._load_profiles()
        self._schedule_updates()

    def _build_variables(self) -> None:
        self.profile_name_var = tk.StringVar()
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.use_cookies_var = tk.BooleanVar(value=False)
        self.cookies_path_var = tk.StringVar()
        self.cache_path_var = tk.StringVar()
        self.speed_var = tk.StringVar(value="1.0")
        self.jobs_var = tk.StringVar(value="4")
        self.notopen_action_var = tk.StringVar(value="retry")

        self.provider_var = tk.StringVar(value="TikuYanxi")
        self.decision_provider_var = tk.StringVar(value="SiliconFlow")
        self.check_connection_var = tk.BooleanVar(value=True)
        self.submit_var = tk.BooleanVar(value=False)
        self.cover_rate_var = tk.StringVar(value="0.90")
        self.delay_var = tk.StringVar(value="1.0")
        self.tokens_var = tk.StringVar()
        self.endpoint_var = tk.StringVar()
        self.key_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.http_proxy_var = tk.StringVar()
        self.min_interval_var = tk.StringVar()
        self.request_timeout_var = tk.StringVar()
        self.silicon_key_var = tk.StringVar()
        self.silicon_model_var = tk.StringVar()
        self.silicon_endpoint_var = tk.StringVar()

        self.notification_provider_var = tk.StringVar()
        self.notification_url_var = tk.StringVar()
        self.tg_chat_id_var = tk.StringVar()
        self.onebot_host_var = tk.StringVar()
        self.onebot_port_var = tk.StringVar()
        self.onebot_path_var = tk.StringVar()
        self.onebot_token_var = tk.StringVar()
        self.onebot_target_type_var = tk.StringVar(value="private")
        self.onebot_user_id_var = tk.StringVar()
        self.onebot_group_id_var = tk.StringVar()
        self.notify_on_start_var = tk.BooleanVar(value=False)
        self.notify_on_success_var = tk.BooleanVar(value=True)
        self.notify_on_failure_var = tk.BooleanVar(value=True)
        self.notify_on_stop_var = tk.BooleanVar(value=True)
        self.attach_log_file_var = tk.BooleanVar(value=True)
        self.include_log_excerpt_var = tk.BooleanVar(value=True)

        self.global_tokens_var = tk.StringVar()
        self.global_endpoint_var = tk.StringVar()
        self.global_key_var = tk.StringVar()
        self.global_model_var = tk.StringVar()
        self.global_proxy_var = tk.StringVar()
        self.global_min_interval_var = tk.StringVar(value="3")
        self.global_timeout_var = tk.StringVar(value="600")
        self.global_silicon_key_var = tk.StringVar()
        self.global_silicon_model_var = tk.StringVar(value="deepseek-ai/DeepSeek-R1")
        self.global_silicon_endpoint_var = tk.StringVar(value="https://api.siliconflow.cn/v1/chat/completions")

        self.global_notification_provider_var = tk.StringVar()
        self.global_notification_url_var = tk.StringVar()
        self.global_tg_chat_id_var = tk.StringVar()
        self.global_onebot_host_var = tk.StringVar(value="127.0.0.1")
        self.global_onebot_port_var = tk.StringVar(value="3001")
        self.global_onebot_path_var = tk.StringVar(value="/")
        self.global_onebot_token_var = tk.StringVar()
        self.global_onebot_target_type_var = tk.StringVar(value="private")
        self.global_onebot_user_id_var = tk.StringVar()
        self.global_onebot_group_id_var = tk.StringVar()
        self.global_notify_start_var = tk.BooleanVar(value=False)
        self.global_notify_success_var = tk.BooleanVar(value=True)
        self.global_notify_failure_var = tk.BooleanVar(value=True)
        self.global_notify_stop_var = tk.BooleanVar(value=True)
        self.global_attach_log_var = tk.BooleanVar(value=True)
        self.global_excerpt_var = tk.BooleanVar(value=True)

        self.system_notifications_var = tk.BooleanVar(value=True)
        self.in_app_notifications_var = tk.BooleanVar(value=True)
        self.desktop_notify_completed_var = tk.BooleanVar(value=True)
        self.desktop_notify_failed_var = tk.BooleanVar(value=True)
        self.desktop_notify_stopped_var = tk.BooleanVar(value=True)

        self.overview_var = tk.StringVar(value="正在初始化…")

    def _build_layout(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(12, 12, 12, 6))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        for column in range(8):
            toolbar.columnconfigure(column, weight=0)
        toolbar.columnconfigure(8, weight=1)

        ttk.Label(toolbar, text=APP_TITLE, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Button(toolbar, text="新建档案", command=self._create_profile).grid(row=0, column=1, padx=4)
        ttk.Button(toolbar, text="重新载入", command=self._load_profiles).grid(row=0, column=2, padx=4)
        ttk.Button(toolbar, text="启动选中项", command=self._start_selected_profiles).grid(row=0, column=3, padx=4)
        ttk.Button(toolbar, text="停止选中项", command=self._stop_selected_profiles).grid(row=0, column=4, padx=4)
        ttk.Button(toolbar, text="删除选中项", command=self._delete_selected_profiles).grid(row=0, column=5, padx=4)
        ttk.Button(toolbar, text="保存当前档案", command=self._save_current_profile).grid(row=0, column=6, padx=4)
        ttk.Button(toolbar, text="保存全局设置", command=self._save_global_settings).grid(row=0, column=7, padx=4)

        sidebar = ttk.Frame(self, padding=(12, 6, 8, 12))
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.rowconfigure(1, weight=1)
        sidebar.columnconfigure(0, weight=1)

        ttk.Label(sidebar, text="档案列表", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.profile_listbox = tk.Listbox(sidebar, selectmode=tk.EXTENDED, exportselection=False)
        self.profile_listbox.grid(row=1, column=0, sticky="nsew")
        self.profile_listbox.bind("<<ListboxSelect>>", self._on_profile_select)

        selection_bar = ttk.Frame(sidebar, padding=(0, 8, 0, 0))
        selection_bar.grid(row=2, column=0, sticky="ew")
        ttk.Button(selection_bar, text="全选", command=self._select_all_profiles).pack(side="left", padx=(0, 6))
        ttk.Button(selection_bar, text="清空", command=self._clear_profile_selection).pack(side="left")

        main_panel = ttk.Frame(self, padding=(8, 6, 12, 12))
        main_panel.grid(row=1, column=1, sticky="nsew")
        main_panel.rowconfigure(0, weight=1)
        main_panel.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(main_panel)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self._build_overview_tab()
        self._build_profile_tab()
        self._build_json_tab()
        self._build_global_tab()
        self._build_logs_tab()

        status_bar = ttk.Frame(self, padding=(12, 0, 12, 10))
        status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(status_bar, text=f"数据目录：{DATA_DIR}").pack(side="left")

    def _build_overview_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=16)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text="概览")

        ttk.Label(frame, text="运行概况", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.overview_var, justify="left").grid(row=1, column=0, sticky="nw", pady=(12, 16))

        quick_box = ttk.LabelFrame(frame, text="当前选中档案", padding=12)
        quick_box.grid(row=2, column=0, sticky="ew")
        quick_box.columnconfigure(1, weight=1)
        ttk.Label(quick_box, text="名称").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Label(quick_box, textvariable=self.profile_name_var).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Button(quick_box, text="刷新课程列表", command=self._refresh_courses_async).grid(row=1, column=0, padx=(0, 12), pady=(8, 0), sticky="w")
        ttk.Button(quick_box, text="同步结构化 -> JSON", command=self._sync_profile_json_from_form).grid(row=1, column=1, pady=(8, 0), sticky="w")

    def _build_profile_tab(self) -> None:
        outer = ttk.Frame(self.notebook)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        self.notebook.add(outer, text="档案设置")

        scrollable = ScrollableFrame(outer)
        scrollable.grid(row=0, column=0, sticky="nsew")
        container = scrollable.container
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        common_box = ttk.LabelFrame(container, text="基础设置", padding=12)
        common_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))
        common_box.columnconfigure(1, weight=1)
        self._entry_row(common_box, 0, "档案名称", self.profile_name_var, readonly=True)
        self._entry_row(common_box, 1, "用户名", self.username_var)
        self._entry_row(common_box, 2, "密码", self.password_var, show="*")
        self._check_row(common_box, 3, "优先使用 Cookies", self.use_cookies_var)
        self._entry_row(common_box, 4, "Cookies 路径", self.cookies_path_var)
        self._entry_row(common_box, 5, "缓存路径", self.cache_path_var)
        self._entry_row(common_box, 6, "倍速", self.speed_var)
        self._entry_row(common_box, 7, "并发线程数", self.jobs_var)
        self._combo_row(common_box, 8, "未开放章节策略", self.notopen_action_var, NOTOPEN_ACTION_OPTIONS)

        tiku_box = ttk.LabelFrame(container, text="题库设置", padding=12)
        tiku_box.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        tiku_box.columnconfigure(1, weight=1)
        self._combo_row(tiku_box, 0, "主题库", self.provider_var, PROVIDER_OPTIONS)
        self._combo_row(tiku_box, 1, "冲突仲裁题库", self.decision_provider_var, DECISION_PROVIDER_OPTIONS)
        self._check_row(tiku_box, 2, "启动前检查大模型连接", self.check_connection_var)
        self._check_row(tiku_box, 3, "提交前自动作答", self.submit_var)
        self._entry_row(tiku_box, 4, "覆盖率", self.cover_rate_var)
        self._entry_row(tiku_box, 5, "请求间隔", self.delay_var)
        self._entry_row(tiku_box, 6, "公共 Tokens", self.tokens_var)
        self._entry_row(tiku_box, 7, "AI Endpoint", self.endpoint_var)
        self._entry_row(tiku_box, 8, "AI Key", self.key_var, show="*")
        self._entry_row(tiku_box, 9, "AI Model", self.model_var)
        self._entry_row(tiku_box, 10, "代理", self.http_proxy_var)
        self._entry_row(tiku_box, 11, "最小请求间隔（秒）", self.min_interval_var)
        self._entry_row(tiku_box, 12, "请求超时（秒）", self.request_timeout_var)
        self._entry_row(tiku_box, 13, "硅基 Key", self.silicon_key_var, show="*")
        self._entry_row(tiku_box, 14, "硅基模型", self.silicon_model_var)
        self._entry_row(tiku_box, 15, "硅基 Endpoint", self.silicon_endpoint_var)

        collab_box = ttk.LabelFrame(container, text="协同题库与课程", padding=12)
        collab_box.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))
        collab_box.columnconfigure(0, weight=1)
        collab_box.columnconfigure(1, weight=1)
        ttk.Label(collab_box, text="协同题库").grid(row=0, column=0, sticky="w")
        ttk.Label(collab_box, text="课程列表").grid(row=0, column=1, sticky="w")

        self.provider_listbox = tk.Listbox(collab_box, selectmode=tk.MULTIPLE, exportselection=False, height=8)
        self.provider_listbox.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(6, 0))
        for item in COLLAB_PROVIDER_OPTIONS:
            self.provider_listbox.insert(tk.END, item)

        course_side = ttk.Frame(collab_box)
        course_side.grid(row=1, column=1, sticky="nsew", pady=(6, 0))
        course_side.rowconfigure(1, weight=1)
        course_side.columnconfigure(0, weight=1)
        ttk.Button(course_side, text="刷新课程列表", command=self._refresh_courses_async).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.course_listbox = tk.Listbox(course_side, selectmode=tk.MULTIPLE, exportselection=False, height=8)
        self.course_listbox.grid(row=1, column=0, sticky="nsew")

        notification_box = ttk.LabelFrame(container, text="通知设置", padding=12)
        notification_box.grid(row=1, column=1, sticky="nsew", pady=(0, 10))
        notification_box.columnconfigure(1, weight=1)
        self._combo_row(notification_box, 0, "通知提供方", self.notification_provider_var, NOTIFICATION_PROVIDER_OPTIONS)
        self._entry_row(notification_box, 1, "通知 URL", self.notification_url_var)
        self._entry_row(notification_box, 2, "Telegram Chat ID", self.tg_chat_id_var)
        self._entry_row(notification_box, 3, "OneBot 主机", self.onebot_host_var)
        self._entry_row(notification_box, 4, "OneBot 端口", self.onebot_port_var)
        self._entry_row(notification_box, 5, "OneBot 路径", self.onebot_path_var)
        self._entry_row(notification_box, 6, "OneBot Token", self.onebot_token_var, show="*")
        self._combo_row(notification_box, 7, "OneBot 目标类型", self.onebot_target_type_var, NOTIFICATION_TARGET_OPTIONS)
        self._entry_row(notification_box, 8, "QQ 号", self.onebot_user_id_var)
        self._entry_row(notification_box, 9, "群号", self.onebot_group_id_var)
        self._check_row(notification_box, 10, "启动时通知", self.notify_on_start_var)
        self._check_row(notification_box, 11, "成功时通知", self.notify_on_success_var)
        self._check_row(notification_box, 12, "异常时通知", self.notify_on_failure_var)
        self._check_row(notification_box, 13, "停止时通知", self.notify_on_stop_var)
        self._check_row(notification_box, 14, "附带日志文件", self.attach_log_file_var)
        self._check_row(notification_box, 15, "附带日志摘要", self.include_log_excerpt_var)

    def _build_json_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=12)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.notebook.add(frame, text="高级 JSON")

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(toolbar, text="从当前表单生成 JSON", command=self._sync_profile_json_from_form).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="应用当前 JSON", command=self._apply_profile_json).pack(side="left")

        self.profile_json_text = ScrolledText(frame, wrap="none", font=("Consolas", 10))
        self.profile_json_text.grid(row=1, column=0, sticky="nsew")

    def _build_global_tab(self) -> None:
        outer = ttk.Frame(self.notebook)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        self.notebook.add(outer, text="全局设置")

        scrollable = ScrollableFrame(outer)
        scrollable.grid(row=0, column=0, sticky="nsew")
        container = scrollable.container
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        tiku_box = ttk.LabelFrame(container, text="默认题库设置", padding=12)
        tiku_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))
        tiku_box.columnconfigure(1, weight=1)
        self._entry_row(tiku_box, 0, "默认 Tokens", self.global_tokens_var)
        self._entry_row(tiku_box, 1, "默认 AI Endpoint", self.global_endpoint_var)
        self._entry_row(tiku_box, 2, "默认 AI Key", self.global_key_var, show="*")
        self._entry_row(tiku_box, 3, "默认 AI Model", self.global_model_var)
        self._entry_row(tiku_box, 4, "默认代理", self.global_proxy_var)
        self._entry_row(tiku_box, 5, "默认最小请求间隔（秒）", self.global_min_interval_var)
        self._entry_row(tiku_box, 6, "默认请求超时（秒）", self.global_timeout_var)
        self._entry_row(tiku_box, 7, "默认硅基 Key", self.global_silicon_key_var, show="*")
        self._entry_row(tiku_box, 8, "默认硅基模型", self.global_silicon_model_var)
        self._entry_row(tiku_box, 9, "默认硅基 Endpoint", self.global_silicon_endpoint_var)

        notification_box = ttk.LabelFrame(container, text="默认通知设置", padding=12)
        notification_box.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        notification_box.columnconfigure(1, weight=1)
        self._combo_row(notification_box, 0, "默认通知提供方", self.global_notification_provider_var, NOTIFICATION_PROVIDER_OPTIONS)
        self._entry_row(notification_box, 1, "默认通知 URL", self.global_notification_url_var)
        self._entry_row(notification_box, 2, "默认 Telegram Chat ID", self.global_tg_chat_id_var)
        self._entry_row(notification_box, 3, "默认 OneBot 主机", self.global_onebot_host_var)
        self._entry_row(notification_box, 4, "默认 OneBot 端口", self.global_onebot_port_var)
        self._entry_row(notification_box, 5, "默认 OneBot 路径", self.global_onebot_path_var)
        self._entry_row(notification_box, 6, "默认 OneBot Token", self.global_onebot_token_var, show="*")
        self._combo_row(notification_box, 7, "默认 OneBot 目标类型", self.global_onebot_target_type_var, NOTIFICATION_TARGET_OPTIONS)
        self._entry_row(notification_box, 8, "默认 QQ 号", self.global_onebot_user_id_var)
        self._entry_row(notification_box, 9, "默认群号", self.global_onebot_group_id_var)
        self._check_row(notification_box, 10, "默认启动时通知", self.global_notify_start_var)
        self._check_row(notification_box, 11, "默认成功时通知", self.global_notify_success_var)
        self._check_row(notification_box, 12, "默认异常时通知", self.global_notify_failure_var)
        self._check_row(notification_box, 13, "默认停止时通知", self.global_notify_stop_var)
        self._check_row(notification_box, 14, "默认附带日志文件", self.global_attach_log_var)
        self._check_row(notification_box, 15, "默认附带日志摘要", self.global_excerpt_var)

        desktop_box = ttk.LabelFrame(container, text="桌面提醒", padding=12)
        desktop_box.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))
        desktop_box.columnconfigure(0, weight=1)
        ttk.Checkbutton(desktop_box, text="启用系统通知", variable=self.system_notifications_var).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Checkbutton(desktop_box, text="启用应用内提示", variable=self.in_app_notifications_var).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Checkbutton(desktop_box, text="任务成功时提醒", variable=self.desktop_notify_completed_var).grid(row=2, column=0, sticky="w", pady=4)
        ttk.Checkbutton(desktop_box, text="任务异常时提醒", variable=self.desktop_notify_failed_var).grid(row=3, column=0, sticky="w", pady=4)
        ttk.Checkbutton(desktop_box, text="任务停止时提醒", variable=self.desktop_notify_stopped_var).grid(row=4, column=0, sticky="w", pady=4)

        json_box = ttk.LabelFrame(container, text="全局设置 JSON", padding=12)
        json_box.grid(row=1, column=1, sticky="nsew", pady=(0, 10))
        json_box.columnconfigure(0, weight=1)
        json_box.rowconfigure(1, weight=1)
        button_bar = ttk.Frame(json_box)
        button_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(button_bar, text="从当前表单生成 JSON", command=self._sync_global_json_from_form).pack(side="left", padx=(0, 6))
        ttk.Button(button_bar, text="应用当前 JSON", command=self._apply_global_json).pack(side="left")
        self.global_json_text = ScrolledText(json_box, wrap="none", font=("Consolas", 10), height=18)
        self.global_json_text.grid(row=1, column=0, sticky="nsew")

    def _build_logs_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=12)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.notebook.add(frame, text="运行日志")

        self.run_tree = ttk.Treeview(
            frame,
            columns=("profile", "status", "started", "ended"),
            show="headings",
            height=8,
        )
        self.run_tree.grid(row=0, column=0, sticky="ew")
        for column, title, width in (
            ("profile", "档案", 200),
            ("status", "状态", 90),
            ("started", "开始时间", 180),
            ("ended", "结束时间", 180),
        ):
            self.run_tree.heading(column, text=title)
            self.run_tree.column(column, width=width, anchor="w")
        self.run_tree.bind("<<TreeviewSelect>>", lambda event: self._refresh_log_text())

        self.log_text = ScrolledText(frame, wrap="word", font=("Consolas", 10))
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

    def _entry_row(
        self,
        parent: ttk.Widget,
        row: int,
        label: str,
        variable: tk.Variable,
        *,
        show: str | None = None,
        readonly: bool = False,
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        entry = ttk.Entry(parent, textvariable=variable, show=show or "")
        if readonly:
            entry.state(["readonly"])
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        return entry

    def _combo_row(self, parent: ttk.Widget, row: int, label: str, variable: tk.Variable, values: list[str]) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=4)
        return combo

    def _check_row(self, parent: ttk.Widget, row: int, label: str, variable: tk.BooleanVar) -> ttk.Checkbutton:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        check = ttk.Checkbutton(parent, variable=variable)
        check.grid(row=row, column=1, sticky="w", pady=4)
        return check

    def _load_profiles(self) -> None:
        self.profiles = {}
        for path in list_json_profiles():
            profile = load_json_profile(path.stem)
            self.profiles[profile["name"]] = profile

        self.profile_listbox.delete(0, tk.END)
        for name in sorted(self.profiles):
            self.profile_listbox.insert(tk.END, name)

        if self.current_profile_name in self.profiles:
            self._select_profile(self.current_profile_name)
        elif self.profiles:
            self._select_profile(sorted(self.profiles)[0])
        else:
            self._clear_profile_form()

        self._refresh_overview()
        self._refresh_runs()

    def _load_global_settings(self) -> None:
        settings = load_global_settings()
        defaults = settings.get("defaults", {})
        tiku = defaults.get("tiku", {})
        notification = defaults.get("notification", {})
        desktop = settings.get("desktop", {})

        self.global_tokens_var.set(str(tiku.get("tokens", "") or ""))
        self.global_endpoint_var.set(str(tiku.get("endpoint", "") or ""))
        self.global_key_var.set(str(tiku.get("key", "") or ""))
        self.global_model_var.set(str(tiku.get("model", "") or ""))
        self.global_proxy_var.set(str(tiku.get("http_proxy", "") or ""))
        self.global_min_interval_var.set(str(tiku.get("min_interval_seconds", "3") or "3"))
        self.global_timeout_var.set(str(tiku.get("request_timeout_seconds", "600") or "600"))
        self.global_silicon_key_var.set(str(tiku.get("siliconflow_key", "") or ""))
        self.global_silicon_model_var.set(str(tiku.get("siliconflow_model", "") or ""))
        self.global_silicon_endpoint_var.set(str(tiku.get("siliconflow_endpoint", "") or ""))

        self.global_notification_provider_var.set(str(notification.get("provider", "") or ""))
        self.global_notification_url_var.set(str(notification.get("url", "") or ""))
        self.global_tg_chat_id_var.set(str(notification.get("tg_chat_id", "") or ""))
        self.global_onebot_host_var.set(str(notification.get("onebot_host", "") or ""))
        self.global_onebot_port_var.set(str(notification.get("onebot_port", "") or ""))
        self.global_onebot_path_var.set(str(notification.get("onebot_path", "") or ""))
        self.global_onebot_token_var.set(str(notification.get("onebot_access_token", "") or ""))
        self.global_onebot_target_type_var.set(str(notification.get("onebot_target_type", "private") or "private"))
        self.global_onebot_user_id_var.set(str(notification.get("onebot_user_id", "") or ""))
        self.global_onebot_group_id_var.set(str(notification.get("onebot_group_id", "") or ""))
        self.global_notify_start_var.set(bool(notification.get("notify_on_start", False)))
        self.global_notify_success_var.set(bool(notification.get("notify_on_success", True)))
        self.global_notify_failure_var.set(bool(notification.get("notify_on_failure", True)))
        self.global_notify_stop_var.set(bool(notification.get("notify_on_stop", True)))
        self.global_attach_log_var.set(bool(notification.get("attach_log_file", True)))
        self.global_excerpt_var.set(bool(notification.get("include_log_excerpt", True)))

        self.system_notifications_var.set(bool(desktop.get("system_notifications", True)))
        self.in_app_notifications_var.set(bool(desktop.get("in_app_notifications", True)))
        self.desktop_notify_completed_var.set(bool(desktop.get("notify_on_completed", True)))
        self.desktop_notify_failed_var.set(bool(desktop.get("notify_on_failed", True)))
        self.desktop_notify_stopped_var.set(bool(desktop.get("notify_on_stopped", True)))

        self._sync_global_json_from_form()

    def _select_profile(self, name: str) -> None:
        if name not in self.profiles:
            return
        names = list(self.profile_listbox.get(0, tk.END))
        try:
            index = names.index(name)
        except ValueError:
            return

        self.profile_listbox.selection_clear(0, tk.END)
        self.profile_listbox.selection_set(index)
        self.profile_listbox.see(index)
        self._on_profile_select()

    def _selected_profile_names(self) -> list[str]:
        return [self.profile_listbox.get(index) for index in self.profile_listbox.curselection()]

    def _select_all_profiles(self) -> None:
        self.profile_listbox.selection_set(0, tk.END)
        self._on_profile_select()

    def _clear_profile_selection(self) -> None:
        self.profile_listbox.selection_clear(0, tk.END)
        self.current_profile_name = None
        self._clear_profile_form()
        self._refresh_log_text()

    def _on_profile_select(self, event=None) -> None:
        selected = self._selected_profile_names()
        if len(selected) == 1:
            self.current_profile_name = selected[0]
            self._load_profile_to_form(self.profiles[self.current_profile_name])
        else:
            self.current_profile_name = None
        self._refresh_overview()
        self._refresh_log_text()

    def _clear_profile_form(self) -> None:
        self.profile_name_var.set("")
        self.username_var.set("")
        self.password_var.set("")
        self.use_cookies_var.set(False)
        self.cookies_path_var.set("")
        self.cache_path_var.set("")
        self.speed_var.set("1.0")
        self.jobs_var.set("4")
        self.notopen_action_var.set("retry")

        self.provider_var.set("TikuYanxi")
        self.decision_provider_var.set("SiliconFlow")
        self.check_connection_var.set(True)
        self.submit_var.set(False)
        self.cover_rate_var.set("0.90")
        self.delay_var.set("1.0")
        self.tokens_var.set("")
        self.endpoint_var.set("")
        self.key_var.set("")
        self.model_var.set("")
        self.http_proxy_var.set("")
        self.min_interval_var.set("")
        self.request_timeout_var.set("")
        self.silicon_key_var.set("")
        self.silicon_model_var.set("")
        self.silicon_endpoint_var.set("")

        self.notification_provider_var.set("")
        self.notification_url_var.set("")
        self.tg_chat_id_var.set("")
        self.onebot_host_var.set("")
        self.onebot_port_var.set("")
        self.onebot_path_var.set("")
        self.onebot_token_var.set("")
        self.onebot_target_type_var.set("private")
        self.onebot_user_id_var.set("")
        self.onebot_group_id_var.set("")
        self.notify_on_start_var.set(False)
        self.notify_on_success_var.set(True)
        self.notify_on_failure_var.set(True)
        self.notify_on_stop_var.set(True)
        self.attach_log_file_var.set(True)
        self.include_log_excerpt_var.set(True)

        self.provider_listbox.selection_clear(0, tk.END)
        self.course_items = []
        self.course_listbox.delete(0, tk.END)
        self.profile_json_text.delete("1.0", tk.END)

    def _load_profile_to_form(self, profile: dict) -> None:
        effective = build_effective_profile(profile, load_global_settings())
        common = profile.get("common", {})
        tiku = profile.get("tiku", {})
        notification = profile.get("notification", {})

        self.profile_name_var.set(profile["name"])
        self.username_var.set(str(common.get("username", "") or ""))
        self.password_var.set(str(common.get("password", "") or ""))
        self.use_cookies_var.set(bool(common.get("use_cookies", False)))
        self.cookies_path_var.set(str(common.get("cookies_path", "") or ""))
        self.cache_path_var.set(str(common.get("cache_path", "") or ""))
        self.speed_var.set(str(common.get("speed", 1.0)))
        self.jobs_var.set(str(common.get("jobs", 4)))
        self.notopen_action_var.set(str(common.get("notopen_action", "retry") or "retry"))

        self.provider_var.set(str(tiku.get("provider", "TikuYanxi") or "TikuYanxi"))
        self.decision_provider_var.set(str(tiku.get("decision_provider", "SiliconFlow") or "SiliconFlow"))
        self.check_connection_var.set(bool(tiku.get("check_llm_connection", True)))
        self.submit_var.set(bool(tiku.get("submit", False)))
        self.cover_rate_var.set(str(tiku.get("cover_rate", 0.9)))
        self.delay_var.set(str(tiku.get("delay", 1.0)))
        self.tokens_var.set(str(tiku.get("tokens", "") or ""))
        self.endpoint_var.set(str(tiku.get("endpoint", "") or ""))
        self.key_var.set(str(tiku.get("key", "") or ""))
        self.model_var.set(str(tiku.get("model", "") or ""))
        self.http_proxy_var.set(str(tiku.get("http_proxy", "") or ""))
        self.min_interval_var.set("" if tiku.get("min_interval_seconds") is None else str(tiku.get("min_interval_seconds")))
        self.request_timeout_var.set("" if tiku.get("request_timeout_seconds") is None else str(tiku.get("request_timeout_seconds")))
        self.silicon_key_var.set(str(tiku.get("siliconflow_key", "") or ""))
        self.silicon_model_var.set(str(tiku.get("siliconflow_model", "") or ""))
        self.silicon_endpoint_var.set(str(tiku.get("siliconflow_endpoint", "") or ""))

        self.notification_provider_var.set(str(notification.get("provider", "") or ""))
        self.notification_url_var.set(str(notification.get("url", "") or ""))
        self.tg_chat_id_var.set(str(notification.get("tg_chat_id", "") or ""))
        self.onebot_host_var.set(str(notification.get("onebot_host", "") or ""))
        self.onebot_port_var.set("" if notification.get("onebot_port") is None else str(notification.get("onebot_port")))
        self.onebot_path_var.set(str(notification.get("onebot_path", "") or ""))
        self.onebot_token_var.set(str(notification.get("onebot_access_token", "") or ""))
        self.onebot_target_type_var.set(str(notification.get("onebot_target_type", "private") or "private"))
        self.onebot_user_id_var.set(str(notification.get("onebot_user_id", "") or ""))
        self.onebot_group_id_var.set(str(notification.get("onebot_group_id", "") or ""))
        self.notify_on_start_var.set(bool(notification.get("notify_on_start", False)))
        self.notify_on_success_var.set(bool(notification.get("notify_on_success", True)))
        self.notify_on_failure_var.set(bool(notification.get("notify_on_failure", True)))
        self.notify_on_stop_var.set(bool(notification.get("notify_on_stop", True)))
        self.attach_log_file_var.set(bool(notification.get("attach_log_file", True)))
        self.include_log_excerpt_var.set(bool(notification.get("include_log_excerpt", True)))

        self.provider_listbox.selection_clear(0, tk.END)
        selected_providers = list(tiku.get("providers", []) or [])
        for index, item in enumerate(COLLAB_PROVIDER_OPTIONS):
            if item in selected_providers:
                self.provider_listbox.selection_set(index)

        self.course_items = []
        self.course_listbox.delete(0, tk.END)
        for course_id in effective.get("common", {}).get("course_list", []):
            self.course_items.append({"courseId": course_id, "title": course_id, "teacher": "", "selected": True})
            self.course_listbox.insert(tk.END, course_id)
            self.course_listbox.selection_set(tk.END)

        self.profile_json_text.delete("1.0", tk.END)
        self.profile_json_text.insert("1.0", json.dumps(profile, ensure_ascii=False, indent=2) + "\n")

    def _collect_profile_from_form(self) -> dict:
        if not self.current_profile_name and not self.profile_name_var.get().strip():
            raise ValueError("当前没有可保存的档案")

        name = self.profile_name_var.get().strip() or self.current_profile_name
        selected_courses = [self.course_items[index]["courseId"] for index in self.course_listbox.curselection() if index < len(self.course_items)]
        selected_providers = [COLLAB_PROVIDER_OPTIONS[index] for index in self.provider_listbox.curselection()]

        payload = json.loads(json.dumps(DEFAULT_PROFILE))
        payload["name"] = name
        payload["common"] = {
            "use_cookies": self.use_cookies_var.get(),
            "cookies_path": self.cookies_path_var.get().strip(),
            "cache_path": self.cache_path_var.get().strip(),
            "username": self.username_var.get().strip(),
            "password": self.password_var.get().strip(),
            "course_list": selected_courses,
            "speed": _to_float(self.speed_var.get(), 1.0),
            "jobs": _to_int(self.jobs_var.get(), 4),
            "notopen_action": self.notopen_action_var.get().strip() or "retry",
        }
        payload["tiku"] = {
            "provider": self.provider_var.get().strip() or "TikuYanxi",
            "providers": selected_providers,
            "decision_provider": self.decision_provider_var.get().strip() or "SiliconFlow",
            "check_llm_connection": self.check_connection_var.get(),
            "submit": self.submit_var.get(),
            "cover_rate": _to_float(self.cover_rate_var.get(), 0.9),
            "delay": _to_float(self.delay_var.get(), 1.0),
            "tokens": _to_optional_str(self.tokens_var.get()),
            "endpoint": _to_optional_str(self.endpoint_var.get()),
            "key": _to_optional_str(self.key_var.get()),
            "model": _to_optional_str(self.model_var.get()),
            "http_proxy": _to_optional_str(self.http_proxy_var.get()),
            "min_interval_seconds": _to_optional_str(self.min_interval_var.get()),
            "request_timeout_seconds": _to_optional_str(self.request_timeout_var.get()),
            "siliconflow_key": _to_optional_str(self.silicon_key_var.get()),
            "siliconflow_model": _to_optional_str(self.silicon_model_var.get()),
            "siliconflow_endpoint": _to_optional_str(self.silicon_endpoint_var.get()),
            "true_list": ["正确", "对", "√", "是"],
            "false_list": ["错误", "错", "×", "否", "不对", "不正确"],
        }
        payload["notification"] = {
            "provider": _to_optional_str(self.notification_provider_var.get()),
            "url": _to_optional_str(self.notification_url_var.get()),
            "tg_chat_id": _to_optional_str(self.tg_chat_id_var.get()),
            "onebot_host": _to_optional_str(self.onebot_host_var.get()),
            "onebot_port": _to_optional_str(self.onebot_port_var.get()),
            "onebot_path": _to_optional_str(self.onebot_path_var.get()),
            "onebot_access_token": _to_optional_str(self.onebot_token_var.get()),
            "onebot_target_type": _to_optional_str(self.onebot_target_type_var.get()),
            "onebot_user_id": _to_optional_str(self.onebot_user_id_var.get()),
            "onebot_group_id": _to_optional_str(self.onebot_group_id_var.get()),
            "notify_on_start": self.notify_on_start_var.get(),
            "notify_on_success": self.notify_on_success_var.get(),
            "notify_on_failure": self.notify_on_failure_var.get(),
            "notify_on_stop": self.notify_on_stop_var.get(),
            "attach_log_file": self.attach_log_file_var.get(),
            "include_log_excerpt": self.include_log_excerpt_var.get(),
        }
        return payload

    def _save_current_profile(self) -> None:
        try:
            payload = self._collect_profile_from_form()
            previous_name = self.current_profile_name
            saved_path = save_json_profile(payload)
            if previous_name and previous_name != payload["name"]:
                delete_json_profile(previous_name, remove_runtime_state=False)
            self.current_profile_name = payload["name"]
            self._load_profiles()
            self._select_profile(payload["name"])
            messagebox.showinfo(APP_TITLE, f"档案已保存到：\n{saved_path}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"保存档案失败：\n{exc}")

    def _sync_profile_json_from_form(self) -> None:
        try:
            payload = self._collect_profile_from_form()
            self.profile_json_text.delete("1.0", tk.END)
            self.profile_json_text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"生成 JSON 失败：\n{exc}")

    def _apply_profile_json(self) -> None:
        try:
            payload = json.loads(self.profile_json_text.get("1.0", tk.END))
            if self.current_profile_name and payload.get("name") not in ("", None, self.current_profile_name):
                delete_json_profile(self.current_profile_name, remove_runtime_state=False)
            save_json_profile(payload)
            self.current_profile_name = payload.get("name") or self.current_profile_name
            self._load_profiles()
            if self.current_profile_name:
                self._select_profile(self.current_profile_name)
            messagebox.showinfo(APP_TITLE, "JSON 已应用到档案。")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"应用 JSON 失败：\n{exc}")

    def _create_profile(self) -> None:
        name = simpledialog.askstring(APP_TITLE, "请输入新档案名称：", parent=self)
        if not name:
            return
        try:
            create_json_profile(name)
            self._load_profiles()
            self._select_profile(name)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"创建档案失败：\n{exc}")

    def _delete_selected_profiles(self) -> None:
        names = self._selected_profile_names()
        if not names:
            return
        if not messagebox.askyesno(APP_TITLE, f"确定删除以下档案吗？\n\n" + "\n".join(names), parent=self):
            return

        errors = []
        for name in names:
            try:
                self.run_manager.remove_profile_state(name, stop_running=True)
                delete_json_profile(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        self._load_profiles()
        if errors:
            messagebox.showerror(APP_TITLE, "以下档案删除失败：\n\n" + "\n".join(errors))

    def _start_selected_profiles(self) -> None:
        names = self._selected_profile_names()
        if not names:
            return
        errors = []
        for name in names:
            try:
                self.run_manager.start_profile(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        self._refresh_runs()
        if errors:
            messagebox.showerror(APP_TITLE, "以下档案启动失败：\n\n" + "\n".join(errors))

    def _stop_selected_profiles(self) -> None:
        names = self._selected_profile_names()
        if not names:
            return
        errors = []
        for name in names:
            try:
                self.run_manager.stop_profile(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        self._refresh_runs()
        if errors:
            messagebox.showerror(APP_TITLE, "以下档案停止失败：\n\n" + "\n".join(errors))

    def _refresh_courses_async(self) -> None:
        if not self.current_profile_name:
            messagebox.showwarning(APP_TITLE, "请先选中一个档案。")
            return

        profile_name = self.current_profile_name

        def worker() -> None:
            try:
                courses = fetch_courses_for_profile(profile_name)
                self.ui_queue.put(("courses", (profile_name, courses)))
            except Exception as exc:
                self.ui_queue.put(("courses_error", (profile_name, str(exc))))

        threading.Thread(target=worker, daemon=True, name=f"FetchCourses-{profile_name}").start()

    def _apply_courses(self, profile_name: str, courses: list[dict]) -> None:
        if profile_name != self.current_profile_name:
            return
        self.course_items = courses
        self.course_listbox.delete(0, tk.END)
        for index, course in enumerate(courses):
            label = course["title"]
            if course.get("teacher"):
                label += f" | {course['teacher']}"
            self.course_listbox.insert(tk.END, label)
            if course.get("selected"):
                self.course_listbox.selection_set(index)

    def _collect_global_settings(self) -> dict:
        return {
            "schema_version": DEFAULT_GLOBAL_SETTINGS["schema_version"],
            "theme": {"accent": "snow"},
            "desktop": {
                "system_notifications": self.system_notifications_var.get(),
                "in_app_notifications": self.in_app_notifications_var.get(),
                "notify_on_completed": self.desktop_notify_completed_var.get(),
                "notify_on_failed": self.desktop_notify_failed_var.get(),
                "notify_on_stopped": self.desktop_notify_stopped_var.get(),
            },
            "defaults": {
                "tiku": {
                    "tokens": self.global_tokens_var.get().strip(),
                    "endpoint": self.global_endpoint_var.get().strip(),
                    "key": self.global_key_var.get().strip(),
                    "model": self.global_model_var.get().strip(),
                    "http_proxy": self.global_proxy_var.get().strip(),
                    "min_interval_seconds": self.global_min_interval_var.get().strip() or "3",
                    "request_timeout_seconds": self.global_timeout_var.get().strip() or "600",
                    "siliconflow_key": self.global_silicon_key_var.get().strip(),
                    "siliconflow_model": self.global_silicon_model_var.get().strip(),
                    "siliconflow_endpoint": self.global_silicon_endpoint_var.get().strip(),
                    "url": "",
                    "likeapi_search": "false",
                    "likeapi_vision": "true",
                    "likeapi_model": "glm-4.5-air",
                    "likeapi_retry": "true",
                    "likeapi_retry_times": "3",
                },
                "notification": {
                    "provider": self.global_notification_provider_var.get().strip(),
                    "url": self.global_notification_url_var.get().strip(),
                    "tg_chat_id": self.global_tg_chat_id_var.get().strip(),
                    "onebot_host": self.global_onebot_host_var.get().strip(),
                    "onebot_port": _to_int(self.global_onebot_port_var.get(), 3001),
                    "onebot_path": self.global_onebot_path_var.get().strip() or "/",
                    "onebot_access_token": self.global_onebot_token_var.get().strip(),
                    "onebot_target_type": self.global_onebot_target_type_var.get().strip() or "private",
                    "onebot_user_id": self.global_onebot_user_id_var.get().strip(),
                    "onebot_group_id": self.global_onebot_group_id_var.get().strip(),
                    "notify_on_start": self.global_notify_start_var.get(),
                    "notify_on_success": self.global_notify_success_var.get(),
                    "notify_on_failure": self.global_notify_failure_var.get(),
                    "notify_on_stop": self.global_notify_stop_var.get(),
                    "attach_log_file": self.global_attach_log_var.get(),
                    "include_log_excerpt": self.global_excerpt_var.get(),
                },
            },
        }

    def _sync_global_json_from_form(self) -> None:
        payload = self._collect_global_settings()
        self.global_json_text.delete("1.0", tk.END)
        self.global_json_text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _apply_global_json(self) -> None:
        try:
            payload = json.loads(self.global_json_text.get("1.0", tk.END))
            save_global_settings(payload)
            self._load_global_settings()
            messagebox.showinfo(APP_TITLE, "全局设置 JSON 已应用。")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"应用全局 JSON 失败：\n{exc}")

    def _save_global_settings(self) -> None:
        try:
            payload = self._collect_global_settings()
            path = save_global_settings(payload)
            self._load_global_settings()
            messagebox.showinfo(APP_TITLE, f"全局设置已保存到：\n{path}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"保存全局设置失败：\n{exc}")

    def _desktop_settings(self) -> dict[str, bool]:
        return {
            "system_notifications": self.system_notifications_var.get(),
            "in_app_notifications": self.in_app_notifications_var.get(),
            "notify_on_completed": self.desktop_notify_completed_var.get(),
            "notify_on_failed": self.desktop_notify_failed_var.get(),
            "notify_on_stopped": self.desktop_notify_stopped_var.get(),
        }

    def _maybe_notify_status_change(self, run) -> None:
        previous_status = self._known_run_statuses.get(run.profile_name)
        self._known_run_statuses[run.profile_name] = run.status
        if previous_status == run.status:
            return

        desktop = self._desktop_settings()
        enabled_map = {
            "completed": desktop["notify_on_completed"],
            "failed": desktop["notify_on_failed"],
            "stopped": desktop["notify_on_stopped"],
        }
        if run.status not in enabled_map or not enabled_map[run.status]:
            return

        title_map = {
            "completed": "任务完成",
            "failed": "任务异常",
            "stopped": "任务已停止",
        }
        title = f"{title_map.get(run.status, run.status)}"
        message = f"档案：{run.profile_name}"
        if desktop["in_app_notifications"]:
            self._show_toast(title, message, run.status)
        if desktop["system_notifications"]:
            self._show_system_notification(title, message)

    def _show_toast(self, title: str, message: str, status: str) -> None:
        color_map = {
            "completed": "#1f7a1f",
            "failed": "#b42318",
            "stopped": "#8a6d1d",
        }
        toast = tk.Toplevel(self)
        toast.withdraw()
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=color_map.get(status, "#1f1f1f"))

        card = ttk.Frame(toast, padding=12)
        card.pack(fill="both", expand=True)
        ttk.Label(card, text=title, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(card, text=message).pack(anchor="w", pady=(4, 0))

        toast.update_idletasks()
        width = max(280, toast.winfo_reqwidth())
        height = max(90, toast.winfo_reqheight())
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        margin = 20
        index = len(self._toast_windows)
        x = screen_width - width - margin
        y = screen_height - height - margin - index * (height + 10)
        toast.geometry(f"{width}x{height}+{x}+{y}")
        toast.deiconify()

        self._toast_windows.append(toast)

        def close_toast() -> None:
            if toast in self._toast_windows:
                self._toast_windows.remove(toast)
            if toast.winfo_exists():
                toast.destroy()
            self._reflow_toasts()

        toast.after(4500, close_toast)

    def _reflow_toasts(self) -> None:
        margin = 20
        for index, toast in enumerate(list(self._toast_windows)):
            if not toast.winfo_exists():
                continue
            toast.update_idletasks()
            width = toast.winfo_width()
            height = toast.winfo_height()
            x = self.winfo_screenwidth() - width - margin
            y = self.winfo_screenheight() - height - margin - index * (height + 10)
            toast.geometry(f"{width}x{height}+{x}+{y}")

    def _show_system_notification(self, title: str, message: str) -> None:
        system = platform.system().lower()
        try:
            if system == "windows":
                self._show_windows_notification(title, message)
            elif system == "darwin":
                safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
                safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
                subprocess.Popen(
                    ["osascript", "-e", f'display notification "{safe_message}" with title "{safe_title}"'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["notify-send", title, message],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            return

    def _show_windows_notification(self, title: str, message: str) -> None:
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Visible = $true
$notify.BalloonTipTitle = '{title.replace("'", "''")}'
$notify.BalloonTipText = '{message.replace("'", "''")}'
$notify.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$notify.Dispose()
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _refresh_overview(self) -> None:
        running = 0
        failed = 0
        completed = 0
        for run in self.run_manager.list_runs():
            if run.status == "running":
                running += 1
            elif run.status == "failed":
                failed += 1
            elif run.status == "completed":
                completed += 1

        selected_names = self._selected_profile_names()
        selected_text = "、".join(selected_names) if selected_names else "未选择"
        lines = [
            f"档案总数：{len(self.profiles)}",
            f"运行中：{running}",
            f"最近完成：{completed}",
            f"需关注：{failed}",
            f"当前选中：{selected_text}",
            f"数据目录：{DATA_DIR}",
        ]

        if self.current_profile_name and self.current_profile_name in self.profiles:
            summary = profile_summary(self.profiles[self.current_profile_name], load_global_settings())
            lines.extend(
                [
                    "",
                    f"当前档案：{summary['name']}",
                    f"账号：{summary['username'] or '未填写'}",
                    f"题库：{summary['provider']}",
                    f"课程数：{summary['course_count']}",
                    f"Cookies 登录：{_bool_label(bool(summary['use_cookies']))}",
                ]
            )

        self.overview_var.set("\n".join(lines))

    def _refresh_runs(self) -> None:
        selected_item = self.run_tree.selection()
        selected_profile = None
        if selected_item:
            selected_profile = self.run_tree.item(selected_item[0], "values")[0]

        self.run_tree.delete(*self.run_tree.get_children())
        for run in self.run_manager.list_runs():
            self._maybe_notify_status_change(run)
            self.run_tree.insert(
                "",
                tk.END,
                iid=run.profile_name,
                values=(
                    run.profile_name,
                    STATUS_LABELS.get(run.status, run.status),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.started_at)),
                    "-" if not run.ended_at else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.ended_at)),
                ),
            )

        if selected_profile and self.run_tree.exists(selected_profile):
            self.run_tree.selection_set(selected_profile)

        self._refresh_log_text()
        self._refresh_overview()

    def _refresh_log_text(self) -> None:
        target_profile = None
        selected_item = self.run_tree.selection()
        if selected_item:
            target_profile = self.run_tree.item(selected_item[0], "values")[0]
        elif self.current_profile_name:
            target_profile = self.current_profile_name

        payload = self.run_manager.logs_for_profile(target_profile) if target_profile else ""
        if payload == self._last_log_payload:
            return

        self._last_log_payload = payload
        self.log_text.delete("1.0", tk.END)
        if payload:
            self.log_text.insert("1.0", payload)

    def _process_ui_queue(self) -> None:
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "courses":
                profile_name, courses = payload
                self._apply_courses(profile_name, courses)
            elif kind == "courses_error":
                profile_name, error_text = payload
                if profile_name == self.current_profile_name:
                    messagebox.showerror(APP_TITLE, f"刷新课程列表失败：\n{error_text}")

    def _schedule_updates(self) -> None:
        self._process_ui_queue()
        self._refresh_runs()
        self.after(1000, self._schedule_updates)


def run_app() -> int:
    app = LightweightApp()
    app.mainloop()
    return 0
