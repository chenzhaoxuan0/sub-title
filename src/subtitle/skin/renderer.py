"""Qt renderer for responsive, animated subtitle skin layers."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap, QPolygonF, QTransform

from .model import (
    AssetType,
    HorizontalPin,
    Layer,
    LayerPlane,
    SkinDefinition,
    VerticalPin,
)


class SkinRenderer:
    """Render the base pose plus transient action overrides."""

    def __init__(self, skin: SkinDefinition, base_dir: Path):
        self._skin = skin
        self._base_dir = Path(base_dir)
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._current_time = 0.0
        self._overrides: dict[str, dict[str, float]] = {}
        self._layer_times: dict[str, float] = {}

    @property
    def skin(self) -> SkinDefinition:
        return self._skin

    @skin.setter
    def skin(self, value: SkinDefinition) -> None:
        self._skin = value
        self.invalidate_cache()
        self.clear_runtime_state()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: Path) -> None:
        self._base_dir = Path(value)
        self.invalidate_cache()

    def set_time(self, time_value: float) -> None:
        self._current_time = max(0.0, float(time_value))

    def set_runtime_state(
        self,
        overrides: Optional[dict[str, dict[str, float]]] = None,
        layer_times: Optional[dict[str, float]] = None,
    ) -> None:
        self._overrides = overrides or {}
        self._layer_times = layer_times or {}

    def clear_runtime_state(self) -> None:
        self._overrides = {}
        self._layer_times = {}

    def render(
        self,
        painter: QPainter,
        width: int,
        height: int,
        plane: Optional[LayerPlane | str] = None,
    ) -> None:
        requested_plane = LayerPlane(plane) if isinstance(plane, str) else plane
        for layer in self._skin.layers:
            if not layer.visible or (requested_plane is not None and layer.plane != requested_plane):
                continue
            self._render_layer(painter, layer, width, height)

    def _value(self, layer: Layer, property_name: str) -> float:
        layer_override = self._overrides.get(layer.id, {})
        if property_name in layer_override:
            return float(layer_override[property_name])
        return layer.get_animated_value(property_name, self._current_time)

    def _render_layer(self, painter: QPainter, layer: Layer, canvas_w: int, canvas_h: int) -> None:
        pixmap = self._get_pixmap(layer)
        if pixmap is None or pixmap.isNull():
            return
        opacity = self._value(layer, "opacity")
        if opacity <= 0:
            return
        transform = self.get_layer_transform(layer, canvas_w, canvas_h, pixmap)
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        composition_modes = {
            "multiply": QPainter.CompositionMode_Multiply,
            "screen": QPainter.CompositionMode_Screen,
            "overlay": QPainter.CompositionMode_Overlay,
        }
        if layer.blend_mode in composition_modes:
            painter.setCompositionMode(composition_modes[layer.blend_mode])
        painter.setTransform(transform, combine=True)
        painter.drawPixmap(
            int(-pixmap.width() * layer.anchor_x),
            int(-pixmap.height() * layer.anchor_y),
            pixmap,
        )
        painter.restore()

    def _reference_scale(self, canvas_w: int, canvas_h: int) -> float:
        design_w = max(1, self._skin.design_width)
        design_h = max(1, self._skin.design_height)
        return max(0.0001, min(canvas_w / design_w, canvas_h / design_h))

    @staticmethod
    def _pin_value(pin: HorizontalPin | VerticalPin, size: float) -> float:
        if pin.value == "center":
            return size / 2
        if pin.value in ("right", "bottom"):
            return size
        return 0.0

    def resolve_layer_origin(self, layer: Layer, canvas_w: int, canvas_h: int) -> QPointF:
        scale = self._reference_scale(canvas_w, canvas_h)
        reference_x = self._pin_value(layer.pin_x, self._skin.design_width)
        reference_y = self._pin_value(layer.pin_y, self._skin.design_height)
        target_x = self._pin_value(layer.pin_x, canvas_w)
        target_y = self._pin_value(layer.pin_y, canvas_h)
        x = target_x + (self._value(layer, "x") - reference_x) * scale
        y = target_y + (self._value(layer, "y") - reference_y) * scale
        return QPointF(x, y)

    def get_layer_transform(
        self,
        layer: Layer,
        canvas_w: int,
        canvas_h: int,
        pixmap: Optional[QPixmap] = None,
    ) -> QTransform:
        pixmap = pixmap or self._get_pixmap(layer)
        if pixmap is None:
            return QTransform()
        origin = self.resolve_layer_origin(layer, canvas_w, canvas_h)
        reference_scale = self._reference_scale(canvas_w, canvas_h)
        scale_x = self._value(layer, "scale_x") * reference_scale
        scale_y = self._value(layer, "scale_y") * reference_scale
        pivot_x = origin.x() + pixmap.width() * layer.anchor_x * scale_x
        pivot_y = origin.y() + pixmap.height() * layer.anchor_y * scale_y
        transform = QTransform()
        transform.translate(pivot_x, pivot_y)
        transform.rotate(self._value(layer, "rotation"))
        transform.scale(scale_x, scale_y)
        return transform

    def get_layer_polygon(self, layer: Layer, canvas_w: int, canvas_h: int) -> QPolygonF:
        pixmap = self._get_pixmap(layer)
        if pixmap is None or pixmap.isNull():
            return QPolygonF()
        left = -pixmap.width() * layer.anchor_x
        top = -pixmap.height() * layer.anchor_y
        local = QPolygonF([
            QPointF(left, top),
            QPointF(left + pixmap.width(), top),
            QPointF(left + pixmap.width(), top + pixmap.height()),
            QPointF(left, top + pixmap.height()),
        ])
        return self.get_layer_transform(layer, canvas_w, canvas_h, pixmap).map(local)

    def get_layer_bounds(
        self,
        layer: Layer,
        time_value: float = 0.0,
        canvas_w: Optional[int] = None,
        canvas_h: Optional[int] = None,
    ) -> QRectF:
        previous = self._current_time
        self._current_time = time_value
        try:
            width = canvas_w if canvas_w is not None else self._skin.design_width
            height = canvas_h if canvas_h is not None else self._skin.design_height
            return self.get_layer_polygon(layer, width, height).boundingRect()
        finally:
            self._current_time = previous

    def layer_at(
        self,
        point: QPointF,
        canvas_w: int,
        canvas_h: int,
        plane: Optional[LayerPlane] = None,
        alpha_test: bool = True,
    ) -> Optional[Layer]:
        for layer in reversed(self._skin.layers):
            if not layer.visible or (plane is not None and layer.plane != plane):
                continue
            pixmap = self._get_pixmap(layer)
            if pixmap is None or pixmap.isNull():
                continue
            inverse, invertible = self.get_layer_transform(layer, canvas_w, canvas_h, pixmap).inverted()
            if not invertible:
                continue
            local = inverse.map(point)
            image_x = int(local.x() + pixmap.width() * layer.anchor_x)
            image_y = int(local.y() + pixmap.height() * layer.anchor_y)
            if not (0 <= image_x < pixmap.width() and 0 <= image_y < pixmap.height()):
                continue
            if alpha_test and pixmap.toImage().pixelColor(image_x, image_y).alpha() <= 10:
                continue
            return layer
        return None

    def _get_pixmap(self, layer: Layer) -> Optional[QPixmap]:
        path = self._asset_path_at(layer)
        if not path:
            return None
        if path in self._pixmap_cache:
            return self._pixmap_cache[path]
        resolved = self._base_dir / path
        if not resolved.exists():
            resolved = Path(path)
        if not resolved.exists():
            return None
        pixmap = QPixmap(str(resolved))
        if pixmap.isNull():
            return None
        self._pixmap_cache[path] = pixmap
        return pixmap

    def _asset_path_at(self, layer: Layer) -> str:
        if layer.asset_type != AssetType.SEQUENCE or not layer.sequence_frames:
            return layer.image_path
        frame_count = len(layer.sequence_frames)
        frame_time = self._layer_times.get(layer.id, self._current_time)
        index = max(0, int(frame_time * max(0.01, layer.sequence_fps)))
        if layer.sequence_loop:
            index %= frame_count
        else:
            index = min(index, frame_count - 1)
        return layer.sequence_frames[index]

    def invalidate_cache(self) -> None:
        self._pixmap_cache.clear()
