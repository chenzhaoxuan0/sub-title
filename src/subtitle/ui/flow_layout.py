"""自动换行水平布局 —— 当一行放不下时自动换到下一行。

移植自 Qt 官方示例（QLayout 子类化），适配 PySide6。
用在小尺寸容器里塞多个按钮（比如设置对话框的主题管理行 8 个按钮），
避免横向被挤出去后需要手动拉宽窗口才能看到。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QStyle, QWidget


class FlowLayout(QLayout):
    """水平 flow 布局：行满自动换。"""

    def __init__(self, parent: QWidget | None = None,
                 margin: int = 0, h_spacing: int = -1, v_spacing: int = -1):
        super().__init__(parent)
        if parent is not None and margin > 0:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items: list[QLayout.Item] = []

    def __del__(self):
        while self.count():
            item = self.takeAt(0)
            if item is not None:
                del item

    # ---- QLayout 接口 ----
    def addItem(self, item: QLayout.Item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayout.Item | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayout.Item | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        # 标准 Qt 示例直接 return minimumSize()，这在"父布局不查 heightForWidth"时
        # 会让父布局以为这个 widget 只要 30px 高（一个按钮的高度），
        # FlowLayout 里的多行按钮就被裁掉。
        # 改为：假设一个合理宽度（≈400px，能放 5-6 个 70px 按钮），
        # 按这个宽度算需要的高度，让父布局知道 widget 真正想要多大。
        assumed_w = 400
        h = self.heightForWidth(assumed_w)
        return QSize(assumed_w, h)

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    # ---- 内部 ----
    def _smart_spacing(self, pm: QStyle.PixelMetric) -> int:
        parent = self.parent()
        if parent is None:
            return -1
        if parent.isWidgetType():
            return parent.style().pixelMetric(pm, None, parent)
        return parent.spacing()

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        h_space = (self._h_space if self._h_space >= 0
                   else self._smart_spacing(QStyle.PM_LayoutHorizontalSpacing))
        v_space = (self._v_space if self._v_space >= 0
                   else self._smart_spacing(QStyle.PM_LayoutVerticalSpacing))

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + h_space
            # 行满 → 折行
            if next_x - h_space > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + v_space
                next_x = x + hint.width() + h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + m.bottom()
