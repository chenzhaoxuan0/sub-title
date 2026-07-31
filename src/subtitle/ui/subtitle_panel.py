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

import html
import platform
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QTimer, Signal, QEvent
from PySide6.QtGui import QFont, QTextCursor, QMouseEvent, QPainter, QColor, QCursor, QRegion
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QLabel, QComboBox, QSizeGrip, QSlider, QSpinBox, QFontComboBox,
    QApplication, QMessageBox, QCheckBox, QSizePolicy,
)

from ..config import UiConfig
from ..audio import list_loopback_devices, list_microphone_devices
from ..core.speaker_names import SpeakerNameMap
from .line_breaker import LineBreaker
from .theme_engine import Theme, ThemeManager, get_theme_manager


IS_MAC = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"


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
    def __init__(self, plane, parent=None):
        super().__init__(parent)
        self.plane = plane
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._runtime = None
        self._renderer = None

    def set_runtime(self, runtime):
        self._runtime = runtime
        self._renderer = runtime.renderer if runtime is not None else None
        self.update()

    def set_renderer(self, renderer):
        self._renderer = renderer

    def paintEvent(self, event):
        if self._renderer is not None:
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                self._renderer.render(painter, self.width(), self.height(), self.plane)
            finally:
                if painter.isActive():
                    painter.end()


class SkinExtensionWindow(QWidget):
    """Draw only the skin pixels outside the subtitle content rectangle."""

    def __init__(self, panel):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
        if not IS_WINDOWS:
            transparent_input = getattr(Qt, "WindowTransparentForInput", None)
            if transparent_input is not None:
                flags |= transparent_input
        super().__init__(panel, flags)
        self.panel = panel
        self._runtime = None
        self._renderer = None
        self._paint_origin = QPointF()
        self._content_clip = QRect()
        self._protected_ui_region = QRegion()
        self._canvas_width = 1
        self._canvas_height = 1
        self.setObjectName("skin_extension")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not IS_WINDOWS)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

    def set_runtime(self, runtime) -> None:
        self._runtime = runtime
        self._renderer = runtime.renderer if runtime is not None else None
        self.sync_geometry()

    def sync_geometry(self) -> None:
        if self._renderer is None or not self.panel.isVisible():
            self.hide()
            return
        width = max(1, self.panel.container.width())
        height = max(1, self.panel.container.height())
        content = QRectF(0, 0, width, height)
        bounds = self._renderer.get_stable_skin_bounds(width, height)
        if bounds.isEmpty() or content.contains(bounds):
            self.hide()
            return
        window_rect = bounds.adjusted(-2, -2, 2, 2).toAlignedRect()
        if window_rect.isEmpty():
            self.hide()
            return
        content_origin = self.panel.container.mapToGlobal(QPoint(0, 0))
        self._paint_origin = QPointF(-window_rect.left(), -window_rect.top())
        self._content_clip = QRect(
            -window_rect.left(), -window_rect.top(), width, height
        )
        self._canvas_width = width
        self._canvas_height = height
        geometry = QRect(
            content_origin.x() + window_rect.left(),
            content_origin.y() + window_rect.top(),
            window_rect.width(),
            window_rect.height(),
        )
        panel_origin = self.panel.mapToGlobal(QPoint(0, 0))
        self._protected_ui_region = QRegion(QRect(
            panel_origin.x() - geometry.x(),
            panel_origin.y() - geometry.y(),
            self.panel.width(),
            self.panel.height(),
        ))
        if self.geometry() != geometry:
            self.setGeometry(geometry)
        if not self.isVisible():
            self.show()
            self.raise_()
        self.update()

    def paintEvent(self, event) -> None:
        del event
        if self._renderer is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            outside = QRegion(self.rect()).subtracted(self._protected_ui_region)
            painter.setClipRegion(outside)
            painter.translate(self._paint_origin)
            self._renderer.render(painter, self._canvas_width, self._canvas_height)
        finally:
            if painter.isActive():
                painter.end()

    def _layer_at_local(self, point: QPointF):
        if self._renderer is None or self._protected_ui_region.contains(point.toPoint()):
            return None
        scene_point = point - self._paint_origin
        if self._runtime is not None and hasattr(self._runtime, "hit_test"):
            return self._runtime.hit_test(
                scene_point, self._canvas_width, self._canvas_height
            )
        return self._renderer.layer_at(
            scene_point, self._canvas_width, self._canvas_height, alpha_test=True
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        layer = self._layer_at_local(event.position())
        button_names = {
            Qt.LeftButton: "left", Qt.RightButton: "right", Qt.MiddleButton: "middle",
        }
        button_name = button_names.get(event.button())
        if layer is not None and button_name and self._runtime is not None:
            self.panel.skin_clicked.emit(layer.id, button_name)
            if hasattr(self._runtime, "on_layer_clicked"):
                self._runtime.on_layer_clicked(layer.id, button_name)
        if event.button() == Qt.RightButton:
            self.panel.context_menu_requested.emit(event.globalPosition().toPoint())
        event.accept()

    def nativeEvent(self, event_type, message):
        if IS_WINDOWS:
            try:
                import ctypes
                import ctypes.wintypes
                native_message = ctypes.wintypes.MSG.from_address(int(message))
                if native_message.message == 0x0084:
                    local = self.mapFromGlobal(QCursor.pos())
                    if self._layer_at_local(QPointF(local)) is None:
                        return True, -1
                    return True, 1
            except (TypeError, ValueError, OSError):
                pass
        return super().nativeEvent(event_type, message)


class SubtitlePanel(QWidget):
    """沉浸式无边框字幕窗口 v4。"""

    _text_appended = Signal(str, bool, str, object)  # (text, is_final, source, spk_id)
    # 译文到达：(原文, 译文, source)。翻译后台线程完成后 emit，主线程槽写进 view_trans
    _translation_appended = Signal(str, str, str)
    hide_requested = Signal()
    quit_requested = Signal()
    theme_changed = Signal(str)  # 主题切换信号
    context_menu_requested = Signal(QPoint)  # 全局坐标；右键任意位置时发出
    skin_clicked = Signal(str, str)
    preview_state_changed = Signal()
    # 新 spk_id 发现：每当字幕流里出现新的 spk_id，外部 editor 会收到这个信号去自动加一行。
    spk_id_seen = Signal(str, int)  # (source, spk_id)

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
        self._translation_appended.connect(self._on_translation_appended)
        self._drag_offset: QPoint | None = None
        self._font_size = ui_cfg.font_size or 22
        self._skin_runtime = None
        self._grabbing_skin_background = False

        # 说话人区分：每个 source 一份 SpeakerNameMap，跨会话持久化显示名。
        # 改回默认名 = 删除条目（display 回退「说话人 N」）。
        self._speaker_names: dict[str, SpeakerNameMap] = {
            "system": SpeakerNameMap("system", self),
            "mic": SpeakerNameMap("mic", self),
        }
        # 已发现的 spk_id 集合（按 source 分组），用于 spk_id_seen 信号去重
        self._seen_spk_ids: dict[str, set[int]] = {"system": set(), "mic": set()}

        # 应用配置中的主题
        theme_name = ui_cfg.theme or "Dark"
        self._theme_mgr.apply_theme(theme_name)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(getattr(ui_cfg, "toolbar_hide_delay_ms", 800))
        self._hide_timer.timeout.connect(self._hide_overlays)

        # P0 性能：字幕追加节流。emit_text 累积到 _pending（按到达顺序的列表，
        # 每条 (text, is_final, source)），16ms 定时器到点一次性 flush，
        # 把每秒 10+ 次插入降到 ~60fps 上限。双源时两路文本在此串行化，互不撕裂。
        # 每条 (text, is_final, source, spk_id)；spk_id 来自说话人区分引擎（funasr+cam++），
        # 开启说话人区分时由 panel 调用 SpeakerNameMap.display(spk_id) 解析为显示名。
        self._pending: list[tuple[str, bool, str, object]] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(16)
        self._flush_timer.timeout.connect(self._flush_pending_text)

        # 自动分行器：识别到句末标点或引擎边界时换行。只作用于字幕显示，
        # 不影响 skin 触发器（skin 走原始文本路径）。
        self._line_breaker = LineBreaker(enabled=self.ui_cfg.line_break_enabled)
        # 段尾换行要等下一条非空字幕到来时才实际插入，否则 QTextDocument 会保留
        # 一个空白末段，滚动到底后最后一条字幕会被顶到倒数第二行。
        self._deferred_line_break = False
        # interim 覆盖：按 (source, spk_id) 维护"当前未定稿的中间结果"在文档里的绝对
        # 位置区间 [start, end)（QChar 单位）。partial 覆盖它（实时逐字 + 跨次纠错），
        # final 定稿（清区间，文本永久）。详见 _insert_item。
        self._interim: dict[tuple[str, object], tuple[int, int]] = {}
        # 拆分模式（仅 funasr nano WSL 流式）：_split_sources 里的 source 走"按句号拆——
        # 历史可纠错、当前句 append-only 不纠错"。其余 source 走上面的 interim 覆盖（原状）。
        self._split_sources: set[str] = set()
        # 拆分模式专属状态（按 key=(source,spk_id)，只 split 模式的 key 进这些表）：
        # _regions 三元组 (start,split,end)：[start,split) 历史(可纠错)、[split,end) 当前(append-only)
        self._regions: dict[tuple[str, object], tuple[int, int, int]] = {}
        self._hist_sentences: dict[tuple[str, object], list[str]] = {}
        self._finalized_count: dict[tuple[str, object], int] = {}
        self._cur_text: dict[tuple[str, object], str] = {}

        # 译文流专属状态：与原文流完全独立（不共享 _interim / _regions）。
        # 译文是"定稿后才来"的完整句子，走简单追加 + 句号分行模型，无需 interim 覆盖。
        self._trans_line_breaker = LineBreaker(enabled=self.ui_cfg.line_break_enabled)
        self._trans_deferred_break = False

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

        # 字幕下层贴图：背景框、身体等装饰。
        self.underlay_layer = OverlayLayer("below_text", self.container)
        self.underlay_layer.setObjectName("skin_underlay")

        # 字幕文本区
        self.view = _SubtitleView(self.container)
        theme = self._theme_mgr.current
        geo = theme.geometry
        font_family = self.ui_cfg.font_family or geo.font_family
        self.view.setFont(QFont(font_family, self._font_size))
        self.view.setPlaceholderText("点击 ▶ 开始，播放任意视频/音频，字幕会实时出现……")

        # 译文 view：开启翻译时显示在原文下方，独立 interim + 独立行管理，
        # 与原文各行出字、互不干扰。默认隐藏（翻译关闭 = 单行原状）。
        self.view_trans = _SubtitleView(self.container)
        self.view_trans.setObjectName("subtitle_trans")
        trans_scale = float(getattr(self.ui_cfg, "translation_font_scale", 0.85) or 0.85)
        self._trans_font_size = max(4, int(round(self._font_size * trans_scale)))
        self.view_trans.setFont(QFont(font_family, self._trans_font_size))
        self.view_trans.hide()   # 翻译未启用时隐藏，不占布局空间
        # 运行期标记：译文区当前是否可见（不落盘，纯实例属性）
        self._translation_visible = False

        # 字幕上层贴图：耳朵、尾巴和前景装饰。
        self.overlay_layer = OverlayLayer("above_text", self.container)
        self.overlay_layer.setObjectName("skin_overlay")
        self.skin_extension = SkinExtensionWindow(self)

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

        # 麦克风设备下拉（与"声音源"分离：麦克风是独立输入路径，互不干扰）
        self.mic_combo = _PopupCombo()
        self.mic_combo.addItem("（默认麦克风）", None)
        try:
            for d in list_microphone_devices():
                label = d.name + (" [默认]" if d.is_default_output else "")
                self.mic_combo.addItem(label, d.name)
        except Exception as e:
            print(f"[ui] 枚举麦克风设备失败: {e}")

        # 两路输入源开关（checkable）。运行中禁用，下次开始识别生效（与 device_combo 一致）。
        # 首启默认：电脑声音开、麦克风关（保持老用户体验）。
        self.sys_toggle = QPushButton("🔊 机:开")
        self.sys_toggle.setCheckable(True)
        self.sys_toggle.setChecked(True)
        self.mic_toggle = QPushButton("🎤 麦:关")
        self.mic_toggle.setCheckable(True)
        self.mic_toggle.setChecked(False)

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
        self._add_expanding(tb, self.sys_toggle)
        # 麦克风开关 + 设备下拉（仅当麦克风启用时可选设备）
        tb.addWidget(self.mic_toggle)
        self._mic_combo_container = QWidget()
        mc = QHBoxLayout(self._mic_combo_container)
        mc.setContentsMargins(0, 0, 0, 0)
        mc.setSpacing(4)
        self._mic_label = QLabel("麦：")
        mc.addWidget(self._mic_label)
        mc.addWidget(self.mic_combo)
        tb.addWidget(self._mic_combo_container)
        self.mic_combo.setEnabled(self.mic_toggle.isChecked())
        self._mic_combo_container.setVisible(self.mic_toggle.isChecked())
        self.sys_toggle.toggled.connect(self._on_sys_toggle)
        self.mic_toggle.toggled.connect(self._on_mic_toggle)
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

        # ---- 状态栏（overlay，浮在 container 底部）----
        # 之前是 addLayout(bottom) → view 被往上顶，状态栏显隐会让字幕位置跳一下。
        # 改成 overlay：view 始终占满整个 container 高度，状态栏显隐不影响字幕位置。
        # 关键：parent 必须是 self.container（不是 self），坐标系才和 grip 一致；
        # 不要设 WA_TranslucentBackground —— 在 child of translucent parent 上会触发
        # "提升为顶层窗口" 的副作用，setGeometry 会被解读为全局坐标，状态栏飘到桌面左上角。
        # QLabel 默认背景就是透明的（没 QSS 背景），不需要这个 flag。
        self.status_label = QLabel("就绪", self.container)
        self.status_label.setObjectName("status")

        # ---- 缩放手柄（也是 overlay，child of container）----
        self.grip = QSizeGrip(self.container)
        self.grip.setObjectName("grip")
        self.grip.setFixedSize(16, 16)

        # 组装 container —— 原文 view 撑满；译文 view（隐藏时不占位）在下方
        container_layout.addWidget(self.view, 3)
        container_layout.addWidget(self.view_trans, 2)
        self.underlay_layer.lower()
        self.view.raise_()
        self.view_trans.raise_()
        self.overlay_layer.raise_()
        self.status_label.raise_()
        self.grip.raise_()
        # status_label + grip 在 _layout_window 里绝对定位（不 add 到 layout）

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
        if obj in getattr(self, "_filtered_children", ()) and event.type() == QEvent.MouseButtonPress:
            panel_pos = self.mapFromGlobal(obj.mapToGlobal(event.pos()))
            self._dispatch_skin_click(panel_pos, event.button())
            if event.button() == Qt.RightButton:
                self.context_menu_requested.emit(obj.mapToGlobal(event.pos()))
                return True
        return super().eventFilter(obj, event)

    def _dispatch_skin_click(self, panel_pos: QPoint, button) -> None:
        if not self._skin_runtime or not self._skin_runtime.has_click_triggers():
            return
        button_names = {
            Qt.LeftButton: "left", Qt.RightButton: "right", Qt.MiddleButton: "middle",
        }
        button_name = button_names.get(button)
        if not button_name:
            return
        local = self.container.mapFrom(self, panel_pos)
        if not self.container.rect().contains(local):
            return
        layer = self._skin_runtime.hit_test(
            QPointF(local), self.container.width(), self.container.height()
        )
        if layer is not None:
            self.skin_clicked.emit(layer.id, button_name)
            self._skin_runtime.on_layer_clicked(layer.id, button_name)

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
        # 译文区 padding：左右与原文对齐，上下减半（两行更紧凑）；颜色按 translation_color
        trans_color = getattr(self.ui_cfg, "translation_color", "") or colors.subtitle_text
        self.view_trans.setStyleSheet(f"""
            QTextEdit {{
                background-color: transparent;
                color: {trans_color};
                border: none;
                padding: {max(2, pad_t // 2)}px {pad_r}px {max(2, pad_b // 2)}px {pad_l}px;
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
            self.preview_state_changed.emit()

    def set_theme_obj(self, theme: Theme):
        """直接应用 Theme 对象（用于实时预览）。"""
        self._theme_mgr.apply_theme_obj(theme)
        self._apply_theme()

    # ---------- 字体/字号 ----------
    def _apply_font(self):
        try:
            self.view.setFont(QFont(self.ui_cfg.font_family, self._font_size))
            # 译文字号联动（按 translation_font_scale 缩放）
            scale = float(getattr(self.ui_cfg, "translation_font_scale", 0.85) or 0.85)
            self._trans_font_size = max(4, int(round(self._font_size * scale)))
            self.view_trans.setFont(QFont(self.ui_cfg.font_family, self._trans_font_size))
        except Exception as e:
            print(f"[ui] 应用字体失败: {e}")

    def _on_font_activated(self, index: int):
        font = self.font_combo.currentFont()
        self.ui_cfg.font_family = font.family()
        self._apply_font()
        self._notify_geometry()
        if not self._grabbing_skin_background:
            self.preview_state_changed.emit()

    def _change_font_size(self, delta: int):
        # 最小 4，与全局设置里 font_size_spin 的下界对齐（之前是 12）
        self._font_size = max(4, min(56, self._font_size + delta))
        self._apply_font()
        self.ui_cfg.font_size = self._font_size
        self._notify_geometry()
        self.preview_state_changed.emit()

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
        if not self._grabbing_skin_background:
            self.preview_state_changed.emit()
        self.pin_btn.setText("📌 已置顶" if pinned else "📌 未置顶")
        self.ui_cfg.always_on_top = pinned
        self.skin_extension.sync_geometry()
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
        self._dispatch_skin_click(e.pos(), e.button())
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_offset is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._drag_offset is not None:
            self._drag_offset = None
            self._notify_geometry()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "skin_extension"):
            self.skin_extension.sync_geometry()

    def wheelEvent(self, e):
        bar = self.view.verticalScrollBar()
        # macOS 触控板产生细粒度 momentum-scroll（angleDelta 常 <120，如 ±2/±8），
        # 整除 120 会得到 0 导致滚不动。改成"有方向至少 1 步"，保证两平台都能滚。
        delta_y = e.angleDelta().y()
        steps = delta_y // 120 if abs(delta_y) >= 120 else (1 if delta_y > 0 else (-1 if delta_y < 0 else 0))
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
        # 同步皮肤层尺寸
        self.underlay_layer.setGeometry(self.container.rect())
        self.overlay_layer.setGeometry(self.container.rect())
        if not self._grabbing_skin_background:
            self.preview_state_changed.emit()
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
            # 皮肤层跟随 container
            self.underlay_layer.setGeometry(0, 0, content_w, content_h)
            self.overlay_layer.setGeometry(0, 0, content_w, content_h)
            # 状态栏 + 缩放手柄：浮在 container 底部（不占 view 空间）
            self._layout_bottom_overlay(content_w, content_h)
            if hasattr(self, "skin_extension"):
                self.skin_extension.sync_geometry()
        finally:
            self._laying_out = False

    def _layout_bottom_overlay(self, content_w: int, content_h: int) -> None:
        """把 status_label + grip 浮在 container 底部（坐标系：container 局部）。"""
        # 缩放手柄：右下角
        self.grip.move(content_w - 16 - 4, content_h - 16 - 4)
        # 状态栏：底部贴底，左对齐，宽度留出 grip 位置
        # 关键安全：content_w 可能很小（30px 最小窗口），要给个最小宽度
        status_h = self.status_label.sizeHint().height() or 18
        status_w = max(40, content_w - 32 - 16 - 4)   # 最少 40px，避免 0 宽
        status_x = 10
        # 坐标：container 内的局部坐标（status_label 是 container 的 child）
        self.status_label.setGeometry(
            status_x,                    # 左边距
            content_h - status_h - 2,    # 贴底（留 2px 边距）
            status_w,
            status_h,
        )
        # 状态栏和 grip 都 raise_ 到最上层，确保不被 view 盖住
        self.status_label.raise_()
        self.grip.raise_()

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
    def emit_text(self, text: str, is_final: bool, source: str = "system", spk_id=None):
        """跨线程安全：只 emit 信号。Qt 会用 QueuedConnection 把调用送到主线程。
        绝不能在这里直接操作 QTimer/QWidget（它们属于主线程），否则 PySide6 报
        'startTimer from another thread'。"""
        self._text_appended.emit(text, is_final, source, spk_id)

    def emit_translation(self, orig: str, trans: str, source: str = "system"):
        """跨线程安全：译文到达时调（翻译后台线程）。orig 是原文（用于按 source 着色/对齐），
        trans 是译文。主线程槽 _on_translation_appended 写进 view_trans。"""
        self._translation_appended.emit(orig, trans, source)

    def _on_translation_appended(self, orig: str, trans: str, source: str = "system"):
        """主线程槽：把译文写进 view_trans（与原文流独立，互不干扰）。

        译文是"定稿后才来"的完整句子，走简单追加 + 句号分行模型，不需要 interim 覆盖
        （翻译不会逐字纠错）。每次到一条译文追加一行，与原文滚动联动。
        """
        if not trans or not trans.strip():
            return
        # 按 line_breaker 给译文按句末标点分行（与原文同规则，独立实例）
        processed = self._trans_line_breaker.feed(trans, is_final=True)
        ends_with_break = processed.endswith("\n")
        if ends_with_break:
            processed = processed[:-1]

        bar = self.view_trans.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - bar.singleStep()
        saved_pos = bar.value()
        doc = self.view_trans.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.End)
        if self._trans_deferred_break:
            cursor.insertText("\n")
            self._trans_deferred_break = False
        color = self._translation_color()
        prefix = self._speaker_prefix(source, None)  # 译文不带说话人前缀（保持简洁）
        for i, seg in enumerate(processed.split("\n")):
            if i > 0:
                cursor.insertText("\n")
            if seg:
                safe = html.escape(seg)
                this_prefix = prefix if i == 0 else ""
                cursor.insertHtml(f'<span style="color:{color};">{this_prefix}{safe}</span>')
        # 译文区 trim（与原文同阈值，独立 doc）
        self._trim_trans_if_needed(doc, cursor)
        cursor.endEditBlock()

        self._trans_deferred_break = ends_with_break
        # 滚动锚定（与原文一致策略）
        if getattr(self.ui_cfg, "lock_scroll_to_bottom", False) or at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(saved_pos)

    def _translation_color(self) -> str:
        """译文颜色：translation_color 优先，否则跟随主题文本色（略浅让原文更突出）。"""
        custom = getattr(self.ui_cfg, "translation_color", "") or ""
        if custom:
            return custom
        theme = self._theme_mgr.current if self._theme_mgr else None
        return theme.colors.subtitle_text if theme else "#cccccc"

    def _trim_trans_if_needed(self, doc, cursor) -> None:
        """译文区字符上限裁剪（独立于原文 _trim_if_needed）。"""
        max_chars = getattr(self.ui_cfg, "max_chars", 20000)
        total = doc.characterCount()
        if total <= max_chars:
            return
        keep = int(max_chars * 0.9)
        D = total - keep
        c = QTextCursor(doc)
        c.movePosition(QTextCursor.Start)
        c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, D)
        c.removeSelectedText()

    def _on_text_appended(self, text: str, is_final: bool, source: str = "system", spk_id=None):
        """主线程槽：累积到 buffer + 节流 flush（这里操作 QTimer 安全）。"""
        # 说话人区分：发现新 spk_id → 通知外部 editor 自动加一行
        if spk_id is not None and isinstance(spk_id, int):
            seen = self._seen_spk_ids.setdefault(source, set())
            if spk_id not in seen:
                seen.add(spk_id)
                self.spk_id_seen.emit(source, spk_id)
        self._pending.append((text, is_final, source, spk_id))
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending_text(self):
        """定时器到点：把累积的文本一次性插入文档（interim 覆盖语义）。

        is_final=False（partial，当前句完整中间文本）覆盖当前 interim 区间（实时逐字
        + 跨次纠错），is_final=True（final）定稿（清区间、文本永久）。按 (source, spk_id)
        维护多条 interim，支持双源/多说话人交错。详见 _insert_item。
        """
        if not self._pending:
            return
        items = self._pending
        self._pending = []
        # 合并：同 (source, spk_id) 且都 is_final=False 的连续项只保留最后一条（累积型
        # partial，最后者最全；原"拼接"在覆盖语义下会堆积）。其余不合并（各自独立处理）。
        merged: list[tuple[str, bool, str, object]] = []
        for text, is_final, source, spk_id in items:
            if (
                merged
                and merged[-1][2] == source
                and merged[-1][3] == spk_id
                and not is_final
                and not merged[-1][1]
            ):
                merged[-1] = (text, is_final, source, spk_id)   # partial: 后者覆盖前者
            else:
                merged.append((text, is_final, source, spk_id))

        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - bar.singleStep()
        saved_pos = bar.value()
        doc = self.view.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        for text, is_final, source, spk_id in merged:
            if not text:
                continue
            if source in self._split_sources:
                # 拆分模式（funasr nano 流式）：历史纠错 + 当前句 append-only（句号换行）
                key = (source, spk_id)
                if is_final:
                    self._handle_final(cursor, doc, key, source, spk_id, text)
                else:
                    self._handle_partial(cursor, doc, key, source, spk_id, text)
            else:
                # 原状：interim 覆盖（commit=False=partial 写回区间，True=final 定稿不写回）
                self._insert_item(cursor, doc, source, spk_id, text, is_final,
                                  commit=is_final)
            self._trim_if_needed()
        cursor.endEditBlock()
        # 滚动锚定（原样）
        if getattr(self.ui_cfg, "lock_scroll_to_bottom", False):
            bar.setValue(bar.maximum())
        elif at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(saved_pos)
        if not self._grabbing_skin_background:
            self.preview_state_changed.emit()

    def _doc_end(self, doc) -> int:
        """文档最后一个可插入位置（characterCount 含末尾 block 分隔符，-1）。"""
        return doc.characterCount() - 1

    def _shift_after(self, boundary_pos: int, delta: int) -> None:
        """boundary_pos 之后的所有区间点整体 +delta（原状 _interim + 拆分 _regions 三元组）。

        用于中间区间覆盖/插入后其后方区间平移（删旧插新净变化 delta）。基于旧坐标
        +delta = 新坐标（其它 key 的旧坐标在本次删/插后净偏移正好是 delta）。
        """
        if delta == 0:
            return
        for key, (s, e) in list(self._interim.items()):
            if s >= boundary_pos:
                self._interim[key] = (s + delta, e + delta)
        for key, (s, sp, e) in list(self._regions.items()):
            self._regions[key] = (
                s + delta if s >= boundary_pos else s,
                sp + delta if sp >= boundary_pos else sp,
                e + delta if e >= boundary_pos else e,
            )

    def _insert_item(self, cursor, doc, source, spk_id, text, is_final, commit: bool) -> None:
        """把一条文本插入文档：命中 interim 区间则覆盖，否则追加。

        commit=False（partial）：插入后把新区间写回 _interim[key]，下次同 key partial
        覆盖它（实时逐字 + 跨次纠错）。
        commit=True（final）：同样覆盖原区间（权威文本替换临时），但**不写回**→区间清空、
        文本永久；该 key 下条 partial 命中不到 → 走追加起新行。

        用 cursor.position() 在 insertHtml 前后快照记录区间（避 len(text)≠QChar 数陷阱：
        span 颜色不占字符位；项目禁 emoji，partial 全 BMP）。is_endmost 必须在删旧区间
        之前判（删完 characterCount 已变）。deferred_line_break 规则：尾换行落在末尾→
        deferred，落在中间→立即落盘（不动 deferred，它属于末尾别的 key）。
        """
        key = (source, spk_id)
        # partial（commit=False）**不过 LineBreaker**：当前句保持单段，避免标点断成多段
        # 导致覆盖时段数变化、位置跳动（用户反馈"最后一句跳到上一行"——partial 含句号
        # 时断成多行，新增内容把前段顶到上一行）。超长由 QTextEdit 自动软换行（视觉换行，
        # 不改文档段落，不跳）。final（commit=True）过 LineBreaker：定稿句按标点分历史
        # 多行 + 尾换行（定稿一次性，跳一次可接受）。布局：partial 独占最后一行实时纠错，
        # 定稿后该文本留在原位成为历史行、新 partial 另起一行。
        if commit:
            processed = self._line_breaker.feed(text, is_final)
        else:
            processed = text or ""
        ends_with_break = processed.endswith("\n")
        if ends_with_break:
            processed = processed[:-1]
        segments = processed.split("\n")
        color = self._source_style(source)
        prefix = self._speaker_prefix(source, spk_id)

        rng = self._interim.get(key)
        if rng is not None:
            # 覆盖路径：删旧 interim，原地重插
            start, end = rng
            is_endmost = (end == self._doc_end(doc))   # 删前判（删完 characterCount 已变）
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()                # cursor 塌缩到 start
            cursor.setPosition(start)
            materialize_trailing = ends_with_break and not is_endmost
        else:
            # 追加路径：到末尾，先补 deferred 换行（分隔已有内容）
            cursor.movePosition(QTextCursor.End)
            if self._deferred_line_break:
                cursor.insertText("\n")
                self._deferred_line_break = False
            is_endmost = True
            materialize_trailing = False

        start_pos = cursor.position()
        prefix_emitted = False
        for i, seg in enumerate(segments):
            if i > 0:
                cursor.insertText("\n")
            if seg:
                this_prefix = prefix if not prefix_emitted else ""
                prefix_emitted = True
                safe = html.escape(seg)
                cursor.insertHtml(f'<span style="color:{color};">{this_prefix}{safe}</span>')
        if materialize_trailing:
            cursor.insertText("\n")
        end_pos = cursor.position()

        # 维护其它 key 的区间（覆盖路径使旧 end 之后整体平移 delta = 新end - 旧end）
        if rng is not None:
            self._shift_after(rng[1], end_pos - rng[1])
        # deferred 只由"落在文档末尾"的操作更新
        if is_endmost:
            self._deferred_line_break = ends_with_break
        # partial 写回区间（下次覆盖）；final 定稿清区间（文本永久）
        if not commit:
            self._interim[key] = (start_pos, end_pos)
        elif rng is not None:
            self._interim.pop(key, None)

    # ---------- 拆分模式（仅 _split_sources 里的 source：funasr nano WSL 流式）----------
    # 按"最后一个句末标点"拆 partial：前缀（含标点，历史，可纠错覆盖）+ 后缀（当前句，
    # append-only 不纠错）。句号出现→旧当前定稿进历史、新句起最底行。布局：历史在上、
    # 当前句独占最底行实时增长（不跳）。详见 plan 文档。

    _SENTENCE_END = frozenset("。！？!?…")   # 与 line_breaker._SENTENCE_END 同源

    def set_split_sources(self, names: set[str]) -> None:
        """设置走拆分逻辑的 source 名集合（app._on_started 调，funasr nano 流式）。"""
        self._split_sources = set(names)

    def _split_sentences(self, text: str) -> list[str]:
        """按句末标点切句，标点归前句。'A。B！' -> ['A。', 'B！']；无标点 -> [text]。"""
        out, buf = [], []
        for ch in text:
            buf.append(ch)
            if ch in self._SENTENCE_END:
                out.append("".join(buf)); buf = []
        if buf:
            out.append("".join(buf))
        return out

    def _split_partial(self, text: str) -> tuple[list[str], str]:
        """partial -> (历史句列表, 当前句 suffix)。最后一个句末标点（含）前=历史、后=当前。"""
        idx = max((i for i, ch in enumerate(text) if ch in self._SENTENCE_END), default=-1)
        if idx == -1:
            return ([], text)
        return (self._split_sentences(text[:idx + 1]), text[idx + 1:])

    def _drop_split_key(self, key) -> None:
        """清理一个 split key 的所有状态（trim 区间腐败时）。"""
        self._regions.pop(key, None)
        self._hist_sentences.pop(key, None)
        self._finalized_count.pop(key, None)
        self._cur_text.pop(key, None)

    def _doc_ends_with_break(self, doc) -> bool:
        c = QTextCursor(doc); c.movePosition(QTextCursor.End)
        c.movePosition(QTextCursor.PreviousCharacter, QTextCursor.KeepAnchor)
        return c.selectedText() in ("\n", " ")

    def _insert_html_segments(self, cursor, text, source, spk_id, with_prefix: bool) -> None:
        """把含 \\n 的 text 作为着色 html 插入；with_prefix 时仅首段带 [说话人] 前缀。"""
        color = self._source_style(source)
        prefix = self._speaker_prefix(source, spk_id) if with_prefix else ""
        prefix_emitted = not with_prefix
        for i, seg in enumerate(text.split("\n")):
            if i > 0:
                cursor.insertText("\n")
            if seg:
                this_prefix = prefix if not prefix_emitted else ""
                prefix_emitted = True
                cursor.insertHtml(f'<span style="color:{color};">{this_prefix}{html.escape(seg)}</span>')

    def _create_span(self, cursor, doc, key, source, spk_id, sentences, suffix) -> None:
        """首次建档：在文档末尾建 [历史+当前] 连续区间。"""
        cursor.movePosition(QTextCursor.End)
        if doc.characterCount() > 1 and not self._doc_ends_with_break(doc):
            cursor.insertText("\n")          # 跨 key 分隔符（不纳入区间，位于 start 之前）
        start = cursor.position()
        hist = "\n".join(sentences)
        if hist:
            self._insert_html_segments(cursor, hist, source, spk_id, with_prefix=True)
        split = cursor.position()
        if suffix:
            if sentences:
                cursor.insertText("\n")      # 历史↔当前分隔符（归当前子区间头部）
            self._insert_html_segments(cursor, suffix, source, spk_id, with_prefix=not sentences)
        end = cursor.position()
        self._regions[key] = (start, split, end)
        self._hist_sentences[key] = list(sentences)
        self._finalized_count[key] = 0
        self._cur_text[key] = suffix

    def _rebuild_history(self, cursor, key, sentences, source, spk_id) -> None:
        """历史子区间 [start,split) 整段重建（删旧重插，可纠错）。"""
        start, split, end = self._regions[key]
        if split > start:
            cursor.setPosition(start)
            cursor.setPosition(split, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
        cursor.setPosition(start)
        hist = "\n".join(sentences)
        if hist:
            self._insert_html_segments(cursor, hist, source, spk_id, with_prefix=True)
        new_split = cursor.position()
        self._shift_after(split, new_split - split)
        _, _, end2 = self._regions[key]
        self._regions[key] = (start, new_split, end2)
        self._hist_sentences[key] = list(sentences)

    def _set_current(self, cursor, key, new_suffix, source, spk_id) -> None:
        """重置当前子区间 [split,end)：删旧 + 插 sep+new_suffix（句号定稿后起新句）。"""
        start, split, end = self._regions[key]
        if end > split:
            cursor.setPosition(split)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
        cursor.setPosition(split)
        if new_suffix and self._hist_sentences.get(key):
            cursor.insertText("\n")
        if new_suffix:
            self._insert_html_segments(cursor, new_suffix, source, spk_id, with_prefix=False)
        new_end = cursor.position()
        if new_end - end:
            self._shift_after(end, new_end - end)
        self._regions[key] = (start, split, new_end)
        self._cur_text[key] = new_suffix

    def _append_current(self, cursor, key, delta_text, source, spk_id) -> None:
        """当前句追加新增字（在 end 处插，append-only 不纠错）。"""
        start, split, end = self._regions[key]
        cursor.setPosition(end)
        if end == split and self._hist_sentences.get(key):
            cursor.insertText("\n")          # 当前原空且有历史 → 先补分隔符
        self._insert_html_segments(cursor, delta_text, source, spk_id, with_prefix=False)
        new_end = cursor.position()
        if new_end - end:
            self._shift_after(end, new_end - end)
        self._regions[key] = (start, split, new_end)
        self._cur_text[key] = self._cur_text.get(key, "") + delta_text

    def _clear_current(self, cursor, key) -> None:
        """定稿当前句：清当前子区间 [split,end)。"""
        start, split, end = self._regions[key]
        if end > split:
            cursor.setPosition(split)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            self._shift_after(end, -(end - split))
        self._regions[key] = (start, split, split)
        self._cur_text[key] = ""

    def _dedup_tail(self, permanent, final_sentences):
        if not permanent or not final_sentences:
            return final_sentences
        k = len(final_sentences)
        return [] if (len(permanent) >= k and permanent[-k:] == final_sentences) else final_sentences

    def _handle_partial(self, cursor, doc, key, source, spk_id, text) -> None:
        prefix_sentences, suffix = self._split_partial(text)
        if key not in self._regions:
            self._create_span(cursor, doc, key, source, spk_id, prefix_sentences, suffix)
            return
        finalized = self._finalized_count.get(key, 0)
        old_sentences = self._hist_sentences.get(key, [])
        old_live = old_sentences[finalized:]
        old_cur = self._cur_text.get(key, "")
        new_sentences = old_sentences[:finalized] + prefix_sentences
        live_grew = len(prefix_sentences) > len(old_live)   # 句号把旧当前定稿进历史
        if new_sentences != old_sentences:
            self._rebuild_history(cursor, key, new_sentences, source, spk_id)
        if live_grew:
            self._set_current(cursor, key, suffix, source, spk_id)
        elif suffix == old_cur:
            pass                                            # 不变
        elif suffix.startswith(old_cur):
            self._append_current(cursor, key, suffix[len(old_cur):], source, spk_id)
        else:
            pass                                            # 纠错/回退：保护当前句不动

    def _handle_final(self, cursor, doc, key, source, spk_id, text) -> None:
        finalized = self._finalized_count.get(key, 0)
        if key not in self._regions:
            sents = [s for s in self._split_sentences(text.strip()) if s]
            self._create_span(cursor, doc, key, source, spk_id, sents, "")
            self._finalized_count[key] = len(self._hist_sentences.get(key, []))
            return
        permanent = self._hist_sentences.get(key, [])[:finalized]
        final_sents = self._dedup_tail(permanent, [s for s in self._split_sentences(text.strip()) if s])
        new_sentences = permanent + final_sents
        self._rebuild_history(cursor, key, new_sentences, source, spk_id)
        self._clear_current(cursor, key)
        self._finalized_count[key] = len(new_sentences)

    def set_status(self, text: str, color: str | None = None):
        self.status_label.setText(text)

    # ---------- 字幕插入文档（实际渲染，主线程）----------
    def _source_style(self, source: str) -> str:
        """返回来源颜色。电脑声音跟随主题文本色，麦克风用 mic_color。"""
        if source == "mic":
            return getattr(self.ui_cfg, "mic_color", None) or "#5aa9ff"
        # system / 未知来源：用主题文本色
        theme = self._theme_mgr.current if self._theme_mgr else None
        return theme.colors.subtitle_text if theme else "#f2f2f2"

    def _speaker_prefix(self, source: str, spk_id) -> str:
        """根据 spk_id 渲染 [显示名] 前缀（HTML 片段）。

        - spk_id 为 None → 空（不开说话人区分的源）
        - spk_id 有值 → [显示名] 颜色块，显示名由 self._speaker_names 查
          （每个 source 独立一份 SpeakerNameMap 实例）
        返回的是已 escape 后的 HTML 字符串（不含外层 span，颜色由调用方统一控制）。
        """
        if spk_id is None:
            return ""
        smap = self._speaker_names.get(source)
        if smap is None:
            return ""
        name = smap.display(spk_id)
        # 用半角方括号 + 空格分隔，方便眼睛扫读；颜色继承调用方的 span
        return f"[{html.escape(name)}] "

    def _insert_text(self, text: str, source: str = "system"):
        """单条文本插入（保留给皮肤编辑器等老调用方；主字幕走 _flush_pending_text）。"""
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - bar.singleStep()
        saved_pos = bar.value()
        color = self._source_style(source)
        safe = html.escape(text)
        snippet = f'<span style="color:{color};">{safe}</span>'
        cursor = QTextCursor(self.view.document())
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        cursor.insertHtml(snippet)
        self._trim_if_needed()
        cursor.endEditBlock()
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
        D = total - keep
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, D)
        cursor.removeSelectedText()
        # 头部删了 D 字符 → 修正所有区间（原状 _interim + 拆分 _regions）
        for key, (s, e) in list(self._interim.items()):
            if e <= D:                      # 整段被删
                del self._interim[key]
            elif s < D:                     # 部分重叠 → 区间腐败，丢弃（该 interim 重建）
                del self._interim[key]
            else:
                self._interim[key] = (s - D, e - D)
        for key, (s, sp, e) in list(self._regions.items()):
            if e <= D or s < D:             # 整段被删 / 部分重叠 → 丢弃管理（文本留壳）
                self._drop_split_key(key)
            else:
                self._regions[key] = (s - D, sp - D, e - D)

    # ---------- 按钮 ----------
    def _on_clear(self):
        self.view.clear()
        self._interim.clear()
        self._deferred_line_break = False
        self._regions.clear()
        self._hist_sentences.clear()
        self._finalized_count.clear()
        self._cur_text.clear()
        # 同步清译文区
        self.view_trans.clear()
        self._trans_deferred_break = False

    def closeEvent(self, e):
        if getattr(self, "_force_quit", False):
            self._notify_geometry()
            super().closeEvent(e)
        else:
            e.ignore()
            self._on_close()

    def _on_start(self):
        dev = self.device_combo.currentData()
        mic_dev = self.mic_combo.currentData() if self.mic_toggle.isChecked() else None
        sys_on = self.sys_toggle.isChecked()
        mic_on = self.mic_toggle.isChecked()
        # 至少要开一路
        if not sys_on and not mic_on:
            self.set_status("请至少开启一路输入源（电脑声音或麦克风）")
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.device_combo.setEnabled(False)
        self.mic_combo.setEnabled(False)
        self.sys_toggle.setEnabled(False)
        self.mic_toggle.setEnabled(False)
        self.set_status("启动中……")
        if self.on_start:
            try:
                self.on_start(dev, mic_dev, sys_enabled=sys_on, mic_enabled=mic_on)
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
        self.mic_combo.setEnabled(self.mic_toggle.isChecked())
        self.sys_toggle.setEnabled(True)
        self.mic_toggle.setEnabled(True)

    def _on_sys_toggle(self, checked: bool):
        self.sys_toggle.setText("🔊 机:开" if checked else "🔊 机:关")

    def _on_mic_toggle(self, checked: bool):
        self.mic_toggle.setText("🎤 麦:开" if checked else "🎤 麦:关")
        # 麦克风关闭时隐藏设备下拉，减少工具栏占用
        self.mic_combo.setEnabled(checked)
        self._mic_combo_container.setVisible(checked)

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
        self.skin_extension.hide()
        self.hide()
        self.hide_requested.emit()

    def showEvent(self, event):
        super().showEvent(event)
        self.skin_extension.sync_geometry()
        if self._skin_runtime:
            self._skin_runtime.on_window_shown()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.skin_extension.hide()
        if self._skin_runtime:
            self._skin_runtime.on_window_hidden()

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

    def set_skin_runtime(self, runtime):
        self._skin_runtime = runtime
        self.underlay_layer.set_runtime(runtime)
        self.overlay_layer.set_runtime(runtime)
        self.skin_extension.set_runtime(runtime)
        self.update_skin_layers()

    def update_skin_layers(self):
        self.underlay_layer.update()
        self.overlay_layer.update()
        self.skin_extension.sync_geometry()

    def grab_skin_background(self):
        if self._grabbing_skin_background:
            return self.container.grab()
        self._grabbing_skin_background = True
        underlay_visible = self.underlay_layer.isVisible()
        overlay_visible = self.overlay_layer.isVisible()
        try:
            self.underlay_layer.hide()
            self.overlay_layer.hide()
            return self.container.grab()
        finally:
            self.underlay_layer.setVisible(underlay_visible)
            self.overlay_layer.setVisible(overlay_visible)
            self._grabbing_skin_background = False

    def get_lock_scroll(self) -> bool:
        return getattr(self.ui_cfg, "lock_scroll_to_bottom", False)

    def get_transcript(self) -> str:
        return self.view.toPlainText()

    def get_devices(self):
        out = []
        for i in range(self.device_combo.count()):
            out.append((self.device_combo.itemText(i), self.device_combo.itemData(i)))
        return out

    def get_mic_devices(self):
        """麦克风设备列表（供设置对话框用）。"""
        out = []
        for i in range(self.mic_combo.count()):
            out.append((self.mic_combo.itemText(i), self.mic_combo.itemData(i)))
        return out

    def get_source_states(self) -> tuple[bool, bool]:
        """返回 (电脑声音启用, 麦克风启用) —— 当前工具栏开关状态。"""
        return self.sys_toggle.isChecked(), self.mic_toggle.isChecked()

    def set_source_states(self, sys_enabled: bool, mic_enabled: bool) -> None:
        """同步两路开关状态（设置对话框/启动时用，不触发重复信号风暴）。"""
        self.sys_toggle.blockSignals(True)
        self.mic_toggle.blockSignals(True)
        try:
            self.sys_toggle.setChecked(bool(sys_enabled))
            self.mic_toggle.setChecked(bool(mic_enabled))
            self.sys_toggle.setText("🔊 机:开" if sys_enabled else "🔊 机:关")
            self.mic_toggle.setText("🎤 麦:开" if mic_enabled else "🎤 麦:关")
            self.mic_combo.setEnabled(bool(mic_enabled))
            self._mic_combo_container.setVisible(bool(mic_enabled))
        finally:
            self.sys_toggle.blockSignals(False)
            self.mic_toggle.blockSignals(False)

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
        self.preview_state_changed.emit()

    def set_font_size(self, size: int):
        # 最小 4，与工具栏 A-/A+ 按钮的下界对齐
        self._font_size = max(4, min(72, int(size)))
        self.ui_cfg.font_size = self._font_size
        self._apply_font()
        self.preview_state_changed.emit()

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
        self.preview_state_changed.emit()

    def set_pin(self, pinned: bool):
        if self.get_pin() != pinned:
            self._toggle_pin()

    def set_window_size(self, w: int, h: int):
        w = max(self.minimumWidth(), int(w))
        h = max(self.minimumHeight(), int(h))
        self._content_h = h
        toolbar_height = self.toolbar.sizeHint().height() if self.toolbar.isVisible() else 0
        self.resize(w, h + toolbar_height)
        self._layout_window()
        if not self._grabbing_skin_background:
            self.preview_state_changed.emit()
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
        self._interim.clear()
        self._deferred_line_break = False
        self._regions.clear()
        self._hist_sentences.clear()
        self._finalized_count.clear()
        self._cur_text.clear()
        # 同步清译文区
        self.view_trans.clear()
        self._trans_deferred_break = False

    def set_toolbar_hide_delay(self, ms: int):
        self.ui_cfg.toolbar_hide_delay_ms = int(ms)
        self._hide_timer.setInterval(int(ms))

    def set_max_chars(self, n: int):
        self.ui_cfg.max_chars = int(n)

    def set_line_break(self, enabled: bool):
        """开关自动分行。即时生效（下次 flush 按新设置），无需重渲染已有文本。"""
        self.ui_cfg.line_break_enabled = bool(enabled)
        self._line_breaker.set_enabled(bool(enabled))

    def get_line_break(self) -> bool:
        return self.ui_cfg.line_break_enabled

    # ---------- 翻译 ----------
    def set_translation_enabled(self, enabled: bool) -> None:
        """开关译文区显示。enabled=True 显示 view_trans（双行），False 隐藏（单行原状）。
        不清空已有译文——重新开启时历史译文仍在。"""
        self._translation_visible = bool(enabled)
        self.view_trans.setVisible(bool(enabled))

    def set_translation_style(self, font_scale: float | None = None,
                              color: str | None = None) -> None:
        """更新译文字号缩放 / 颜色（设置对话框实时预览用）。"""
        if font_scale is not None:
            self.ui_cfg.translation_font_scale = float(font_scale)
            self._apply_font()   # _apply_font 内会按 scale 重算 _trans_font_size
        if color is not None:
            self.ui_cfg.translation_color = color
            self._apply_theme()  # 重套 QSS 让译文颜色生效

    def set_translation_line_break(self, enabled: bool) -> None:
        """译文是否按句末标点分行（与原文 line_break 独立开关，默认跟随）。"""
        self._trans_line_breaker.set_enabled(bool(enabled))

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
        """程序化启动（设置对话框的"开始"按钮用）。走和工具栏 _on_start 一致的双源逻辑：
        device_name 显式传入时只影响电脑声音设备；麦克风与两路开关仍读工具栏当前状态。"""
        if device_name is not None:
            # 临时把指定设备选进下拉，让 _on_start 读到
            idx = self.device_combo.findData(device_name)
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)
        self._on_start()

    def stop_recognition(self):
        self._on_stop()
