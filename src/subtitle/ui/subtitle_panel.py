"""沉浸式无边框字幕面板 v3。

架构（修复"底色全透明"根因）：
  SubtitlePanel（根窗口：透明、无边框、置顶）
    └─ container（真正画背景色的 QWidget，圆角）
         ├─ toolbar      （悬停显示）
         ├─ view         （字幕文本区）
         ├─ status_label （悬停显示）
         └─ grip         （悬停显示，右下角缩放手柄）

修复点：
- 底色真正画出来（内层 container 承载 rgba 背景色，根窗口透明）
- 工具栏延时隐藏 800ms，且鼠标进入容器内任何控件都能取消隐藏
- 缩放手柄与工具栏联动显示（平时完全隐藏，悬停出现）
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor, QMouseEvent
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QLabel, QComboBox, QSizeGrip, QSlider, QSpinBox, QFontComboBox,
    QApplication, QMessageBox, QCheckBox,
)

from ..config import UiConfig
from ..audio import list_loopback_devices


THEMES = {
    "dark": {"bg": "#1a1a1a", "text": "#f2f2f2"},
    "light": {"bg": "#f5f5f5", "text": "#1a1a1a"},
}


# 全局：记录当前是否有弹窗控件处于打开状态。由 _PopupCombo / _PopupFontCombo 维护。
_POPUP_ACTIVE = {"count": 0}


class _PopupCombo(QComboBox):
    """下拉框：弹出/收起时维护全局计数，避免工具栏误隐藏。"""
    def showPopup(self, *a, **kw):
        _POPUP_ACTIVE["count"] += 1
        super().showPopup(*a, **kw)

    def hidePopup(self, *a, **kw):
        super().hidePopup(*a, **kw)
        _POPUP_ACTIVE["count"] = max(0, _POPUP_ACTIVE["count"] - 1)


class _PopupFontCombo(QFontComboBox):
    """字体下拉框：同上。"""
    def showPopup(self, *a, **kw):
        _POPUP_ACTIVE["count"] += 1
        super().showPopup(*a, **kw)

    def hidePopup(self, *a, **kw):
        super().hidePopup(*a, **kw)
        _POPUP_ACTIVE["count"] = max(0, _POPUP_ACTIVE["count"] - 1)


class _SubtitleView(QTextEdit):
    """只读、透明背景的文本区。鼠标事件透传给父窗口，由父窗口统一处理拖动/滚轮。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameStyle(QTextEdit.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.viewport().setAttribute(Qt.WA_TransparentForMouseEvents, True)


class SubtitlePanel(QWidget):
    """沉浸式无边框字幕窗口（根窗口透明，内层 container 画底色）。"""

    _text_appended = pyqtSignal(str, bool)
    hide_requested = pyqtSignal()    # 点 ✕ 时发出：由 app 决定隐藏还是退出
    quit_requested = pyqtSignal()    # 真正退出时发出

    def __init__(self, ui_cfg: UiConfig, on_start=None, on_stop=None, on_quit=None,
                 on_geometry_changed=None):
        super().__init__()
        self.ui_cfg = ui_cfg
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_quit = on_quit
        self.on_geometry_changed = on_geometry_changed

        self._text_appended.connect(self._on_text_appended)
        self._drag_offset: QPoint | None = None
        self._current_theme = ui_cfg.theme or "dark"
        self._font_size = ui_cfg.font_size or 22

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(getattr(ui_cfg, "toolbar_hide_delay_ms", 800))
        self._hide_timer.timeout.connect(self._hide_overlays)

        self._init_window_flags()
        self._init_ui()
        self._apply_theme()
        self._restore_geometry()

    # ---------- 初始化 ----------
    def _init_window_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.ui_cfg.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        # 根窗口保持透明（无边框圆角要求），背景由内层 container 画
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def _init_ui(self):
        # 根窗口：透明，不画任何东西
        self.setObjectName("root")

        # 字幕区 container：固定高度 = 窗口内容高度，位置永不变。
        # 工具栏是根窗口的另一个子 widget，显隐时改变窗口总高 + container 位置，
        # 但 container 自身的几何（屏幕上的位置和大小）始终不变 → 字幕不跳。
        self.container = QWidget(self)
        self.container.setObjectName("container")
        self.container.setAttribute(Qt.WA_StyledBackground, True)
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        self.view = _SubtitleView(self.container)
        self.view.setFont(QFont(self.ui_cfg.font_family, self._font_size))
        self.view.setPlaceholderText("点击 ▶ 开始，播放任意视频/音频，字幕会实时出现……")

        # ---- 工具栏：根窗口的子，绝对定位在 container 上方 ----
        self.toolbar = QWidget(self)
        self.toolbar.setObjectName("toolbar")
        self.toolbar.setAttribute(Qt.WA_StyledBackground, True)
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(10, 6, 10, 4)
        tb.setSpacing(6)

        self.device_combo = _PopupCombo()
        self.device_combo.addItem("（默认输出）", None)
        try:
            for d in list_loopback_devices():
                label = d.name + (" [默认]" if d.is_default_output else "")
                self.device_combo.addItem(label, d.name)
        except Exception as e:
            print(f"[ui] 枚举 loopback 设备失败: {e}")

        self.start_btn = QPushButton("▶ 开始")
        self.stop_btn = QPushButton("■ 停止")
        self.clear_btn = QPushButton("清空")
        self.font_inc_btn = QPushButton("A+")
        self.font_dec_btn = QPushButton("A-")
        self.pin_btn = QPushButton("📌 已置顶" if self.ui_cfg.always_on_top else "📌 未置顶")
        self.theme_btn = QPushButton("🌙" if self._current_theme == "dark" else "☀️")
        self.close_btn = QPushButton("✕")

        # 字体选择框（系统字体列表）
        self.font_combo = _PopupFontCombo()
        self.font_combo.setCurrentFont(QFont(self.ui_cfg.font_family, self._font_size))
        self.font_combo.setToolTip("字幕字体")
        self.font_combo.setFixedWidth(140)

        # 透明度：滑块(0-100) + 可输入数字框，二者双向联动
        init_op = int(round(self.ui_cfg.window_opacity * 100))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(init_op)
        self.opacity_slider.setFixedWidth(80)
        self.opacity_slider.setToolTip("背景透明度")
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setValue(init_op)
        self.opacity_spin.setSuffix("%")
        self.opacity_spin.setFixedWidth(70)
        self.opacity_spin.setToolTip("背景透明度（可直接输入）")

        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)
        self.clear_btn.clicked.connect(self._on_clear)
        self.font_inc_btn.clicked.connect(lambda: self._change_font_size(+2))
        self.font_dec_btn.clicked.connect(lambda: self._change_font_size(-2))
        self.pin_btn.clicked.connect(self._toggle_pin)
        self.theme_btn.clicked.connect(self._toggle_theme)
        self.close_btn.clicked.connect(self._on_close)
        # 字体切换：用 activated（用户真正选中并确认时才触发，而非 hover 每一项），
        # 避免 currentFontChanged 在弹窗存活期间高频回调导致崩溃
        self.font_combo.activated.connect(self._on_font_activated)
        # 透明度：滑块和输入框双向联动
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.opacity_slider.sliderReleased.connect(self._notify_geometry)
        self.opacity_spin.valueChanged.connect(self._on_opacity_changed)
        # 避免互相触发时死循环：在 _on_opacity_changed 里临时阻塞信号

        self._device_label = QLabel("声音源：")
        tb.addWidget(self._device_label)
        tb.addWidget(self.device_combo)   # 下拉框保持自然宽度，不拉伸
        # 按钮设为水平拉伸，均匀填满整行，避免缩小后出现割裂空白
        self._add_expanding(tb, self.start_btn)
        self._add_expanding(tb, self.stop_btn)
        self._add_expanding(tb, self.clear_btn)
        self._font_label = QLabel("字体：")
        tb.addWidget(self._font_label)
        tb.addWidget(self.font_combo)
        self._add_expanding(tb, self.font_dec_btn)
        self._add_expanding(tb, self.font_inc_btn)
        self._add_expanding(tb, self.pin_btn)
        self._add_expanding(tb, self.theme_btn)
        # 透明度：标签 + 滑动条 装进一个紧贴的小容器，避免被工具栏 stretch 拉开
        # 整体作为一个 widget 加入工具栏，缩小时一起隐藏
        self.opacity_group = QWidget()
        self.opacity_group.setObjectName("opacity_group")
        og_layout = QHBoxLayout(self.opacity_group)
        og_layout.setContentsMargins(0, 0, 0, 0)
        og_layout.setSpacing(4)
        self._opacity_label = QLabel("透明度")
        og_layout.addWidget(self._opacity_label)
        og_layout.addWidget(self.opacity_slider, 1)   # 滑条在组内拉伸填满
        tb.addWidget(self.opacity_group)
        self._add_expanding(tb, self.opacity_spin)
        self._add_expanding(tb, self.close_btn)

        # ---- 状态栏 ----
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("status")

        # ---- 缩放手柄（与工具栏联动显示）----
        self.grip = QSizeGrip(self.container)
        self.grip.setObjectName("grip")
        self.grip.setFixedSize(16, 16)

        # 组装 container：只有字幕区 + 底部状态栏/grip（工具栏不在这里）
        container_layout.addWidget(self.view, 1)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 0, 10, 4)
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        bottom.addWidget(self.grip, 0, Qt.AlignRight | Qt.AlignBottom)
        container_layout.addLayout(bottom)

        # 根窗口无布局：container 和 toolbar 都用绝对定位（见 _layout_window）
        # 这样工具栏显隐能改变窗口总高，但 container 几何不变

        # 初始隐藏覆盖层
        self.toolbar.setVisible(False)
        self.status_label.setVisible(False)
        self.grip.setVisible(False)

    def _restore_geometry(self):
        # 最小尺寸（放大下限，避免缩太小）——针对字幕区高度
        self.setMinimumSize(
            getattr(self.ui_cfg, "min_win_w", 480),
            getattr(self.ui_cfg, "min_win_h", 120),
        )
        # 字幕区高度（用户配置的 win_h 是字幕区高度，不是含工具栏的窗口总高）
        self._content_h = self.ui_cfg.win_h or 150
        self.resize(self.ui_cfg.win_w or 760, self._content_h)
        if self.ui_cfg.win_x is not None and self.ui_cfg.win_y is not None:
            self.move(self.ui_cfg.win_x, self.ui_cfg.win_y)
        # 初始布局 + 按宽度决定工具栏精简
        self._layout_window()
        self._update_toolbar_compact()

    # ---------- 主题 ----------
    def _apply_theme(self):
        th = THEMES[self._current_theme]
        opacity = self.ui_cfg.window_opacity
        r, g, b = self._hex_to_rgb(th["bg"])
        text = th["text"]
        bg_rgba = f"rgba({r}, {g}, {b}, {opacity})"
        # 工具栏：中性灰底（不透明），与字幕区清晰分离。
        # 按钮：高对比度——dark 用亮底深字，light 用深底浅字，保证可读。
        if self._current_theme == "dark":
            toolbar_bg = "#2d2d2d"          # 中性深灰
            btn_bg = "#d8d8d8"               # 亮按钮底
            btn_text = "#1a1a1a"             # 深字（高对比）
            btn_border = "#b0b0b0"
            btn_hover = "#ffffff"
            btn_disabled_bg = "#555555"
            btn_disabled_text = "#999999"
            combo_view_bg, combo_sel = "#2a2a2a", "#3a6ea5"
            combo_text = "#f0f0f0"
        else:
            toolbar_bg = "#e0e0e0"          # 中性浅灰
            btn_bg = "#3a3a3a"              # 深按钮底
            btn_text = "#ffffff"            # 亮字（高对比）
            btn_border = "#555555"
            btn_hover = "#1a1a1a"
            btn_disabled_bg = "#bbbbbb"
            btn_disabled_text = "#888888"
            combo_view_bg, combo_sel = "#ffffff", "#b8d4f0"
            combo_text = "#1a1a1a"

        # QSS 设到根窗口 self，这样能覆盖所有子控件（toolbar 和 container 都管到）
        self.setStyleSheet(f"""
            #container {{
                background-color: {bg_rgba};
                border-radius: 12px;
            }}
            #toolbar {{ background-color: {toolbar_bg}; }}
            #status {{ background-color: transparent; }}
            QTextEdit {{
                background-color: transparent;
                color: {text};
                border: none;
            }}
            QLabel {{ color: {text}; background-color: transparent; }}
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_text};
                border: 1px solid {btn_border};
                border-radius: 5px;
                padding: 4px 10px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
            QPushButton:disabled {{ color: {btn_disabled_text}; background-color: {btn_disabled_bg}; }}
            QComboBox {{
                background-color: {btn_bg};
                color: {btn_text};
                border: 1px solid {btn_border};
                border-radius: 5px;
                padding: 2px 6px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {combo_view_bg};
                color: {combo_text};
                selection-background-color: {combo_sel};
                border: 1px solid {btn_border};
            }}
            QSizeGrip {{ background-color: transparent; border: none; }}
            QSlider {{
                background-color: transparent;
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {btn_border};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {btn_text};
                width: 12px;
                height: 12px;
                margin: -5px 0;
                border-radius: 6px;
            }}
            QSlider::sub-page:horizontal {{
                background: {btn_text};
                border-radius: 2px;
            }}
            QFontComboBox {{
                background-color: {btn_bg};
                color: {btn_text};
                border: 1px solid {btn_border};
                border-radius: 5px;
                padding: 2px 6px;
            }}
            QFontComboBox QAbstractItemView {{
                background-color: {combo_view_bg};
                color: {btn_text};
                selection-background-color: {combo_sel};
                border: 1px solid {btn_border};
            }}
            QSpinBox {{
                background-color: {btn_bg};
                color: {btn_text};
                border: 1px solid {btn_border};
                border-radius: 5px;
                padding: 2px 4px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {btn_border};
                width: 14px;
            }}
        """)

    @staticmethod
    def _hex_to_rgb(hex_color: str):
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def _toggle_theme(self):
        self._current_theme = "light" if self._current_theme == "dark" else "dark"
        self.theme_btn.setText("🌙" if self._current_theme == "dark" else "☀️")
        self._apply_theme()
        self._notify_geometry()

    # ---------- 字体/字号 ----------
    def _apply_font(self):
        """把当前字体+字号应用到字幕区。加保护防崩溃。"""
        try:
            self.view.setFont(QFont(self.ui_cfg.font_family, self._font_size))
        except Exception as e:
            print(f"[ui] 应用字体失败: {e}")

    def _on_font_activated(self, index: int):
        """用户在字体下拉框真正选中某项（回车/点击）时触发。
        用 activated 而非 currentFontChanged，避免弹窗存活期间 hover 高频回调崩溃。
        """
        font = self.font_combo.currentFont()
        self.ui_cfg.font_family = font.family()
        self._apply_font()
        self._notify_geometry()

    def _on_font_changed(self, font: QFont):
        """字体下拉框切换时（保留供外部调用）。"""
        self.ui_cfg.font_family = font.family()
        self._apply_font()
        self._notify_geometry()

    def _change_font_size(self, delta: int):
        self._font_size = max(12, min(56, self._font_size + delta))
        self._apply_font()
        self.ui_cfg.font_size = self._font_size
        self._notify_geometry()

    # ---------- 透明度（滑块与输入框双向联动）----------
    def _on_opacity_changed(self, value: int):
        """value 是 0-100 的百分比。实时改底色 + 同步另一个控件。"""
        self.ui_cfg.window_opacity = value / 100.0
        # 同步另一个控件，临时阻塞其信号避免递归
        sender = self.sender()
        if sender is self.opacity_slider:
            self.opacity_spin.blockSignals(True)
            self.opacity_spin.setValue(value)
            self.opacity_spin.blockSignals(False)
        elif sender is self.opacity_spin:
            self.opacity_slider.blockSignals(True)
            self.opacity_slider.setValue(value)
            self.opacity_slider.blockSignals(False)
        self._apply_theme()   # 实时重画底色

    # ---------- 置顶 ----------
    def _toggle_pin(self):
        pinned = not bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
        flags = Qt.FramelessWindowHint | Qt.Tool
        if pinned:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.show()
        self._apply_theme()
        self.pin_btn.setText("📌 已置顶" if pinned else "📌 未置顶")
        self.ui_cfg.always_on_top = pinned
        self._notify_geometry()

    # ---------- 悬停显隐 ----------
    def enterEvent(self, e):
        self._show_overlays()

    def leaveEvent(self, e):
        # 鼠标离开窗口时启动延时隐藏。但在 _hide_overlays 里会再次校验：
        # 如果当前有弹窗控件（下拉框/菜单）正打开，或鼠标实际还在窗口内，就不隐藏。
        self._hide_timer.start()

    def _show_overlays(self):
        self._hide_timer.stop()
        self.toolbar.setVisible(True)
        self.status_label.setVisible(True)
        self.grip.setVisible(True)
        self._layout_window()

    def _hide_overlays(self):
        # 关键防御：如果有任何弹窗控件正在交互（字体下拉、声音源下拉、数字框、菜单），
        # 或者鼠标其实还在窗口几何范围内，则不隐藏，避免下拉框一翻就被收回。
        if self._has_active_popup() or self._mouse_inside():
            return
        self.toolbar.setVisible(False)
        self.status_label.setVisible(False)
        self.grip.setVisible(False)
        self._layout_window()

    def _has_active_popup(self) -> bool:
        """是否有弹窗控件（下拉框/菜单）正处于活动状态。
        优先用全局计数（最可靠），再兜底用 activePopupWidget。"""
        if _POPUP_ACTIVE["count"] > 0:
            return True
        app = QApplication.instance()
        if app is not None:
            popup = app.activePopupWidget()
            if popup is not None and popup.isVisible():
                return True
        return False

    def _mouse_inside(self) -> bool:
        """鼠标是否还在窗口几何范围内（leaveEvent 可能在子控件间跳转时误触发）。"""
        gpos = self.cursor().pos()  # QWidget.cursor().pos() 返回全局坐标
        local = self.mapFromGlobal(gpos)
        return self.rect().contains(local)

    # ---------- 拖动 ----------
    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_offset is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._drag_offset is not None:
            self._drag_offset = None
            self._notify_geometry()

    def wheelEvent(self, e):
        bar = self.view.verticalScrollBar()
        steps = e.angleDelta().y() // 120
        bar.setValue(bar.value() - steps * bar.singleStep() * 4)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 用户拖拽 grip 改变窗口尺寸。高度是"窗口总高"，换算成字幕区高度。
        # 但要避免和 _layout_window 互相重入：只在非布局中处理。
        if getattr(self, "_laying_out", False):
            return
        tb_h = self.toolbar.height() if self.toolbar.isVisible() else 0
        new_content_h = max(self.minimumHeight(), self.height() - tb_h)
        if new_content_h != getattr(self, "_content_h", None):
            self._content_h = new_content_h
        # 宽度变化或 content_h 变化，重新布局（保持 container 屏幕位置）
        self._layout_window()
        self._update_toolbar_compact()
        self._notify_geometry()

    def _layout_window(self):
        """核心：工具栏显隐时改变窗口总高 + container 的 y，但 container 屏幕几何不变。

        - 工具栏显示：窗口顶部上移 tb_h，container 仍在原屏幕位置（y=tb_h 相对窗口）
        - 工具栏隐藏：窗口缩回，container 回到 y=0
        这样字幕区位置/大小永远不变，行数不跳。
        """
        # 防御递归
        if getattr(self, "_laying_out", False):
            return
        self._laying_out = True
        try:
            content_h = getattr(self, "_content_h", self.height())
            content_w = self.width()
            tb_visible = self.toolbar.isVisible()
            tb_h = self.toolbar.sizeHint().height() if tb_visible else 0

            # 记住 container 当前屏幕位置（要保持不变）
            old_screen_pos = self.container.mapToGlobal(self.container.rect().topLeft()) \
                if self.container.isVisible() else None

            # 1) 窗口总高度 = 字幕区高度 + 工具栏高度
            new_total_h = content_h + tb_h
            if tb_visible:
                # 显示工具栏：窗口顶部上移 tb_h，保持 container 屏幕位置
                if old_screen_pos is not None:
                    new_top = old_screen_pos.y() - tb_h
                    self.setGeometry(self.x(), new_top, content_w, new_total_h)
                else:
                    self.resize(content_w, new_total_h)
            else:
                # 隐藏工具栏：窗口缩回，container 回到 y=0，保持屏幕位置
                if old_screen_pos is not None:
                    self.setGeometry(self.x(), old_screen_pos.y(), content_w, new_total_h)
                else:
                    self.resize(content_w, new_total_h)

            # 2) 工具栏定位在窗口顶部
            self.toolbar.setGeometry(0, 0, content_w, tb_h)
            self.toolbar.raise_()
            # 3) container 定位（紧跟工具栏下方）
            self.container.setGeometry(0, tb_h, content_w, content_h)
        finally:
            self._laying_out = False

    def _add_expanding(self, layout, widget):
        """把 widget 加入布局，并设为水平拉伸——均匀填满整行，避免割裂空白。"""
        from PyQt5.QtWidgets import QSizePolicy
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(widget)

    def _update_toolbar_compact(self):
        """按窗口宽度渐进式隐藏工具栏控件。

        从 1200px 起逐渐隐藏（间隔75），到 600px 停止；
        最终最小窗口只保留：透明度输入框 / A+ / A-。
        """
        w = self.width()
        # (控件列表, 隐藏阈值)：宽度 < 阈值 时该组隐藏
        # 从 1200px 起逐步隐藏（间隔75），到 600px 停止隐藏；
        # 最小窗口(480)时只剩 透明度输入/A+/A-
        hide_rules = [
            ([self.pin_btn], 1200),
            ([self._font_label, self.font_combo], 1125),
            ([self._device_label, self.device_combo], 1050),
            ([self.clear_btn], 975),
            ([self.theme_btn], 900),
            ([self.close_btn], 825),
            ([self.opacity_group], 750),
            ([self.start_btn], 675),
            ([self.stop_btn], 600),
        ]
        for widgets, threshold in hide_rules:
            hide = w < threshold
            for wid in widgets:
                wid.setVisible(not hide)

    def _notify_geometry(self):
        if self.on_geometry_changed is not None:
            # 存字幕区(container)的屏幕几何，而非窗口总几何——
            # 这样工具栏显隐导致的窗口高度/y变化不会被持久化，字幕区位置稳定。
            cg = self.container.geometry()
            screen_top = self.container.mapToGlobal(cg.topLeft())
            self.on_geometry_changed(screen_top.x(), screen_top.y(),
                                     cg.width(), cg.height(),
                                     self.ui_cfg.always_on_top, self._current_theme)

    # ---------- 对外接口 ----------
    def emit_text(self, text: str, is_final: bool):
        self._text_appended.emit(text, is_final)

    def set_status(self, text: str, color: str | None = None):
        self.status_label.setText(text)

    # ---------- 字幕追加 ----------
    def _on_text_appended(self, text: str, is_final: bool):
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - bar.singleStep()
        saved_pos = bar.value()
        cursor = QTextCursor(self.view.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self._trim_if_needed()
        # 锁定模式：强制跟到底，无视用户位置
        if getattr(self.ui_cfg, "lock_scroll_to_bottom", False):
            bar.setValue(bar.maximum())
        elif at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(saved_pos)

    def _trim_if_needed(self):
        max_chars = getattr(self.ui_cfg, "max_chars", 20000)
        doc = self.view.document()
        total = doc.characterCount()
        if total <= max_chars:
            return
        keep = int(max_chars * 0.9)
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, total - keep)
        cursor.removeSelectedText()

    # ---------- 按钮 ----------
    def _on_clear(self):
        self.view.clear()

    def closeEvent(self, e):
        """拦截系统关闭（Alt+F4 等）：走和 ✕ 一样的询问逻辑。
        只有 do_quit（设了 _force_quit）时才真正关闭。"""
        if getattr(self, "_force_quit", False):
            # 真正退出：保存配置后允许关闭
            self._notify_geometry()
            super().closeEvent(e)
        else:
            # 拦截，交给 _on_close 的询问逻辑处理
            e.ignore()
            self._on_close()

    def _on_start(self):
        dev = self.device_combo.currentData()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.device_combo.setEnabled(False)
        self.set_status("启动中……")
        if self.on_start:
            try:
                self.on_start(dev)
            except Exception as e:
                self.set_status(f"出错：{e}")
                self._reset_buttons()

    def _on_stop(self):
        self._reset_buttons()
        self.set_status("已停止")
        if self.on_stop:
            self.on_stop()

    def _reset_buttons(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.device_combo.setEnabled(True)

    def _on_close(self):
        """点 ✕ 按钮：根据 close_action 配置决定行为。
        ask=弹窗询问 / hide=直接隐藏到托盘 / quit=直接退出。
        """
        action = getattr(self.ui_cfg, "close_action", "ask")
        if action == "hide":
            self._do_hide()
        elif action == "quit":
            self.do_quit()
        else:  # ask
            self._ask_close_action()

    def _ask_close_action(self):
        """弹窗询问：隐藏到托盘 / 退出程序。带「下次不再询问」。"""
        msg = QMessageBox(self)
        msg.setWindowTitle("关闭字幕")
        msg.setText("你想怎么关闭？")
        hide_btn = msg.addButton("隐藏到托盘", QMessageBox.AcceptRole)
        quit_btn = msg.addButton("退出程序", QMessageBox.RejectRole)
        msg.setDefaultButton(hide_btn)
        cb = QCheckBox("下次不再询问（可在托盘菜单「设置」中修改）")
        msg.setCheckBox(cb)
        msg.exec_()
        remember = cb.isChecked()

        if msg.clickedButton() is hide_btn:
            if remember:
                self.ui_cfg.close_action = "hide"
            self._do_hide()
        elif msg.clickedButton() is quit_btn:
            if remember:
                self.ui_cfg.close_action = "quit"
            self.do_quit()

    def _do_hide(self):
        """隐藏窗口到托盘。"""
        self._notify_geometry()
        self.hide_requested.emit()

    def toggle_visibility(self):
        """显示/隐藏窗口（供托盘左键单击调用）。"""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def do_quit(self):
        """真正退出程序：保存配置 + 关闭。"""
        self._force_quit = True
        self._notify_geometry()
        self.quit_requested.emit()
        self.close()

    # ============================================================
    # 公共 API（供 SettingsDialog 驱动，避免逻辑分叉）
    # ============================================================

    # ---------- 读取 ----------
    def get_font_family(self) -> str:
        return self.ui_cfg.font_family

    def get_font_size(self) -> int:
        return self._font_size

    def get_opacity(self) -> int:
        return int(round(self.ui_cfg.window_opacity * 100))

    def get_theme(self) -> str:
        return self._current_theme

    def get_pin(self) -> bool:
        return bool(self.windowFlags() & Qt.WindowStaysOnTopHint)

    def get_window_size(self):
        """返回字幕区尺寸（不含工具栏）。"""
        return self.container.width(), self.container.height()

    def get_lock_scroll(self) -> bool:
        return getattr(self.ui_cfg, "lock_scroll_to_bottom", False)

    def get_transcript(self) -> str:
        """完整字幕文本（供文稿回看）。"""
        return self.view.toPlainText()

    def get_devices(self):
        """返回 [(显示名, data)] 列表，data 是设备名或 None。"""
        out = []
        for i in range(self.device_combo.count()):
            out.append((self.device_combo.itemText(i), self.device_combo.itemData(i)))
        return out

    def is_recording(self) -> bool:
        return self.stop_btn.isEnabled()  # 运行中时停止按钮可用

    # ---------- 设置（立即生效）----------
    def set_font_family(self, name: str):
        self.ui_cfg.font_family = name
        self._apply_font()
        # 同步工具栏字体框
        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentFont(QFont(name))
        self.font_combo.blockSignals(False)

    def set_font_size(self, size: int):
        self._font_size = max(8, min(72, int(size)))
        self.ui_cfg.font_size = self._font_size
        self._apply_font()

    def set_opacity(self, value: int):
        value = max(0, min(100, int(value)))
        self.ui_cfg.window_opacity = value / 100.0
        # 同步工具栏控件
        self.opacity_slider.blockSignals(True)
        self.opacity_spin.blockSignals(True)
        self.opacity_slider.setValue(value)
        self.opacity_spin.setValue(value)
        self.opacity_slider.blockSignals(False)
        self.opacity_spin.blockSignals(False)
        self._apply_theme()

    def set_theme(self, name: str):
        if name not in THEMES:
            return
        if self._current_theme != name:
            self._current_theme = name
            self.theme_btn.setText("🌙" if name == "dark" else "☀️")
            self._apply_theme()
            self.ui_cfg.theme = name
            self._notify_geometry()

    def set_pin(self, pinned: bool):
        if self.get_pin() != pinned:
            self._toggle_pin()

    def set_window_size(self, w: int, h: int):
        """设置字幕区尺寸（h 是字幕区高度，不含工具栏）。"""
        w = max(self.minimumWidth(), int(w))
        h = max(self.minimumHeight(), int(h))
        self._content_h = h
        self._layout_window()
        self._notify_geometry()

    def set_lock_scroll(self, locked: bool):
        self.ui_cfg.lock_scroll_to_bottom = bool(locked)
        if locked:
            self.scroll_to_bottom_now()

    def scroll_to_bottom_now(self):
        """立刻滚动到字幕最底部。"""
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_transcript(self):
        self.view.clear()

    def set_toolbar_hide_delay(self, ms: int):
        self.ui_cfg.toolbar_hide_delay_ms = int(ms)
        self._hide_timer.setInterval(int(ms))

    def set_max_chars(self, n: int):
        self.ui_cfg.max_chars = int(n)

    def set_close_action(self, action: str):
        self.ui_cfg.close_action = action

    # ---------- 识别控制（包装内部方法，供设置对话框按钮）----------
    def start_recognition(self, device_name=None):
        """device_name 为 None 时用当前下拉选择。"""
        if device_name is None:
            device_name = self.device_combo.currentData()
        self._on_start_with_device(device_name)

    def stop_recognition(self):
        self._on_stop()

    def _on_start_with_device(self, device_name):
        # 复用 _on_start 的逻辑，但显式传 device
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.device_combo.setEnabled(False)
        self.set_status("启动中……")
        if self.on_start:
            try:
                self.on_start(device_name)
            except Exception as e:
                self.set_status(f"出错：{e}")
                self._reset_buttons()

