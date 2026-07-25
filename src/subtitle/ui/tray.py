"""系统托盘图标 + 右键菜单。

功能：
- 左键单击托盘：切换窗口显示/隐藏
- 右键菜单：显示/隐藏、开始/停止、主题切换、置顶切换、退出
- 窗口关闭时收到托盘（不退出程序），从托盘菜单「退出」才真正退出
"""
from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPolygonF
from PyQt5.QtWidgets import (
    QSystemTrayIcon, QMenu, QAction,
)


def _make_icon(color: str = "#3a6ea5") -> QIcon:
    """程序内生成一个简单的字幕图标（圆角矩形 + 横线），不依赖外部图片。"""
    from PyQt5.QtCore import QPointF, Qt
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    # 圆角背景
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 12, 56, 40, 8, 8)
    # 三条字幕线
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(12, 22, 28, 5, 2, 2)
    p.drawRoundedRect(12, 32, 40, 5, 2, 2)
    p.drawRoundedRect(12, 42, 22, 5, 2, 2)
    p.end()
    return QIcon(pix)


class TrayController(QObject):
    """托盘控制器。通过信号通知主应用执行操作。"""

    # 信号：托盘请求主应用执行的动作
    toggle_visibility_requested = pyqtSignal()   # 显示/隐藏窗口
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    toggle_theme_requested = pyqtSignal()
    toggle_pin_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tray = QSystemTrayIcon(_make_icon(), parent)
        self.tray.setToolTip("实时字幕 · sub-title")
        self.tray.activated.connect(self._on_activated)
        self._menu: QMenu | None = None
        self._build_menu()

    def _build_menu(self):
        self._menu = QMenu()
        self._menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a2a;
                color: #f0f0f0;
                border: 1px solid #444;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #3a6ea5;
            }
            QMenu::separator {
                height: 1px;
                background: #444;
                margin: 4px 8px;
            }
        """)

        self.act_show = QAction("显示/隐藏窗口", self._menu)
        self.act_show.triggered.connect(self.toggle_visibility_requested.emit)
        self._menu.addAction(self.act_show)

        self._menu.addSeparator()

        self.act_start = QAction("▶ 开始识别", self._menu)
        self.act_start.triggered.connect(self.start_requested.emit)
        self._menu.addAction(self.act_start)

        self.act_stop = QAction("■ 停止识别", self._menu)
        self.act_stop.triggered.connect(self.stop_requested.emit)
        self._menu.addAction(self.act_stop)

        self._menu.addSeparator()

        self.act_theme = QAction("🌙 切换主题", self._menu)
        self.act_theme.triggered.connect(self.toggle_theme_requested.emit)
        self._menu.addAction(self.act_theme)

        self.act_pin = QAction("📌 切换置顶", self._menu)
        self.act_pin.triggered.connect(self.toggle_pin_requested.emit)
        self._menu.addAction(self.act_pin)

        self.act_settings = QAction("⚙ 设置…", self._menu)
        self.act_settings.triggered.connect(self.settings_requested.emit)
        self._menu.addAction(self.act_settings)

        self._menu.addSeparator()

        self.act_quit = QAction("✕ 退出", self._menu)
        self.act_quit.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(self.act_quit)

        self.tray.setContextMenu(self._menu)

    def _on_activated(self, reason):
        # 左键单击：切换窗口显示/隐藏
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_visibility_requested.emit()

    def show(self):
        self.tray.show()

    def notify(self, title: str, message: str):
        """弹出托盘气泡通知。"""
        self.tray.showMessage(title, message, QSystemTrayIcon.Information, 2000)

    def set_running(self, running: bool):
        """根据运行状态启用/禁用菜单项。"""
        self.act_start.setEnabled(not running)
        self.act_stop.setEnabled(running)
