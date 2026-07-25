"""设置对话框 —— 从托盘菜单「设置」打开。

集中暴露可调配置：关闭行为、工具栏隐藏延时、默认置顶、启动主题等。
改完点「应用」即时生效 + 持久化。
"""
from __future__ import annotations

from typing import Optional, Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QSpinBox,
    QCheckBox, QDialogButtonBox, QLabel, QGroupBox,
)

from ..config import UiConfig


class SettingsDialog(QDialog):
    """设置对话框。接受一个 UiConfig 实例，修改后回调通知应用。"""

    def __init__(self, cfg: UiConfig, on_apply: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.on_apply = on_apply
        self.setWindowTitle("设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(380)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ---- 关闭行为组 ----
        close_group = QGroupBox("关闭按钮行为")
        close_form = QFormLayout(close_group)
        self.close_combo = QComboBox()
        self.close_combo.addItem("每次询问", "ask")
        self.close_combo.addItem("直接隐藏到托盘", "hide")
        self.close_combo.addItem("直接退出程序", "quit")
        idx = self.close_combo.findData(self.cfg.close_action)
        self.close_combo.setCurrentIndex(max(0, idx))
        close_form.addRow("点 ✕ / Alt+F4 时：", self.close_combo)
        layout.addWidget(close_group)

        # ---- 界面行为组 ----
        ui_group = QGroupBox("界面行为")
        ui_form = QFormLayout(ui_group)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(200, 5000)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setSuffix(" 毫秒")
        self.delay_spin.setValue(self.cfg.toolbar_hide_delay_ms)
        ui_form.addRow("工具栏自动隐藏延时：", self.delay_spin)

        self.topmost_check = QCheckBox("启动时窗口置顶")
        self.topmost_check.setChecked(self.cfg.always_on_top)
        ui_form.addRow(self.topmost_check)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("黑底白字", "dark")
        self.theme_combo.addItem("白底黑字", "light")
        tidx = self.theme_combo.findData(self.cfg.theme)
        self.theme_combo.setCurrentIndex(max(0, tidx))
        ui_form.addRow("启动主题：", self.theme_combo)

        self.maxchars_spin = QSpinBox()
        self.maxchars_spin.setRange(1000, 200000)
        self.maxchars_spin.setSingleStep(1000)
        self.maxchars_spin.setSuffix(" 字符")
        self.maxchars_spin.setValue(self.cfg.max_chars)
        ui_form.addRow("字幕最大字符数：", self.maxchars_spin)
        layout.addWidget(ui_group)

        # ---- 按钮 ----
        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # 说明
        layout.addWidget(QLabel("提示：主题、置顶、字体、字号、透明度也可在工具栏中实时调整。"))

    def _on_apply(self):
        self.cfg.close_action = self.close_combo.currentData()
        self.cfg.toolbar_hide_delay_ms = self.delay_spin.value()
        self.cfg.always_on_top = self.topmost_check.isChecked()
        self.cfg.theme = self.theme_combo.currentData()
        self.cfg.max_chars = self.maxchars_spin.value()
        if self.on_apply is not None:
            self.on_apply()
        self.accept()
