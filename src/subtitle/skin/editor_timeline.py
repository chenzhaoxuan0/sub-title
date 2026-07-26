"""Dope-sheet timeline with per-property keyframe editing."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .model import ANIMATABLE_PROPERTIES, AnimationClip, Interpolation, Keyframe, Layer


class ActionTimeline(QWidget):
    time_changed = Signal(float)
    property_selected = Signal(str)
    changed = Signal()
    edit_started = Signal()
    edit_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.action: Optional[AnimationClip] = None
        self.layer: Optional[Layer] = None
        self.current_time = 0.0
        self.current_property = "x"
        self.pixels_per_second = 140.0
        self.header_width = 110
        self.ruler_height = 25
        self.row_height = 25
        self._selected_ids: set[int] = set()
        self._clipboard: list[tuple[str, float, float, Interpolation]] = []
        self._mode: Optional[str] = None
        self._press = QPointF()
        self._drag_items: list[tuple[Keyframe, float]] = []
        self._selection_rect = QRectF()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._play_tick)
        self._playing = False
        self.fps = 30
        self.setMinimumHeight(self.ruler_height + len(ANIMATABLE_PROPERTIES) * self.row_height + 8)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_context(self, action: Optional[AnimationClip], layer: Optional[Layer]) -> None:
        self.action = action
        self.layer = layer
        self.current_time = 0.0
        self._selected_ids.clear()
        self._timer.setInterval(max(1, int(1000 / 30)))
        self.time_changed.emit(0.0)
        self.update()

    def set_time(self, time_value: float) -> None:
        duration = self.action.duration if self.action else 1.0
        self.current_time = max(0.0, min(float(time_value), duration))
        self.time_changed.emit(self.current_time)
        self.update()

    def toggle_play(self) -> None:
        if self.action is None:
            return
        self._playing = not self._playing
        if self._playing:
            self._timer.start()
        else:
            self._timer.stop()

    def stop(self) -> None:
        self._playing = False
        self._timer.stop()
        self.set_time(0.0)

    def _play_tick(self) -> None:
        if self.action is None:
            return
        next_time = self.current_time + self._timer.interval() / 1000
        if next_time >= self.action.duration:
            next_time = 0.0
        self.set_time(next_time)

    def _track(self, property_name: str):
        if self.action is None or self.layer is None:
            return None
        return self.action.tracks.get(self.layer.id, {}).get(property_name)

    def _x(self, time_value: float) -> float:
        return self.header_width + time_value * self.pixels_per_second

    def _update_scale(self) -> None:
        duration = self.action.duration if self.action else 1.0
        self.pixels_per_second = max(30.0, (self.width() - self.header_width - 12) / max(0.05, duration))

    def _time(self, x_value: float) -> float:
        duration = self.action.duration if self.action else 1.0
        return max(0.0, min((x_value - self.header_width) / self.pixels_per_second, duration))

    def _row_at(self, y_value: float) -> Optional[str]:
        index = int((y_value - self.ruler_height) // self.row_height)
        return ANIMATABLE_PROPERTIES[index] if 0 <= index < len(ANIMATABLE_PROPERTIES) else None

    def _keyframe_points(self):
        for row, property_name in enumerate(ANIMATABLE_PROPERTIES):
            track = self._track(property_name)
            if not track:
                continue
            y_value = self.ruler_height + row * self.row_height + self.row_height / 2
            for keyframe in track.keyframes:
                yield property_name, keyframe, QPointF(self._x(keyframe.time), y_value)

    def paintEvent(self, event) -> None:
        del event
        self._update_scale()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#181b24"))
        painter.fillRect(0, 0, self.width(), self.ruler_height, QColor("#262a36"))
        duration = self.action.duration if self.action else 1.0
        painter.setPen(QColor("#9299aa"))
        tick = 0.0
        while tick <= duration + 0.0001:
            x_value = self._x(tick)
            painter.drawLine(QPointF(x_value, self.ruler_height - 7), QPointF(x_value, self.ruler_height))
            painter.drawText(int(x_value + 3), 15, f"{tick:.1f}")
            tick += 0.5
        for row, property_name in enumerate(ANIMATABLE_PROPERTIES):
            top = self.ruler_height + row * self.row_height
            background = QColor("#252936") if property_name == self.current_property else QColor("#20232d")
            painter.fillRect(0, top, self.width(), self.row_height, background)
            painter.setPen(QColor("#c6cad5"))
            painter.drawText(8, top + 17, property_name)
            painter.setPen(QPen(QColor("#343947"), 1))
            painter.drawLine(self.header_width, top + self.row_height, self.width(), top + self.row_height)
        for property_name, keyframe, point in self._keyframe_points():
            selected = id(keyframe) in self._selected_ids
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#ffcf4a") if selected else QColor("#8fbce8"))
            painter.drawPolygon(QPolygonF([
                point + QPointF(0, -6), point + QPointF(6, 0),
                point + QPointF(0, 6), point + QPointF(-6, 0),
            ]))
        playhead = self._x(self.current_time)
        painter.setPen(QPen(QColor("#ff5e69"), 2))
        painter.drawLine(QPointF(playhead, 0), QPointF(playhead, self.height()))
        if not self._selection_rect.isNull():
            painter.setPen(QPen(QColor("#56c8ff"), 1, Qt.DashLine))
            painter.setBrush(QColor(86, 200, 255, 30))
            painter.drawRect(self._selection_rect.normalized())
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.action is None:
            return
        self._update_scale()
        self.setFocus()
        self._press = event.position()
        property_name = self._row_at(self._press.y())
        if property_name:
            self.current_property = property_name
            self.property_selected.emit(property_name)
        nearest = None
        for candidate_property, keyframe, point in self._keyframe_points():
            if abs(point.x() - self._press.x()) <= 8 and abs(point.y() - self._press.y()) <= 8:
                nearest = (candidate_property, keyframe)
                break
        if nearest:
            candidate_property, keyframe = nearest
            self.current_property = candidate_property
            self.property_selected.emit(candidate_property)
            if not (event.modifiers() & Qt.ControlModifier):
                if id(keyframe) not in self._selected_ids:
                    self._selected_ids = {id(keyframe)}
            elif id(keyframe) in self._selected_ids:
                self._selected_ids.remove(id(keyframe))
            else:
                self._selected_ids.add(id(keyframe))
            self._drag_items = [
                (item, item.time) for _, item, _ in self._keyframe_points()
                if id(item) in self._selected_ids
            ]
            self._mode = "drag"
            self.edit_started.emit()
        elif event.position().x() >= self.header_width:
            self.set_time(self._time(event.position().x()))
            if event.modifiers() & Qt.ShiftModifier:
                self._mode = "select"
                self._selection_rect = QRectF(self._press, self._press)
            else:
                self._selected_ids.clear()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._mode == "select":
            self._selection_rect = QRectF(self._press, event.position())
            self.update()
        elif self._mode == "drag" and self._drag_items and self.action:
            delta = (event.position().x() - self._press.x()) / self.pixels_per_second
            frame = 1 / max(1, self.fps)
            anchor_original = min(original for _, original in self._drag_items)
            anchor_time = round((anchor_original + delta) / frame) * frame
            if not (event.modifiers() & Qt.AltModifier):
                other_times = [
                    keyframe.time for _, keyframe, _ in self._keyframe_points()
                    if id(keyframe) not in self._selected_ids
                ]
                if other_times:
                    nearest = min(other_times, key=lambda value: abs(value - anchor_time))
                    if abs(nearest - anchor_time) * self.pixels_per_second <= 7:
                        anchor_time = nearest
            snapped_delta = anchor_time - anchor_original
            for keyframe, original in self._drag_items:
                keyframe.time = max(0.0, min(self.action.duration, original + snapped_delta))
            for property_name in ANIMATABLE_PROPERTIES:
                track = self._track(property_name)
                if track:
                    track.keyframes.sort(key=lambda item: item.time)
            self.changed.emit()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._mode == "select":
            rect = self._selection_rect.normalized()
            self._selected_ids = {
                id(keyframe) for _, keyframe, point in self._keyframe_points() if rect.contains(point)
            }
            self._selection_rect = QRectF()
        elif self._mode == "drag":
            self.edit_finished.emit()
        self._mode = None
        self.update()

    def copy_selected(self) -> None:
        selected = [
            (property_name, keyframe) for property_name, keyframe, _ in self._keyframe_points()
            if id(keyframe) in self._selected_ids
        ]
        if not selected:
            return
        first_time = min(keyframe.time for _, keyframe in selected)
        self._clipboard = [
            (property_name, keyframe.time - first_time, keyframe.value, keyframe.interpolation)
            for property_name, keyframe in selected
        ]

    def paste_selected(self) -> None:
        if not self._clipboard or self.action is None or self.layer is None:
            return
        self.edit_started.emit()
        self._selected_ids.clear()
        for property_name, offset, value, interpolation in self._clipboard:
            track = self.action.get_track(
                self.layer.id, property_name, float(getattr(self.layer, property_name))
            )
            keyframe = Keyframe(
                min(self.action.duration, self.current_time + offset), value, interpolation
            )
            track.add_keyframe(keyframe)
            self._selected_ids.add(id(keyframe))
        self.changed.emit()
        self.edit_finished.emit()
        self.update()

    def delete_selected(self) -> None:
        if not self._selected_ids:
            return
        self.edit_started.emit()
        for property_name in ANIMATABLE_PROPERTIES:
            track = self._track(property_name)
            if track:
                track.keyframes = [
                    keyframe for keyframe in track.keyframes if id(keyframe) not in self._selected_ids
                ]
        self._selected_ids.clear()
        self.changed.emit()
        self.edit_finished.emit()
        self.update()

    def scale_selected(self, factor: float) -> None:
        selected = [
            keyframe for _, keyframe, _ in self._keyframe_points()
            if id(keyframe) in self._selected_ids
        ]
        if not selected or self.action is None:
            return
        origin = min(keyframe.time for keyframe in selected)
        self.edit_started.emit()
        for keyframe in selected:
            keyframe.time = min(self.action.duration, origin + (keyframe.time - origin) * factor)
        for property_name in ANIMATABLE_PROPERTIES:
            track = self._track(property_name)
            if track:
                track.keyframes.sort(key=lambda item: item.time)
        self.changed.emit()
        self.edit_finished.emit()
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.Copy):
            self.copy_selected()
        elif event.matches(QKeySequence.Paste):
            self.paste_selected()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(event)
