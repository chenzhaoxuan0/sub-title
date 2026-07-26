"""桌宠皮肤编辑器 —— 可视化贴图摆放 + 图层管理 + 关键帧时间轴。

面向 AE/剪辑用户的交互逻辑：
- 左侧：图层列表（拖拽排序、显隐、锁定）
- 中间：画布（可视化摆放贴图、拖拽移动、缩放手柄）
- 右侧：属性面板（选中图层的变换参数 + 关键帧打点）
- 底部：时间轴（关键帧菱形标记、播放头、播放/暂停）
- 顶部：工具栏（导入图片、添加图层、保存、触发器管理）
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    Qt, QPoint, QPointF, QRectF, QTimer, Signal, QSize,
)
from PySide6.QtGui import (
    QPainter, QPixmap, QColor, QPen, QBrush, QFont, QMouseEvent,
    QWheelEvent, QKeyEvent, QTransform, QImage, QAction,
)
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QSpinBox,
    QDoubleSpinBox, QSlider, QComboBox, QFileDialog, QMessageBox,
    QGroupBox, QFormLayout, QCheckBox, QToolBar,
    QScrollArea, QFrame, QSizePolicy, QInputDialog, QMenu,
    QAbstractItemView, QApplication, QStatusBar,
)

from ..config import Config
from .model import (
    SkinDefinition, Layer, Keyframe, PropertyTrack, Interpolation,
    AnimationAction, Trigger, TriggerType, ANIMATABLE_PROPERTIES,
)
from .renderer import SkinRenderer
from .events import TriggerManager


# ============================================================
# 画布 —— 可视化贴图摆放
# ============================================================

class SkinCanvas(QWidget):
    """皮肤编辑画布：显示字幕区背景 + 所有图层，支持拖拽/选中/缩放。"""

    layer_selected = Signal(str)  # layer_id
    layer_moved = Signal(str, float, float)  # layer_id, x, y
    canvas_clicked = Signal()  # 点击空白处（取消选中）

    def __init__(self, skin: SkinDefinition, base_dir: Path, parent=None):
        super().__init__(parent)
        self._skin = skin
        self._base_dir = base_dir
        self._renderer = SkinRenderer(skin, base_dir)
        self._selected_layer_id: Optional[str] = None
        self._drag_offset: Optional[QPointF] = None
        self._current_time: float = 0.0
        self._show_grid: bool = True
        self._grid_size: int = 8
        self._zoom: float = 1.0

        self.setMinimumSize(400, 200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_skin(self, skin: SkinDefinition):
        self._skin = skin
        self._renderer.skin = skin
        self.update()

    def set_time(self, t: float):
        self._current_time = t
        self._renderer.set_time(t)
        self.update()

    def select_layer(self, layer_id: Optional[str]):
        self._selected_layer_id = layer_id
        self.update()

    def set_grid(self, show: bool, size: int):
        self._show_grid = show
        self._grid_size = size
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        w, h = self.width(), self.height()

        # 背景（模拟字幕区）
        painter.fillRect(0, 0, w, h, QColor("#1a1a1a"))

        # 网格
        if self._show_grid:
            painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
            for x in range(0, w, self._grid_size):
                painter.drawLine(x, 0, x, h)
            for y in range(0, h, self._grid_size):
                painter.drawLine(0, y, w, y)

        # 中心辅助线
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.DashLine))
        painter.drawLine(w // 2, 0, w // 2, h)
        painter.drawLine(0, h // 2, w, h // 2)

        # 渲染图层
        self._renderer.set_time(self._current_time)
        self._renderer.render(painter, w, h)

        # 选中图层的边框 + 手柄
        if self._selected_layer_id:
            layer = self._skin.get_layer_by_id(self._selected_layer_id)
            if layer:
                bounds = self._renderer.get_layer_bounds(layer, self._current_time)
                if not bounds.isEmpty():
                    painter.setPen(QPen(QColor("#4fc3f7"), 2, Qt.DashLine))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(bounds)
                    # 四角手柄
                    handle_size = 8
                    painter.setBrush(QColor("#4fc3f7"))
                    painter.setPen(Qt.NoPen)
                    corners = [
                        bounds.topLeft(), bounds.topRight(),
                        bounds.bottomLeft(), bounds.bottomRight(),
                    ]
                    for c in corners:
                        painter.drawRect(
                            int(c.x()) - handle_size // 2,
                            int(c.y()) - handle_size // 2,
                            handle_size, handle_size,
                        )

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            pos = QPointF(event.pos())
            # 从顶到底检测点击了哪个图层
            for layer in reversed(self._skin.layers):
                if not layer.visible or layer.locked:
                    continue
                bounds = self._renderer.get_layer_bounds(layer, self._current_time)
                if bounds.contains(pos):
                    self._selected_layer_id = layer.id
                    self._drag_offset = pos - QPointF(layer.x, layer.y)
                    self.layer_selected.emit(layer.id)
                    self.update()
                    return
            # 点击空白
            self._selected_layer_id = None
            self.canvas_clicked.emit()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_offset is not None and self._selected_layer_id:
            pos = QPointF(event.pos())
            layer = self._skin.get_layer_by_id(self._selected_layer_id)
            if layer and not layer.locked:
                new_x = pos.x() - self._drag_offset.x()
                new_y = pos.y() - self._drag_offset.y()
                # 网格吸附
                if self._show_grid:
                    new_x = round(new_x / self._grid_size) * self._grid_size
                    new_y = round(new_y / self._grid_size) * self._grid_size
                layer.x = new_x
                layer.y = new_y
                self.layer_moved.emit(layer.id, new_x, new_y)
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_offset = None


# ============================================================
# 时间轴 —— 关键帧编辑
# ============================================================

class TimelineWidget(QWidget):
    """时间轴：显示播放头、关键帧菱形标记、图层轨道。"""

    time_changed = Signal(float)
    play_state_changed = Signal(bool)

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self._skin = skin
        self._current_time: float = 0.0
        self._playing: bool = False
        self._selected_layer_id: Optional[str] = None
        self._selected_prop: Optional[str] = None
        self._px_per_second: float = 60.0  # 每秒像素数

        self.setMinimumHeight(160)
        self.setMouseTracking(True)

        # 播放定时器
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(1000 // skin.fps)
        self._play_timer.timeout.connect(self._on_play_tick)

    def set_skin(self, skin: SkinDefinition):
        self._skin = skin
        self._play_timer.setInterval(1000 // skin.fps)
        self.update()

    def select_layer(self, layer_id: Optional[str], prop: Optional[str] = None):
        self._selected_layer_id = layer_id
        self._selected_prop = prop
        self.update()

    def set_time(self, t: float):
        self._current_time = max(0, min(t, self._skin.total_duration))
        self.time_changed.emit(self._current_time)
        self.update()

    def toggle_play(self):
        self._playing = not self._playing
        if self._playing:
            self._play_timer.start()
        else:
            self._play_timer.stop()
        self.play_state_changed.emit(self._playing)

    def stop_play(self):
        self._playing = False
        self._play_timer.stop()
        self._current_time = 0
        self.play_state_changed.emit(False)
        self.update()

    def _on_play_tick(self):
        self._current_time += 1.0 / self._skin.fps
        if self._current_time >= self._skin.total_duration:
            self._current_time = 0  # 循环
        self.time_changed.emit(self._current_time)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 背景
        painter.fillRect(0, 0, w, h, QColor("#1e1e2e"))

        # 时间刻度
        ruler_h = 24
        painter.fillRect(0, 0, w, ruler_h, QColor("#2a2a3a"))
        painter.setPen(QPen(QColor("#888"), 1))
        font = QFont("Consolas", 8)
        painter.setFont(font)
        total_secs = self._skin.total_duration
        for sec in range(int(total_secs) + 1):
            x = int(sec * self._px_per_second) + 40
            if x > w:
                break
            painter.drawLine(x, ruler_h - 8, x, ruler_h)
            painter.drawText(x + 2, ruler_h - 10, f"{sec}s")

        # 图层轨道
        track_y = ruler_h + 4
        track_h = 28
        for layer in self._skin.layers:
            # 轨道背景
            is_selected = layer.id == self._selected_layer_id
            bg = QColor("#3a3a5a") if is_selected else QColor("#2a2a3a")
            painter.fillRect(0, track_y, w, track_h, bg)

            # 图层名
            painter.setPen(QColor("#ccc"))
            painter.drawText(4, track_y + track_h // 2 + 4, layer.name[:12])

            # 关键帧菱形
            for prop_name, track in layer.tracks.items():
                for kf in track.keyframes:
                    kx = int(kf.time * self._px_per_second) + 40
                    ky = track_y + track_h // 2
                    # 菱形
                    painter.setPen(Qt.NoPen)
                    color = QColor("#ffd700") if is_selected else QColor("#aaa")
                    painter.setBrush(color)
                    diamond = [
                        QPointF(kx, ky - 5),
                        QPointF(kx + 5, ky),
                        QPointF(kx, ky + 5),
                        QPointF(kx - 5, ky),
                    ]
                    painter.drawPolygon(diamond)

            track_y += track_h + 2

        # 播放头（红色竖线）
        playhead_x = int(self._current_time * self._px_per_second) + 40
        painter.setPen(QPen(QColor("#ff4444"), 2))
        painter.drawLine(playhead_x, 0, playhead_x, h)
        # 播放头三角
        painter.setBrush(QColor("#ff4444"))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon([
            QPointF(playhead_x - 5, 0),
            QPointF(playhead_x + 5, 0),
            QPointF(playhead_x, 8),
        ])

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        """点击时间轴设置播放头位置。"""
        x = event.pos().x() - 40
        t = max(0, x / self._px_per_second)
        self.set_time(t)


# ============================================================
# 属性面板
# ============================================================

class PropertyPanel(QWidget):
    """选中图层的属性编辑面板 + 关键帧打点。"""

    property_changed = Signal(str, str, float)  # layer_id, prop, value
    keyframe_added = Signal(str, str, float, float)  # layer_id, prop, time, value
    keyframe_removed = Signal(str, str, float)  # layer_id, prop, time

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer: Optional[Layer] = None
        self._current_time: float = 0.0
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.title_label = QLabel("未选中图层")
        self.title_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        layout.addWidget(self.title_label)

        # 变换属性
        g_transform = QGroupBox("变换")
        f = QFormLayout(g_transform)

        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(-2000, 2000)
        self.x_spin.setSuffix(" px")
        self.x_spin.valueChanged.connect(lambda v: self._on_prop_changed("x", v))
        f.addRow("X：", self.x_spin)

        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(-2000, 2000)
        self.y_spin.setSuffix(" px")
        self.y_spin.valueChanged.connect(lambda v: self._on_prop_changed("y", v))
        f.addRow("Y：", self.y_spin)

        self.sx_spin = QDoubleSpinBox()
        self.sx_spin.setRange(0.01, 10.0)
        self.sx_spin.setSingleStep(0.1)
        self.sx_spin.valueChanged.connect(lambda v: self._on_prop_changed("scale_x", v))
        f.addRow("缩放 X：", self.sx_spin)

        self.sy_spin = QDoubleSpinBox()
        self.sy_spin.setRange(0.01, 10.0)
        self.sy_spin.setSingleStep(0.1)
        self.sy_spin.valueChanged.connect(lambda v: self._on_prop_changed("scale_y", v))
        f.addRow("缩放 Y：", self.sy_spin)

        self.rot_spin = QDoubleSpinBox()
        self.rot_spin.setRange(-360, 360)
        self.rot_spin.setSuffix("°")
        self.rot_spin.valueChanged.connect(lambda v: self._on_prop_changed("rotation", v))
        f.addRow("旋转：", self.rot_spin)

        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.valueChanged.connect(lambda v: self._on_prop_changed("opacity", v))
        f.addRow("不透明度：", self.opacity_spin)

        layout.addWidget(g_transform)

        # 关键帧操作
        g_kf = QGroupBox("关键帧")
        kf_layout = QVBoxLayout(g_kf)

        self.kf_time_label = QLabel("时间: 0.00s")
        kf_layout.addWidget(self.kf_time_label)

        kf_btns = QHBoxLayout()
        self.add_kf_btn = QPushButton("◆ 添加关键帧")
        self.add_kf_btn.setToolTip("在当前时间为所有属性添加关键帧")
        self.add_kf_btn.clicked.connect(self._on_add_keyframe)
        kf_btns.addWidget(self.add_kf_btn)

        self.del_kf_btn = QPushButton("◇ 删除关键帧")
        self.del_kf_btn.clicked.connect(self._on_remove_keyframe)
        kf_btns.addWidget(self.del_kf_btn)
        kf_layout.addLayout(kf_btns)

        # 插值选择
        interp_row = QHBoxLayout()
        interp_row.addWidget(QLabel("插值："))
        self.interp_combo = QComboBox()
        for interp in Interpolation:
            self.interp_combo.addItem(interp.value, interp)
        interp_row.addWidget(self.interp_combo)
        kf_layout.addLayout(interp_row)

        layout.addWidget(g_kf)
        layout.addStretch(1)

    def set_layer(self, layer: Optional[Layer]):
        self._layer = layer
        if layer is None:
            self.title_label.setText("未选中图层")
            return
        self.title_label.setText(f"图层: {layer.name}")
        self._sync_spins()

    def set_time(self, t: float):
        self._current_time = t
        self.kf_time_label.setText(f"时间: {t:.2f}s")

    def _sync_spins(self):
        """从图层同步值到 spin boxes。"""
        if not self._layer:
            return
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.sx_spin.blockSignals(True)
        self.sy_spin.blockSignals(True)
        self.rot_spin.blockSignals(True)
        self.opacity_spin.blockSignals(True)

        self.x_spin.setValue(self._layer.x)
        self.y_spin.setValue(self._layer.y)
        self.sx_spin.setValue(self._layer.scale_x)
        self.sy_spin.setValue(self._layer.scale_y)
        self.rot_spin.setValue(self._layer.rotation)
        self.opacity_spin.setValue(self._layer.opacity)

        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)
        self.sx_spin.blockSignals(False)
        self.sy_spin.blockSignals(False)
        self.rot_spin.blockSignals(False)
        self.opacity_spin.blockSignals(False)

    def _on_prop_changed(self, prop: str, value: float):
        if self._layer:
            setattr(self._layer, prop, value)
            self.property_changed.emit(self._layer.id, prop, value)

    def _on_add_keyframe(self):
        """在当前时间为所有属性添加关键帧。"""
        if not self._layer:
            return
        interp = self.interp_combo.currentData()
        for prop in ANIMATABLE_PROPERTIES:
            value = getattr(self._layer, prop, 0.0)
            track = self._layer.get_track(prop)
            kf = Keyframe(time=self._current_time, value=value, interpolation=interp)
            track.add_keyframe(kf)
            self.keyframe_added.emit(self._layer.id, prop, self._current_time, value)

    def _on_remove_keyframe(self):
        """删除当前时间附近的关键帧。"""
        if not self._layer:
            return
        for prop in ANIMATABLE_PROPERTIES:
            if prop in self._layer.tracks:
                if self._layer.tracks[prop].remove_keyframe_at(self._current_time):
                    self.keyframe_removed.emit(self._layer.id, prop, self._current_time)


# ============================================================
# 图层面板
# ============================================================

class LayerPanel(QWidget):
    """图层列表面板。"""

    layer_selected = Signal(str)
    layer_visibility_changed = Signal(str, bool)
    layer_order_changed = Signal()

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self._skin = skin
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QHBoxLayout()
        header.addWidget(QLabel("图层"))
        self.add_layer_btn = QPushButton("+")
        self.add_layer_btn.setFixedWidth(28)
        self.add_layer_btn.setToolTip("添加图层（导入图片）")
        header.addWidget(self.add_layer_btn)
        self.del_layer_btn = QPushButton("-")
        self.del_layer_btn.setFixedWidth(28)
        self.del_layer_btn.setToolTip("删除选中图层")
        header.addWidget(self.del_layer_btn)
        layout.addLayout(header)

        self.layer_list = QListWidget()
        self.layer_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.layer_list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self.layer_list)

        self.refresh()

    def set_skin(self, skin: SkinDefinition):
        self._skin = skin
        self.refresh()

    def refresh(self):
        self.layer_list.clear()
        # 从顶到底显示（反转）
        for layer in reversed(self._skin.layers):
            icon = "👁" if layer.visible else "🚫"
            lock = "🔒" if layer.locked else ""
            item = QListWidgetItem(f"{icon} {layer.name} {lock}")
            item.setData(Qt.UserRole, layer.id)
            self.layer_list.addItem(item)

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        item = self.layer_list.item(row)
        if item:
            layer_id = item.data(Qt.UserRole)
            self.layer_selected.emit(layer_id)

    def select_layer(self, layer_id: str):
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            if item.data(Qt.UserRole) == layer_id:
                self.layer_list.setCurrentRow(i)
                break


# ============================================================
# 触发器面板
# ============================================================

class TriggerPanel(QWidget):
    """触发器/事件管理面板。"""

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self._skin = skin
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QHBoxLayout()
        header.addWidget(QLabel("触发器"))
        self.add_trigger_btn = QPushButton("+")
        self.add_trigger_btn.setFixedWidth(28)
        header.addWidget(self.add_trigger_btn)
        layout.addLayout(header)

        self.trigger_list = QListWidget()
        layout.addWidget(self.trigger_list)

        # 触发器编辑
        g_edit = QGroupBox("触发器设置")
        f = QFormLayout(g_edit)

        self.type_combo = QComboBox()
        for tt in TriggerType:
            self.type_combo.addItem(tt.value, tt)
        f.addRow("类型：", self.type_combo)

        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.5, 60.0)
        self.interval_spin.setSuffix(" s")
        f.addRow("间隔：", self.interval_spin)

        self.action_combo = QComboBox()
        f.addRow("触发动作：", self.action_combo)

        self.enabled_check = QCheckBox("启用")
        self.enabled_check.setChecked(True)
        f.addRow(self.enabled_check)

        layout.addWidget(g_edit)
        self.refresh()

    def set_skin(self, skin: SkinDefinition):
        self._skin = skin
        self.refresh()

    def refresh(self):
        self.trigger_list.clear()
        for trigger in self._skin.triggers:
            icon = "✓" if trigger.enabled else "✗"
            self.trigger_list.addItem(f"{icon} {trigger.name} [{trigger.trigger_type.value}]")
        # 刷新动作列表
        self.action_combo.clear()
        for action in self._skin.actions:
            self.action_combo.addItem(action.name)


# ============================================================
# 主编辑器窗口
# ============================================================

class SkinEditorWindow(QMainWindow):
    """桌宠皮肤编辑器主窗口。"""

    def __init__(self, cfg: Config, panel, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.panel = panel
        self._base_dir = Path(__file__).resolve().parents[2] / "skins"
        self._base_dir.mkdir(parents=True, exist_ok=True)

        # 加载或创建皮肤
        self._skin = SkinDefinition(name="新皮肤", fps=cfg.skin.animation_fps)
        self._current_file: Optional[Path] = None

        self.setWindowTitle("桌宠皮肤编辑器 - sub-title")
        self.resize(1200, 800)
        self._init_ui()
        self._init_toolbar()
        self._init_statusbar()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # 主分割器：左(图层) | 中(画布) | 右(属性)
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：图层面板 + 触发器
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.layer_panel = LayerPanel(self._skin)
        self.trigger_panel = TriggerPanel(self._skin)
        left_layout.addWidget(self.layer_panel, 2)
        left_layout.addWidget(self.trigger_panel, 1)
        left_widget.setMaximumWidth(220)
        splitter.addWidget(left_widget)

        # 中间：画布
        self.canvas = SkinCanvas(self._skin, self._base_dir)
        splitter.addWidget(self.canvas)

        # 右侧：属性面板
        self.prop_panel = PropertyPanel()
        self.prop_panel.setMaximumWidth(260)
        splitter.addWidget(self.prop_panel)

        splitter.setSizes([200, 600, 260])
        main_layout.addWidget(splitter, 3)

        # 底部：时间轴
        self.timeline = TimelineWidget(self._skin)
        main_layout.addWidget(self.timeline, 1)

        # 连接信号
        self.layer_panel.layer_selected.connect(self._on_layer_selected)
        self.canvas.layer_selected.connect(self._on_layer_selected)
        self.canvas.canvas_clicked.connect(lambda: self._on_layer_selected(None))
        self.canvas.layer_moved.connect(self._on_layer_moved)
        self.timeline.time_changed.connect(self._on_time_changed)
        self.prop_panel.property_changed.connect(self._on_property_changed)
        self.prop_panel.keyframe_added.connect(self._on_keyframe_added)
        self.prop_panel.keyframe_removed.connect(self._on_keyframe_removed)
        self.layer_panel.add_layer_btn.clicked.connect(self._on_add_layer)
        self.layer_panel.del_layer_btn.clicked.connect(self._on_delete_layer)

    def _init_toolbar(self):
        tb = QToolBar("工具")
        self.addToolBar(tb)

        act_import = QAction("📁 导入图片", self)
        act_import.triggered.connect(self._on_add_layer)
        tb.addAction(act_import)

        act_save = QAction("💾 保存", self)
        act_save.triggered.connect(self._on_save)
        tb.addAction(act_save)

        act_save_as = QAction("💾 另存为", self)
        act_save_as.triggered.connect(self._on_save_as)
        tb.addAction(act_save_as)

        act_open = QAction("📂 打开", self)
        act_open.triggered.connect(self._on_open)
        tb.addAction(act_open)

        tb.addSeparator()

        act_play = QAction("▶ 播放", self)
        act_play.triggered.connect(self.timeline.toggle_play)
        tb.addAction(act_play)

        act_stop = QAction("■ 停止", self)
        act_stop.triggered.connect(self.timeline.stop_play)
        tb.addAction(act_stop)

        tb.addSeparator()

        act_preview = QAction("👁 预览到字幕", self)
        act_preview.triggered.connect(self._on_preview)
        tb.addAction(act_preview)

    def _init_statusbar(self):
        self.statusBar().showMessage("就绪")

    # ---------- 图层操作 ----------
    def _on_add_layer(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择贴图图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;所有文件 (*)"
        )
        if not path:
            return
        # 复制图片到皮肤目录
        import shutil
        src = Path(path)
        dst = self._base_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)

        layer = Layer(
            name=src.stem,
            image_path=src.name,
            x=self.canvas.width() // 2 - 50,
            y=self.canvas.height() // 2 - 50,
        )
        self._skin.layers.append(layer)
        self.layer_panel.refresh()
        self.canvas.update()
        self._on_layer_selected(layer.id)
        self.statusBar().showMessage(f"已添加图层: {layer.name}")

    def _on_delete_layer(self):
        row = self.layer_panel.layer_list.currentRow()
        if row < 0:
            return
        item = self.layer_panel.layer_list.item(row)
        layer_id = item.data(Qt.UserRole)
        self._skin.layers = [l for l in self._skin.layers if l.id != layer_id]
        self.layer_panel.refresh()
        self.canvas.update()
        self.prop_panel.set_layer(None)

    def _on_layer_selected(self, layer_id: Optional[str]):
        if layer_id is None:
            self.prop_panel.set_layer(None)
            self.canvas.select_layer(None)
            self.timeline.select_layer(None)
            return
        layer = self._skin.get_layer_by_id(layer_id)
        if layer:
            self.prop_panel.set_layer(layer)
            self.canvas.select_layer(layer_id)
            self.timeline.select_layer(layer_id)
            self.layer_panel.select_layer(layer_id)

    def _on_layer_moved(self, layer_id: str, x: float, y: float):
        self.prop_panel._sync_spins()

    def _on_time_changed(self, t: float):
        self.canvas.set_time(t)
        self.prop_panel.set_time(t)

    def _on_property_changed(self, layer_id: str, prop: str, value: float):
        self.canvas.update()

    def _on_keyframe_added(self, layer_id: str, prop: str, time: float, value: float):
        self.timeline.update()
        self.canvas.update()
        self.statusBar().showMessage(f"关键帧已添加: {prop} @ {time:.2f}s = {value:.2f}")

    def _on_keyframe_removed(self, layer_id: str, prop: str, time: float):
        self.timeline.update()
        self.statusBar().showMessage(f"关键帧已删除: {prop} @ {time:.2f}s")

    # ---------- 文件操作 ----------
    def _on_save(self):
        if self._current_file:
            self._skin.save(self._current_file)
            self.statusBar().showMessage(f"已保存: {self._current_file.name}")
        else:
            self._on_save_as()

    def _on_save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存皮肤", str(self._base_dir / f"{self._skin.name}.json"),
            "皮肤文件 (*.json)"
        )
        if path:
            self._current_file = Path(path)
            self._skin.save(self._current_file)
            self.statusBar().showMessage(f"已保存: {path}")

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开皮肤", str(self._base_dir),
            "皮肤文件 (*.json)"
        )
        if path:
            try:
                self._skin = SkinDefinition.load(Path(path))
                self._current_file = Path(path)
                self._refresh_all()
                self.statusBar().showMessage(f"已打开: {path}")
            except Exception as e:
                QMessageBox.warning(self, "打开失败", str(e))

    def _on_preview(self):
        """将当前皮肤应用到字幕面板预览。"""
        try:
            self.panel.overlay_layer.set_renderer(
                SkinRenderer(self._skin, self._base_dir)
            )
            self.panel.overlay_layer.update()
            self.statusBar().showMessage("已应用到字幕面板预览")
        except Exception as e:
            self.statusBar().showMessage(f"预览失败: {e}")

    def _refresh_all(self):
        self.layer_panel.set_skin(self._skin)
        self.trigger_panel.set_skin(self._skin)
        self.canvas.set_skin(self._skin)
        self.timeline.set_skin(self._skin)
        self.prop_panel.set_layer(None)
