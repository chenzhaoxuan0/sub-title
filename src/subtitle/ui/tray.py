"""系统托盘图标 + 右键菜单 v2 —— 精美图标、子菜单、跨平台适配。

功能：
- 左键单击托盘：切换窗口显示/隐藏
- 右键菜单：完整功能菜单（含主题子菜单、皮肤子菜单）
- 跨平台图标适配（Win/Mac 不同风格）
- 主题感知（菜单样式跟随当前主题）
"""
from __future__ import annotations

import platform
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QPen, QBrush, QAction, QActionGroup
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from .theme_engine import get_theme_manager, ThemeManager

IS_MAC = platform.system() == "Darwin"


def _make_icon(color: str = "#3a6ea5", style: str = "modern") -> QIcon:
    """生成精美的字幕图标。

    style:
      - "modern": 圆角矩形 + 字幕线条（默认）
      - "minimal": 极简线条风格（Mac 菜单栏适配）
    """
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    if style == "minimal" and IS_MAC:
        # Mac 菜单栏：纯线条图标，跟随系统深浅色
        pen = QPen(QColor("#333333" if IS_MAC else color), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # 字幕框轮廓
        p.drawRoundedRect(8, 16, 48, 32, 6, 6)
        # 三条字幕线
        p.drawLine(16, 26, 40, 26)
        p.drawLine(16, 34, 48, 34)
        p.drawLine(16, 42, 34, 42)
    else:
        # 现代风格：渐变圆角背景 + 白色字幕线
        from PySide6.QtGui import QLinearGradient
        grad = QLinearGradient(4, 12, 60, 52)
        base = QColor(color)
        grad.setColorAt(0, base.lighter(120))
        grad.setColorAt(1, base.darker(110))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(4, 12, 56, 40, 10, 10)
        # 字幕线
        p.setBrush(QColor("#ffffff"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(12, 22, 28, 4, 2, 2)
        p.drawRoundedRect(12, 30, 40, 4, 2, 2)
        p.drawRoundedRect(12, 38, 22, 4, 2, 2)
        # 小圆点装饰（表示"实时"）
        p.setBrush(QColor("#4caf50"))
        p.drawEllipse(48, 14, 8, 8)

    p.end()
    return QIcon(pix)


def _make_menu_stylesheet(theme_mgr: ThemeManager) -> str:
    """根据当前主题生成菜单 QSS。"""
    colors = theme_mgr.current.colors
    return f"""
        QMenu {{
            background-color: {colors.tray_bg};
            color: {colors.tray_text};
            border: 1px solid {colors.subtitle_border};
            border-radius: 8px;
            padding: 6px;
            font-size: 13px;
        }}
        QMenu::item {{
            padding: 7px 28px 7px 12px;
            border-radius: 5px;
            margin: 1px 4px;
        }}
        QMenu::item:selected {{
            background-color: {colors.tray_hover};
        }}
        QMenu::item:disabled {{
            color: {colors.btn_disabled_text};
        }}
        QMenu::separator {{
            height: 1px;
            background: {colors.subtitle_border};
            margin: 4px 8px;
        }}
        QMenu::indicator {{
            width: 16px;
            height: 16px;
            margin-left: 6px;
        }}
    """


class TrayController(QObject):
    """托盘控制器 v2。"""

    # 信号
    toggle_visibility_requested = Signal()
    start_requested = Signal()
    stop_requested = Signal()
    toggle_theme_requested = Signal()
    toggle_pin_requested = Signal()
    settings_requested = Signal()
    skin_editor_requested = Signal()
    quit_requested = Signal()
    theme_switch_requested = Signal(str)  # 切换到指定主题

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme_mgr = get_theme_manager()

        icon_style = "minimal" if IS_MAC else "modern"
        accent = self._theme_mgr.current.colors.accent
        self.tray = QSystemTrayIcon(_make_icon(accent, icon_style), parent)
        self.tray.setToolTip("实时字幕 · sub-title")
        self.tray.activated.connect(self._on_activated)
        self._menu: QMenu | None = None
        self._build_menu()

    def _build_menu(self):
        self._menu = QMenu()
        self._menu.setStyleSheet(_make_menu_stylesheet(self._theme_mgr))

        # ---- 显示/隐藏 ----
        self.act_show = QAction("  显示/隐藏窗口", self._menu)
        self.act_show.triggered.connect(self.toggle_visibility_requested.emit)
        self._menu.addAction(self.act_show)

        self._menu.addSeparator()

        # ---- 识别控制 ----
        self.act_start = QAction("  ▶  开始识别", self._menu)
        self.act_start.triggered.connect(self.start_requested.emit)
        self._menu.addAction(self.act_start)

        self.act_stop = QAction("  ■  停止识别", self._menu)
        self.act_stop.triggered.connect(self.stop_requested.emit)
        self._menu.addAction(self.act_stop)

        self._menu.addSeparator()

        # ---- 主题子菜单 ----
        self.theme_menu = self._menu.addMenu("  🎨  主题")
        self.theme_menu.setStyleSheet(_make_menu_stylesheet(self._theme_mgr))
        self._theme_group = QActionGroup(self.theme_menu)
        self._theme_group.setExclusive(True)
        self._rebuild_theme_submenu()

        # ---- 皮肤子菜单 ----
        self.skin_menu = self._menu.addMenu("  🐱  桌宠皮肤")
        self.skin_menu.setStyleSheet(_make_menu_stylesheet(self._theme_mgr))
        self.act_skin_editor = QAction("  打开皮肤编辑器…", self.skin_menu)
        self.act_skin_editor.triggered.connect(self.skin_editor_requested.emit)
        self.skin_menu.addAction(self.act_skin_editor)
        self.skin_menu.addSeparator()
        self.act_skin_none = QAction("  无（纯字幕）", self.skin_menu)
        self.act_skin_none.setCheckable(True)
        self.act_skin_none.setChecked(True)
        self.skin_menu.addAction(self.act_skin_none)

        self._menu.addSeparator()

        # ---- 置顶 ----
        self.act_pin = QAction("  📌  窗口置顶", self._menu)
        self.act_pin.setCheckable(True)
        self.act_pin.setChecked(True)
        self.act_pin.triggered.connect(self.toggle_pin_requested.emit)
        self._menu.addAction(self.act_pin)

        # ---- 设置 ----
        self.act_settings = QAction("  ⚙  全局设置…", self._menu)
        self.act_settings.triggered.connect(self.settings_requested.emit)
        self._menu.addAction(self.act_settings)

        self._menu.addSeparator()

        # ---- 退出 ----
        self.act_quit = QAction("  ✕  退出", self._menu)
        self.act_quit.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(self.act_quit)

        self.tray.setContextMenu(self._menu)

    def _rebuild_theme_submenu(self):
        """重建主题子菜单（主题列表可能变化）。"""
        self.theme_menu.clear()
        self._theme_group = QActionGroup(self.theme_menu)
        self._theme_group.setExclusive(True)
        current_name = self._theme_mgr.current.name
        for name in self._theme_mgr.get_all_themes():
            act = QAction(f"  {name}", self.theme_menu)
            act.setCheckable(True)
            act.setChecked(name == current_name)
            act.setData(name)
            act.triggered.connect(lambda checked, n=name: self._on_theme_selected(n))
            self._theme_group.addAction(act)
            self.theme_menu.addAction(act)

    def _on_theme_selected(self, name: str):
        self.theme_switch_requested.emit(name)

    def refresh_theme(self):
        """主题切换后刷新菜单样式和图标。"""
        stylesheet = _make_menu_stylesheet(self._theme_mgr)
        self._menu.setStyleSheet(stylesheet)
        self.theme_menu.setStyleSheet(stylesheet)
        self.skin_menu.setStyleSheet(stylesheet)
        self._rebuild_theme_submenu()
        # 更新图标颜色
        accent = self._theme_mgr.current.colors.accent
        icon_style = "minimal" if IS_MAC else "modern"
        self.tray.setIcon(_make_icon(accent, icon_style))

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_visibility_requested.emit()

    def show(self):
        self.tray.show()

    def notify(self, title: str, message: str):
        self.tray.showMessage(title, message, QSystemTrayIcon.Information, 2000)

    def set_running(self, running: bool):
        self.act_start.setEnabled(not running)
        self.act_stop.setEnabled(running)

    def set_pin_state(self, pinned: bool):
        self.act_pin.setChecked(pinned)
