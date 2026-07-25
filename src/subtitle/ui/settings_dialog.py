"""全功能设置对话框 —— 双标签页（设置 / 文稿回看）。

所有操作通过 SubtitlePanel 的公共 API 驱动，避免逻辑分叉。
点「应用」即时生效；切换到「文稿回看」标签页自动刷新全文。
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QClipboard
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QSpinBox,
    QCheckBox, QDialogButtonBox, QLabel, QGroupBox, QTabWidget, QWidget,
    QTextEdit, QPushButton, QFontComboBox, QApplication, QMessageBox,
)

from ..config import UiConfig


class SettingsDialog(QDialog):
    """设置对话框。持有 panel 引用，直接驱动 panel 做改动。"""

    def __init__(self, cfg: UiConfig, panel, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.panel = panel
        self.setWindowTitle("设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(520, 560)
        self._init_ui()
        self._load_current_state()

    # ---------- 初始化 ----------
    def _init_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        tabs.addTab(self._build_settings_tab(), "⚙ 设置")
        tabs.addTab(self._build_transcript_tab(), "📄 文稿回看")
        tabs.currentChanged.connect(self._on_tab_changed)

        # 底部按钮
        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ---------- 标签页1：设置 ----------
    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)

        # ---- 识别 ----
        g_recog = QGroupBox("识别")
        f_recog = QFormLayout(g_recog)
        self.device_combo = QComboBox()
        # 用 panel 的设备列表填充
        for name, data in self.panel.get_devices():
            self.device_combo.addItem(name, data)
        self.device_combo.setCurrentIndex(0)
        f_recog.addRow("输入源：", self.device_combo)

        recog_btns = QHBoxLayout()
        self.start_btn = QPushButton("▶ 开始识别")
        self.stop_btn = QPushButton("■ 停止识别")
        self.start_btn.clicked.connect(self._on_start_click)
        self.stop_btn.clicked.connect(self._on_stop_click)
        recog_btns.addWidget(self.start_btn)
        recog_btns.addWidget(self.stop_btn)
        recog_btns.addStretch(1)
        f_recog.addRow("", recog_btns)
        v.addWidget(g_recog)

        # ---- 外观 ----
        g_appearance = QGroupBox("外观")
        f_appearance = QFormLayout(g_appearance)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("黑底白字", "dark")
        self.theme_combo.addItem("白底黑字", "light")
        f_appearance.addRow("主题：", self.theme_combo)

        self.font_combo = QFontComboBox()
        f_appearance.addRow("字体：", self.font_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 72)
        self.font_size_spin.setSuffix(" pt")
        f_appearance.addRow("字号：", self.font_size_spin)

        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setSuffix("%")
        f_appearance.addRow("背景透明度：", self.opacity_spin)
        v.addWidget(g_appearance)

        # ---- 窗口 ----
        g_win = QGroupBox("字幕窗口")
        f_win = QFormLayout(g_win)
        win_row = QHBoxLayout()
        self.win_w_spin = QSpinBox()
        self.win_w_spin.setRange(self.panel.minimumWidth(), 4000)
        self.win_w_spin.setSuffix(" px")
        self.win_h_spin = QSpinBox()
        self.win_h_spin.setRange(self.panel.minimumHeight(), 4000)
        self.win_h_spin.setSuffix(" px")
        win_row.addWidget(self.win_w_spin)
        win_row.addWidget(self.win_h_spin)
        self.apply_size_btn = QPushButton("应用尺寸")
        self.apply_size_btn.clicked.connect(self._on_apply_size)
        win_row.addWidget(self.apply_size_btn)
        f_win.addRow("宽 × 高：", win_row)
        min_hint = QLabel(f"最小尺寸：{self.panel.minimumWidth()} × {self.panel.minimumHeight()} px")
        min_hint.setStyleSheet("color: #888;")
        f_win.addRow("", min_hint)
        v.addWidget(g_win)

        # ---- 行为 ----
        g_behavior = QGroupBox("行为")
        f_behavior = QFormLayout(g_behavior)
        self.topmost_check = QCheckBox("启动时窗口置顶")
        f_behavior.addRow(self.topmost_check)

        self.lock_scroll_check = QCheckBox("锁定滚动到底部（新字幕强制跟随）")
        f_behavior.addRow(self.lock_scroll_check)

        scroll_row = QHBoxLayout()
        self.scroll_btn = QPushButton("📍 立刻滚动到底部")
        self.scroll_btn.clicked.connect(self._on_scroll_bottom)
        scroll_row.addWidget(self.scroll_btn)
        scroll_row.addStretch(1)
        f_behavior.addRow("", scroll_row)

        self.close_combo = QComboBox()
        self.close_combo.addItem("每次询问", "ask")
        self.close_combo.addItem("直接隐藏到托盘", "hide")
        self.close_combo.addItem("直接退出程序", "quit")
        f_behavior.addRow("点 ✕ / Alt+F4 时：", self.close_combo)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(200, 5000)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setSuffix(" 毫秒")
        f_behavior.addRow("工具栏自动隐藏延时：", self.delay_spin)
        v.addWidget(g_behavior)

        # ---- 字幕 ----
        g_sub = QGroupBox("字幕文本")
        f_sub = QFormLayout(g_sub)
        self.maxchars_spin = QSpinBox()
        self.maxchars_spin.setRange(1000, 200000)
        self.maxchars_spin.setSingleStep(1000)
        self.maxchars_spin.setSuffix(" 字符")
        f_sub.addRow("字幕最大字符数：", self.maxchars_spin)

        clear_row = QHBoxLayout()
        self.clear_btn = QPushButton("🗑 清空当前字幕")
        self.clear_btn.clicked.connect(self._on_clear_transcript)
        clear_row.addWidget(self.clear_btn)
        clear_row.addStretch(1)
        f_sub.addRow("", clear_row)
        v.addWidget(g_sub)

        v.addStretch(1)
        return tab

    # ---------- 标签页2：文稿回看 ----------
    def _build_transcript_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        btns = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 刷新")
        self.copy_btn = QPushButton("📋 复制全部")
        self.clear_btn2 = QPushButton("🗑 清空")
        self.refresh_btn.clicked.connect(self._refresh_transcript)
        self.copy_btn.clicked.connect(self._copy_transcript)
        self.clear_btn2.clicked.connect(self._on_clear_transcript)
        btns.addWidget(self.refresh_btn)
        btns.addWidget(self.copy_btn)
        btns.addWidget(self.clear_btn2)
        btns.addStretch(1)
        self.transcript_view = QTextEdit()
        self.transcript_view.setReadOnly(True)
        self.transcript_view.setPlaceholderText("（暂无字幕文本）")
        v.addLayout(btns)
        v.addWidget(self.transcript_view, 1)
        return tab

    # ---------- 加载当前状态 ----------
    def _load_current_state(self):
        # 识别按钮初始状态
        self._update_recog_buttons()
        # 外观
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(self.panel.get_theme())))
        self.font_combo.setCurrentFont(QFont(self.panel.get_font_family()))
        self.font_size_spin.setValue(self.panel.get_font_size())
        self.opacity_spin.setValue(self.panel.get_opacity())
        # 窗口
        w, h = self.panel.get_window_size()
        self.win_w_spin.setValue(w)
        self.win_h_spin.setValue(h)
        # 行为
        self.topmost_check.setChecked(self.panel.get_pin())
        self.lock_scroll_check.setChecked(self.panel.get_lock_scroll())
        self.close_combo.setCurrentIndex(
            max(0, self.close_combo.findData(self.cfg.close_action)))
        self.delay_spin.setValue(self.cfg.toolbar_hide_delay_ms)
        # 字幕
        self.maxchars_spin.setValue(self.cfg.max_chars)

    def _update_recog_buttons(self):
        running = self.panel.is_recording()
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    # ---------- 各按钮事件 ----------
    def _on_start_click(self):
        dev = self.device_combo.currentData()
        self.panel.start_recognition(dev)
        self._update_recog_buttons()

    def _on_stop_click(self):
        self.panel.stop_recognition()
        self._update_recog_buttons()

    def _on_apply_size(self):
        self.panel.set_window_size(self.win_w_spin.value(), self.win_h_spin.value())

    def _on_scroll_bottom(self):
        self.panel.scroll_to_bottom_now()

    def _on_clear_transcript(self):
        ret = QMessageBox.question(self, "清空字幕", "确定清空当前所有字幕文本？")
        if ret == QMessageBox.Yes:
            self.panel.clear_transcript()
            self._refresh_transcript()

    # ---------- 文稿回看 ----------
    def _refresh_transcript(self):
        self.transcript_view.setPlainText(self.panel.get_transcript())
        # 滚到底，显示最新
        bar = self.transcript_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _copy_transcript(self):
        QApplication.clipboard().setText(self.panel.get_transcript())
        self.copy_btn.setText("✓ 已复制")
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("📋 复制全部"))

    def _on_tab_changed(self, idx: int):
        # 切到文稿回看标签页时自动刷新
        if idx == 1:
            self._refresh_transcript()

    # ---------- 应用（把设置控件值写回 panel + config）----------
    def _on_apply(self):
        p = self.panel
        p.set_theme(self.theme_combo.currentData())
        p.set_font_family(self.font_combo.currentFont().family())
        p.set_font_size(self.font_size_spin.value())
        p.set_opacity(self.opacity_spin.value())
        p.set_window_size(self.win_w_spin.value(), self.win_h_spin.value())
        p.set_pin(self.topmost_check.isChecked())
        p.set_lock_scroll(self.lock_scroll_check.isChecked())
        p.set_close_action(self.close_combo.currentData())
        p.set_toolbar_hide_delay(self.delay_spin.value())
        p.set_max_chars(self.maxchars_spin.value())
        p._notify_geometry()
        self._update_recog_buttons()
        self.accept()
