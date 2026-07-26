"""Visual editor for responsive subtitle decoration skins."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from ..config import Config
from .editor_canvas import SkinCanvas
from .editor_timeline import ActionTimeline
from .model import (
    ANIMATABLE_PROPERTIES, AnimationClip, AssetType, HorizontalPin,
    Interpolation, Keyframe, Layer, LayerPlane, SkinDefinition, Trigger,
    TriggerType, VerticalPin,
)
from .package import (
    create_skin_directory, export_skin_package, import_skin_package,
    peek_skin_package, safe_name, skins_root,
)


class LayerPanel(QWidget):
    selected = Signal(str)
    changed = Signal()
    add_static_requested = Signal()
    add_sequence_requested = Signal()
    duplicate_requested = Signal()
    delete_requested = Signal()

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self.skin = skin
        self._refreshing = False
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        for text, signal in (
            ("+图片", self.add_static_requested), ("+序列", self.add_sequence_requested),
            ("复制", self.duplicate_requested), ("删除", self.delete_requested),
        ):
            button = QPushButton(text)
            button.clicked.connect(signal.emit)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemChanged.connect(self._item_changed)
        self.list.model().rowsMoved.connect(lambda *args: self._sync_order())
        layout.addWidget(self.list)
        self.refresh()

    def set_skin(self, skin: SkinDefinition) -> None:
        self.skin = skin
        self.refresh()

    def refresh(self, selected_id: str = "") -> None:
        self._refreshing = True
        self.list.clear()
        for layer in reversed(self.skin.layers):
            prefix = "上" if layer.plane == LayerPlane.ABOVE_TEXT else "下"
            item = QListWidgetItem(f"[{prefix}] {layer.name}")
            item.setData(Qt.UserRole, layer.id)
            item.setCheckState(Qt.Checked if layer.visible else Qt.Unchecked)
            item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled)
            self.list.addItem(item)
            if layer.id == selected_id:
                self.list.setCurrentItem(item)
        self._refreshing = False

    def selected_id(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def select(self, layer_id: str) -> None:
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.UserRole) == layer_id:
                self.list.setCurrentItem(item)
                return

    def _selected(self, current, previous) -> None:
        del previous
        if current:
            self.selected.emit(current.data(Qt.UserRole))

    def _item_changed(self, item: QListWidgetItem) -> None:
        if self._refreshing:
            return
        layer = self.skin.get_layer_by_id(item.data(Qt.UserRole))
        if layer:
            layer.visible = item.checkState() == Qt.Checked
            text = item.text()
            layer.name = text.split("] ", 1)[-1].strip() or layer.name
            self.changed.emit()

    def _sync_order(self) -> None:
        if self._refreshing:
            return
        ids = [self.list.item(index).data(Qt.UserRole) for index in range(self.list.count())]
        lookup = {layer.id: layer for layer in self.skin.layers}
        self.skin.layers = [lookup[layer_id] for layer_id in reversed(ids) if layer_id in lookup]
        self.changed.emit()


class ActionPanel(QWidget):
    selected = Signal(str)
    changed = Signal()
    add_requested = Signal()
    duplicate_requested = Signal()
    delete_requested = Signal()

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self.skin = skin
        self._refreshing = False
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        for text, signal in (
            ("新建", self.add_requested), ("复制", self.duplicate_requested),
            ("删除", self.delete_requested),
        ):
            button = QPushButton(text)
            button.clicked.connect(signal.emit)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemChanged.connect(self._renamed)
        layout.addWidget(self.list)
        group = QGroupBox("动作设置")
        form = QFormLayout(group)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.05, 120)
        self.duration.setSuffix(" s")
        self.priority = QSpinBox()
        self.priority.setRange(-100, 100)
        self.loop = QCheckBox("循环")
        self.loop_count = QSpinBox()
        self.loop_count.setRange(1, 999)
        self.interruptible = QCheckBox("允许高优先级动作打断")
        self.cooldown = QDoubleSpinBox()
        self.cooldown.setRange(0, 3600)
        self.cooldown.setSuffix(" s")
        form.addRow("时长", self.duration)
        form.addRow("优先级", self.priority)
        form.addRow(self.loop)
        form.addRow("循环次数", self.loop_count)
        form.addRow(self.interruptible)
        form.addRow("动作冷却", self.cooldown)
        layout.addWidget(group)
        for widget in (self.duration, self.priority, self.loop, self.loop_count,
                       self.interruptible, self.cooldown):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._save)
            else:
                widget.valueChanged.connect(self._save)
        self.refresh()

    def set_skin(self, skin: SkinDefinition) -> None:
        self.skin = skin
        self.refresh()

    def selected_action(self) -> Optional[AnimationClip]:
        item = self.list.currentItem()
        return self.skin.get_action_by_id(item.data(Qt.UserRole)) if item else None

    def selected_id(self) -> str:
        action = self.selected_action()
        return action.id if action else ""

    def select(self, action_id: str) -> None:
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.UserRole) == action_id:
                self.list.setCurrentItem(item)
                return

    def refresh(self, selected_id: str = "") -> None:
        self._refreshing = True
        self.list.clear()
        base = QListWidgetItem("基础状态（无动作）")
        base.setData(Qt.UserRole, "")
        self.list.addItem(base)
        for action in self.skin.actions:
            item = QListWidgetItem(action.name)
            item.setData(Qt.UserRole, action.id)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.list.addItem(item)
            if action.id == selected_id:
                self.list.setCurrentItem(item)
        if not selected_id:
            self.list.setCurrentRow(0)
        self._refreshing = False
        self._load_form()

    def _selected(self, current, previous) -> None:
        del previous
        self._load_form()
        if current:
            self.selected.emit(current.data(Qt.UserRole))

    def _renamed(self, item: QListWidgetItem) -> None:
        if self._refreshing:
            return
        action = self.skin.get_action_by_id(item.data(Qt.UserRole) or "")
        if action and item.text().strip():
            action.name = item.text().strip()
            self.changed.emit()

    def _load_form(self) -> None:
        action = self.selected_action()
        self._refreshing = True
        enabled = action is not None
        for widget in (self.duration, self.priority, self.loop, self.loop_count,
                       self.interruptible, self.cooldown):
            widget.setEnabled(enabled)
        if action:
            self.duration.setValue(action.duration)
            self.priority.setValue(action.priority)
            self.loop.setChecked(action.loop)
            self.loop_count.setValue(action.loop_count)
            self.interruptible.setChecked(action.interruptible)
            self.cooldown.setValue(action.cooldown)
        self._refreshing = False

    def _save(self, *args) -> None:
        del args
        if self._refreshing:
            return
        action = self.selected_action()
        if action:
            action.duration = self.duration.value()
            action.priority = self.priority.value()
            action.loop = self.loop.isChecked()
            action.loop_count = self.loop_count.value()
            action.interruptible = self.interruptible.isChecked()
            action.cooldown = self.cooldown.value()
            self.changed.emit()


class TriggerPanel(QWidget):
    changed = Signal()
    test_requested = Signal(str)

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self.skin = skin
        self._refreshing = False
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        add = QPushButton("新建")
        delete = QPushButton("删除")
        test = QPushButton("测试")
        add.clicked.connect(self._add)
        delete.clicked.connect(self._delete)
        test.clicked.connect(self._test)
        buttons.addWidget(add)
        buttons.addWidget(delete)
        buttons.addWidget(test)
        layout.addLayout(buttons)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda current, previous: self._load_form())
        self.list.itemChanged.connect(self._renamed)
        layout.addWidget(self.list)
        group = QGroupBox("触发条件")
        form = QFormLayout(group)
        self.type = QComboBox()
        for trigger_type in TriggerType:
            self.type.addItem(trigger_type.value, trigger_type)
        self.action = QComboBox()
        self.enabled = QCheckBox("启用")
        self.interval = self._seconds(0.05, 3600)
        self.delay = self._seconds(0, 3600)
        self.random_min = self._seconds(0.05, 3600)
        self.random_max = self._seconds(0.05, 3600)
        self.idle = self._seconds(0.05, 3600)
        self.keyword = QComboBox()
        self.keyword.setEditable(True)
        self.pattern = QComboBox()
        self.pattern.setEditable(True)
        self.case_sensitive = QCheckBox("区分大小写")
        self.volume = QDoubleSpinBox()
        self.volume.setRange(0, 1)
        self.volume.setSingleStep(0.01)
        self.hold = self._seconds(0, 60)
        self.layer = QComboBox()
        self.mouse_button = QComboBox()
        self.mouse_button.addItems(["left", "right", "middle"])
        self.cooldown = self._seconds(0, 3600)
        self.allow_retrigger = QCheckBox("动作播放中允许重新触发")
        self.probability = QDoubleSpinBox()
        self.probability.setRange(0, 1)
        self.probability.setSingleStep(0.05)
        self.max_fires = QSpinBox()
        self.max_fires.setRange(0, 999999)
        self.priority = QSpinBox()
        self.priority.setRange(-101, 100)
        self.priority.setSpecialValueText("使用动作值")
        rows = (
            ("事件类型", self.type), ("触发动作", self.action), ("", self.enabled),
            ("间隔", self.interval), ("首次延迟", self.delay),
            ("随机最小", self.random_min), ("随机最大", self.random_max),
            ("空闲时间", self.idle), ("关键词", self.keyword),
            ("正则表达式", self.pattern), ("", self.case_sensitive),
            ("音量阈值", self.volume), ("持续时间", self.hold),
            ("点击图层", self.layer), ("鼠标按键", self.mouse_button),
            ("触发冷却", self.cooldown), ("", self.allow_retrigger),
            ("触发概率", self.probability),
            ("最多次数（0无限）", self.max_fires), ("优先级覆盖", self.priority),
        )
        for label, widget in rows:
            form.addRow(label, widget)
        layout.addWidget(group)
        for widget in (
            self.type, self.action, self.enabled, self.interval, self.delay,
            self.random_min, self.random_max, self.idle, self.keyword, self.pattern,
            self.case_sensitive, self.volume, self.hold, self.layer,
            self.mouse_button, self.cooldown, self.probability, self.max_fires,
            self.priority, self.allow_retrigger,
        ):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._save_form)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._save_form)
                if widget.isEditable():
                    widget.currentTextChanged.connect(self._save_form)
            else:
                widget.valueChanged.connect(self._save_form)
        self.refresh()

    @staticmethod
    def _seconds(minimum: float, maximum: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSuffix(" s")
        widget.setDecimals(2)
        return widget

    def set_skin(self, skin: SkinDefinition) -> None:
        self.skin = skin
        self.refresh()

    def selected_trigger(self) -> Optional[Trigger]:
        item = self.list.currentItem()
        if not item:
            return None
        trigger_id = item.data(Qt.UserRole)
        return next((trigger for trigger in self.skin.triggers if trigger.id == trigger_id), None)

    def refresh(self, selected_id: str = "") -> None:
        self._refreshing = True
        self.list.clear()
        for trigger in self.skin.triggers:
            item = QListWidgetItem(f"{'✓' if trigger.enabled else '✗'} {trigger.name}")
            item.setData(Qt.UserRole, trigger.id)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.list.addItem(item)
            if trigger.id == selected_id:
                self.list.setCurrentItem(item)
        self.action.clear()
        self.action.addItem("（无动作）", "")
        for action in self.skin.actions:
            self.action.addItem(action.name, action.id)
        self.layer.clear()
        self.layer.addItem("（任意图层）", "")
        for layer in self.skin.layers:
            self.layer.addItem(layer.name, layer.id)
        self._refreshing = False
        self._load_form()

    def _add(self) -> None:
        trigger = Trigger(name=f"触发器 {len(self.skin.triggers) + 1}")
        self.skin.triggers.append(trigger)
        self.refresh(trigger.id)
        self.changed.emit()

    def _delete(self) -> None:
        trigger = self.selected_trigger()
        if not trigger:
            return
        self.skin.triggers = [item for item in self.skin.triggers if item.id != trigger.id]
        self.refresh()
        self.changed.emit()

    def _test(self) -> None:
        trigger = self.selected_trigger()
        if trigger:
            self.test_requested.emit(trigger.id)

    def _renamed(self, item: QListWidgetItem) -> None:
        if self._refreshing:
            return
        trigger_id = item.data(Qt.UserRole)
        trigger = next((value for value in self.skin.triggers if value.id == trigger_id), None)
        if trigger:
            text = item.text().lstrip("✓✗ ").strip()
            if text:
                trigger.name = text
                self.changed.emit()

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _load_form(self) -> None:
        trigger = self.selected_trigger()
        self._refreshing = True
        for widget in self.findChildren(QWidget):
            if widget not in (self.list,):
                widget.setEnabled(trigger is not None or isinstance(widget, QPushButton))
        if trigger:
            self._select_data(self.type, trigger.trigger_type)
            self._select_data(self.action, trigger.action_id)
            self.enabled.setChecked(trigger.enabled)
            self.interval.setValue(trigger.interval)
            self.delay.setValue(trigger.delay)
            self.random_min.setValue(trigger.random_min)
            self.random_max.setValue(trigger.random_max)
            self.idle.setValue(trigger.idle_timeout)
            self.keyword.setCurrentText(trigger.keyword)
            self.pattern.setCurrentText(trigger.pattern)
            self.case_sensitive.setChecked(trigger.case_sensitive)
            self.volume.setValue(trigger.volume_threshold)
            self.hold.setValue(trigger.hold_seconds)
            self._select_data(self.layer, trigger.target_layer_id)
            self.mouse_button.setCurrentText(trigger.mouse_button)
            self.cooldown.setValue(trigger.cooldown)
            self.allow_retrigger.setChecked(trigger.allow_retrigger)
            self.probability.setValue(trigger.probability)
            self.max_fires.setValue(trigger.max_fires)
            self.priority.setValue(-101 if trigger.priority_override is None else trigger.priority_override)
        self._refreshing = False

    def _save_form(self, *args) -> None:
        del args
        if self._refreshing:
            return
        trigger = self.selected_trigger()
        if not trigger:
            return
        trigger.trigger_type = self.type.currentData()
        trigger.action_id = self.action.currentData() or ""
        trigger.enabled = self.enabled.isChecked()
        trigger.interval = self.interval.value()
        trigger.delay = self.delay.value()
        trigger.random_min = self.random_min.value()
        trigger.random_max = self.random_max.value()
        trigger.idle_timeout = self.idle.value()
        trigger.keyword = self.keyword.currentText()
        trigger.pattern = self.pattern.currentText()
        trigger.case_sensitive = self.case_sensitive.isChecked()
        trigger.volume_threshold = self.volume.value()
        trigger.hold_seconds = self.hold.value()
        trigger.target_layer_id = self.layer.currentData() or ""
        trigger.mouse_button = self.mouse_button.currentText()
        trigger.cooldown = self.cooldown.value()
        trigger.allow_retrigger = self.allow_retrigger.isChecked()
        trigger.probability = self.probability.value()
        trigger.max_fires = self.max_fires.value()
        trigger.priority_override = None if self.priority.value() == -101 else self.priority.value()
        self.changed.emit()


class PropertyPanel(QWidget):
    property_changed = Signal(str, float)
    structure_changed = Signal()
    add_keyframe_requested = Signal(str, object)
    remove_keyframe_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layer: Optional[Layer] = None
        self.action: Optional[AnimationClip] = None
        self.time_value = 0.0
        self._refreshing = False
        layout = QVBoxLayout(self)
        self.title = QLabel("未选择图层")
        layout.addWidget(self.title)
        general = QGroupBox("图层")
        form = QFormLayout(general)
        self.name = QComboBox()
        self.name.setEditable(True)
        self.visible = QCheckBox("显示")
        self.locked = QCheckBox("锁定")
        self.plane = QComboBox()
        self.plane.addItem("字幕下方", LayerPlane.BELOW_TEXT)
        self.plane.addItem("字幕上方", LayerPlane.ABOVE_TEXT)
        self.pin_x = QComboBox()
        for value in HorizontalPin:
            self.pin_x.addItem(value.value, value)
        self.pin_y = QComboBox()
        for value in VerticalPin:
            self.pin_y.addItem(value.value, value)
        self.sequence_fps = QDoubleSpinBox()
        self.sequence_fps.setRange(0.1, 120)
        self.sequence_fps.setSuffix(" fps")
        self.sequence_loop = QCheckBox("序列帧循环")
        form.addRow("名称", self.name)
        form.addRow(self.visible)
        form.addRow(self.locked)
        form.addRow("层级", self.plane)
        form.addRow("水平锚定", self.pin_x)
        form.addRow("垂直锚定", self.pin_y)
        form.addRow("序列帧率", self.sequence_fps)
        form.addRow(self.sequence_loop)
        layout.addWidget(general)
        transform = QGroupBox("变换")
        transform_form = QFormLayout(transform)
        self.spins: dict[str, QDoubleSpinBox] = {}
        ranges = {
            "x": (-10000, 10000, 1), "y": (-10000, 10000, 1),
            "scale_x": (0.01, 100, 0.05), "scale_y": (0.01, 100, 0.05),
            "rotation": (-3600, 3600, 1), "opacity": (0, 1, 0.05),
        }
        for property_name in ANIMATABLE_PROPERTIES:
            spin = QDoubleSpinBox()
            minimum, maximum, step = ranges[property_name]
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(3)
            spin.valueChanged.connect(
                lambda value, name=property_name: self._property_value(name, value)
            )
            self.spins[property_name] = spin
            transform_form.addRow(property_name, spin)
        layout.addWidget(transform)
        keyframes = QGroupBox("关键帧")
        keyframe_form = QFormLayout(keyframes)
        self.property = QComboBox()
        self.property.addItems(ANIMATABLE_PROPERTIES)
        self.interpolation = QComboBox()
        for value in Interpolation:
            self.interpolation.addItem(value.value, value)
        self.auto_key = QCheckBox("动作编辑时自动打点")
        self.auto_key.setChecked(True)
        buttons = QHBoxLayout()
        add = QPushButton("添加/更新")
        remove = QPushButton("删除")
        add.clicked.connect(lambda: self.add_keyframe_requested.emit(
            self.property.currentText(), self.interpolation.currentData()
        ))
        remove.clicked.connect(lambda: self.remove_keyframe_requested.emit(
            self.property.currentText()
        ))
        buttons.addWidget(add)
        buttons.addWidget(remove)
        keyframe_form.addRow("当前属性", self.property)
        keyframe_form.addRow("插值", self.interpolation)
        keyframe_form.addRow(self.auto_key)
        keyframe_form.addRow(buttons)
        layout.addWidget(keyframes)
        layout.addStretch(1)
        self.name.currentTextChanged.connect(self._structure)
        self.visible.toggled.connect(self._structure)
        self.locked.toggled.connect(self._structure)
        self.plane.currentIndexChanged.connect(self._structure)
        self.pin_x.currentIndexChanged.connect(self._structure)
        self.pin_y.currentIndexChanged.connect(self._structure)
        self.sequence_fps.valueChanged.connect(self._structure)
        self.sequence_loop.toggled.connect(self._structure)
        self.property.currentTextChanged.connect(lambda value: self.sync_values())

    def set_context(
        self, layer: Optional[Layer], action: Optional[AnimationClip], time_value: float
    ) -> None:
        self.layer = layer
        self.action = action
        self.time_value = time_value
        self.sync_values()

    def set_time(self, time_value: float) -> None:
        self.time_value = time_value
        self.sync_values()

    def _effective(self, property_name: str) -> float:
        if not self.layer:
            return 0.0
        if self.action:
            track = self.action.tracks.get(self.layer.id, {}).get(property_name)
            if track and track.keyframes:
                return track.get_value_at(self.time_value)
        return float(getattr(self.layer, property_name))

    def sync_values(self) -> None:
        self._refreshing = True
        enabled = self.layer is not None
        for widget in self.findChildren(QWidget):
            if widget is not self.title:
                widget.setEnabled(enabled)
        if not self.layer:
            self.title.setText("未选择图层")
            self._refreshing = False
            return
        self.title.setText(f"图层：{self.layer.name}")
        self.name.setCurrentText(self.layer.name)
        self.visible.setChecked(self.layer.visible)
        self.locked.setChecked(self.layer.locked)
        self._select_data(self.plane, self.layer.plane)
        self._select_data(self.pin_x, self.layer.pin_x)
        self._select_data(self.pin_y, self.layer.pin_y)
        self.sequence_fps.setValue(self.layer.sequence_fps)
        self.sequence_loop.setChecked(self.layer.sequence_loop)
        is_sequence = self.layer.asset_type == AssetType.SEQUENCE
        self.sequence_fps.setEnabled(is_sequence)
        self.sequence_loop.setEnabled(is_sequence)
        for property_name, spin in self.spins.items():
            spin.setValue(self._effective(property_name))
        self._refreshing = False

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _property_value(self, property_name: str, value: float) -> None:
        if not self._refreshing and self.layer:
            self.property.setCurrentText(property_name)
            self.property_changed.emit(property_name, value)

    def _structure(self, *args) -> None:
        del args
        if self._refreshing or not self.layer:
            return
        self.layer.name = self.name.currentText().strip() or self.layer.name
        self.layer.visible = self.visible.isChecked()
        self.layer.locked = self.locked.isChecked()
        self.layer.plane = self.plane.currentData()
        self.layer.pin_x = self.pin_x.currentData()
        self.layer.pin_y = self.pin_y.currentData()
        self.layer.sequence_fps = self.sequence_fps.value()
        self.layer.sequence_loop = self.sequence_loop.isChecked()
        self.structure_changed.emit()


class SkinEditorWindow(QMainWindow):
    skin_saved = Signal(str)

    def __init__(self, cfg: Config, panel, runtime=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.panel = panel
        self.runtime = runtime
        self.root = skins_root(cfg.skin.skins_dir)
        self._draft_temp = None
        self._original_skin = (
            SkinDefinition.from_dict(runtime.skin.to_dict())
            if runtime and runtime.skin else None
        )
        self._original_base_dir = Path(runtime.base_dir) if runtime and runtime.base_dir else None
        self._preview_applied = False
        if runtime and runtime.skin and runtime.base_dir:
            self.skin = SkinDefinition.from_dict(runtime.skin.to_dict())
            self.base_dir = Path(runtime.base_dir)
        else:
            width, height = panel.get_window_size()
            self.skin = SkinDefinition(
                name="新皮肤", fps=cfg.skin.animation_fps,
                design_width=max(1, width), design_height=max(1, height),
            )
            self.base_dir = self._create_draft()
        self.current_action: Optional[AnimationClip] = None
        self.current_layer: Optional[Layer] = None
        self.current_time = 0.0
        self._history = [self.skin.to_dict()]
        self._history_index = 0
        self._editing = False
        self.setWindowTitle("字幕皮肤编辑器")
        self.resize(1380, 900)
        self._init_ui()
        self._init_toolbar()
        self._mirror_timer = QTimer(self)
        self._mirror_timer.setSingleShot(True)
        self._mirror_timer.setInterval(100)
        self._mirror_timer.timeout.connect(self._refresh_mirror)
        self._connect()
        self._refresh_mirror()
        self.statusBar().showMessage(f"皮肤目录：{self.base_dir}")

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        splitter = QSplitter(Qt.Horizontal)
        self.tabs = QTabWidget()
        self.layer_panel = LayerPanel(self.skin)
        self.action_panel = ActionPanel(self.skin)
        self.trigger_panel = TriggerPanel(self.skin)
        self.tabs.addTab(self.layer_panel, "图层")
        self.tabs.addTab(self.action_panel, "动作")
        self.tabs.addTab(self.trigger_panel, "事件")
        self.tabs.setMaximumWidth(350)
        splitter.addWidget(self.tabs)
        self.canvas = SkinCanvas(self.skin, self.base_dir)
        self.canvas.grid_enabled = self.cfg.skin.editor_grid_snap
        self.canvas.grid_size = self.cfg.skin.editor_grid_size
        self.canvas.guides_enabled = self.cfg.skin.editor_show_guides
        splitter.addWidget(self.canvas)
        self.properties = PropertyPanel()
        self.properties.setMaximumWidth(300)
        splitter.addWidget(self.properties)
        splitter.setSizes([320, 760, 280])
        layout.addWidget(splitter, 3)
        self.timeline = ActionTimeline()
        self.timeline.fps = self.skin.fps
        layout.addWidget(self.timeline, 1)

    def _toolbar_action(self, toolbar, text: str, callback, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        toolbar.addAction(action)
        return action

    def _init_toolbar(self) -> None:
        toolbar = QToolBar("皮肤工具")
        self.addToolBar(toolbar)
        self._toolbar_action(toolbar, "新建", self._new_skin, QKeySequence.New)
        self._toolbar_action(toolbar, "打开", self._open_skin, QKeySequence.Open)
        self._toolbar_action(toolbar, "保存", self._save, QKeySequence.Save)
        self._toolbar_action(toolbar, "导入包", self._import_package)
        self._toolbar_action(toolbar, "导出包", self._export_package)
        toolbar.addSeparator()
        self._toolbar_action(toolbar, "撤销", self._undo, QKeySequence.Undo)
        self._toolbar_action(toolbar, "重做", self._redo, QKeySequence.Redo)
        toolbar.addSeparator()
        self._toolbar_action(toolbar, "播放/暂停", self.timeline.toggle_play, Qt.Key_Space)
        self._toolbar_action(toolbar, "停止", self.timeline.stop)
        self._toolbar_action(toolbar, "关键帧×0.5", lambda: self.timeline.scale_selected(0.5))
        self._toolbar_action(toolbar, "关键帧×2", lambda: self.timeline.scale_selected(2.0))
        toolbar.addSeparator()
        self._toolbar_action(toolbar, "应用预览", self._preview)

    def _connect(self) -> None:
        self.layer_panel.selected.connect(self._select_layer)
        self.layer_panel.changed.connect(self._model_changed)
        self.layer_panel.add_static_requested.connect(self._add_static_layer)
        self.layer_panel.add_sequence_requested.connect(self._add_sequence_layer)
        self.layer_panel.duplicate_requested.connect(self._duplicate_layer)
        self.layer_panel.delete_requested.connect(self._delete_layer)
        self.action_panel.selected.connect(self._select_action)
        self.action_panel.changed.connect(self._model_changed)
        self.action_panel.add_requested.connect(self._add_action)
        self.action_panel.duplicate_requested.connect(self._duplicate_action)
        self.action_panel.delete_requested.connect(self._delete_action)
        self.trigger_panel.changed.connect(self._model_changed)
        self.trigger_panel.test_requested.connect(self._test_trigger)
        self.canvas.layer_selected.connect(self._select_layer)
        self.canvas.canvas_clicked.connect(lambda: self._select_layer(""))
        self.canvas.transform_changed.connect(self._set_property)
        self.canvas.edit_started.connect(self._begin_edit)
        self.canvas.edit_finished.connect(self._finish_edit)
        self.timeline.time_changed.connect(self._set_time)
        self.timeline.property_selected.connect(self.properties.property.setCurrentText)
        self.timeline.changed.connect(self._refresh_animation)
        self.timeline.edit_started.connect(self._begin_edit)
        self.timeline.edit_finished.connect(self._finish_edit)
        self.properties.property_changed.connect(
            lambda name, value: self._set_property(
                self.current_layer.id if self.current_layer else "", name, value
            )
        )
        self.properties.structure_changed.connect(self._structure_changed)
        self.properties.add_keyframe_requested.connect(self._add_keyframe)
        self.properties.remove_keyframe_requested.connect(self._remove_keyframe)
        self.panel.preview_state_changed.connect(self._schedule_mirror_refresh)

    def _schedule_mirror_refresh(self) -> None:
        self._mirror_timer.start()

    def _begin_edit(self) -> None:
        self._editing = True

    def _finish_edit(self) -> None:
        if self._editing:
            self._editing = False
            self._commit_history()

    def _commit_history(self) -> None:
        snapshot = self.skin.to_dict()
        if snapshot == self._history[self._history_index]:
            return
        self._history = self._history[:self._history_index + 1]
        self._history.append(snapshot)
        self._history_index += 1

    def _undo(self) -> None:
        if self._history_index <= 0:
            return
        self._history_index -= 1
        self._restore_snapshot(self._history[self._history_index])

    def _redo(self) -> None:
        if self._history_index >= len(self._history) - 1:
            return
        self._history_index += 1
        self._restore_snapshot(self._history[self._history_index])

    def _restore_snapshot(self, snapshot: dict) -> None:
        layer_id = self.current_layer.id if self.current_layer else ""
        action_id = self.current_action.id if self.current_action else ""
        self.skin = SkinDefinition.from_dict(snapshot)
        self._refresh_all(layer_id, action_id)

    def _refresh_all(self, layer_id: str = "", action_id: str = "") -> None:
        self.layer_panel.set_skin(self.skin)
        self.action_panel.set_skin(self.skin)
        self.trigger_panel.set_skin(self.skin)
        self.canvas.set_skin(self.skin, self.base_dir)
        self.timeline.fps = self.skin.fps
        if action_id:
            self.action_panel.select(action_id)
        else:
            self._select_action("")
        if layer_id:
            self.layer_panel.select(layer_id)
        else:
            self._select_layer("")
        self._refresh_mirror()

    def _refresh_mirror(self) -> None:
        if not hasattr(self, "canvas"):
            return
        width, height = self.panel.get_window_size()
        if self.skin.design_width <= 1 or self.skin.design_height <= 1:
            self.skin.design_width, self.skin.design_height = width, height
        self.canvas.set_background(self.panel.grab_skin_background())

    def _model_changed(self) -> None:
        self.canvas.update_state()
        self.timeline.update()
        self.properties.sync_values()
        self.trigger_panel.refresh(
            self.trigger_panel.selected_trigger().id if self.trigger_panel.selected_trigger() else ""
        )
        if not self._editing:
            self._commit_history()

    def _create_draft(self) -> Path:
        if self._draft_temp is not None:
            self._draft_temp.cleanup()
        self._draft_temp = tempfile.TemporaryDirectory(prefix=".skin-draft-", dir=self.root)
        directory = Path(self._draft_temp.name)
        (directory / "assets").mkdir(exist_ok=True)
        return directory

    def _discard_draft(self) -> None:
        if self._draft_temp is not None:
            self._draft_temp.cleanup()
            self._draft_temp = None

    def _structure_changed(self) -> None:
        layer_id = self.current_layer.id if self.current_layer else ""
        self.layer_panel.refresh(layer_id)
        self.trigger_panel.refresh()
        self.canvas.update_state()
        self._commit_history()

    def _select_layer(self, layer_id: str) -> None:
        self.current_layer = self.skin.get_layer_by_id(layer_id) if layer_id else None
        self.canvas.select_layer(layer_id or None)
        if layer_id and self.layer_panel.selected_id() != layer_id:
            self.layer_panel.select(layer_id)
        self.timeline.set_context(self.current_action, self.current_layer)
        self.timeline.set_time(self.current_time)
        self.properties.set_context(self.current_layer, self.current_action, self.current_time)

    def _select_action(self, action_id: str) -> None:
        self.current_action = self.skin.get_action_by_id(action_id) if action_id else None
        self.current_time = 0.0
        self.canvas.set_action(self.current_action)
        self.timeline.set_context(self.current_action, self.current_layer)
        self.properties.set_context(self.current_layer, self.current_action, 0.0)
        self.statusBar().showMessage(
            f"正在编辑：{self.current_action.name}" if self.current_action else "正在编辑基础状态"
        )

    def _set_time(self, time_value: float) -> None:
        self.current_time = time_value
        self.canvas.set_time(time_value)
        self.properties.set_time(time_value)

    def _set_property(self, layer_id: str, property_name: str, value: float) -> None:
        layer = self.skin.get_layer_by_id(layer_id)
        if layer is None:
            return
        if self.current_action and self.properties.auto_key.isChecked():
            track = self.current_action.get_track(
                layer.id, property_name, float(getattr(layer, property_name))
            )
            existing = track.keyframe_at(self.current_time, tolerance=0.02)
            interpolation = self.properties.interpolation.currentData()
            if existing:
                existing.value = value
                existing.interpolation = interpolation
            else:
                track.add_keyframe(Keyframe(self.current_time, value, interpolation))
        else:
            setattr(layer, property_name, value)
        self.canvas.update_state()
        self.timeline.update()
        self.properties.sync_values()
        if not self._editing:
            self._commit_history()

    def _add_keyframe(self, property_name: str, interpolation: Interpolation) -> None:
        if not self.current_action or not self.current_layer:
            self.statusBar().showMessage("请先选择一个动作和图层")
            return
        track = self.current_action.get_track(
            self.current_layer.id,
            property_name,
            float(getattr(self.current_layer, property_name)),
        )
        value = self.properties.spins[property_name].value()
        track.add_keyframe(Keyframe(self.current_time, value, interpolation))
        self._refresh_animation()
        self._commit_history()

    def _remove_keyframe(self, property_name: str) -> None:
        if not self.current_action or not self.current_layer:
            return
        track = self.current_action.tracks.get(self.current_layer.id, {}).get(property_name)
        if track and track.remove_keyframe_at(self.current_time):
            self._refresh_animation()
            self._commit_history()

    def _refresh_animation(self) -> None:
        self.canvas.update_state()
        self.properties.sync_values()
        self.timeline.update()

    def _unique_asset(self, source: Path, directory: Optional[Path] = None) -> Path:
        directory = directory or (self.base_dir / "assets")
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / source.name
        suffix = 2
        while destination.exists() and source.resolve() != destination.resolve():
            destination = directory / f"{source.stem}-{suffix}{source.suffix.lower()}"
            suffix += 1
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        return destination

    def _relative_asset(self, path: Path) -> str:
        return path.relative_to(self.base_dir).as_posix()

    def _add_static_layer(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 PNG/WebP 贴图", "", "贴图 (*.png *.webp)"
        )
        if not path:
            return
        source = Path(path)
        destination = self._unique_asset(source)
        layer = Layer(
            name=source.stem, image_path=self._relative_asset(destination),
            x=self.skin.design_width / 2, y=self.skin.design_height / 2,
        )
        self.skin.layers.append(layer)
        self.layer_panel.refresh(layer.id)
        self.trigger_panel.refresh()
        self._select_layer(layer.id)
        self._commit_history()

    def _add_sequence_layer(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择按顺序排列的 PNG/WebP 帧", "", "序列帧 (*.png *.webp)"
        )
        if not paths:
            return
        sources = sorted((Path(path) for path in paths), key=lambda path: path.name.lower())
        sequence_dir = self.base_dir / "assets" / sources[0].stem
        suffix = 2
        while sequence_dir.exists():
            sequence_dir = self.base_dir / "assets" / f"{sources[0].stem}-{suffix}"
            suffix += 1
        sequence_dir.mkdir(parents=True)
        frames = []
        for index, source in enumerate(sources, 1):
            destination = sequence_dir / f"{index:04d}{source.suffix.lower()}"
            shutil.copy2(source, destination)
            frames.append(self._relative_asset(destination))
        layer = Layer(
            name=sources[0].stem, image_path=frames[0], asset_type=AssetType.SEQUENCE,
            sequence_frames=frames, x=self.skin.design_width / 2,
            y=self.skin.design_height / 2,
        )
        self.skin.layers.append(layer)
        self.layer_panel.refresh(layer.id)
        self.trigger_panel.refresh()
        self._select_layer(layer.id)
        self._commit_history()

    def _duplicate_layer(self) -> None:
        if not self.current_layer:
            return
        clone = Layer.from_dict(self.current_layer.to_dict())
        clone.id = Layer().id
        clone.name = f"{self.current_layer.name} 副本"
        clone.x += 16
        clone.y += 16
        self.skin.layers.append(clone)
        self.layer_panel.refresh(clone.id)
        self.trigger_panel.refresh()
        self._select_layer(clone.id)
        self._commit_history()

    def _delete_layer(self) -> None:
        if not self.current_layer:
            return
        layer_id = self.current_layer.id
        self.skin.layers = [layer for layer in self.skin.layers if layer.id != layer_id]
        for action in self.skin.actions:
            action.tracks.pop(layer_id, None)
        for trigger in self.skin.triggers:
            if trigger.target_layer_id == layer_id:
                trigger.target_layer_id = ""
        self.current_layer = None
        self.layer_panel.refresh()
        self.trigger_panel.refresh()
        self._select_layer("")
        self._commit_history()

    def _add_action(self) -> None:
        name, ok = QInputDialog.getText(self, "新建动作", "动作名称")
        if not ok or not name.strip():
            return
        action = AnimationClip(name=name.strip())
        self.skin.actions.append(action)
        self.action_panel.refresh(action.id)
        self.trigger_panel.refresh()
        self._select_action(action.id)
        self._commit_history()

    def _duplicate_action(self) -> None:
        if not self.current_action:
            return
        clone = AnimationClip.from_dict(self.current_action.to_dict())
        clone.id = AnimationClip().id
        clone.name = f"{self.current_action.name} 副本"
        self.skin.actions.append(clone)
        self.action_panel.refresh(clone.id)
        self.trigger_panel.refresh()
        self._select_action(clone.id)
        self._commit_history()

    def _delete_action(self) -> None:
        if not self.current_action:
            return
        action_id = self.current_action.id
        self.skin.actions = [action for action in self.skin.actions if action.id != action_id]
        for trigger in self.skin.triggers:
            if trigger.action_id == action_id:
                trigger.action_id = ""
        self.current_action = None
        self.action_panel.refresh()
        self.trigger_panel.refresh()
        self._select_action("")
        self._commit_history()

    def _test_trigger(self, trigger_id: str) -> None:
        self._preview()
        if self.runtime and self.runtime.triggers:
            self.runtime.triggers.fire_for_test(trigger_id)

    def _new_skin(self) -> None:
        name, ok = QInputDialog.getText(self, "新建皮肤", "皮肤名称", text="新皮肤")
        if not ok or not name.strip():
            return
        width, height = self.panel.get_window_size()
        self.skin = SkinDefinition(
            name=name.strip(), fps=self.cfg.skin.animation_fps,
            design_width=max(1, width), design_height=max(1, height),
        )
        self.base_dir = self._create_draft()
        self.current_action = None
        self.current_layer = None
        self._history = [self.skin.to_dict()]
        self._history_index = 0
        self._refresh_all()

    def _open_skin(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开皮肤", str(self.root), "皮肤定义 (skin.json *.json)"
        )
        if not path:
            return
        try:
            loaded = SkinDefinition.load(Path(path))
            self._discard_draft()
            self.skin = loaded
            self.base_dir = Path(path).parent
            self._history = [self.skin.to_dict()]
            self._history_index = 0
            self._refresh_all()
        except Exception as error:
            QMessageBox.warning(self, "打开失败", str(error))

    def _save(self) -> None:
        errors = self.skin.validate()
        if errors:
            QMessageBox.warning(self, "皮肤引用错误", "\n".join(errors))
            return
        if self._draft_temp is not None:
            draft_directory = self.base_dir
            destination = create_skin_directory(self.root, self.skin)
            shutil.copytree(
                draft_directory / "assets", destination / "assets", dirs_exist_ok=True
            )
            self.base_dir = destination
            self.canvas.base_dir = destination
            self.canvas.renderer.base_dir = destination
            self._discard_draft()
        self.skin.save(self.base_dir / "skin.json")
        previous = self.canvas.selected_layer_id
        self.canvas.select_layer(None)
        self.canvas.grab().scaled(
            480, 270, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ).save(str(self.base_dir / "thumbnail.png"))
        self.canvas.select_layer(previous)
        self.cfg.skin.enabled = True
        self.cfg.skin.active_skin = self.base_dir.name
        self.skin_saved.emit(str(self.base_dir))
        self._original_skin = SkinDefinition.from_dict(self.skin.to_dict())
        self._original_base_dir = self.base_dir
        self._preview_applied = False
        self.statusBar().showMessage(f"已保存并应用：{self.base_dir / 'skin.json'}")

    def _import_package(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入皮肤包", "", "皮肤包 (*.zip)")
        if not path:
            return
        try:
            imported = peek_skin_package(Path(path))
            overwrite = False
            if (self.root / safe_name(imported.name)).exists():
                answer = QMessageBox.question(
                    self, "同名皮肤已存在",
                    "选择“是”覆盖现有皮肤；选择“否”自动另存为新皮肤。",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.No,
                )
                if answer == QMessageBox.Cancel:
                    return
                overwrite = answer == QMessageBox.Yes
            directory = import_skin_package(Path(path), self.root, overwrite=overwrite)
            loaded = SkinDefinition.load(directory / "skin.json")
            self._discard_draft()
            self.skin = loaded
            self.base_dir = directory
            self._history = [self.skin.to_dict()]
            self._history_index = 0
            self._refresh_all()
        except Exception as error:
            QMessageBox.warning(self, "导入失败", str(error))

    def _export_package(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出皮肤包", str(self.root / f"{self.skin.name}.zip"), "皮肤包 (*.zip)"
        )
        if not path:
            return
        try:
            output = export_skin_package(self.skin, self.base_dir, Path(path))
            self.statusBar().showMessage(f"已导出：{output}")
        except Exception as error:
            QMessageBox.warning(self, "导出失败", str(error))

    def _preview(self) -> None:
        if not self.runtime:
            return
        preview_skin = SkinDefinition.from_dict(self.skin.to_dict())
        self.runtime.apply_skin(preview_skin, self.base_dir, start_triggers=True)
        self._preview_applied = True
        self.statusBar().showMessage("已实时应用到字幕窗口")

    def closeEvent(self, event) -> None:
        if self._preview_applied and self.runtime:
            if self._original_skin is not None and self._original_base_dir is not None:
                self.runtime.apply_skin(
                    SkinDefinition.from_dict(self._original_skin.to_dict()),
                    self._original_base_dir,
                    start_triggers=True,
                )
            else:
                self.runtime.disable()
        self._discard_draft()
        super().closeEvent(event)
