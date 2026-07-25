"""皮肤渲染器 —— 将图层按时间渲染到 QPainter 上。

职责：
- 加载图层图片（缓存）
- 按时间计算每个图层的动画属性值
- 按图层顺序（底→顶）绘制到画布
- 支持混合模式
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import QPainter, QPixmap, QTransform, QImage

from .model import SkinDefinition, Layer


class SkinRenderer:
    """皮肤渲染器：给定时间 t，渲染所有图层。"""

    def __init__(self, skin: SkinDefinition, base_dir: Path):
        self._skin = skin
        self._base_dir = base_dir  # 皮肤资源目录（图片相对路径的根）
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._current_time: float = 0.0

    @property
    def skin(self) -> SkinDefinition:
        return self._skin

    @skin.setter
    def skin(self, value: SkinDefinition):
        self._skin = value
        self._pixmap_cache.clear()

    def set_time(self, t: float):
        """设置当前播放时间。"""
        self._current_time = t

    def render(self, painter: QPainter, width: int, height: int):
        """渲染所有可见图层到 painter。"""
        t = self._current_time
        for layer in self._skin.layers:
            if not layer.visible:
                continue
            self._render_layer(painter, layer, t, width, height)

    def _render_layer(self, painter: QPainter, layer: Layer, t: float,
                      canvas_w: int, canvas_h: int):
        """渲染单个图层。"""
        pixmap = self._get_pixmap(layer)
        if pixmap is None or pixmap.isNull():
            return

        # 获取动画属性值
        x = layer.get_animated_value("x", t)
        y = layer.get_animated_value("y", t)
        sx = layer.get_animated_value("scale_x", t)
        sy = layer.get_animated_value("scale_y", t)
        rot = layer.get_animated_value("rotation", t)
        opacity = layer.get_animated_value("opacity", t)

        if opacity <= 0:
            return

        # 计算变换
        img_w = pixmap.width() * sx
        img_h = pixmap.height() * sy

        # 锚点（变换中心）
        ax = x + pixmap.width() * layer.anchor_x * sx
        ay = y + pixmap.height() * layer.anchor_y * sy

        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))

        # 设置混合模式
        if layer.blend_mode == "multiply":
            painter.setCompositionMode(QPainter.CompositionMode_Multiply)
        elif layer.blend_mode == "screen":
            painter.setCompositionMode(QPainter.CompositionMode_Screen)
        elif layer.blend_mode == "overlay":
            painter.setCompositionMode(QPainter.CompositionMode_Overlay)

        # 平移到锚点 → 旋转 → 平移回 → 绘制
        painter.translate(ax, ay)
        if rot != 0:
            painter.rotate(rot)
        painter.scale(sx, sy)
        # 绘制位置：以锚点为中心
        draw_x = -pixmap.width() * layer.anchor_x
        draw_y = -pixmap.height() * layer.anchor_y
        painter.drawPixmap(int(draw_x), int(draw_y), pixmap)

        painter.restore()

    def _get_pixmap(self, layer: Layer) -> Optional[QPixmap]:
        """获取图层图片（带缓存）。"""
        if not layer.image_path:
            return None
        if layer.image_path in self._pixmap_cache:
            return self._pixmap_cache[layer.image_path]

        # 解析路径
        img_path = self._base_dir / layer.image_path
        if not img_path.exists():
            # 尝试绝对路径
            img_path = Path(layer.image_path)
        if not img_path.exists():
            return None

        pixmap = QPixmap(str(img_path))
        if not pixmap.isNull():
            self._pixmap_cache[layer.image_path] = pixmap
        return pixmap

    def invalidate_cache(self):
        """清除图片缓存（图片文件变更时调用）。"""
        self._pixmap_cache.clear()

    def get_layer_bounds(self, layer: Layer, t: float) -> QRectF:
        """获取图层在指定时间的边界矩形（用于编辑器选中检测）。"""
        pixmap = self._get_pixmap(layer)
        if pixmap is None:
            return QRectF()

        x = layer.get_animated_value("x", t)
        y = layer.get_animated_value("y", t)
        sx = layer.get_animated_value("scale_x", t)
        sy = layer.get_animated_value("scale_y", t)

        w = pixmap.width() * sx
        h = pixmap.height() * sy
        return QRectF(x, y, w, h)
