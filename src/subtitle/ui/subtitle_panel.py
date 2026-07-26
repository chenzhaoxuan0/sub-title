"""沉浸式无边框字幕面板 v4 —— 主题引擎驱动 + 完整自定义。

架构：
  SubtitlePanel（根窗口：透明、无边框、置顶）
    └─ container（真正画背景色的 QWidget，圆角可自定义）
         ├─ overlay_layer（贴图渲染层，桌宠皮肤用）
         ├─ view（字幕文本区）
         ├─ status_label（悬停显示）
         └─ grip（悬停显示，右下角缩放手柄）
    └─ toolbar（绝对定位在 container 上方，悬停显示）

v4 新增：
- 主题引擎驱动所有颜色/几何参数
- 圆角、内边距、行间距全自定义
- 贴图叠加层（为桌宠功能预留）
- 跨平台适配（Win/Mac 原生风格微调）
"""
from __future__ import annotations

import platform
from PySide6.QtCore import Qt, QPoint, QTimer, Signal, QEvent
from PySide6.QtGui import QFont, QTextCursor, QMouseEvent, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QLabel, QComboBox, QSizeGrip, QSlider, QSpinBox, QFontComboBox,
    QApplication, QMessageBox, QCheckBox, QSizePolicy,
)

from ..config import UiConfig
from ..audio import list_loopback_devices
from .theme_engine import Theme, ThemeManager, get_theme_manager


IS_MAC = platform.system() == "Darwin"


# 全局：记录当前是否有弹窗控件处于打开状态
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
    """只读、透明背景的文本区。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameStyle(QTextEdit.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.viewport().setAttribute(Qt.WA_TransparentForMouseEvents, True)


class OverlayLayer(QWidget):
    """贴图渲染层 —— 桌宠/皮肤贴图的画布。

    透明覆盖在字幕区上方，由 SkinRenderer 驱动绘制。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._renderer = None  # SkinRenderer 实例，由外部注入

    def set_renderer(self, renderer):
        self._renderer = renderer

    def paintEvent(self, event):
        if self._renderer is not None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            self._renderer.render(painter, self.width(), self.height())
            painter.end()


class SubtitlePanel(QWidget):
    """沉浸式无边框字幕窗口 v4。"""

    _text_appended = Signal(str, bool)
    hide_requested = Signal()
    quit_requested = Signal()
    theme_changed = Signal(str)  # 主题切换信号
    context_menu_requested = Signal(QPoint)  # 全局坐标；右键任意位置时发出

    def __init__(self, ui_cfg: UiConfig, on_start=None, on_stop=None, on_quit=None,
                 on_geometry_changed=None):
        super().__init__()
        self.ui_cfg = ui_cfg
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_quit = on_quit
        self.on_geometry_changed = on_geometry_changed

        self._theme_mgr = get_theme_manager()
        self._text_appended.connect(self._on_text_appended)
        self._drag_offset: QPoint | None = None
        self._font_size = ui_cfg.font_size or 22

        # 应用配置中的主题
        theme_name = ui_cfg.theme or "Dark"
        self._theme_mgr.apply_theme(theme_name)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(getattr(ui_cfg, "toolbar_hide_delay_ms", 800))
        self._hide_timer.timeout.connect(self._hide_overlays)

        # P0 性能：字幕追加节流。emit_text 累积到 _pending_text，
        # 16ms 定时器到点一次性 flush，把每秒 10+ 次插入降到 ~60fps 上限。
        self._pending_text = ""
        self._pending_has_final = False
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(16)
        self._flush_timer.timeout.connect(self._flush_pending_text)

        self._init_window_flags()
        self._init_ui()
        self._apply_theme()
        self._restore_geometry()

    # ---------- 初始化 ----------
    def _init_window_flags(self):
        # 注意：不加 Qt.Tool —— 否则窗口不会出现在任务栏，
        # 用户要拿 OBS 抓取或 Alt-Tab 切换就找不到。
        flags = Qt.FramelessWindowHint
        if self.ui_cfg.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def _init_ui(self):
        self.setObjectName("root")

        # 字幕区 container
        self.container = QWidget(self)
        self.container.setObjectName("container")
        self.container.setAttribute(Qt.WA_StyledBackground, True)
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # 贴图叠加层（桌宠皮肤）
        self.overlay_layer = OverlayLayer(self.container)
        self.overlay_layer.setObjectName("overlay")

        # 字幕文本区
        self.view = _SubtitleView(self.container)
        theme = self._theme_mgr.current
        geo = theme.geometry
        font_family = self.ui_cfg.font_family or geo.font_family
        self.view.setFont(QFont(font_family, self._font_size))
        self.view.setPlaceholderText("点击 ▶ 开始，播放任意视频/音频，字幕会实时出现……")

        # ---- 工具栏 ----
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
        self.theme_btn = QPushButton("🎨")
        self.theme_btn.setToolTip("切换主题")
        self.close_btn = QPushButton("✕")

        self.font_combo = _PopupFontCombo()
        self.font_combo.setCurrentFont(QFont(font_family, self._font_size))
        self.font_combo.setToolTip("字幕字体（点击 ▼ 下拉选择）")
        self.font_combo.setFixedWidth(180)

        # 透明度
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
        self.theme_btn.clicked.connect(self._cycle_theme)
        self.close_btn.clicked.connect(self._on_close)
        self.font_combo.activated.connect(self._on_font_activated)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.opacity_slider.sliderReleased.connect(self._notify_geometry)
        self.opacity_spin.valueChanged.connect(self._on_opacity_changed)

        self._device_label = QLabel("声音源：")
        tb.addWidget(self._device_label)
        tb.addWidget(self.device_combo)
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
        # 透明度组
        self.opacity_group = QWidget()
        self.opacity_group.setObjectName("opacity_group")
        og_layout = QHBoxLayout(self.opacity_group)
        og_layout.setContentsMargins(0, 0, 0, 0)
        og_layout.setSpacing(4)
        self._opacity_label = QLabel("透明度")
        og_layout.addWidget(self._opacity_label)
        og_layout.addWidget(self.opacity_slider, 1)
        tb.addWidget(self.opacity_group)
        self._add_expanding(tb, self.opacity_spin)
        self._add_expanding(tb, self.close_btn)

        # ---- 状态栏 ----
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("status")

        # ---- 缩放手柄 ----
        self.grip = QSizeGrip(self.container)
        self.grip.setObjectName("grip")
        self.grip.setFixedSize(16, 16)

        # 组装 container
        container_layout.addWidget(self.view, 1)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 0, 10, 4)
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        bottom.addWidget(self.grip, 0, Qt.AlignRight | Qt.AlignBottom)
        container_layout.addLayout(bottom)

        # 初始隐藏
        self.toolbar.setVisible(False)
        self.status_label.setVisible(False)
        self.grip.setVisible(False)

    def _restore_geometry(self):
        # 最小尺寸硬编码 30x30，不读 ui_cfg.min_win_w/min_win_h。
        # 原因：旧版本（< 30 之前）的 config.yaml 存了 min_win_w=480/min_win_h=120，
        # 不强制覆盖的话那个老值会一直把窗口锁死在 480x120。
        # min_win_w/h 字段保留在 UiConfig 里兼容旧文件，但实际不用。
        self.setMinimumSize(30, 30)
        self._content_h = self.ui_cfg.win_h or 150
        self.resize(self.ui_cfg.win_w or 760, self._content_h)
        if self.ui_cfg.win_x is not None and self.ui_cfg.win_y is not None:
            # 检查保存的位置是否在可见屏幕内，不在则忽略（用默认位置）
            # 避免：切换显示器配置后窗口跑到屏幕外看不见
            if self._pos_on_any_screen(self.ui_cfg.win_x, self.ui_cfg.win_y):
                self.move(self.ui_cfg.win_x, self.ui_cfg.win_y)
            else:
                print(f"[ui] 保存的窗口位置 ({self.ui_cfg.win_x},{self.ui_cfg.win_y}) "
                      f"不在任何屏幕内，使用默认位置")
        self._layout_window()
        self._update_toolbar_compact()
        # 装好之后再接管右键：任意子控件 / 空白处的右键都触发 context_menu_requested
        self._setup_context_menu()

    def _setup_context_menu(self):
        """在任何位置右键都弹出托盘菜单。

        思路：每个子控件都装上事件过滤器，拦 MouseButtonPress(RightButton) →
        转发为全局 context_menu_requested 信号（带 globalPos）。
        panel 自己用 setContextMenuPolicy(CustomContextMenu) 走 Qt 标准通道。
        """
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_panel_context_menu)
        # 拦截所有子控件的右键（按钮、toolbar、container、grip 等）
        self._filtered_children = self.findChildren(QWidget)
        for child in self._filtered_children:
            child.installEventFilter(self)

    def _on_panel_context_menu(self, pos: QPoint):
        """panel 自身空白处的右键。"""
        self.context_menu_requested.emit(self.mapToGlobal(pos))

    def eventFilter(self, obj, event):
        """子控件的右键 → 转发为 context_menu_requested。"""
        if (obj in getattr(self, "_filtered_children", ())
                and event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.RightButton):
            # event.pos() 是 child 局部坐标；mapToGlobal 转到屏幕坐标
            self.context_menu_requested.emit(obj.mapToGlobal(event.pos()))
            return True  # 消费掉，不再传给 child
        return super().eventFilter(obj, event)

    @staticmethod
    def _pos_on_any_screen(x: int, y: int) -> bool:
        """检查 (x,y) 是否在任一屏幕的可用区域内。"""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return True  # 没有 app 无法判断，保守认为可见
        for screen in app.screens():
            g = screen.availableGeometry()
            if g.x() <= x < g.x() + g.width() and g.y() <= y < g.y() + g.height():
                return True
        return False

    # ---------- 主题 ----------
    def _apply_theme(self):
        """从 ThemeManager 读取当前主题，生成 QSS 并应用。"""
        theme = self._theme_mgr.current
        colors = theme.colors
        geo = theme.geometry
        opacity = self.ui_cfg.window_opacity

        # 几何参数（config 覆盖 > 主题默认）
        radius = self.ui_cfg.border_radius if self.ui_cfg.border_radius is not None else geo.border_radius
        pad_t = self.ui_cfg.padding_top if self.ui_cfg.padding_top is not None else geo.padding_top
        pad_b = self.ui_cfg.padding_bottom if self.ui_cfg.padding_bottom is not None else geo.padding_bottom
        pad_l = self.ui_cfg.padding_left if self.ui_cfg.padding_left is not None else geo.padding_left
        pad_r = self.ui_cfg.padding_right if self.ui_cfg.padding_right is not None else geo.padding_right
        line_sp = self.ui_cfg.line_spacing if self.ui_cfg.line_spacing is not None else geo.line_spacing

        r, g, b = self._hex_to_rgb(colors.subtitle_bg)
        bg_rgba = f"rgba({r}, {g}, {b}, {opacity})"

        # 跨平台微调
        toolbar_radius = geo.toolbar_radius
        if IS_MAC:
            toolbar_radius = max(toolbar_radius, 10)

        self.setStyleSheet(f"""
            #container {{
                background-color: {bg_rgba};
                border-radius: {radius}px;
                border: 1px solid {colors.subtitle_border};
            }}
            #toolbar {{
                background-color: {colors.toolbar_bg};
                border-radius: {toolbar_radius}px;
                border: 1px solid {colors.subtitle_border};
            }}
            #status {{ background-color: transparent; }}
            QTextEdit {{
                background-color: transparent;
                color: {colors.subtitle_text};
                border: none;
                padding: {pad_t}px {pad_r}px {pad_b}px {pad_l}px;
                line-height: {line_sp};
            }}
            QLabel {{ color: {colors.toolbar_text}; background-color: transparent; }}
            QPushButton {{
                background-color: {colors.btn_bg};
                color: {colors.btn_text};
                border: 1px solid {colors.btn_border};
                border-radius: {geo.btn_radius}px;
                padding: 4px 10px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {colors.btn_hover}; }}
            QPushButton:disabled {{
                color: {colors.btn_disabled_text};
                background-color: {colors.btn_disabled_bg};
            }}
            QComboBox {{
                background-color: {colors.btn_bg};
                color: {colors.btn_text};
                border: 1px solid {colors.btn_border};
                border-radius: {geo.btn_radius}px;
                padding: 2px 6px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {colors.combo_bg};
                color: {colors.combo_text};
                selection-background-color: {colors.combo_selected};
                border: 1px solid {colors.btn_border};
            }}
            QSizeGrip {{ background-color: transparent; border: none; }}
            QSlider {{ background-color: transparent; }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {colors.btn_border};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {colors.accent};
                width: 12px;
                height: 12px;
                margin: -5px 0;
                border-radius: 6px;
            }}
            QSlider::sub-page:horizontal {{
                background: {colors.accent};
                border-radius: 2px;
            }}
            QFontComboBox {{
                background-color: {colors.btn_bg};
                color: {colors.btn_text};
                border: 1px solid {colors.btn_border};
                border-radius: {geo.btn_radius}px;
                padding: 2px 6px;
            }}
            QFontComboBox QAbstractItemView {{
                background-color: {colors.combo_bg};
                color: {colors.combo_text};
                selection-background-color: {colors.combo_selected};
                border: 1px solid {colors.btn_border};
            }}
            QSpinBox {{
                background-color: {colors.btn_bg};
                color: {colors.btn_text};
                border: 1px solid {colors.btn_border};
                border-radius: {geo.btn_radius}px;
                padding: 2px 4px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {colors.btn_border};
                width: 14px;
            }}
        """)

        # 更新字幕区 padding
        self.view.setStyleSheet(f"""
            QTextEdit {{
                background-color: transparent;
                color: {colors.subtitle_text};
                border: none;
                padding: {pad_t}px {pad_r}px {pad_b}px {pad_l}px;
            }}
        """)

    @staticmethod
    def _hex_to_rgb(hex_color: str):
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def _cycle_theme(self):
        """循环切换主题。"""
        all_themes = list(self._theme_mgr.get_all_themes().keys())
        current = self._theme_mgr.current.name
        idx = all_themes.index(current) if current in all_themes else 0
        next_name = all_themes[(idx + 1) % len(all_themes)]
        self.set_theme(next_name)

    def set_theme(self, name: str):
        """切换到指定主题。"""
        if self._theme_mgr.apply_theme(name):
            self._apply_theme()
            self.ui_cfg.theme = name
            self.theme_changed.emit(name)
            self._notify_geometry()

    def set_theme_obj(self, theme: Theme):
        """直接应用 Theme 对象（用于实时预览）。"""
        self._theme_mgr.apply_theme_obj(theme)
        self._apply_theme()

    # ---------- 字体/字号 ----------
    def _apply_font(self):
        try:
            self.view.setFont(QFont(self.ui_cfg.font_family, self._font_size))
        except Exception as e:
            print(f"[ui] 应用字体失败: {e}")

    def _on_font_activated(self, index: int):
        font = self.font_combo.currentFont()
        self.ui_cfg.font_family = font.family()
        self._apply_font()
        self._notify_geometry()

    def _change_font_size(self, delta: int):
        self._font_size = max(12, min(56, self._font_size + delta))
        self._apply_font()
        self.ui_cfg.font_size = self._font_size
        self._notify_geometry()

    # ---------- 透明度 ----------
    def _on_opacity_changed(self, value: int):
        self.ui_cfg.window_opacity = value / 100.0
        sender = self.sender()
        if sender is self.opacity_slider:
            self.opacity_spin.blockSignals(True)
            self.opacity_spin.setValue(value)
            self.opacity_spin.blockSignals(False)
        elif sender is self.opacity_spin:
            self.opacity_slider.blockSignals(True)
            self.opacity_slider.setValue(value)
            self.opacity_slider.blockSignals(False)
        # P0 性能优化：透明度改变只更新 container 背景色，不重设全量 QSS
        self._apply_container_bg()

    def _apply_container_bg(self):
        """只更新 container 的背景色（透明度热路径优化，避免全量 QSS 重解析）。"""
        theme = self._theme_mgr.current
        colors = theme.colors
        geo = theme.geometry
        opacity = self.ui_cfg.window_opacity
        r, g, b = self._hex_to_rgb(colors.subtitle_bg)
        bg_rgba = f"rgba({r}, {g}, {b}, {opacity})"
        radius = self.ui_cfg.border_radius if self.ui_cfg.border_radius is not None else geo.border_radius
        # 只给 container 设局部样式，不波及按钮等其它控件
        self.container.setStyleSheet(
            f"#container {{ background-color: {bg_rgba}; border-radius: {radius}px; "
            f"border: 1px solid {colors.subtitle_border}; }}"
        )

    # ---------- 置顶 ----------
    def _toggle_pin(self):
        pinned = not bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
        # 注意：不加 Qt.Tool —— 否则置顶切换时窗口会从任务栏消失。
        flags = Qt.FramelessWindowHint
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
        self._hide_timer.start()

    def _show_overlays(self):
        self._hide_timer.stop()
        self.toolbar.setVisible(True)
        self.status_label.setVisible(True)
        self.grip.setVisible(True)
        self._layout_window()

    def _hide_overlays(self):
        if self._has_active_popup() or self._mouse_inside():
            return
        self.toolbar.setVisible(False)
        self.status_label.setVisible(False)
        self.grip.setVisible(False)
        self._layout_window()

    def _has_active_popup(self) -> bool:
        if _POPUP_ACTIVE["count"] > 0:
            return True
        app = QApplication.instance()
        if app is not None:
            popup = app.activePopupWidget()
            if popup is not None and popup.isVisible():
                return True
        return False

    def _mouse_inside(self) -> bool:
        gpos = self.cursor().pos()
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
        if getattr(self, "_laying_out", False):
            return
        tb_h = self.toolbar.height() if self.toolbar.isVisible() else 0
        new_content_h = max(self.minimumHeight(), self.height() - tb_h)
        if new_content_h != getattr(self, "_content_h", None):
            self._content_h = new_content_h
        self._layout_window()
        self._update_toolbar_compact()
        # 同步 overlay 尺寸
        self.overlay_layer.setGeometry(self.container.rect())
        self._notify_geometry()

    def _layout_window(self):
        if getattr(self, "_laying_out", False):
            return
        self._laying_out = True
        try:
            content_h = getattr(self, "_content_h", self.height())
            content_w = self.width()
            tb_visible = self.toolbar.isVisible()
            tb_h = self.toolbar.sizeHint().height() if tb_visible else 0

            old_screen_pos = self.container.mapToGlobal(self.container.rect().topLeft()) \
                if self.container.isVisible() else None

            new_total_h = content_h + tb_h
            if tb_visible:
                if old_screen_pos is not None:
                    new_top = old_screen_pos.y() - tb_h
                    self.setGeometry(self.x(), new_top, content_w, new_total_h)
                else:
                    self.resize(content_w, new_total_h)
            else:
                if old_screen_pos is not None:
                    self.setGeometry(self.x(), old_screen_pos.y(), content_w, new_total_h)
                else:
                    self.resize(content_w, new_total_h)

            self.toolbar.setGeometry(0, 0, content_w, tb_h)
            self.toolbar.raise_()
            self.container.setGeometry(0, tb_h, content_w, content_h)
            # overlay 跟随 container
            self.overlay_layer.setGeometry(0, 0, content_w, content_h)
        finally:
            self._laying_out = False

    def _add_expanding(self, layout, widget):
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(widget)

    def _update_toolbar_compact(self):
        w = self.width()
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
            cg = self.container.geometry()
            screen_top = self.container.mapToGlobal(cg.topLeft())
            self.on_geometry_changed(screen_top.x(), screen_top.y(),
                                     cg.width(), cg.height(),
                                     self.ui_cfg.always_on_top,
                                     self._theme_mgr.current.name)

    # ---------- 对外接口 ----------
    def emit_text(self, text: str, is_final: bool):
        """跨线程安全：只 emit 信号。Qt 会用 QueuedConnection 把调用送到主线程。
        绝不能在这里直接操作 QTimer/QWidget（它们属于主线程），否则 PySide6 报
        'startTimer from another thread'。"""
        self._text_appended.emit(text, is_final)

    def _on_text_appended(self, text: str, is_final: bool):
        """主线程槽：累积到 buffer + 节流 flush（这里操作 QTimer 安全）。"""
        self._pending_text += text
        if is_final:
            self._pending_has_final = True
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending_text(self):
        """定时器到点：把累积的文本一次性插入文档。"""
        if not self._pending_text:
            return
        text = self._pending_text
        self._pending_text = ""
        self._pending_has_final = False
        self._insert_text(text)

    def set_status(self, text: str, color: str | None = None):
        self.status_label.setText(text)

    # ---------- 字幕插入文档（实际渲染，主线程）----------
    def _insert_text(self, text: str):
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - bar.singleStep()
        saved_pos = bar.value()
        cursor = QTextCursor(self.view.document())
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        cursor.insertText(text)
        self._trim_if_needed()
        cursor.endEditBlock()
        # 锁定模式：强制跟到底
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
        if getattr(self, "_force_quit", False):
            self._notify_geometry()
            super().closeEvent(e)
        else:
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
        action = getattr(self.ui_cfg, "close_action", "ask")
        if action == "hide":
            self._do_hide()
        elif action == "quit":
            self.do_quit()
        else:
            self._ask_close_action()

    def _ask_close_action(self):
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
        self._notify_geometry()
        self.hide_requested.emit()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def do_quit(self):
        self._force_quit = True
        self._notify_geometry()
        self.quit_requested.emit()
        self.close()

    # ============================================================
    # 公共 API
    # ============================================================

    def get_font_family(self) -> str:
        return self.ui_cfg.font_family

    def get_font_size(self) -> int:
        return self._font_size

    def get_opacity(self) -> int:
        return int(round(self.ui_cfg.window_opacity * 100))

    def get_theme(self) -> str:
        return self._theme_mgr.current.name

    def get_pin(self) -> bool:
        return bool(self.windowFlags() & Qt.WindowStaysOnTopHint)

    def get_window_size(self):
        return self.container.width(), self.container.height()

    def get_lock_scroll(self) -> bool:
        return getattr(self.ui_cfg, "lock_scroll_to_bottom", False)

    def get_transcript(self) -> str:
        return self.view.toPlainText()

    def get_devices(self):
        out = []
        for i in range(self.device_combo.count()):
            out.append((self.device_combo.itemText(i), self.device_combo.itemData(i)))
        return out

    def is_recording(self) -> bool:
        return self.stop_btn.isEnabled()

    def get_theme_manager(self) -> ThemeManager:
        return self._theme_mgr

    # ---------- 设置 ----------
    def set_font_family(self, name: str):
        self.ui_cfg.font_family = name
        self._apply_font()
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
        self.opacity_slider.blockSignals(True)
        self.opacity_spin.blockSignals(True)
        self.opacity_slider.setValue(value)
        self.opacity_spin.setValue(value)
        self.opacity_slider.blockSignals(False)
        self.opacity_spin.blockSignals(False)
        self._apply_theme()

    def set_pin(self, pinned: bool):
        if self.get_pin() != pinned:
            self._toggle_pin()

    def set_window_size(self, w: int, h: int):
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

    def set_border_radius(self, radius: int):
        self.ui_cfg.border_radius = int(radius)
        self._apply_theme()

    def set_padding(self, top: int, bottom: int, left: int, right: int):
        self.ui_cfg.padding_top = top
        self.ui_cfg.padding_bottom = bottom
        self.ui_cfg.padding_left = left
        self.ui_cfg.padding_right = right
        self._apply_theme()

    def set_line_spacing(self, spacing: float):
        self.ui_cfg.line_spacing = spacing
        self._apply_theme()

    # ---------- 识别控制 ----------
    def start_recognition(self, device_name=None):
        if device_name is None:
            device_name = self.device_combo.currentData()
        self._on_start_with_device(device_name)

    def stop_recognition(self):
        self._on_stop()

    def _on_start_with_device(self, device_name):
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
