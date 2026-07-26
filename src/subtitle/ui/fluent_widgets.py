"""Fluent 风格组件（手写，纯 QSS，无第三方库依赖）。

参照 Microsoft Fluent Design 的 SettingCard 视觉规范，用 QSS + 少量自绘实现：
- SettingCard：圆角卡片，左侧标题+说明，右侧控件槽
- SettingCardGroup：分组容器（顶部标题 + 纵向堆叠卡片）
- ToggleSwitch：开关（替代 QCheckBox，checked 态变 accent 色）

配色跟随 ThemeColors（由 settings_dialog 的主题应用传入），保持与字幕窗口主题一致。
不引入 GPLv3 的 PyQt-Fluent-Widgets，本项目保持 MIT 许可证。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF, QSize
from PySide6.QtGui import QPainter, QColor, QPalette
from PySide6.QtWidgets import (
    QFrame, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSizePolicy,
)


# ------------------------------------------------------------------
# 颜色工具：把 hex 颜色按比例提亮/变暗，用于卡片背景等派生色
# ------------------------------------------------------------------
def _mix(hex_color: str, target: str, ratio: float) -> str:
    """把 hex_color 向 target 混合 ratio（0=原色，1=全 target）。"""
    def parse(h: str):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r1, g1, b1 = parse(hex_color)
    r2, g2, b2 = parse(target)
    r = int(r1 + (r2 - r1) * ratio)
    g = int(g1 + (g2 - g1) * ratio)
    b = int(b1 + (b2 - b1) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def lighten(hex_color: str, ratio: float) -> str:
    """向白色提亮。"""
    return _mix(hex_color, "#ffffff", ratio)


def darken(hex_color: str, ratio: float) -> str:
    """向黑色变暗。"""
    return _mix(hex_color, "#000000", ratio)


def is_dark(hex_color: str) -> bool:
    """判断颜色是否偏暗（用于选文字色）。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    # 感知亮度
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


# ------------------------------------------------------------------
# SettingCard：圆角卡片，左侧标题+说明，右侧控件槽
# ------------------------------------------------------------------
class SettingCard(QFrame):
    """Fluent 风格设置卡片。

    左侧：标题（粗体）+ 说明（小号灰字）
    右侧：控件（通过 add_widget 或构造时传入）
    """

    def __init__(self, title: str, content: str = "", widget: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("settingCard")
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._hbox = QHBoxLayout(self)
        self._hbox.setContentsMargins(16, 8, 16, 8)
        self._hbox.setSpacing(12)

        # 左侧文字列
        self._text_col = QWidget()
        text_layout = QVBoxLayout(self._text_col)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("cardTitle")
        self._content_label = QLabel(content)
        self._content_label.setObjectName("cardContent")
        text_layout.addWidget(self._title_label)
        text_layout.addWidget(self._content_label)
        text_layout.addStretch(1)
        self._hbox.addWidget(self._text_col, 1)

        # 右侧控件
        self._widget: QWidget | None = None
        if widget is not None:
            self.set_widget(widget)

    def set_widget(self, widget: QWidget):
        """设置右侧控件。"""
        if self._widget is not None:
            self._widget.setParent(None)
        self._widget = widget
        widget.setParent(self)
        self._hbox.addWidget(widget)

    def get_widget(self):
        return self._widget

    def set_title(self, title: str):
        self._title_label.setText(title)

    def set_content(self, content: str):
        self._content_label.setText(content)
        self._content_label.setVisible(bool(content))


# ------------------------------------------------------------------
# SettingCardGroup：分组容器（顶部标题 + 纵向堆叠卡片）
# ------------------------------------------------------------------
class SettingCardGroup(QFrame):
    """一组卡片的容器，顶部带分组标题。"""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("cardGroup")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)

        if title:
            self._title_label = QLabel(title)
            self._title_label.setObjectName("groupTitle")
            self._layout.addWidget(self._title_label)

        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(2)
        self._layout.addWidget(self._cards_widget)

    def add_card(self, card: SettingCard):
        self._cards_layout.addWidget(card)

    def add_cards(self, cards: list):
        for c in cards:
            self._cards_layout.addWidget(c)

    def add_stretch(self):
        self._cards_layout.addStretch(1)


# ------------------------------------------------------------------
# ToggleSwitch：开关（替代 QCheckBox）
# ------------------------------------------------------------------
class ToggleSwitch(QWidget):
    """Fluent 风格开关。checkedChanged 信号替代 QCheckBox.toggled。"""

    checkedChanged = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._checked = checked
        self._accent = "#0078d4"      # 默认 accent，由主题应用覆盖
        self._track_bg = "#666666"
        self._track_bg_off = "#666666"
        self._knob_color = "#ffffff"

    def set_accent(self, accent: str):
        self._accent = accent
        self.update()

    def set_track_colors(self, off_color: str, knob_color: str):
        self._track_bg_off = off_color
        self._knob_color = knob_color
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if self._checked != checked:
            self._checked = checked
            self.checkedChanged.emit(checked)
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
        super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 轨道
        track_color = self._accent if self._checked else self._track_bg_off
        p.setBrush(QColor(track_color))
        p.setPen(Qt.NoPen)
        p.drawRoundedFRect = None  # 避免拼写警告
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 11, 11)
        # 滑块
        knob_r = 9
        if self._checked:
            knob_x = self.width() - knob_r - 2
        else:
            knob_x = knob_r + 2
        p.setBrush(QColor(self._knob_color))
        p.drawEllipse(QRectF(knob_x - knob_r, self.height() / 2 - knob_r, knob_r * 2, knob_r * 2))


# ------------------------------------------------------------------
# Fluent 风格 QSS 生成（由 settings_dialog 调用，传入 ThemeColors）
# ------------------------------------------------------------------
def build_fluent_qss(colors) -> str:
    """生成整套 Fluent 风格 QSS 字符串。

    colors: ThemeColors dataclass（来自 theme_engine）。
    用到的字段：tray_bg, tray_text, accent, subtitle_border, btn_*,
                toolbar_bg, combo_* 等。
    """
    bg = colors.tray_bg
    text = colors.tray_text
    accent = colors.accent
    border = colors.subtitle_border
    # 卡片背景 = 对话框底色提亮一点（深色主题提亮、浅色主题略暗）
    card_bg = lighten(bg, 0.08) if is_dark(bg) else darken(bg, 0.04)
    card_hover = lighten(card_bg, 0.05)
    content_color = lighten(text, -0.35) if is_dark(text) or not is_dark(bg) \
        else darken(text, 0.35)
    # 简化：说明文字统一调暗
    content_color = darken(text, 0.4) if is_dark(bg) else darken(text, 0.45)

    return f"""
        QDialog {{
            background-color: {bg};
            color: {text};
        }}
        QTabWidget::pane {{
            border: 1px solid {border};
            border-radius: 8px;
            background-color: {bg};
            top: -1px;
        }}
        QTabBar::tab {{
            background-color: transparent;
            color: {content_color};
            padding: 10px 20px;
            margin-right: 2px;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            font-size: 13px;
        }}
        QTabBar::tab:selected {{
            color: {text};
            border-bottom: 2px solid {accent};
        }}
        QTabBar::tab:hover:!selected {{
            color: {text};
            background-color: {card_hover};
        }}
        /* 卡片 */
        #settingCard {{
            background-color: {card_bg};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        #settingCard:hover {{
            background-color: {card_hover};
            border-color: {accent};
        }}
        #cardTitle {{
            color: {text};
            font-size: 13px;
            font-weight: bold;
            background: transparent;
            border: none;
        }}
        #cardContent {{
            color: {content_color};
            font-size: 11px;
            background: transparent;
            border: none;
        }}
        #groupTitle {{
            color: {accent};
            font-size: 13px;
            font-weight: bold;
            padding: 4px 0;
            background: transparent;
            border: none;
        }}
        /* QScrollArea 透明（透出对话框底色，避免白底） */
        QScrollArea, QScrollArea > QWidget > QWidget {{
            background-color: transparent;
            border: none;
        }}
        QLabel {{
            color: {text};
            background: transparent;
            border: none;
        }}
        QGroupBox {{
            color: {text};
            background-color: transparent;
            border: 1px solid {border};
            border-radius: 8px;
            margin-top: 12px;
            padding-top: 12px;
            font-weight: bold;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: {accent};
        }}
        /* 控件 Fluent 化 */
        QPushButton {{
            background-color: {colors.btn_bg};
            color: {colors.btn_text};
            border: 1px solid {colors.btn_border};
            border-radius: 6px;
            padding: 6px 16px;
            font-size: 13px;
        }}
        QPushButton:hover {{
            background-color: {colors.btn_hover};
        }}
        QPushButton:pressed {{
            background-color: {darken(colors.btn_bg, 0.15)};
        }}
        QPushButton:disabled {{
            color: {colors.btn_disabled_text};
            background-color: {colors.btn_disabled_bg};
        }}
        /* 主色按钮（PrimaryPushButton 样式）*/
        QPushButton[primary="true"] {{
            background-color: {accent};
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 6px 16px;
            font-weight: bold;
        }}
        QPushButton[primary="true"]:hover {{
            background-color: {colors.accent_hover};
        }}
        QComboBox {{
            background-color: {card_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 5px 10px;
            min-height: 22px;
        }}
        QComboBox:hover {{
            border-color: {accent};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 22px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {colors.combo_bg};
            color: {colors.combo_text};
            selection-background-color: {colors.combo_selected};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px;
        }}
        QSpinBox, QDoubleSpinBox {{
            background-color: {card_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px 8px;
            min-height: 22px;
        }}
        QSpinBox:hover, QDoubleSpinBox:hover {{
            border-color: {accent};
        }}
        QLineEdit {{
            background-color: {card_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 5px 8px;
            min-height: 22px;
        }}
        QLineEdit:focus {{
            border-color: {accent};
        }}
        QCheckBox {{ background: transparent; color: {text}; spacing: 6px; }}
        QSlider::groove:horizontal {{
            height: 4px; background: {border}; border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {accent}; width: 14px; height: 14px;
            margin: -6px 0; border-radius: 7px;
        }}
        QSlider::sub-page:horizontal {{
            background: {accent}; border-radius: 2px;
        }}
        QTextEdit {{
            background-color: {card_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
        }}
        QListWidget {{
            background-color: {card_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
        }}
        QListWidget::item:selected {{
            background-color: {accent};
            color: #ffffff;
        }}
        QFontComboBox {{
            background-color: {card_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px 8px;
            min-height: 22px;
        }}
        QDialogButtonBox QPushButton {{
            min-width: 80px;
        }}
    """
