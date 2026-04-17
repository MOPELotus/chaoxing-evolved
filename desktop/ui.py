from __future__ import annotations

import contextlib
import io
import json
import sys
from copy import deepcopy
from pathlib import Path

from PyQt5.QtCore import QSize, QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QListWidgetItem,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    from qfluentwidgets import (
        BodyLabel,
        CaptionLabel,
        CardWidget,
        CheckBox,
        ComboBox,
        DoubleSpinBox,
        DisplayLabel,
        FluentIcon,
        FlowLayout,
        HorizontalSeparator,
        InfoBar,
        InfoBarPosition,
        LargeTitleLabel,
        LineEdit,
        ListWidget,
        MSFluentWindow,
        MessageBox,
        MessageBoxBase,
        NavigationItemPosition,
        PillPushButton,
        PlainTextEdit,
        PrimaryPushButton,
        PushButton,
        SearchLineEdit,
        SpinBox,
        StrongBodyLabel,
        SubtitleLabel,
        TitleLabel,
        TransparentPushButton,
    )

from api.json_store import (
    DEFAULT_GLOBAL_SETTINGS,
    DEFAULT_PROFILE,
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
from desktop.runtime import RunManager, fetch_courses_for_profile


APP_TITLE = "超星助手桌面版"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
JSON_PROFILE_DIR = PROJECT_ROOT / "desktop_state" / "profiles"
PROVIDER_OPTIONS = ["TikuYanxi", "SiliconFlow", "AI", "TikuLike", "TikuAdapter", "MultiTiku"]
COLLAB_PROVIDER_OPTIONS = ["TikuYanxi", "SiliconFlow", "AI", "TikuLike", "TikuAdapter"]
DECISION_PROVIDER_OPTIONS = ["SiliconFlow", "AI", "TikuYanxi", "TikuLike", "TikuAdapter"]
NOTOPEN_ACTION_OPTIONS = ["retry", "continue", "ask"]
NOTOPEN_ACTION_LABELS = {
    "retry": "重试",
    "continue": "继续",
    "ask": "人工确认",
}
NOTOPEN_ACTION_VALUE_BY_LABEL = {label: value for value, label in NOTOPEN_ACTION_LABELS.items()}
NOTIFICATION_PROVIDER_OPTIONS = ["不启用", "ServerChan", "Qmsg", "Bark", "Telegram"]
STATUS_LABELS = {
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "stopped": "已停止",
    "idle": "未启动",
}


def split_csv(text: str) -> list[str]:
    return [item.strip() for item in str(text).replace("\n", ",").split(",") if item.strip()]


def join_csv(values: list[str]) -> str:
    return ",".join(str(item).strip() for item in values if str(item).strip())


def set_combo_text(combo: ComboBox, value: str, fallback_index: int = 0) -> None:
    index = combo.findText(value)
    combo.setCurrentIndex(index if index >= 0 else fallback_index)


def set_notopen_action(combo: ComboBox, value: str) -> None:
    set_combo_text(combo, NOTOPEN_ACTION_LABELS.get(value, NOTOPEN_ACTION_LABELS["retry"]))


def get_notopen_action(combo: ComboBox) -> str:
    return NOTOPEN_ACTION_VALUE_BY_LABEL.get(combo.currentText().strip(), "retry")


def exec_dialog(dialog) -> int:
    return dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()


def show_bar(parent: QWidget, level: str, title: str, content: str, duration: int = 3500) -> None:
    fn = getattr(InfoBar, level, InfoBar.info)
    fn(
        title=title,
        content=content,
        orient=Qt.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=duration,
        parent=parent.window() if parent else None,
    )


def dialog_parent(parent: QWidget | None) -> QWidget:
    if parent is not None:
        return parent.window() if hasattr(parent, "window") else parent

    active_window = QApplication.activeWindow()
    if active_window is not None:
        return active_window

    fallback = QWidget()
    fallback.resize(960, 720)
    fallback.hide()
    return fallback


def show_error(parent: QWidget, title: str, message: str) -> None:
    dialog = MessageBox(title, message, dialog_parent(parent))
    if hasattr(dialog, "yesButton"):
        dialog.yesButton.setText("确定")
    if hasattr(dialog, "cancelButton"):
        dialog.cancelButton.hide()
    exec_dialog(dialog)


def confirm_action(
    parent: QWidget | None,
    title: str,
    message: str,
    confirm_text: str = "确定",
    cancel_text: str = "取消",
) -> bool:
    dialog = MessageBox(title, message, dialog_parent(parent))
    if hasattr(dialog, "yesButton"):
        dialog.yesButton.setText(confirm_text)
    if hasattr(dialog, "cancelButton"):
        dialog.cancelButton.setText(cancel_text)
    return exec_dialog(dialog) == 1


def display_status(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def make_field(label: str, widget: QWidget, hint: str = "") -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(BodyLabel(label, container))
    if hint:
        hint_label = CaptionLabel(hint, container)
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
    layout.addWidget(widget)
    return container


class PageFrame(QFrame):
    def __init__(self, title: str, description: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(title.replace(" ", "-"))
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(24, 20, 24, 20)
        self.root_layout.setSpacing(16)

        self.title_label = LargeTitleLabel(title, self)
        self.root_layout.addWidget(self.title_label)

        if description:
            self.description_label = BodyLabel(description, self)
            self.description_label.setWordWrap(True)
            self.root_layout.addWidget(self.description_label)


class SectionCard(CardWidget):
    def __init__(self, title: str, description: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        title_label = StrongBodyLabel(title, self)
        layout.addWidget(title_label)
        if description:
            desc_label = CaptionLabel(description, self)
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)

        self.body_layout = QVBoxLayout()
        self.body_layout.setSpacing(12)
        layout.addLayout(self.body_layout)


class SectionHeader(QWidget):
    def __init__(self, title: str, description: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(12)
        self.title_label = TitleLabel(title, self)
        title_row.addWidget(self.title_label)
        title_row.addWidget(HorizontalSeparator(self), 1)
        layout.addLayout(title_row)

        if description:
            self.description_label = CaptionLabel(description, self)
            self.description_label.setWordWrap(True)
            layout.addWidget(self.description_label)


class MetricTile(CardWidget):
    def __init__(self, title: str, accent_color: str, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(156)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        accent_bar = QFrame(self)
        accent_bar.setFixedHeight(4)
        accent_bar.setStyleSheet(f"background-color: {accent_color}; border-radius: 2px;")
        layout.addWidget(accent_bar)

        self.title_label = CaptionLabel(title, self)
        self.value_label = DisplayLabel("0", self)
        self.detail_label = BodyLabel("", self)
        self.detail_label.setWordWrap(True)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)
        layout.addStretch(1)

    def set_metric(self, value: str, detail: str) -> None:
        self.value_label.setText(str(value))
        self.detail_label.setText(detail)


class DashboardHeroCard(CardWidget):
    refresh_requested = pyqtSignal()
    manage_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dashboardHeroCard")
        self.setStyleSheet(
            """
            QWidget#dashboardHeroCard {
                border: 1px solid rgba(0, 120, 212, 0.18);
                border-radius: 22px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 rgba(245, 250, 255, 255),
                    stop: 0.6 rgba(255, 255, 255, 255),
                    stop: 1 rgba(244, 248, 252, 255)
                );
            }
            QFrame#dashboardHeroBadge {
                background-color: rgba(0, 120, 212, 0.09);
                border: 1px solid rgba(0, 120, 212, 0.18);
                border-radius: 12px;
            }
            QLabel#dashboardHeroBadgeText {
                color: rgb(0, 90, 158);
                font-weight: 600;
            }
            QFrame#dashboardHeroSidePanel {
                background-color: rgba(255, 255, 255, 0.82);
                border: 1px solid rgba(15, 23, 42, 0.08);
                border-radius: 18px;
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(24)

        left_widget = QWidget(self)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        badge = QFrame(left_widget)
        badge.setObjectName("dashboardHeroBadge")
        badge_layout = QHBoxLayout(badge)
        badge_layout.setContentsMargins(12, 6, 12, 6)
        badge_layout.setSpacing(0)
        badge_label = CaptionLabel("桌面控制台", badge)
        badge_label.setObjectName("dashboardHeroBadgeText")
        badge_layout.addWidget(badge_label)
        left_layout.addWidget(badge, 0, Qt.AlignLeft)

        self.title_label = LargeTitleLabel(APP_TITLE, left_widget)
        self.subtitle_label = BodyLabel("统一管理配置、全局凭据与实时运行状态。", left_widget)
        self.subtitle_label.setWordWrap(True)
        self.description_label = CaptionLabel("首页提供概况、关键指标和按配置分区的日志监控视图。", left_widget)
        self.description_label.setWordWrap(True)
        left_layout.addWidget(self.title_label)
        left_layout.addWidget(self.subtitle_label)
        left_layout.addWidget(self.description_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 0)
        action_row.setSpacing(10)
        self.refresh_button = PrimaryPushButton("刷新主页", left_widget)
        self.manage_button = TransparentPushButton("前往配置管理", left_widget)
        action_row.addWidget(self.refresh_button)
        action_row.addWidget(self.manage_button)
        action_row.addStretch(1)
        left_layout.addLayout(action_row)

        side_panel = QFrame(self)
        side_panel.setObjectName("dashboardHeroSidePanel")
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(18, 18, 18, 18)
        side_layout.setSpacing(8)
        side_panel.setMinimumWidth(300)

        self.status_eyebrow = CaptionLabel("系统状态", side_panel)
        self.status_title = TitleLabel("已就绪", side_panel)
        self.status_body = BodyLabel("当前环境可继续进行配置与任务管理。", side_panel)
        self.status_body.setWordWrap(True)
        self.status_note = CaptionLabel("运行状态变化后，首页将自动同步更新。", side_panel)
        self.status_note.setWordWrap(True)
        side_layout.addWidget(self.status_eyebrow)
        side_layout.addWidget(self.status_title)
        side_layout.addWidget(self.status_body)
        side_layout.addStretch(1)
        side_layout.addWidget(HorizontalSeparator(side_panel))
        side_layout.addWidget(self.status_note)

        layout.addWidget(left_widget, 1)
        layout.addWidget(side_panel, 0)

        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.manage_button.clicked.connect(self.manage_requested.emit)

    def set_status(self, title: str, body: str, note: str) -> None:
        self.status_title.setText(title)
        self.status_body.setText(body)
        self.status_note.setText(note)


class ChipPanel(QWidget):
    selection_changed = pyqtSignal()

    def __init__(self, empty_text: str = "暂无项目", parent=None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, PillPushButton] = {}
        self._order: list[str] = []
        self._empty_text = empty_text
        self._muted = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

        self.empty_label = CaptionLabel(empty_text, self)
        self.empty_label.setWordWrap(True)
        self._layout.addWidget(self.empty_label)

        self.flow_widget = QWidget(self)
        self.flow_layout = FlowLayout(self.flow_widget)
        self.flow_layout.setContentsMargins(0, 0, 0, 0)
        self.flow_layout.setHorizontalSpacing(8)
        self.flow_layout.setVerticalSpacing(8)
        self._layout.addWidget(self.flow_widget)
        self.flow_widget.hide()

    def _clear_buttons(self) -> None:
        while self.flow_layout.count():
            widget = self.flow_layout.takeAt(0)
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()
        self._order.clear()

    def set_items(self, items: list[tuple[str, str]], selected: list[str] | set[str] | None = None) -> None:
        self._muted = True
        self._clear_buttons()
        self._order = [value for value, _ in items]
        selected_values = {str(item) for item in (selected or [])}

        for value, label in items:
            button = PillPushButton(label, self.flow_widget)
            button.setCheckable(True)
            button.setChecked(value in selected_values)
            button.toggled.connect(self._emit_changed)
            self.flow_layout.addWidget(button)
            self._buttons[value] = button

        has_items = bool(items)
        self.empty_label.setVisible(not has_items)
        self.flow_widget.setVisible(has_items)
        if not has_items:
            self.empty_label.setText(self._empty_text)
        self._muted = False

    def set_selected(self, values: list[str] | set[str]) -> None:
        selected_values = {str(item) for item in values}
        self._muted = True
        for value, button in self._buttons.items():
            button.setChecked(value in selected_values)
        self._muted = False
        self.selection_changed.emit()

    def selected_values(self) -> list[str]:
        return [value for value in self._order if value in self._buttons and self._buttons[value].isChecked()]

    def clear_selection(self) -> None:
        self.set_selected([])

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        if not self._buttons:
            self.empty_label.setText(text)

    def _emit_changed(self) -> None:
        if not self._muted:
            self.selection_changed.emit()


class TextInputDialog(MessageBoxBase):
    def __init__(
        self,
        title: str,
        content: str,
        placeholder: str,
        confirm_text: str = "确定",
        default_value: str = "",
        parent=None,
    ) -> None:
        super().__init__(dialog_parent(parent))
        self.title_label = SubtitleLabel(title, self)
        self.content_label = BodyLabel(content, self)
        self.content_label.setWordWrap(True)
        self.input_edit = LineEdit(self)
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.setText(default_value)
        self.input_edit.returnPressed.connect(self.yesButton.click)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.content_label)
        self.viewLayout.addWidget(self.input_edit)

        self.widget.setMinimumWidth(440)
        self.yesButton.setText(confirm_text)
        self.cancelButton.setText("取消")
        self.input_edit.setFocus()

    def value(self) -> str:
        return self.input_edit.text().strip()

    def validate(self) -> bool:
        if self.value():
            return True
        show_bar(self, "warning", "名称无效", "请输入配置名称。")
        return False


class ProfileListCard(CardWidget):
    activated = pyqtSignal(str)
    checked_changed = pyqtSignal(str, bool)

    def __init__(self, profile_name: str, status_text: str, summary_text: str, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.profile_name = profile_name
        self.setObjectName("profileListCard")
        self.setProperty("active", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            """
            QWidget#profileListCard[active="true"] {
                border: 1px solid rgba(0, 120, 212, 0.58);
                background-color: rgba(0, 120, 212, 0.08);
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        self.check_box = CheckBox("", self)
        self.check_box.setChecked(checked)
        layout.addWidget(self.check_box, 0, Qt.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self.title_label = StrongBodyLabel(profile_name, self)
        self.status_label = CaptionLabel(status_text, self)
        header_row.addWidget(self.title_label, 1)
        header_row.addWidget(self.status_label)
        text_layout.addLayout(header_row)

        self.summary_label = CaptionLabel(summary_text, self)
        self.summary_label.setWordWrap(True)
        text_layout.addWidget(self.summary_label)
        layout.addLayout(text_layout, 1)

        self.check_box.clicked.connect(self._emit_checked_changed)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.profile_name)

    def _emit_checked_changed(self) -> None:
        self.checked_changed.emit(self.profile_name, self.check_box.isChecked())
        self.activated.emit(self.profile_name)

class CourseFetchThread(QThread):
    loaded = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, profile_name: str, parent=None) -> None:
        super().__init__(parent)
        self.profile_name = profile_name

    def run(self) -> None:
        try:
            courses = fetch_courses_for_profile(self.profile_name)
            self.loaded.emit(self.profile_name, courses)
        except Exception as exc:
            self.failed.emit(self.profile_name, str(exc))


class HomePage(PageFrame):
    def __init__(self, run_manager: RunManager, parent=None) -> None:
        super().__init__(
            "概览",
            "查看配置概况、数据目录与各配置的实时运行日志。",
            parent,
        )
        self.title_label.hide()
        if hasattr(self, "description_label"):
            self.description_label.hide()

        self.run_manager = run_manager
        self.cards: dict[str, LogCard] = {}
        self.run_manager.runs_changed.connect(self.refresh_dashboard)
        self.run_manager.log_received.connect(self.on_log_received)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.root_layout.addWidget(self.scroll, 1)

        self.scroll_content = QWidget(self.scroll)
        self.scroll_content.setObjectName("homeScrollContent")
        self.scroll_content.setStyleSheet(
            """
            QWidget#homeScrollContent QLabel {
                color: rgb(31, 41, 55);
                background: transparent;
            }
            """
        )
        self.content_layout = QVBoxLayout(self.scroll_content)
        self.content_layout.setContentsMargins(0, 0, 4, 14)
        self.content_layout.setSpacing(20)
        self.scroll.setWidget(self.scroll_content)

        self.hero_card = DashboardHeroCard(self.scroll_content)
        self.hero_card.refresh_requested.connect(self.refresh_dashboard)
        self.hero_card.manage_requested.connect(self.open_profiles_page)
        self.content_layout.addWidget(self.hero_card)

        self.metrics_widget = QWidget(self.scroll_content)
        self.metrics_layout = QGridLayout(self.metrics_widget)
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setHorizontalSpacing(14)
        self.metrics_layout.setVerticalSpacing(14)
        self.total_tile = MetricTile("配置总数", "#0078D4", self.metrics_widget)
        self.running_tile = MetricTile("运行中", "#0F9D58", self.metrics_widget)
        self.finished_tile = MetricTile("最近完成", "#C58B00", self.metrics_widget)
        self.attention_tile = MetricTile("需关注", "#D13438", self.metrics_widget)
        metric_tiles = [self.total_tile, self.running_tile, self.finished_tile, self.attention_tile]
        for index, tile in enumerate(metric_tiles):
            self.metrics_layout.addWidget(tile, 0, index)
            self.metrics_layout.setColumnStretch(index, 1)
        self.content_layout.addWidget(self.metrics_widget)

        self.overview_header = SectionHeader("运行概况", "从整体视角查看题库分布与本地数据目录。", self.scroll_content)
        self.content_layout.addWidget(self.overview_header)

        self.overview_widget = QWidget(self.scroll_content)
        self.overview_layout = QGridLayout(self.overview_widget)
        self.overview_layout.setContentsMargins(0, 0, 0, 0)
        self.overview_layout.setHorizontalSpacing(14)
        self.overview_layout.setVerticalSpacing(14)

        summary_card = SectionCard("题库分布", "快速确认当前配置所使用的题库构成。", parent=self.overview_widget)
        self.summary_label = BodyLabel(summary_card)
        self.summary_label.setWordWrap(True)
        summary_card.body_layout.addWidget(self.summary_label)

        path_card = SectionCard("数据目录", "配置、全局设置与运行缓存均位于当前工作区。", parent=self.overview_widget)
        self.path_label = BodyLabel(path_card)
        self.path_label.setWordWrap(True)
        path_card.body_layout.addWidget(self.path_label)

        self.overview_layout.addWidget(summary_card, 0, 0)
        self.overview_layout.addWidget(path_card, 0, 1)
        self.overview_layout.setColumnStretch(0, 1)
        self.overview_layout.setColumnStretch(1, 1)
        self.content_layout.addWidget(self.overview_widget)

        self.logs_header = SectionHeader("运行监控", "按配置展示独立日志卡片，便于同时观察多个任务。", self.scroll_content)
        self.content_layout.addWidget(self.logs_header)

        self.empty_label = CaptionLabel("暂无配置。请先在“配置管理”页面创建或导入配置。", self.scroll_content)
        self.empty_label.setWordWrap(True)
        self.content_layout.addWidget(self.empty_label)

        self.log_host = QWidget(self.scroll_content)
        self.log_layout = QVBoxLayout(self.log_host)
        self.log_layout.setContentsMargins(0, 0, 0, 0)
        self.log_layout.setSpacing(14)
        self.content_layout.addWidget(self.log_host)
        self.content_layout.addStretch(1)

        self.refresh_dashboard()

    def refresh_dashboard(self) -> None:
        self.refresh_summary()
        self.refresh_cards()

    def open_profiles_page(self) -> None:
        window = self.window()
        if hasattr(window, "switchTo") and hasattr(window, "profiles_page"):
            window.switchTo(window.profiles_page)

    def refresh_summary(self) -> None:
        names = [path.stem for path in list_json_profiles()]
        runs = self.run_manager.list_runs()
        running_count = sum(1 for run in runs if run.status == "running")
        finished_count = sum(1 for run in runs if run.status == "completed")
        failed_count = sum(1 for run in runs if run.status in {"failed", "stopped"})
        providers: dict[str, int] = {}

        for name in names:
            profile = load_json_profile(name)
            provider = profile.get("tiku", {}).get("provider", "未配置") or "未配置"
            providers[provider] = providers.get(provider, 0) + 1

        provider_lines = "\n".join(f"- {provider}: {count}" for provider, count in sorted(providers.items()))
        if not provider_lines:
            provider_lines = "- 暂无配置"

        if not names:
            hero_title = "尚未建立配置"
            hero_body = "请先创建或导入配置，随后即可在首页查看运行概况与日志监控。"
            hero_note = "配置建立后，首页会自动生成关键指标卡片和日志区域。"
        elif failed_count:
            hero_title = "存在待处理项目"
            hero_body = f"当前有 {failed_count} 个配置处于停止或失败状态，建议优先查看对应日志。"
            hero_note = "下方运行监控区域会持续显示各配置的最新输出。"
        elif running_count:
            idle_count = max(len(names) - running_count, 0)
            hero_title = "任务运行中"
            hero_body = f"当前有 {running_count} 个配置正在执行，另有 {idle_count} 个配置处于待命状态。"
            hero_note = "运行中的日志卡片会自动滚动到最新输出。"
        else:
            hero_title = "系统已就绪"
            hero_body = f"当前共管理 {len(names)} 个配置，尚未发现需要立即处理的运行异常。"
            hero_note = "可直接前往配置管理调整参数，或在日志卡片中启动任务。"

        self.hero_card.set_status(hero_title, hero_body, hero_note)
        self.total_tile.set_metric(str(len(names)), "当前已纳入管理的配置数量。")
        self.running_tile.set_metric(str(running_count), "正在执行中的配置任务数量。")
        self.finished_tile.set_metric(str(finished_count), "本轮运行中已完成的任务数量。")
        attention_detail = "存在已停止或失败的配置任务。" if failed_count else "当前没有失败或中断的任务。"
        self.attention_tile.set_metric(str(failed_count), attention_detail)

        provider_count = len(providers)
        summary_lines = [
            f"题库类型：{provider_count} 种",
            f"配置总数：{len(names)} 个",
            "",
            "分布明细：",
            provider_lines,
        ]
        self.summary_label.setText("\n".join(summary_lines))
        self.path_label.setText(
            "\n".join(
                [
                    f"配置目录：{JSON_PROFILE_DIR}",
                    f"全局设置：{PROJECT_ROOT / 'desktop_state' / 'global_settings.json'}",
                    "缓存文件：按配置自动生成在 profiles 目录中",
                ]
            )
        )

    def refresh_cards(self) -> None:
        names = [path.stem for path in list_json_profiles()]
        existing_names = set(self.cards)

        for name in sorted(existing_names - set(names)):
            card = self.cards.pop(name)
            card.deleteLater()

        for name in names:
            if name not in self.cards:
                card = LogCard(name, self.run_manager, self.log_host)
                card.start_requested.connect(self.start_profile)
                card.stop_requested.connect(self.stop_profile)
                self.cards[name] = card

        while self.log_layout.count():
            item = self.log_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        for name in names:
            card = self.cards[name]
            self.log_layout.addWidget(card)
            card.refresh_card()
        self.log_layout.addStretch(1)

        self.empty_label.setVisible(not bool(names))
        self.log_host.setVisible(bool(names))

    def start_profile(self, profile_name: str) -> None:
        try:
            self.run_manager.start_profile(profile_name)
        except Exception as exc:
            show_error(self, "启动失败", str(exc))
            return
        self.refresh_dashboard()
        show_bar(self, "success", "启动成功", f"{profile_name} 已启动。")

    def stop_profile(self, profile_name: str) -> None:
        try:
            self.run_manager.stop_profile(profile_name)
        except Exception as exc:
            show_error(self, "停止失败", str(exc))
            return
        self.refresh_dashboard()
        show_bar(self, "success", "停止成功", f"{profile_name} 已停止。")

    def on_log_received(self, profile_name: str, line: str) -> None:
        card = self.cards.get(profile_name)
        if card:
            card.append_log(line)


class ProfileEditorPanel(QWidget):
    profile_saved = pyqtSignal(str)
    start_requested = pyqtSignal(str)
    stop_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(self, run_manager: RunManager, parent=None) -> None:
        super().__init__(parent)
        self.run_manager = run_manager
        self._loading = False
        self._dirty = False
        self._current_profile_name: str | None = None
        self._profile_source = deepcopy(DEFAULT_PROFILE)
        self._course_cache: dict[str, list[dict]] = {}
        self._courses: list[dict] = []
        self._selected_course_ids: list[str] = []
        self._course_fetch_thread: CourseFetchThread | None = None

        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(12)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title_container = QVBoxLayout()
        title_container.setSpacing(4)
        self.profile_title = SubtitleLabel("未选择配置", header)
        self.profile_state = CaptionLabel("请选择左侧配置后再进行编辑。", header)
        self.profile_state.setWordWrap(True)
        title_container.addWidget(self.profile_title)
        title_container.addWidget(self.profile_state)
        header_layout.addLayout(title_container, 1)

        self.reload_button = PushButton("重新载入", header)
        self.save_button = PrimaryPushButton("保存配置", header)
        self.start_button = PushButton("启动当前", header)
        self.stop_button = PushButton("停止当前", header)
        self.delete_button = PushButton("删除当前", header)
        header_layout.addWidget(self.reload_button)
        header_layout.addWidget(self.save_button)
        header_layout.addWidget(self.start_button)
        header_layout.addWidget(self.stop_button)
        header_layout.addWidget(self.delete_button)
        self.root_layout.addWidget(header)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.root_layout.addWidget(self.scroll, 1)

        self.scroll_content = QWidget(self.scroll)
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 4, 12)
        self.scroll_layout.setSpacing(16)
        self.scroll.setWidget(self.scroll_content)

        self._build_common_card()
        self._build_tiku_card()
        self._build_course_card()
        self._build_notification_card()
        self._build_json_card()
        self.scroll_layout.addStretch(1)

        self.reload_button.clicked.connect(self.reload_profile)
        self.save_button.clicked.connect(self.save_profile)
        self.start_button.clicked.connect(self._emit_start)
        self.stop_button.clicked.connect(self._emit_stop)
        self.delete_button.clicked.connect(self._emit_delete)

        self.clear_profile()

    @property
    def current_profile_name(self) -> str | None:
        return self._current_profile_name

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def _build_common_card(self) -> None:
        self.common_card = SectionCard("通用设置", "用于配置登录方式、并发参数与运行节奏。", self.scroll_content)
        common_grid = QGridLayout()
        common_grid.setHorizontalSpacing(16)
        common_grid.setVerticalSpacing(12)

        self.use_cookies_check = CheckBox("优先使用已有 cookies 登录", self.common_card)
        self.username_edit = LineEdit(self.common_card)
        self.username_edit.setPlaceholderText("手机号账号")
        self.password_edit = LineEdit(self.common_card)
        self.password_edit.setEchoMode(LineEdit.Password)
        self.password_edit.setPlaceholderText("登录密码")
        self.speed_spin = DoubleSpinBox(self.common_card)
        self.speed_spin.setRange(1.0, 2.0)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setSingleStep(0.1)
        self.jobs_spin = SpinBox(self.common_card)
        self.jobs_spin.setRange(1, 16)
        self.notopen_combo = ComboBox(self.common_card)
        for value in NOTOPEN_ACTION_OPTIONS:
            self.notopen_combo.addItem(NOTOPEN_ACTION_LABELS[value], value)
        self.cookies_path_edit = LineEdit(self.common_card)
        self.cookies_path_edit.setPlaceholderText("留空时自动使用当前配置的独立 Cookies 文件")
        self.cache_path_edit = LineEdit(self.common_card)
        self.cache_path_edit.setPlaceholderText("留空时自动使用当前配置的独立缓存文件")

        common_grid.addWidget(self.use_cookies_check, 0, 0, 1, 2)
        common_grid.addWidget(make_field("账号", self.username_edit), 1, 0)
        common_grid.addWidget(make_field("密码", self.password_edit), 1, 1)
        common_grid.addWidget(make_field("倍速", self.speed_spin), 2, 0)
        common_grid.addWidget(make_field("并发章节数", self.jobs_spin), 2, 1)
        common_grid.addWidget(make_field("关闭章节处理策略", self.notopen_combo), 3, 0)
        common_grid.addWidget(make_field("Cookies 路径", self.cookies_path_edit), 4, 0)
        common_grid.addWidget(make_field("Cache 路径", self.cache_path_edit), 4, 1)
        self.common_card.body_layout.addLayout(common_grid)
        self.scroll_layout.addWidget(self.common_card)

        self._wire_dirty_signals(
            self.use_cookies_check,
            self.username_edit,
            self.password_edit,
            self.speed_spin,
            self.jobs_spin,
            self.notopen_combo,
            self.cookies_path_edit,
            self.cache_path_edit,
        )

    def _build_tiku_card(self) -> None:
        self.tiku_card = SectionCard(
            "题库与 AI",
            "未填写的字段将继承全局设置。协同题库可直接在下方选择。",
            self.scroll_content,
        )
        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(16)
        top_grid.setVerticalSpacing(12)

        self.provider_combo = ComboBox(self.tiku_card)
        self.provider_combo.addItems(PROVIDER_OPTIONS)
        self.decision_provider_combo = ComboBox(self.tiku_card)
        self.decision_provider_combo.addItems(DECISION_PROVIDER_OPTIONS)
        self.check_connection_check = CheckBox("启动时检查大模型连接", self.tiku_card)
        self.submit_check = CheckBox("达到覆盖率后自动提交", self.tiku_card)
        self.cover_rate_spin = DoubleSpinBox(self.tiku_card)
        self.cover_rate_spin.setRange(0.1, 1.0)
        self.cover_rate_spin.setDecimals(2)
        self.cover_rate_spin.setSingleStep(0.05)
        self.delay_spin = DoubleSpinBox(self.tiku_card)
        self.delay_spin.setRange(0.0, 60.0)
        self.delay_spin.setDecimals(1)
        self.delay_spin.setSingleStep(0.5)

        top_grid.addWidget(make_field("主题库", self.provider_combo), 0, 0)
        top_grid.addWidget(make_field("冲突仲裁题库", self.decision_provider_combo), 0, 1)
        top_grid.addWidget(make_field("最低覆盖率", self.cover_rate_spin), 1, 0)
        top_grid.addWidget(make_field("单题间隔（秒）", self.delay_spin), 1, 1)
        top_grid.addWidget(self.check_connection_check, 2, 0)
        top_grid.addWidget(self.submit_check, 2, 1)
        self.tiku_card.body_layout.addLayout(top_grid)

        self.provider_summary = CaptionLabel(self.tiku_card)
        self.provider_summary.setWordWrap(True)
        self.tiku_card.body_layout.addWidget(self.provider_summary)
        self.provider_chip_panel = ChipPanel("暂无可用的协同题库。", self.tiku_card)
        self.provider_chip_panel.set_items([(item, item) for item in COLLAB_PROVIDER_OPTIONS], [])
        self.tiku_card.body_layout.addWidget(
            make_field(
                "协同题库",
                self.provider_chip_panel,
                "选择 1 个题库时将直接使用该题库；选择 2 个及以上题库时将自动切换为 MultiTiku。",
            )
        )

        detail_grid = QGridLayout()
        detail_grid.setHorizontalSpacing(16)
        detail_grid.setVerticalSpacing(12)

        self.tokens_edit = LineEdit(self.tiku_card)
        self.tokens_edit.setPlaceholderText("Enncy / LIKE 的令牌，多个用英文逗号分隔")
        self.ai_endpoint_edit = LineEdit(self.tiku_card)
        self.ai_endpoint_edit.setPlaceholderText("兼容 OpenAI 格式的接口地址")
        self.ai_key_edit = LineEdit(self.tiku_card)
        self.ai_key_edit.setPlaceholderText("接口密钥")
        self.ai_model_edit = LineEdit(self.tiku_card)
        self.ai_model_edit.setPlaceholderText("模型名称")
        self.http_proxy_edit = LineEdit(self.tiku_card)
        self.http_proxy_edit.setPlaceholderText("可选代理，例如 http://127.0.0.1:7890")
        self.min_interval_spin = SpinBox(self.tiku_card)
        self.min_interval_spin.setRange(0, 120)

        self.silicon_key_edit = LineEdit(self.tiku_card)
        self.silicon_key_edit.setPlaceholderText("SiliconFlow 密钥")
        self.silicon_model_edit = LineEdit(self.tiku_card)
        self.silicon_model_edit.setPlaceholderText("SiliconFlow 模型")
        self.silicon_endpoint_edit = LineEdit(self.tiku_card)
        self.silicon_endpoint_edit.setPlaceholderText("SiliconFlow 接口地址")

        self.like_model_edit = LineEdit(self.tiku_card)
        self.like_model_edit.setPlaceholderText("LIKE 模型")
        self.like_retry_times_spin = SpinBox(self.tiku_card)
        self.like_retry_times_spin.setRange(0, 10)
        self.like_search_check = CheckBox("LIKE 启用联网搜索", self.tiku_card)
        self.like_vision_check = CheckBox("LIKE 启用视觉识图", self.tiku_card)
        self.like_retry_check = CheckBox("LIKE 失败自动重试", self.tiku_card)

        self.adapter_url_edit = LineEdit(self.tiku_card)
        self.adapter_url_edit.setPlaceholderText("TikuAdapter 地址")
        self.true_list_edit = LineEdit(self.tiku_card)
        self.true_list_edit.setPlaceholderText("正确,对,√,是")
        self.false_list_edit = LineEdit(self.tiku_card)
        self.false_list_edit.setPlaceholderText("错误,错,×,否")

        detail_grid.addWidget(make_field("令牌列表", self.tokens_edit, "留空则继承全局令牌"), 0, 0, 1, 2)
        detail_grid.addWidget(make_field("AI 接口地址", self.ai_endpoint_edit), 1, 0)
        detail_grid.addWidget(make_field("AI 密钥", self.ai_key_edit), 1, 1)
        detail_grid.addWidget(make_field("AI 模型", self.ai_model_edit), 2, 0)
        detail_grid.addWidget(make_field("HTTP 代理", self.http_proxy_edit), 2, 1)
        detail_grid.addWidget(make_field("最小请求间隔", self.min_interval_spin), 3, 0)
        detail_grid.addWidget(make_field("硅基密钥", self.silicon_key_edit), 4, 0)
        detail_grid.addWidget(make_field("硅基模型", self.silicon_model_edit), 4, 1)
        detail_grid.addWidget(make_field("硅基接口地址", self.silicon_endpoint_edit), 5, 0, 1, 2)
        detail_grid.addWidget(make_field("LIKE 模型", self.like_model_edit), 6, 0)
        detail_grid.addWidget(make_field("LIKE 重试次数", self.like_retry_times_spin), 6, 1)
        detail_grid.addWidget(self.like_search_check, 7, 0)
        detail_grid.addWidget(self.like_vision_check, 7, 1)
        detail_grid.addWidget(self.like_retry_check, 8, 0)
        detail_grid.addWidget(make_field("TikuAdapter 地址", self.adapter_url_edit), 9, 0, 1, 2)
        detail_grid.addWidget(make_field("判断题真值列表", self.true_list_edit), 10, 0)
        detail_grid.addWidget(make_field("判断题假值列表", self.false_list_edit), 10, 1)
        self.tiku_card.body_layout.addLayout(detail_grid)
        self.scroll_layout.addWidget(self.tiku_card)

        self.provider_combo.currentTextChanged.connect(self._on_provider_combo_changed)
        self.provider_chip_panel.selection_changed.connect(self._on_provider_chips_changed)
        self._wire_dirty_signals(
            self.provider_combo,
            self.decision_provider_combo,
            self.check_connection_check,
            self.submit_check,
            self.cover_rate_spin,
            self.delay_spin,
            self.tokens_edit,
            self.ai_endpoint_edit,
            self.ai_key_edit,
            self.ai_model_edit,
            self.http_proxy_edit,
            self.min_interval_spin,
            self.silicon_key_edit,
            self.silicon_model_edit,
            self.silicon_endpoint_edit,
            self.like_model_edit,
            self.like_retry_times_spin,
            self.like_search_check,
            self.like_vision_check,
            self.like_retry_check,
            self.adapter_url_edit,
            self.true_list_edit,
            self.false_list_edit,
        )

    def _build_course_card(self) -> None:
        self.course_card = SectionCard(
            "课程选择",
            "可根据当前配置的账号或 Cookies 获取课程列表，并以标签形式选择课程。",
            self.scroll_content,
        )
        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        self.refresh_courses_button = PrimaryPushButton("刷新课程列表", self.course_card)
        self.clear_courses_button = PushButton("清空已选课程", self.course_card)
        button_row.addWidget(self.refresh_courses_button)
        button_row.addWidget(self.clear_courses_button)
        button_row.addStretch(1)
        self.course_card.body_layout.addLayout(button_row)

        self.course_status = CaptionLabel("尚未获取课程列表。", self.course_card)
        self.course_status.setWordWrap(True)
        self.course_card.body_layout.addWidget(self.course_status)

        self.course_chip_panel = ChipPanel("尚未获取课程列表。", self.course_card)
        self.course_chip_panel.selection_changed.connect(self._on_course_selection_changed)
        self.course_card.body_layout.addWidget(self.course_chip_panel)
        self.scroll_layout.addWidget(self.course_card)

        self.refresh_courses_button.clicked.connect(self.refresh_courses)
        self.clear_courses_button.clicked.connect(self.clear_courses)

    def _build_notification_card(self) -> None:
        self.notification_card = SectionCard("通知", "如无需通知服务，保持“不启用”即可。", self.scroll_content)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.notification_provider_combo = ComboBox(self.notification_card)
        self.notification_provider_combo.addItems(NOTIFICATION_PROVIDER_OPTIONS)
        self.notification_url_edit = LineEdit(self.notification_card)
        self.notification_url_edit.setPlaceholderText("推送地址")
        self.notification_chat_id_edit = LineEdit(self.notification_card)
        self.notification_chat_id_edit.setPlaceholderText("Telegram 会话 ID")
        grid.addWidget(make_field("通知提供方", self.notification_provider_combo), 0, 0)
        grid.addWidget(make_field("通知地址", self.notification_url_edit), 1, 0, 1, 2)
        grid.addWidget(make_field("Telegram 会话 ID", self.notification_chat_id_edit), 2, 0)
        self.notification_card.body_layout.addLayout(grid)
        self.scroll_layout.addWidget(self.notification_card)

        self._wire_dirty_signals(
            self.notification_provider_combo,
            self.notification_url_edit,
            self.notification_chat_id_edit,
        )

    def _build_json_card(self) -> None:
        self.json_card = SectionCard("高级 JSON 编辑", "如需直接编辑原始配置，可展开 JSON 编辑器并按格式保存。", self.scroll_content)
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(12)
        self.toggle_json_button = TransparentPushButton("展开 JSON 编辑器", self.json_card)
        self.toggle_json_button.clicked.connect(self.toggle_json_editor)
        toggle_row.addWidget(self.toggle_json_button)
        toggle_row.addStretch(1)
        self.json_card.body_layout.addLayout(toggle_row)

        self.json_editor_container = QWidget(self.json_card)
        self.json_editor_container.hide()
        json_layout = QVBoxLayout(self.json_editor_container)
        json_layout.setContentsMargins(0, 0, 0, 0)
        json_layout.setSpacing(10)
        self.json_editor = PlainTextEdit(self.json_editor_container)
        self.json_editor.setPlaceholderText("当前配置的 JSON 内容将显示于此。")
        self.json_editor.setMinimumHeight(260)
        json_layout.addWidget(self.json_editor)

        json_button_row = QHBoxLayout()
        json_button_row.setSpacing(12)
        self.refresh_json_button = PushButton("从表单刷新 JSON", self.json_editor_container)
        self.apply_json_button = PushButton("应用 JSON 到表单", self.json_editor_container)
        self.save_json_button = PrimaryPushButton("直接保存 JSON", self.json_editor_container)
        json_button_row.addWidget(self.refresh_json_button)
        json_button_row.addWidget(self.apply_json_button)
        json_button_row.addWidget(self.save_json_button)
        json_button_row.addStretch(1)
        json_layout.addLayout(json_button_row)
        self.json_card.body_layout.addWidget(self.json_editor_container)
        self.scroll_layout.addWidget(self.json_card)

        self.refresh_json_button.clicked.connect(self.refresh_json_editor)
        self.apply_json_button.clicked.connect(self.apply_json_to_form)
        self.save_json_button.clicked.connect(self.save_json_directly)

    def _wire_dirty_signals(self, *widgets: QWidget) -> None:
        for widget in widgets:
            if isinstance(widget, LineEdit):
                widget.textChanged.connect(self._mark_dirty)
            elif isinstance(widget, (SpinBox, DoubleSpinBox)):
                widget.valueChanged.connect(self._mark_dirty)
            elif isinstance(widget, ComboBox):
                widget.currentTextChanged.connect(self._mark_dirty)
            elif isinstance(widget, CheckBox):
                widget.stateChanged.connect(self._mark_dirty)

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in [
            self.reload_button,
            self.save_button,
            self.start_button,
            self.stop_button,
            self.delete_button,
            self.use_cookies_check,
            self.username_edit,
            self.password_edit,
            self.speed_spin,
            self.jobs_spin,
            self.notopen_combo,
            self.cookies_path_edit,
            self.cache_path_edit,
            self.provider_combo,
            self.decision_provider_combo,
            self.check_connection_check,
            self.submit_check,
            self.cover_rate_spin,
            self.delay_spin,
            self.tokens_edit,
            self.ai_endpoint_edit,
            self.ai_key_edit,
            self.ai_model_edit,
            self.http_proxy_edit,
            self.min_interval_spin,
            self.silicon_key_edit,
            self.silicon_model_edit,
            self.silicon_endpoint_edit,
            self.like_model_edit,
            self.like_retry_times_spin,
            self.like_search_check,
            self.like_vision_check,
            self.like_retry_check,
            self.adapter_url_edit,
            self.true_list_edit,
            self.false_list_edit,
            self.refresh_courses_button,
            self.clear_courses_button,
            self.notification_provider_combo,
            self.notification_url_edit,
            self.notification_chat_id_edit,
            self.toggle_json_button,
        ]:
            widget.setEnabled(enabled)
        self.provider_chip_panel.setEnabled(enabled)
        self.course_chip_panel.setEnabled(enabled)

    def clear_profile(self) -> None:
        self._current_profile_name = None
        self._dirty = False
        self._profile_source = deepcopy(DEFAULT_PROFILE)
        self._courses = []
        self._selected_course_ids = []
        self._loading = True

        self.profile_title.setText("未选择配置")
        self.profile_state.setText("请选择左侧配置后再进行编辑。")
        self.use_cookies_check.setChecked(False)
        self.username_edit.clear()
        self.password_edit.clear()
        self.speed_spin.setValue(1.0)
        self.jobs_spin.setValue(4)
        set_notopen_action(self.notopen_combo, "retry")
        self.cookies_path_edit.clear()
        self.cache_path_edit.clear()

        set_combo_text(self.provider_combo, "TikuYanxi")
        set_combo_text(self.decision_provider_combo, "SiliconFlow")
        self.check_connection_check.setChecked(True)
        self.submit_check.setChecked(False)
        self.cover_rate_spin.setValue(0.9)
        self.delay_spin.setValue(1.0)
        self.tokens_edit.clear()
        self.ai_endpoint_edit.clear()
        self.ai_key_edit.clear()
        self.ai_model_edit.clear()
        self.http_proxy_edit.clear()
        self.min_interval_spin.setValue(3)
        self.silicon_key_edit.clear()
        self.silicon_model_edit.clear()
        self.silicon_endpoint_edit.clear()
        self.like_model_edit.clear()
        self.like_retry_times_spin.setValue(3)
        self.like_search_check.setChecked(False)
        self.like_vision_check.setChecked(True)
        self.like_retry_check.setChecked(True)
        self.adapter_url_edit.clear()
        self.true_list_edit.setText(join_csv(DEFAULT_PROFILE["tiku"]["true_list"]))
        self.false_list_edit.setText(join_csv(DEFAULT_PROFILE["tiku"]["false_list"]))
        self.provider_chip_panel.set_selected([])

        set_combo_text(self.notification_provider_combo, "不启用")
        self.notification_url_edit.clear()
        self.notification_chat_id_edit.clear()
        self.json_editor.clear()
        self.course_chip_panel.set_items([], [])
        self.course_status.setText("尚未获取课程列表。")
        self.provider_summary.setText("尚未指定题库。")
        self._loading = False
        self._set_editor_enabled(False)

    def load_profile(self, profile_name: str) -> None:
        profile = load_json_profile(profile_name)
        self._populate_profile(profile_name, profile)

    def _populate_profile(self, profile_name: str, profile: dict) -> None:
        self._current_profile_name = profile_name
        self._profile_source = deepcopy(profile)
        self._selected_course_ids = list(profile.get("common", {}).get("course_list", []))
        self._courses = self._course_cache.get(profile_name, [])
        self._dirty = False
        self._loading = True

        common = profile.get("common", {})
        tiku = profile.get("tiku", {})
        notification = profile.get("notification", {})

        self.profile_title.setText(profile_name)
        self.use_cookies_check.setChecked(bool(common.get("use_cookies", False)))
        self.username_edit.setText(str(common.get("username", "")))
        self.password_edit.setText(str(common.get("password", "")))
        self.speed_spin.setValue(float(common.get("speed", 1.0) or 1.0))
        self.jobs_spin.setValue(int(common.get("jobs", 4) or 4))
        set_notopen_action(self.notopen_combo, str(common.get("notopen_action", "retry") or "retry"))
        self.cookies_path_edit.setText(str(common.get("cookies_path", "")))
        self.cache_path_edit.setText(str(common.get("cache_path", "")))

        provider = str(tiku.get("provider", "TikuYanxi") or "TikuYanxi")
        selected_providers = list(tiku.get("providers", []) or [])
        if not selected_providers and provider in COLLAB_PROVIDER_OPTIONS:
            selected_providers = [provider]
        set_combo_text(self.provider_combo, provider if provider in PROVIDER_OPTIONS else "TikuYanxi")
        set_combo_text(self.decision_provider_combo, str(tiku.get("decision_provider", "SiliconFlow") or "SiliconFlow"))
        self.check_connection_check.setChecked(bool(tiku.get("check_llm_connection", True)))
        self.submit_check.setChecked(bool(tiku.get("submit", False)))
        self.cover_rate_spin.setValue(float(tiku.get("cover_rate", 0.9) or 0.9))
        self.delay_spin.setValue(float(tiku.get("delay", 1.0) or 1.0))
        self.tokens_edit.setText(str(tiku.get("tokens", "")))
        self.ai_endpoint_edit.setText(str(tiku.get("endpoint", "")))
        self.ai_key_edit.setText(str(tiku.get("key", "")))
        self.ai_model_edit.setText(str(tiku.get("model", "")))
        self.http_proxy_edit.setText(str(tiku.get("http_proxy", "")))
        self.min_interval_spin.setValue(int(tiku.get("min_interval_seconds", 3) or 3))
        self.silicon_key_edit.setText(str(tiku.get("siliconflow_key", "")))
        self.silicon_model_edit.setText(str(tiku.get("siliconflow_model", "")))
        self.silicon_endpoint_edit.setText(str(tiku.get("siliconflow_endpoint", "")))
        self.like_model_edit.setText(str(tiku.get("likeapi_model", "")))
        self.like_retry_times_spin.setValue(int(tiku.get("likeapi_retry_times", 3) or 3))
        self.like_search_check.setChecked(bool(tiku.get("likeapi_search", False)))
        self.like_vision_check.setChecked(bool(tiku.get("likeapi_vision", True)))
        self.like_retry_check.setChecked(bool(tiku.get("likeapi_retry", True)))
        self.adapter_url_edit.setText(str(tiku.get("url", "")))
        self.true_list_edit.setText(join_csv(list(tiku.get("true_list", DEFAULT_PROFILE["tiku"]["true_list"]))))
        self.false_list_edit.setText(join_csv(list(tiku.get("false_list", DEFAULT_PROFILE["tiku"]["false_list"]))))
        self.provider_chip_panel.set_selected(selected_providers)

        provider_name = str(notification.get("provider", "") or "")
        set_combo_text(self.notification_provider_combo, provider_name if provider_name else "不启用")
        self.notification_url_edit.setText(str(notification.get("url", "")))
        self.notification_chat_id_edit.setText(str(notification.get("tg_chat_id", "")))

        if self._courses:
            self._apply_course_cards(self._courses)
        else:
            self.course_chip_panel.set_items([], [])
            self._update_course_summary()

        self._loading = False
        self._set_editor_enabled(True)
        self.refresh_json_editor()
        self._update_provider_summary()
        self.refresh_run_state()

    def refresh_run_state(self) -> None:
        if not self._current_profile_name:
            self.profile_state.setText("请选择左侧配置后再进行编辑。")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            return

        run_state = self.run_manager.get_run(self._current_profile_name)
        if run_state:
            if run_state.status == "running":
                text = f"运行中 | 配置文件：{run_state.profile_path}"
            else:
                text = f"状态：{display_status(run_state.status)} | 配置文件：{run_state.profile_path}"
        else:
            text = f"配置文件：{JSON_PROFILE_DIR / f'{self._current_profile_name}.json'}"

        if self._dirty:
            text += " | 有未保存修改"
        self.profile_state.setText(text)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(bool(run_state and run_state.status == "running"))
        self.delete_button.setEnabled(True)

    def reload_profile(self) -> None:
        if not self._current_profile_name:
            return
        self.load_profile(self._current_profile_name)
        show_bar(self, "success", "重新载入完成", f"{self._current_profile_name} 已从磁盘重新读取。")

    def collect_profile_data(self) -> dict:
        if not self._current_profile_name:
            raise ValueError("当前没有选中的配置")

        profile = deepcopy(self._profile_source)
        profile["name"] = self._current_profile_name
        common = profile.setdefault("common", {})
        tiku = profile.setdefault("tiku", {})
        notification = profile.setdefault("notification", {})

        common["use_cookies"] = self.use_cookies_check.isChecked()
        common["cookies_path"] = self.cookies_path_edit.text().strip()
        common["cache_path"] = self.cache_path_edit.text().strip()
        common["username"] = self.username_edit.text().strip()
        common["password"] = self.password_edit.text().strip()
        common["course_list"] = list(self._selected_course_ids)
        common["speed"] = round(float(self.speed_spin.value()), 2)
        common["jobs"] = int(self.jobs_spin.value())
        common["notopen_action"] = get_notopen_action(self.notopen_combo)

        selected_providers = self.provider_chip_panel.selected_values()
        provider_value = self.provider_combo.currentText().strip() or "TikuYanxi"
        if len(selected_providers) > 1:
            tiku["provider"] = "MultiTiku"
            tiku["providers"] = selected_providers
        elif len(selected_providers) == 1:
            tiku["provider"] = selected_providers[0]
            tiku["providers"] = selected_providers
        else:
            tiku["provider"] = provider_value
            tiku["providers"] = []

        tiku["decision_provider"] = self.decision_provider_combo.currentText().strip() or "SiliconFlow"
        tiku["check_llm_connection"] = self.check_connection_check.isChecked()
        tiku["submit"] = self.submit_check.isChecked()
        tiku["cover_rate"] = round(float(self.cover_rate_spin.value()), 2)
        tiku["delay"] = round(float(self.delay_spin.value()), 2)
        tiku["tokens"] = self.tokens_edit.text().strip()
        tiku["likeapi_search"] = self.like_search_check.isChecked()
        tiku["likeapi_vision"] = self.like_vision_check.isChecked()
        tiku["likeapi_model"] = self.like_model_edit.text().strip()
        tiku["likeapi_retry"] = self.like_retry_check.isChecked()
        tiku["likeapi_retry_times"] = int(self.like_retry_times_spin.value())
        tiku["url"] = self.adapter_url_edit.text().strip()
        tiku["endpoint"] = self.ai_endpoint_edit.text().strip()
        tiku["key"] = self.ai_key_edit.text().strip()
        tiku["model"] = self.ai_model_edit.text().strip()
        tiku["min_interval_seconds"] = int(self.min_interval_spin.value())
        tiku["http_proxy"] = self.http_proxy_edit.text().strip()
        tiku["siliconflow_key"] = self.silicon_key_edit.text().strip()
        tiku["siliconflow_model"] = self.silicon_model_edit.text().strip()
        tiku["siliconflow_endpoint"] = self.silicon_endpoint_edit.text().strip()
        tiku["true_list"] = split_csv(self.true_list_edit.text())
        tiku["false_list"] = split_csv(self.false_list_edit.text())

        notification_provider = self.notification_provider_combo.currentText().strip()
        notification["provider"] = "" if notification_provider == "不启用" else notification_provider
        notification["url"] = self.notification_url_edit.text().strip()
        notification["tg_chat_id"] = self.notification_chat_id_edit.text().strip()
        return profile

    def save_profile(self) -> None:
        try:
            profile = self.collect_profile_data()
            save_json_profile(profile)
        except Exception as exc:
            show_error(self, "保存失败", str(exc))
            return

        self._profile_source = deepcopy(profile)
        self._dirty = False
        self.refresh_json_editor()
        self.refresh_run_state()
        self.profile_saved.emit(profile["name"])
        show_bar(self, "success", "保存成功", f"{profile['name']} 已写入 JSON 配置。")

    def toggle_json_editor(self) -> None:
        is_visible = self.json_editor_container.isVisible()
        self.json_editor_container.setVisible(not is_visible)
        self.toggle_json_button.setText("收起 JSON 编辑器" if not is_visible else "展开 JSON 编辑器")
        if not is_visible:
            self.refresh_json_editor()

    def refresh_json_editor(self) -> None:
        if not self._current_profile_name:
            self.json_editor.clear()
            return
        try:
            profile = self.collect_profile_data()
        except Exception:
            profile = deepcopy(self._profile_source)
        self.json_editor.setPlainText(json.dumps(profile, ensure_ascii=False, indent=2) + "\n")

    def apply_json_to_form(self) -> None:
        if not self._current_profile_name:
            return
        try:
            data = json.loads(self.json_editor.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            show_error(self, "JSON 解析失败", f"第 {exc.lineno} 行第 {exc.colno} 列附近有语法错误。")
            return
        if not isinstance(data, dict):
            show_error(self, "JSON 结构不正确", "顶层必须是一个对象。")
            return

        merged = deepcopy(DEFAULT_PROFILE)
        for section_name, section_value in data.items():
            if isinstance(section_value, dict) and isinstance(merged.get(section_name), dict):
                merged[section_name].update(section_value)
            else:
                merged[section_name] = section_value
        merged["name"] = self._current_profile_name
        self._profile_source = merged
        self._dirty = True
        self._populate_profile(self._current_profile_name, merged)
        self._dirty = True
        self.refresh_run_state()
        show_bar(self, "success", "应用成功", "JSON 内容已同步到结构化表单。")

    def save_json_directly(self) -> None:
        if not self._current_profile_name:
            return
        try:
            data = json.loads(self.json_editor.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            show_error(self, "JSON 解析失败", f"第 {exc.lineno} 行第 {exc.colno} 列附近有语法错误。")
            return
        if not isinstance(data, dict):
            show_error(self, "JSON 结构不正确", "顶层必须是一个对象。")
            return

        data["name"] = self._current_profile_name
        try:
            save_json_profile(data)
        except Exception as exc:
            show_error(self, "保存失败", str(exc))
            return

        self._dirty = False
        self.load_profile(self._current_profile_name)
        self.profile_saved.emit(self._current_profile_name)
        show_bar(self, "success", "保存成功", f"{self._current_profile_name} 已按格式写回 JSON 文件。")

    def refresh_courses(self) -> None:
        if not self._current_profile_name:
            show_bar(self, "warning", "未选择配置", "请先从左侧选择一个配置。")
            return
        if self._course_fetch_thread and self._course_fetch_thread.isRunning():
            show_bar(self, "info", "课程列表获取中", "当前配置的课程列表仍在请求中。")
            return

        self.refresh_courses_button.setEnabled(False)
        self.course_status.setText(f"正在获取 {self._current_profile_name} 的课程列表...")
        self._course_fetch_thread = CourseFetchThread(self._current_profile_name, self)
        self._course_fetch_thread.loaded.connect(self._on_courses_loaded)
        self._course_fetch_thread.failed.connect(self._on_courses_failed)
        self._course_fetch_thread.finished.connect(self._on_courses_thread_finished)
        self._course_fetch_thread.start()

    def clear_courses(self) -> None:
        self._selected_course_ids = []
        if self._courses:
            self.course_chip_panel.clear_selection()
        self._update_course_summary()
        self._mark_dirty()

    def _on_courses_loaded(self, profile_name: str, courses: object) -> None:
        courses = list(courses or [])
        self._course_cache[profile_name] = courses
        if profile_name != self._current_profile_name:
            return
        self._courses = courses
        selected_ids = [course["courseId"] for course in courses if course.get("selected")]
        if selected_ids:
            self._selected_course_ids = selected_ids
        self._apply_course_cards(courses)
        self._update_course_summary()
        show_bar(self, "success", "课程列表已更新", f"{profile_name} 共获取到 {len(courses)} 门课程。")

    def _on_courses_failed(self, profile_name: str, message: str) -> None:
        if profile_name == self._current_profile_name:
            self.course_status.setText(f"刷新失败：{message}")
        show_error(self, "刷新课程列表失败", message)

    def _on_courses_thread_finished(self) -> None:
        self.refresh_courses_button.setEnabled(bool(self._current_profile_name))
        self._course_fetch_thread = None

    def _apply_course_cards(self, courses: list[dict]) -> None:
        items = []
        for course in courses:
            title = str(course.get("title", "")).strip() or str(course.get("courseId", "")).strip()
            teacher = str(course.get("teacher", "")).strip()
            subtitle = f" | {teacher}" if teacher else ""
            items.append((str(course.get("courseId", "")).strip(), f"{title}{subtitle}"))
        self.course_chip_panel.set_items(items, self._selected_course_ids)

    def _update_course_summary(self) -> None:
        if self._courses:
            self._selected_course_ids = self.course_chip_panel.selected_values()
            self.course_status.setText(f"已选择 {len(self._selected_course_ids)} / {len(self._courses)} 门课程。")
            return
        if self._selected_course_ids:
            preview = ", ".join(self._selected_course_ids[:5])
            suffix = " ..." if len(self._selected_course_ids) > 5 else ""
            self.course_status.setText(
                f"当前配置已保存 {len(self._selected_course_ids)} 个 courseId：{preview}{suffix}。获取课程列表后可在标签中调整。"
            )
        else:
            self.course_status.setText("尚未选择课程。")

    def _on_course_selection_changed(self) -> None:
        if self._loading:
            return
        self._selected_course_ids = self.course_chip_panel.selected_values()
        self._update_course_summary()
        self._mark_dirty()

    def _on_provider_combo_changed(self, _value: str) -> None:
        if self._loading:
            return
        provider = self.provider_combo.currentText().strip()
        if provider and provider != "MultiTiku":
            self.provider_chip_panel.set_selected([provider] if provider in COLLAB_PROVIDER_OPTIONS else [])
        self._update_provider_summary()
        self._mark_dirty()

    def _on_provider_chips_changed(self) -> None:
        if self._loading:
            return
        selected = self.provider_chip_panel.selected_values()
        if len(selected) > 1:
            set_combo_text(self.provider_combo, "MultiTiku")
        elif len(selected) == 1:
            set_combo_text(self.provider_combo, selected[0])
        self._update_provider_summary()
        self._mark_dirty()

    def _update_provider_summary(self) -> None:
        selected = self.provider_chip_panel.selected_values()
        decision_provider = self.decision_provider_combo.currentText().strip() or "SiliconFlow"
        if len(selected) > 1:
            self.provider_summary.setText(
                f"当前将以 MultiTiku 模式运行：{' + '.join(selected)}。答案冲突时由 {decision_provider} 进行复核。"
            )
        elif len(selected) == 1:
            self.provider_summary.setText(f"当前题库：{selected[0]}。")
        else:
            self.provider_summary.setText(f"当前题库：{self.provider_combo.currentText().strip() or 'TikuYanxi'}。")

    def _mark_dirty(self, *_args) -> None:
        if self._loading or not self._current_profile_name:
            return
        self._dirty = True
        self.refresh_run_state()

    def _emit_start(self) -> None:
        if self._current_profile_name:
            self.start_requested.emit(self._current_profile_name)

    def _emit_stop(self) -> None:
        if self._current_profile_name:
            self.stop_requested.emit(self._current_profile_name)

    def _emit_delete(self) -> None:
        if self._current_profile_name:
            self.delete_requested.emit(self._current_profile_name)


class ProfilesPage(PageFrame):
    def __init__(self, run_manager: RunManager, on_profiles_changed=None, parent=None) -> None:
        super().__init__(
            "配置管理",
            "左侧用于配置选择与批量操作，右侧用于编辑 JSON 配置。",
            parent,
        )
        self.run_manager = run_manager
        self.run_manager.runs_changed.connect(self.refresh_run_context)
        self.on_profiles_changed = on_profiles_changed
        self._refreshing_list = False
        self._suspend_selection_load = False
        self.checked_profiles: set[str] = set()
        self.profile_items: dict[str, QListWidgetItem] = {}
        self.profile_cards: dict[str, ProfileListCard] = {}

        splitter = QSplitter(Qt.Horizontal, self)
        self.root_layout.addWidget(splitter, 1)

        left_panel = QWidget(splitter)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.create_button = PrimaryPushButton("新建配置", left_panel)
        self.refresh_button = PushButton("重新载入列表", left_panel)
        action_row.addWidget(self.create_button)
        action_row.addWidget(self.refresh_button)
        left_layout.addLayout(action_row)

        self.search_edit = SearchLineEdit(left_panel)
        self.search_edit.setPlaceholderText("搜索配置")
        left_layout.addWidget(self.search_edit)

        select_row = QHBoxLayout()
        select_row.setSpacing(8)
        self.select_all_button = PushButton("全选", left_panel)
        self.invert_button = PushButton("反选", left_panel)
        self.clear_select_button = PushButton("清空选择", left_panel)
        select_row.addWidget(self.select_all_button)
        select_row.addWidget(self.invert_button)
        select_row.addWidget(self.clear_select_button)
        left_layout.addLayout(select_row)

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        self.batch_start_button = PrimaryPushButton("启动选中项", left_panel)
        self.batch_stop_button = PushButton("停止选中项", left_panel)
        self.batch_delete_button = PushButton("删除选中项", left_panel)
        run_row.addWidget(self.batch_start_button)
        run_row.addWidget(self.batch_stop_button)
        run_row.addWidget(self.batch_delete_button)
        left_layout.addLayout(run_row)

        self.selection_status = CaptionLabel("已选中 0 个配置。", left_panel)
        self.selection_status.setWordWrap(True)
        left_layout.addWidget(self.selection_status)

        self.profile_list = ListWidget(left_panel)
        self.profile_list.setFrameShape(QFrame.NoFrame)
        self.profile_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.profile_list.setSpacing(8)
        left_layout.addWidget(self.profile_list, 1)
        splitter.addWidget(left_panel)

        self.editor = ProfileEditorPanel(run_manager, splitter)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 1040])

        self.create_button.clicked.connect(self.create_profile)
        self.refresh_button.clicked.connect(self.refresh_profiles)
        self.search_edit.textChanged.connect(self.refresh_profiles)
        self.select_all_button.clicked.connect(self.select_all)
        self.invert_button.clicked.connect(self.invert_selection)
        self.clear_select_button.clicked.connect(self.clear_selection)
        self.batch_start_button.clicked.connect(self.start_checked_profiles)
        self.batch_stop_button.clicked.connect(self.stop_checked_profiles)
        self.batch_delete_button.clicked.connect(self.delete_checked_profiles)
        self.profile_list.currentItemChanged.connect(self._on_current_item_changed)
        self.editor.profile_saved.connect(self._on_profile_saved)
        self.editor.start_requested.connect(self.start_profile)
        self.editor.stop_requested.connect(self.stop_profile)
        self.editor.delete_requested.connect(self.delete_profile)

        self.refresh_profiles()

    def _item_name(self, item: QListWidgetItem | None) -> str:
        if not item:
            return ""
        return str(item.data(Qt.UserRole) or "")

    def refresh_profiles(self, select_name: str | None = None, preserve_editor: bool = False) -> None:
        names = [path.stem for path in list_json_profiles()]
        query = self.search_edit.text().strip().lower()
        current_name = select_name or self.editor.current_profile_name or self._item_name(self.profile_list.currentItem())
        visible_names = [name for name in names if query in name.lower()]
        preserve_current_editor = preserve_editor or self.editor.is_dirty

        self._refreshing_list = True
        self.profile_list.clear()
        self.profile_items.clear()
        self.profile_cards.clear()
        for name in visible_names:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, name)
            item.setSizeHint(QSize(0, 86))
            self.profile_list.addItem(item)
            self.profile_items[name] = item

            card = ProfileListCard(
                name,
                self._status_text(name),
                self._summary_text(name),
                checked=name in self.checked_profiles,
                parent=self.profile_list,
            )
            card.checked_changed.connect(self._on_profile_card_checked)
            card.activated.connect(self._on_profile_card_activated)
            self.profile_list.setItemWidget(item, card)
            self.profile_cards[name] = card
        self._refreshing_list = False

        if visible_names:
            target_name = current_name if current_name in visible_names else visible_names[0]
            self._suspend_selection_load = preserve_current_editor and target_name == self.editor.current_profile_name
            self._set_current_profile(target_name)
            self._suspend_selection_load = False
        elif not names:
            self.editor.clear_profile()

        self._sync_current_card_styles()
        self._update_selection_status()

    def _status_text(self, profile_name: str) -> str:
        run = self.run_manager.get_run(profile_name)
        return display_status(run.status) if run else display_status("idle")

    def _summary_text(self, profile_name: str) -> str:
        summary = profile_summary(load_json_profile(profile_name))
        providers = summary.get("providers", []) or []
        provider_text = " + ".join(providers) if len(providers) > 1 else summary.get("provider", "未配置")
        return f"题库：{provider_text} | 课程：{summary.get('course_count', 0)}"

    def _set_current_profile(self, profile_name: str) -> None:
        item = self.profile_items.get(profile_name)
        if item:
            self.profile_list.setCurrentItem(item)

    def _on_current_item_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._sync_current_card_styles()
        if self._suspend_selection_load:
            return
        profile_name = self._item_name(current)
        if profile_name:
            self.editor.load_profile(profile_name)
        elif self.profile_list.count() == 0:
            self.editor.clear_profile()

    def _on_profile_card_checked(self, profile_name: str, checked: bool) -> None:
        if self._refreshing_list:
            return
        if checked:
            self.checked_profiles.add(profile_name)
        else:
            self.checked_profiles.discard(profile_name)
        self._update_selection_status()

    def _on_profile_card_activated(self, profile_name: str) -> None:
        self._set_current_profile(profile_name)

    def _sync_current_card_styles(self) -> None:
        current_name = self._item_name(self.profile_list.currentItem())
        for name, card in self.profile_cards.items():
            card.set_active(name == current_name)

    def _update_selection_status(self) -> None:
        total = len([path.stem for path in list_json_profiles()])
        checked = len(self.checked_profiles)
        self.selection_status.setText(f"已选中 {checked} / {total} 个配置。")

    def select_all(self) -> None:
        for path in list_json_profiles():
            self.checked_profiles.add(path.stem)
        self.refresh_profiles()

    def invert_selection(self) -> None:
        all_names = {path.stem for path in list_json_profiles()}
        self.checked_profiles = all_names - self.checked_profiles
        self.refresh_profiles()

    def clear_selection(self) -> None:
        self.checked_profiles.clear()
        self.refresh_profiles()

    def _checked_names(self) -> list[str]:
        return [path.stem for path in list_json_profiles() if path.stem in self.checked_profiles]

    def create_profile(self) -> None:
        dialog = TextInputDialog(
            "新建配置",
            "请输入配置名称。系统将为该配置创建独立的 JSON 文件和运行状态目录。",
            "例如：账号A-课程组",
            confirm_text="创建",
            parent=self,
        )
        if exec_dialog(dialog) != 1:
            return
        name = dialog.value()
        try:
            profile = create_json_profile(name)
        except Exception as exc:
            show_error(self, "创建失败", str(exc))
            return
        self.refresh_profiles(select_name=profile["name"])
        self._notify_profiles_changed()
        show_bar(self, "success", "创建成功", f"{profile['name']} 已创建。")

    def _confirm_delete(self, names: list[str]) -> bool:
        if not names:
            return False

        running_names = [name for name in names if (self.run_manager.get_run(name) and self.run_manager.get_run(name).status == "running")]
        detail_lines = [
            f"将删除 {len(names)} 个配置的 JSON 文件和对应运行时文件。",
            "此操作不可撤销。",
        ]
        if running_names:
            detail_lines.append(f"其中 {len(running_names)} 个配置正在运行，删除前会先停止任务。")
        if self.editor.is_dirty and self.editor.current_profile_name in names:
            detail_lines.append("当前编辑页存在未保存修改，删除后这些修改也会一并丢弃。")
        return confirm_action(
            self,
            "确认删除配置",
            "\n".join(detail_lines),
            confirm_text="删除",
            cancel_text="取消",
        )

    def _delete_profiles(self, names: list[str], select_next: str | None = None) -> tuple[int, list[str]]:
        unique_names = [name for name in dict.fromkeys(names) if name]
        deleted = 0
        failed_messages: list[str] = []

        for name in unique_names:
            try:
                self.run_manager.remove_profile_state(name, stop_running=True)
                delete_json_profile(name, remove_runtime_state=True)
                self.checked_profiles.discard(name)
                deleted += 1
            except Exception as exc:
                failed_messages.append(f"{name}: {exc}")

        target_name = select_next
        if target_name in unique_names:
            target_name = None
        self.refresh_profiles(select_name=target_name)
        self._notify_profiles_changed()
        return deleted, failed_messages

    def start_checked_profiles(self) -> None:
        names = self._checked_names()
        if not names:
            show_bar(self, "warning", "未选择配置", "请先在左侧列表中选择要启动的配置。")
            return

        started = 0
        skipped = 0
        failed_messages = []
        for name in names:
            try:
                self.run_manager.start_profile(name)
                started += 1
            except ValueError:
                skipped += 1
            except Exception as exc:
                failed_messages.append(f"{name}: {exc}")
        self.refresh_run_context()
        message = f"已启动 {started} 个，跳过 {skipped} 个。"
        if failed_messages:
            message += " 失败：" + "；".join(failed_messages[:3])
        show_bar(self, "success" if not failed_messages else "warning", "批量启动完成", message, duration=5000)

    def stop_checked_profiles(self) -> None:
        names = self._checked_names()
        if not names:
            show_bar(self, "warning", "未选择配置", "请先在左侧列表中选择要停止的配置。")
            return

        stopped = 0
        skipped = 0
        for name in names:
            try:
                self.run_manager.stop_profile(name)
                stopped += 1
            except ValueError:
                skipped += 1
        self.refresh_run_context()
        show_bar(self, "success", "批量停止完成", f"已停止 {stopped} 个，跳过 {skipped} 个。")

    def delete_checked_profiles(self) -> None:
        names = self._checked_names()
        if not names:
            show_bar(self, "warning", "未选择配置", "请先在左侧列表中选择要删除的配置。")
            return
        if not self._confirm_delete(names):
            return

        current_name = self.editor.current_profile_name
        deleted, failed_messages = self._delete_profiles(names, select_next=current_name)
        if failed_messages:
            show_bar(
                self,
                "warning",
                "批量删除已完成",
                f"已删除 {deleted} 个。失败：{'；'.join(failed_messages[:3])}",
                duration=5000,
            )
            return
        show_bar(self, "success", "批量删除已完成", f"已删除 {deleted} 个配置。")

    def start_profile(self, profile_name: str) -> None:
        try:
            self.run_manager.start_profile(profile_name)
        except Exception as exc:
            show_error(self, "启动失败", str(exc))
            return
        self.refresh_run_context()
        show_bar(self, "success", "启动成功", f"{profile_name} 已启动。")

    def stop_profile(self, profile_name: str) -> None:
        try:
            self.run_manager.stop_profile(profile_name)
        except Exception as exc:
            show_error(self, "停止失败", str(exc))
            return
        self.refresh_run_context()
        show_bar(self, "success", "停止成功", f"{profile_name} 已停止。")

    def delete_profile(self, profile_name: str) -> None:
        if not profile_name:
            return
        if not self._confirm_delete([profile_name]):
            return

        deleted, failed_messages = self._delete_profiles([profile_name])
        if failed_messages:
            show_error(self, "删除失败", failed_messages[0])
            return
        show_bar(self, "success", "删除成功", f"{profile_name} 已删除。")

    def refresh_run_context(self) -> None:
        self.refresh_profiles(
            select_name=self.editor.current_profile_name,
            preserve_editor=self.editor.is_dirty,
        )
        self.editor.refresh_run_state()

    def _on_profile_saved(self, profile_name: str) -> None:
        self.refresh_profiles(select_name=profile_name)
        self._notify_profiles_changed()

    def _notify_profiles_changed(self) -> None:
        if self.on_profiles_changed:
            self.on_profiles_changed()


class GlobalSettingsPage(PageFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(
            "全局设置",
            "用于维护题库与通知服务的全局默认值。配置内留空时将自动继承此处设置。",
            parent,
        )
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        self.reload_button = PushButton("重新载入", self)
        self.save_button = PrimaryPushButton("保存全局设置", self)
        header_row.addWidget(self.reload_button)
        header_row.addWidget(self.save_button)
        header_row.addStretch(1)
        self.root_layout.addLayout(header_row)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.root_layout.addWidget(self.scroll, 1)

        content = QWidget(self.scroll)
        self.scroll_layout = QVBoxLayout(content)
        self.scroll_layout.setContentsMargins(0, 0, 4, 12)
        self.scroll_layout.setSpacing(16)
        self.scroll.setWidget(content)

        self._build_tiku_defaults_card()
        self._build_notification_defaults_card()
        self.scroll_layout.addStretch(1)

        self.reload_button.clicked.connect(self.load_settings)
        self.save_button.clicked.connect(self.save_settings)
        self.load_settings()

    def _build_tiku_defaults_card(self) -> None:
        self.tiku_card = SectionCard("题库默认值", "用于维护 Enncy、SiliconFlow、通用 AI 与 Adapter 的全局凭据。", self.scroll.widget())
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        self.tokens_edit = LineEdit(self.tiku_card)
        self.tokens_edit.setPlaceholderText("Enncy / LIKE 令牌，多个逗号分隔")
        self.ai_endpoint_edit = LineEdit(self.tiku_card)
        self.ai_endpoint_edit.setPlaceholderText("OpenAI 兼容接口地址")
        self.ai_key_edit = LineEdit(self.tiku_card)
        self.ai_key_edit.setPlaceholderText("默认密钥")
        self.ai_model_edit = LineEdit(self.tiku_card)
        self.ai_model_edit.setPlaceholderText("默认模型")
        self.http_proxy_edit = LineEdit(self.tiku_card)
        self.http_proxy_edit.setPlaceholderText("默认代理")
        self.min_interval_spin = SpinBox(self.tiku_card)
        self.min_interval_spin.setRange(0, 120)
        self.silicon_key_edit = LineEdit(self.tiku_card)
        self.silicon_key_edit.setPlaceholderText("SiliconFlow 密钥")
        self.silicon_model_edit = LineEdit(self.tiku_card)
        self.silicon_model_edit.setPlaceholderText("SiliconFlow 模型")
        self.silicon_endpoint_edit = LineEdit(self.tiku_card)
        self.silicon_endpoint_edit.setPlaceholderText("SiliconFlow 接口地址")
        self.like_model_edit = LineEdit(self.tiku_card)
        self.like_model_edit.setPlaceholderText("LIKE 模型")
        self.like_retry_times_spin = SpinBox(self.tiku_card)
        self.like_retry_times_spin.setRange(0, 10)
        self.like_search_check = CheckBox("LIKE 启用联网搜索", self.tiku_card)
        self.like_vision_check = CheckBox("LIKE 启用视觉识图", self.tiku_card)
        self.like_retry_check = CheckBox("LIKE 失败自动重试", self.tiku_card)
        self.adapter_url_edit = LineEdit(self.tiku_card)
        self.adapter_url_edit.setPlaceholderText("TikuAdapter 地址")

        grid.addWidget(make_field("令牌列表", self.tokens_edit), 0, 0, 1, 2)
        grid.addWidget(make_field("AI 接口地址", self.ai_endpoint_edit), 1, 0)
        grid.addWidget(make_field("AI 密钥", self.ai_key_edit), 1, 1)
        grid.addWidget(make_field("AI 模型", self.ai_model_edit), 2, 0)
        grid.addWidget(make_field("HTTP 代理", self.http_proxy_edit), 2, 1)
        grid.addWidget(make_field("最小请求间隔", self.min_interval_spin), 3, 0)
        grid.addWidget(make_field("硅基密钥", self.silicon_key_edit), 4, 0)
        grid.addWidget(make_field("硅基模型", self.silicon_model_edit), 4, 1)
        grid.addWidget(make_field("硅基接口地址", self.silicon_endpoint_edit), 5, 0, 1, 2)
        grid.addWidget(make_field("LIKE 模型", self.like_model_edit), 6, 0)
        grid.addWidget(make_field("LIKE 重试次数", self.like_retry_times_spin), 6, 1)
        grid.addWidget(self.like_search_check, 7, 0)
        grid.addWidget(self.like_vision_check, 7, 1)
        grid.addWidget(self.like_retry_check, 8, 0)
        grid.addWidget(make_field("TikuAdapter 地址", self.adapter_url_edit), 9, 0, 1, 2)
        self.tiku_card.body_layout.addLayout(grid)
        self.scroll_layout.addWidget(self.tiku_card)

    def _build_notification_defaults_card(self) -> None:
        self.notification_card = SectionCard("通知默认值", "仅在配置未单独填写通知参数时使用。", self.scroll.widget())
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.notification_provider_combo = ComboBox(self.notification_card)
        self.notification_provider_combo.addItems(NOTIFICATION_PROVIDER_OPTIONS)
        self.notification_url_edit = LineEdit(self.notification_card)
        self.notification_url_edit.setPlaceholderText("默认通知地址")
        self.notification_chat_id_edit = LineEdit(self.notification_card)
        self.notification_chat_id_edit.setPlaceholderText("默认 Telegram 会话 ID")
        grid.addWidget(make_field("通知提供方", self.notification_provider_combo), 0, 0)
        grid.addWidget(make_field("通知地址", self.notification_url_edit), 1, 0, 1, 2)
        grid.addWidget(make_field("Telegram 会话 ID", self.notification_chat_id_edit), 2, 0)
        self.notification_card.body_layout.addLayout(grid)
        self.scroll_layout.addWidget(self.notification_card)

    def load_settings(self) -> None:
        settings = load_global_settings()
        defaults = settings.get("defaults", {})
        tiku = defaults.get("tiku", {})
        notification = defaults.get("notification", {})

        self.tokens_edit.setText(str(tiku.get("tokens", "")))
        self.ai_endpoint_edit.setText(str(tiku.get("endpoint", "")))
        self.ai_key_edit.setText(str(tiku.get("key", "")))
        self.ai_model_edit.setText(str(tiku.get("model", "")))
        self.http_proxy_edit.setText(str(tiku.get("http_proxy", "")))
        self.min_interval_spin.setValue(int(tiku.get("min_interval_seconds", 3) or 3))
        self.silicon_key_edit.setText(str(tiku.get("siliconflow_key", "")))
        self.silicon_model_edit.setText(str(tiku.get("siliconflow_model", "")))
        self.silicon_endpoint_edit.setText(str(tiku.get("siliconflow_endpoint", "")))
        self.like_model_edit.setText(str(tiku.get("likeapi_model", "")))
        self.like_retry_times_spin.setValue(int(tiku.get("likeapi_retry_times", 3) or 3))
        self.like_search_check.setChecked(str(tiku.get("likeapi_search", "false")).lower() == "true")
        self.like_vision_check.setChecked(str(tiku.get("likeapi_vision", "true")).lower() == "true")
        self.like_retry_check.setChecked(str(tiku.get("likeapi_retry", "true")).lower() == "true")
        self.adapter_url_edit.setText(str(tiku.get("url", "")))

        provider_name = str(notification.get("provider", "") or "")
        set_combo_text(self.notification_provider_combo, provider_name if provider_name else "不启用")
        self.notification_url_edit.setText(str(notification.get("url", "")))
        self.notification_chat_id_edit.setText(str(notification.get("tg_chat_id", "")))

    def save_settings(self) -> None:
        settings = deepcopy(DEFAULT_GLOBAL_SETTINGS)
        settings["defaults"]["tiku"].update(
            {
                "tokens": self.tokens_edit.text().strip(),
                "endpoint": self.ai_endpoint_edit.text().strip(),
                "key": self.ai_key_edit.text().strip(),
                "model": self.ai_model_edit.text().strip(),
                "http_proxy": self.http_proxy_edit.text().strip(),
                "min_interval_seconds": str(int(self.min_interval_spin.value())),
                "siliconflow_key": self.silicon_key_edit.text().strip(),
                "siliconflow_model": self.silicon_model_edit.text().strip(),
                "siliconflow_endpoint": self.silicon_endpoint_edit.text().strip(),
                "url": self.adapter_url_edit.text().strip(),
                "likeapi_search": "true" if self.like_search_check.isChecked() else "false",
                "likeapi_vision": "true" if self.like_vision_check.isChecked() else "false",
                "likeapi_model": self.like_model_edit.text().strip(),
                "likeapi_retry": "true" if self.like_retry_check.isChecked() else "false",
                "likeapi_retry_times": str(int(self.like_retry_times_spin.value())),
            }
        )
        provider_name = self.notification_provider_combo.currentText().strip()
        settings["defaults"]["notification"].update(
            {
                "provider": "" if provider_name == "不启用" else provider_name,
                "url": self.notification_url_edit.text().strip(),
                "tg_chat_id": self.notification_chat_id_edit.text().strip(),
            }
        )
        save_global_settings(settings)
        show_bar(self, "success", "保存成功", "未填写的配置字段将自动继承当前默认值。")


class LogCard(CardWidget):
    start_requested = pyqtSignal(str)
    stop_requested = pyqtSignal(str)

    def __init__(self, profile_name: str, run_manager: RunManager, parent=None) -> None:
        super().__init__(parent)
        self.profile_name = profile_name
        self.run_manager = run_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        self.title_label = StrongBodyLabel(profile_name, self)
        self.status_label = CaptionLabel("未启动", self)
        header_row.addWidget(self.title_label, 1)
        header_row.addWidget(self.status_label)
        layout.addLayout(header_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.start_button = PrimaryPushButton("启动", self)
        self.stop_button = PushButton("停止", self)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.meta_label = CaptionLabel("尚未启动任务。", self)
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        self.log_view = PlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(240)
        self.log_view.setPlaceholderText("启动任务后将显示实时日志。")
        layout.addWidget(self.log_view, 1)

        self.start_button.clicked.connect(lambda: self.start_requested.emit(self.profile_name))
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit(self.profile_name))

    def refresh_card(self) -> None:
        profile = load_json_profile(self.profile_name)
        summary = profile_summary(profile)
        run = self.run_manager.get_run(self.profile_name)
        if run:
            status = display_status(run.status)
            runtime_info = f"配置文件：{run.profile_path}"
            if run.status == "running":
                runtime_info += " | 日志实时更新"
        else:
            status = display_status("idle")
            runtime_info = "尚未启动"

        providers = summary.get("providers", []) or []
        provider_text = " + ".join(providers) if len(providers) > 1 else summary.get("provider", "未配置")
        self.status_label.setText(status)
        self.meta_label.setText(
            f"题库：{provider_text}\n课程数量：{summary.get('course_count', 0)}\n{runtime_info}"
        )
        self.start_button.setEnabled(status != "running")
        self.stop_button.setEnabled(status == "running")
        logs = self.run_manager.logs_for_profile(self.profile_name)
        if logs.strip():
            self.log_view.setPlainText(logs)
            scrollbar = self.log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        else:
            self.log_view.clear()

    def append_log(self, line: str) -> None:
        if not self.log_view.toPlainText():
            self.log_view.setPlainText(line)
        else:
            self.log_view.appendPlainText(line)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class DesktopMainWindow(MSFluentWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1480, 980)

        ensure_desktop_state()
        self.run_manager = RunManager(self)

        self.home_page = HomePage(self.run_manager, self)
        self.profiles_page = ProfilesPage(self.run_manager, self.refresh_profile_dependent_pages, self)
        self.global_settings_page = GlobalSettingsPage(self)

        self.addSubInterface(self.home_page, FluentIcon.HOME, "概览")
        self.addSubInterface(self.profiles_page, FluentIcon.PEOPLE, "配置管理")
        self.addSubInterface(
            self.global_settings_page,
            FluentIcon.SETTING,
            "全局设置",
            position=NavigationItemPosition.BOTTOM,
        )

        self.refresh_profile_dependent_pages()

    def refresh_profile_dependent_pages(self) -> None:
        self.home_page.refresh_dashboard()


def run_desktop_app() -> int:
    ensure_desktop_state()
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName(APP_TITLE)
    window = DesktopMainWindow()
    window.show()
    return application.exec_()
