"""Interactive mirrored canvas used by the skin editor."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from .model import ANIMATABLE_PROPERTIES, AnimationClip, Layer, SkinDefinition
from .renderer import SkinRenderer


class SkinCanvas(QWidget):
    layer_selected = Signal(str)
    canvas_clicked = Signal()
    edit_started = Signal()
    edit_finished = Signal()
    transform_changed = Signal(str, str, float)

    def __init__(self, skin: SkinDefinition, base_dir: Path, parent=None):
        super().__init__(parent)
        self.skin = skin
        self.base_dir = Path(base_dir)
        self.renderer = SkinRenderer(skin, self.base_dir)
        self.action: Optional[AnimationClip] = None
        self.current_time = 0.0
        self.selected_layer_id: Optional[str] = None
        self.grid_enabled = True
        self.grid_size = 8
        self.guides_enabled = True
        self.background = QPixmap()
        self._mode: Optional[str] = None
        self._press_pos = QPointF()
        self._start_values: dict[str, float] = {}
        self._start_angle = 0.0
        self.setMinimumSize(500, 260)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def set_skin(self, skin: SkinDefinition, base_dir: Optional[Path] = None) -> None:
        self.skin = skin
        if base_dir is not None:
            self.base_dir = Path(base_dir)
            self.renderer.base_dir = self.base_dir
        self.renderer.skin = skin
        self.selected_layer_id = None
        self.update_state()

    def set_action(self, action: Optional[AnimationClip]) -> None:
        self.action = action
        self.current_time = min(self.current_time, action.duration if action else 0.0)
        self.update_state()

    def set_time(self, time_value: float) -> None:
        limit = self.action.duration if self.action else self.skin.total_duration
        self.current_time = max(0.0, min(float(time_value), limit))
        self.update_state()

    def set_background(self, pixmap: QPixmap) -> None:
        self.background = pixmap
        self.update()

    def select_layer(self, layer_id: Optional[str]) -> None:
        self.selected_layer_id = layer_id
        self.update()

    def update_state(self) -> None:
        overrides: dict[str, dict[str, float]] = {}
        if self.action is not None:
            for layer_id, properties in self.action.tracks.items():
                overrides[layer_id] = {
                    name: track.get_value_at(self.current_time)
                    for name, track in properties.items() if track.keyframes
                }
        self.renderer.set_runtime_state(
            overrides, {layer_id: self.current_time for layer_id in overrides}
        )
        self.renderer.set_time(self.current_time)
        self.update()

    def _preview_rect(self) -> QRectF:
        margin = 18.0
        available_w = max(1.0, self.width() - margin * 2)
        available_h = max(1.0, self.height() - margin * 2)
        scale = min(available_w / self.skin.design_width, available_h / self.skin.design_height)
        width = self.skin.design_width * scale
        height = self.skin.design_height * scale
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def _to_preview(self, point: QPointF) -> QPointF:
        rect = self._preview_rect()
        return QPointF(point.x() - rect.left(), point.y() - rect.top())

    def _effective(self, layer: Layer, property_name: str) -> float:
        if self.action is not None:
            track = self.action.tracks.get(layer.id, {}).get(property_name)
            if track and track.keyframes:
                return track.get_value_at(self.current_time)
        return float(getattr(layer, property_name))

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#11131a"))
        preview = self._preview_rect()
        painter.save()
        painter.translate(preview.topLeft())
        target = QRectF(0, 0, preview.width(), preview.height()).toRect()
        if not self.background.isNull():
            painter.drawPixmap(target, self.background)
        else:
            painter.fillRect(target, QColor("#242733"))
        if self.grid_enabled:
            scale = preview.width() / self.skin.design_width
            painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
            step = max(2.0, self.grid_size * scale)
            position = 0.0
            while position <= preview.width():
                painter.drawLine(QPointF(position, 0), QPointF(position, preview.height()))
                position += step
            position = 0.0
            while position <= preview.height():
                painter.drawLine(QPointF(0, position), QPointF(preview.width(), position))
                position += step
        if self.guides_enabled:
            painter.setPen(QPen(QColor(100, 190, 255, 90), 1, Qt.DashLine))
            painter.drawLine(QPointF(preview.width() / 2, 0), QPointF(preview.width() / 2, preview.height()))
            painter.drawLine(QPointF(0, preview.height() / 2), QPointF(preview.width(), preview.height() / 2))
        self.renderer.render(painter, int(preview.width()), int(preview.height()))
        painter.restore()
        painter.setPen(QPen(QColor("#5c637a"), 1))
        painter.drawRect(preview)
        self._draw_selection(painter, preview)
        painter.end()

    def _selection_polygon(self, preview: QRectF) -> QPolygonF:
        layer = self.skin.get_layer_by_id(self.selected_layer_id or "")
        if layer is None:
            return QPolygonF()
        polygon = self.renderer.get_layer_polygon(
            layer, int(preview.width()), int(preview.height())
        )
        return QPolygonF([point + preview.topLeft() for point in polygon])

    def _handles(self, preview: QRectF) -> dict[str, QPointF]:
        polygon = self._selection_polygon(preview)
        if polygon.isEmpty():
            return {}
        bounds = polygon.boundingRect()
        return {
            "scale": bounds.bottomRight(),
            "rotate": QPointF(bounds.center().x(), bounds.top() - 24),
        }

    def _draw_selection(self, painter: QPainter, preview: QRectF) -> None:
        polygon = self._selection_polygon(preview)
        if polygon.isEmpty():
            return
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#56c8ff"), 2, Qt.DashLine))
        painter.drawPolygon(polygon)
        handles = self._handles(preview)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#56c8ff"))
        painter.drawEllipse(handles["rotate"], 6, 6)
        painter.drawRect(QRectF(
            handles["scale"].x() - 6, handles["scale"].y() - 6, 12, 12
        ))
        bounds = polygon.boundingRect()
        painter.setPen(QPen(QColor("#56c8ff"), 1))
        painter.drawLine(bounds.center().x(), bounds.top(), handles["rotate"].x(), handles["rotate"].y())

    @staticmethod
    def _near(point: QPointF, target: QPointF, radius: float = 10.0) -> bool:
        return (point - target).manhattanLength() <= radius * 1.5

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        point = event.position()
        preview = self._preview_rect()
        if not preview.contains(point):
            self.canvas_clicked.emit()
            return
        layer = self.skin.get_layer_by_id(self.selected_layer_id or "")
        handles = self._handles(preview)
        mode = None
        if layer and handles:
            if self._near(point, handles["rotate"]):
                mode = "rotate"
            elif self._near(point, handles["scale"]):
                mode = "scale"
        if mode is None:
            hit = self.renderer.layer_at(
                self._to_preview(point), int(preview.width()), int(preview.height()), alpha_test=True
            )
            if hit is None or hit.locked:
                self.canvas_clicked.emit()
                return
            layer = hit
            self.selected_layer_id = layer.id
            self.layer_selected.emit(layer.id)
            mode = "move"
        self._mode = mode
        self._press_pos = point
        self._start_values = {
            name: self._effective(layer, name) for name in ANIMATABLE_PROPERTIES
        }
        center = self._selection_polygon(preview).boundingRect().center()
        self._start_angle = math.degrees(
            math.atan2(point.y() - center.y(), point.x() - center.x())
        )
        self.edit_started.emit()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._mode or not self.selected_layer_id:
            return
        layer = self.skin.get_layer_by_id(self.selected_layer_id)
        if layer is None or layer.locked:
            return
        point = event.position()
        preview = self._preview_rect()
        design_scale = preview.width() / self.skin.design_width
        if self._mode == "move":
            delta = (point - self._press_pos) / max(0.0001, design_scale)
            new_x = self._start_values["x"] + delta.x()
            new_y = self._start_values["y"] + delta.y()
            if self.grid_enabled and not (event.modifiers() & Qt.AltModifier):
                new_x = round(new_x / self.grid_size) * self.grid_size
                new_y = round(new_y / self.grid_size) * self.grid_size
            self.transform_changed.emit(layer.id, "x", new_x)
            self.transform_changed.emit(layer.id, "y", new_y)
        elif self._mode == "scale":
            center = self._selection_polygon(preview).boundingRect().center()
            start_distance = max(1.0, math.hypot(
                self._press_pos.x() - center.x(), self._press_pos.y() - center.y()
            ))
            distance = max(1.0, math.hypot(point.x() - center.x(), point.y() - center.y()))
            ratio = distance / start_distance
            self.transform_changed.emit(
                layer.id, "scale_x", max(0.01, self._start_values["scale_x"] * ratio)
            )
            self.transform_changed.emit(
                layer.id, "scale_y", max(0.01, self._start_values["scale_y"] * ratio)
            )
        elif self._mode == "rotate":
            center = self._selection_polygon(preview).boundingRect().center()
            angle = math.degrees(math.atan2(point.y() - center.y(), point.x() - center.x()))
            value = self._start_values["rotation"] + angle - self._start_angle
            if not (event.modifiers() & Qt.AltModifier):
                value = round(value / 5) * 5
            self.transform_changed.emit(layer.id, "rotation", value)
        self.update_state()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        del event
        if self._mode:
            self._mode = None
            self.edit_finished.emit()

    def keyPressEvent(self, event) -> None:
        layer = self.skin.get_layer_by_id(self.selected_layer_id or "")
        if layer is None or layer.locked:
            return super().keyPressEvent(event)
        step = self.grid_size if self.grid_enabled else 1
        changes = {
            Qt.Key_Left: ("x", -step), Qt.Key_Right: ("x", step),
            Qt.Key_Up: ("y", -step), Qt.Key_Down: ("y", step),
        }
        if event.key() in changes:
            property_name, delta = changes[event.key()]
            self.edit_started.emit()
            self.transform_changed.emit(
                layer.id, property_name, self._effective(layer, property_name) + delta
            )
            self.edit_finished.emit()
            self.update_state()
            return
        super().keyPressEvent(event)
