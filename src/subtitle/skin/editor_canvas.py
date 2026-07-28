"""Interactive mirrored canvas used by the skin editor."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF, QWheelEvent
from PySide6.QtWidgets import QWidget

from .model import ANIMATABLE_PROPERTIES, AnimationClip, Layer, SkinDefinition
from .renderer import SkinRenderer


class SkinCanvas(QWidget):
    layer_selected = Signal(str)
    canvas_clicked = Signal()
    edit_started = Signal()
    edit_finished = Signal()
    transform_changed = Signal(str, str, float)
    zoom_changed = Signal(float)
    rotation_pivot_selected = Signal(str, float, float)

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
        self.viewport_width = max(1, skin.design_width)
        self.viewport_height = max(1, skin.design_height)
        self._mode: Optional[str] = None
        self._press_pos = QPointF()
        self._start_values: dict[str, float] = {}
        self._start_angle = 0.0
        self._interaction_mapping: Optional[tuple[QRectF, float, QPointF]] = None
        self.zoom = 1.0
        self._pan_offset = QPointF()
        self._pan_start = QPointF()
        self._picking_rotation_pivot = False
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
        self._picking_rotation_pivot = False
        self.unsetCursor()
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

    def set_viewport_size(self, width: int, height: int) -> None:
        width = max(1, int(width))
        height = max(1, int(height))
        if (width, height) == (self.viewport_width, self.viewport_height):
            return
        self.viewport_width = width
        self.viewport_height = height
        self.update()

    def set_zoom(self, value: float) -> None:
        value = max(0.25, min(float(value), 8.0))
        if abs(value - self.zoom) < 0.0001:
            return
        self.zoom = value
        self.zoom_changed.emit(self.zoom)
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / 1.25)

    def reset_view(self) -> None:
        self.zoom = 1.0
        self._pan_offset = QPointF()
        self.zoom_changed.emit(self.zoom)
        self.update()

    def begin_rotation_pivot_pick(self) -> None:
        if self.selected_layer_id:
            self._picking_rotation_pivot = True
            self.setCursor(Qt.CrossCursor)

    def _pick_rotation_pivot(self, point: QPointF) -> bool:
        layer = self.skin.get_layer_by_id(self.selected_layer_id or "")
        if layer is None:
            return False
        image_point = self.renderer.get_layer_image_point(
            layer, self._to_scene(point), self.viewport_width, self.viewport_height
        )
        pixmap = self.renderer.get_layer_pixmap(layer)
        if image_point is None or pixmap is None or pixmap.isNull():
            return False
        self.rotation_pivot_selected.emit(
            layer.id, image_point.x() / pixmap.width(), image_point.y() / pixmap.height()
        )
        self._picking_rotation_pivot = False
        self.unsetCursor()
        self.update()
        return True

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

    def _reference_scale(self) -> float:
        return max(0.0001, min(
            self.viewport_width / max(1, self.skin.design_width),
            self.viewport_height / max(1, self.skin.design_height),
        ))

    def _scene_bounds(self) -> QRectF:
        content = QRectF(0, 0, self.viewport_width, self.viewport_height)
        padding_x = max(80.0, self.viewport_width * 0.25)
        padding_y = max(80.0, self.viewport_height * 0.75)
        bounds = content.adjusted(-padding_x, -padding_y, padding_x, padding_y)
        for layer in self.skin.layers:
            if not layer.visible:
                continue
            polygon = self.renderer.get_layer_polygon(
                layer, self.viewport_width, self.viewport_height
            )
            if not polygon.isEmpty():
                bounds = bounds.united(polygon.boundingRect().adjusted(-24, -24, 24, 24))
        return bounds

    def _scene_mapping(self) -> tuple[QRectF, float, QPointF]:
        if self._interaction_mapping is not None:
            return self._interaction_mapping
        margin = 18.0
        bounds = self._scene_bounds()
        available_w = max(1.0, self.width() - margin * 2)
        available_h = max(1.0, self.height() - margin * 2)
        fit_scale = max(0.0001, min(available_w / bounds.width(), available_h / bounds.height()))
        scale = fit_scale * self.zoom
        rendered_w = bounds.width() * scale
        rendered_h = bounds.height() * scale
        origin = QPointF(
            (self.width() - rendered_w) / 2 - bounds.left() * scale + self._pan_offset.x(),
            (self.height() - rendered_h) / 2 - bounds.top() * scale + self._pan_offset.y(),
        )
        return bounds, scale, origin

    def _preview_rect(self) -> QRectF:
        _, scale, origin = self._scene_mapping()
        return QRectF(
            origin.x(), origin.y(),
            self.viewport_width * scale, self.viewport_height * scale,
        )

    def _to_scene(self, point: QPointF) -> QPointF:
        _, scale, origin = self._scene_mapping()
        return QPointF(
            (point.x() - origin.x()) / scale,
            (point.y() - origin.y()) / scale,
        )

    def _from_scene(self, point: QPointF) -> QPointF:
        _, scale, origin = self._scene_mapping()
        return QPointF(point.x() * scale + origin.x(), point.y() * scale + origin.y())

    def _effective(self, layer: Layer, property_name: str) -> float:
        if self.action is not None:
            track = self.action.tracks.get(layer.id, {}).get(property_name)
            if track and track.keyframes:
                return track.get_value_at(self.current_time)
        return float(getattr(layer, property_name))

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.fillRect(self.rect(), QColor("#11131a"))
            preview = self._preview_rect()
            _, display_scale, origin = self._scene_mapping()
            painter.save()
            painter.translate(origin)
            painter.scale(display_scale, display_scale)
            target = QRectF(0, 0, self.viewport_width, self.viewport_height).toRect()
            if not self.background.isNull():
                painter.drawPixmap(target, self.background)
            else:
                painter.fillRect(target, QColor("#242733"))
            if self.grid_enabled:
                painter.setPen(QPen(QColor(255, 255, 255, 22), 1 / display_scale))
                step = max(2.0, self.grid_size * self._reference_scale())
                position = 0.0
                while position <= self.viewport_width:
                    painter.drawLine(QPointF(position, 0), QPointF(position, self.viewport_height))
                    position += step
                position = 0.0
                while position <= self.viewport_height:
                    painter.drawLine(QPointF(0, position), QPointF(self.viewport_width, position))
                    position += step
            if self.guides_enabled:
                painter.setPen(QPen(QColor(100, 190, 255, 90), 1 / display_scale, Qt.DashLine))
                painter.drawLine(
                    QPointF(self.viewport_width / 2, 0),
                    QPointF(self.viewport_width / 2, self.viewport_height),
                )
                painter.drawLine(
                    QPointF(0, self.viewport_height / 2),
                    QPointF(self.viewport_width, self.viewport_height / 2),
                )
            self.renderer.render(painter, self.viewport_width, self.viewport_height)
            painter.restore()
            painter.setPen(QPen(QColor("#5c637a"), 1))
            painter.drawRect(preview)
            self._draw_selection(painter, preview)
        finally:
            if painter.isActive():
                painter.end()

    def _selection_polygon(self, preview: QRectF) -> QPolygonF:
        layer = self.skin.get_layer_by_id(self.selected_layer_id or "")
        if layer is None:
            return QPolygonF()
        polygon = self.renderer.get_layer_polygon(
            layer, self.viewport_width, self.viewport_height
        )
        return QPolygonF([self._from_scene(point) for point in polygon])

    def _handles(self, preview: QRectF) -> dict[str, QPointF]:
        layer = self.skin.get_layer_by_id(self.selected_layer_id or "")
        if layer is None:
            return {}
        polygon = self._selection_polygon(preview)
        if polygon.isEmpty():
            return {}
        bounds = polygon.boundingRect()
        pivot = self.renderer.get_layer_pivot(
            layer, self.viewport_width, self.viewport_height
        )
        return {
            "scale": bounds.bottomRight(),
            "rotate": QPointF(bounds.center().x(), bounds.top() - 24),
            "pivot": self._from_scene(pivot),
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
        painter.setPen(QPen(QColor("#ffcf4a"), 2))
        painter.drawLine(handles["pivot"] + QPointF(-7, 0), handles["pivot"] + QPointF(7, 0))
        painter.drawLine(handles["pivot"] + QPointF(0, -7), handles["pivot"] + QPointF(0, 7))

    @staticmethod
    def _near(point: QPointF, target: QPointF, radius: float = 10.0) -> bool:
        return (point - target).manhattanLength() <= radius * 1.5

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._picking_rotation_pivot:
            if event.button() == Qt.LeftButton:
                self._pick_rotation_pivot(event.position())
            return
        if event.button() == Qt.MiddleButton:
            self._mode = "pan"
            self._press_pos = event.position()
            self._pan_start = QPointF(self._pan_offset)
            return
        if event.button() != Qt.LeftButton:
            return
        point = event.position()
        preview = self._preview_rect()
        if not self.rect().contains(point.toPoint()):
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
            scene_point = self._to_scene(point)
            hit = self.renderer.layer_at(
                scene_point, self.viewport_width, self.viewport_height, alpha_test=True
            )
            if hit is None and layer is not None:
                selected_polygon = self.renderer.get_layer_polygon(
                    layer, self.viewport_width, self.viewport_height
                )
                if selected_polygon.containsPoint(scene_point, Qt.OddEvenFill):
                    hit = layer
            if hit is None:
                self.canvas_clicked.emit()
                return
            layer = hit
            self.selected_layer_id = layer.id
            self.layer_selected.emit(layer.id)
            if layer.locked:
                self.update()
                return
            mode = "move"
        self._mode = mode
        self._interaction_mapping = self._scene_mapping()
        self._press_pos = point
        self._start_values = {
            name: self._effective(layer, name) for name in ANIMATABLE_PROPERTIES
        }
        center = self._from_scene(self.renderer.get_layer_pivot(
            layer, self.viewport_width, self.viewport_height
        ))
        self._start_angle = math.degrees(
            math.atan2(point.y() - center.y(), point.x() - center.x())
        )
        self.edit_started.emit()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._mode:
            return
        if self._mode == "pan":
            self._pan_offset = self._pan_start + event.position() - self._press_pos
            self.update()
            return
        if not self.selected_layer_id:
            return
        layer = self.skin.get_layer_by_id(self.selected_layer_id)
        if layer is None or layer.locked:
            return
        point = event.position()
        preview = self._preview_rect()
        _, display_scale, _ = self._scene_mapping()
        design_scale = display_scale * self._reference_scale()
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
            center = self._from_scene(self.renderer.get_layer_pivot(
                layer, self.viewport_width, self.viewport_height
            ))
            angle = math.degrees(math.atan2(point.y() - center.y(), point.x() - center.x()))
            value = self._start_values["rotation"] + angle - self._start_angle
            if not (event.modifiers() & Qt.AltModifier):
                value = round(value / 5) * 5
            self.transform_changed.emit(layer.id, "rotation", value)
        self.update_state()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        del event
        if self._mode:
            was_edit = self._mode in {"move", "scale", "rotate"}
            self._mode = None
            self._interaction_mapping = None
            if was_edit:
                self.edit_finished.emit()
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not (event.modifiers() & Qt.ControlModifier):
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta:
            self.set_zoom(self.zoom * (1.15 if delta > 0 else 1 / 1.15))
        event.accept()

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
