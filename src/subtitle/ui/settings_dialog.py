"""全功能设置对话框 v2 —— 现代化多标签页 + 颜色选择器 + 主题管理。

标签页：
  1. 识别 — 引擎选择 + 设备 + 参数
  2. 外观 — 主题选择/自定义颜色/几何参数
  3. 行为 — 窗口行为 + 工具栏 + 字幕
  4. 皮肤 — 桌宠贴图皮肤管理
  5. 文稿 — 文稿回看
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QSpinBox,
    QCheckBox, QDialogButtonBox, QLabel, QGroupBox, QTabWidget, QWidget,
    QTextEdit, QPushButton, QFontComboBox, QApplication, QMessageBox,
    QLineEdit, QStackedWidget, QDoubleSpinBox, QColorDialog, QSlider,
    QScrollArea, QFrame, QSizePolicy, QFileDialog, QListWidget,
    QListWidgetItem, QAbstractItemView,
)

from ..config import Config
from .theme_engine import (
    Theme, ThemeColors, ThemeGeometry, ThemeManager, get_theme_manager,
    BUILTIN_THEMES,
)


class ColorButton(QPushButton):
    """颜色选择按钮：显示当前颜色色块，点击弹出颜色选择器。"""

    def __init__(self, color: str = "#000000", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(60, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()
        self.clicked.connect(self._pick_color)

    def _update_style(self):
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {self._color};
                border: 2px solid #888;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                border-color: #aaa;
            }}
        """)

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self._color), self, "选择颜色")
        if color.isValid():
            self._color = color.name()
            self._update_style()

    def get_color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = color
        self._update_style()


class SettingsDialog(QDialog):
    """设置对话框 v2。"""

    def __init__(self, cfg: Config, panel, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.panel = panel
        self._theme_mgr = get_theme_manager()
        self.setWindowTitle("全局设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(600, 700)
        self._init_ui()
        self._load_current_state()
        self._apply_dialog_theme()

    def _apply_dialog_theme(self):
        """设置对话框自身的样式（跟随当前主题）。"""
        colors = self._theme_mgr.current.colors
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {colors.tray_bg};
                color: {colors.tray_text};
            }}
            QTabWidget::pane {{
                border: 1px solid {colors.subtitle_border};
                border-radius: 6px;
                background-color: {colors.tray_bg};
            }}
            /* QScrollArea 及其内容透明，透出 QDialog/Tab 的深色背景，
               否则外观/行为页会显示系统默认白底，白字看不见 */
            QScrollArea, QScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            QFrame[frameShape="0"] {{
                background-color: transparent;
            }}
            QTabBar::tab {{
                background-color: {colors.toolbar_bg};
                color: {colors.toolbar_text};
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{
                background-color: {colors.accent};
                color: #ffffff;
            }}
            QGroupBox {{
                color: {colors.tray_text};
                background-color: transparent;
                border: 1px solid {colors.subtitle_border};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }}
            QLabel {{ color: {colors.tray_text}; }}
            QComboBox {{
                background-color: {colors.btn_bg};
                color: {colors.btn_text};
                border: 1px solid {colors.btn_border};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {colors.combo_bg};
                color: {colors.combo_text};
                selection-background-color: {colors.combo_selected};
            }}
            QSpinBox, QDoubleSpinBox {{
                background-color: {colors.btn_bg};
                color: {colors.btn_text};
                border: 1px solid {colors.btn_border};
                border-radius: 4px;
                padding: 3px 6px;
            }}
            QPushButton {{
                background-color: {colors.btn_bg};
                color: {colors.btn_text};
                border: 1px solid {colors.btn_border};
                border-radius: 5px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{ background-color: {colors.btn_hover}; }}
            QCheckBox {{ color: {colors.tray_text}; spacing: 8px; }}
            QLineEdit {{
                background-color: {colors.combo_bg};
                color: {colors.combo_text};
                border: 1px solid {colors.btn_border};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QTextEdit {{
                background-color: {colors.combo_bg};
                color: {colors.combo_text};
                border: 1px solid {colors.subtitle_border};
                border-radius: 6px;
            }}
            QListWidget {{
                background-color: {colors.combo_bg};
                color: {colors.combo_text};
                border: 1px solid {colors.subtitle_border};
                border-radius: 6px;
            }}
            QListWidget::item:selected {{
                background-color: {colors.combo_selected};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {colors.btn_border};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {colors.accent};
                width: 14px; height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{
                background: {colors.accent};
                border-radius: 2px;
            }}
        """)

    # ---------- 初始化 ----------
    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_recognition_tab(), "识别")
        self.tabs.addTab(self._build_appearance_tab(), "外观")
        self.tabs.addTab(self._build_behavior_tab(), "行为")
        self.tabs.addTab(self._build_skin_tab(), "皮肤")
        self.tabs.addTab(self._build_transcript_tab(), "文稿")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # 底部按钮
        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ---------- 标签页1：识别 ----------
    def _build_recognition_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)

        g_recog = QGroupBox("识别引擎")
        f_recog = QFormLayout(g_recog)

        self.device_combo = QComboBox()
        for name, data in self.panel.get_devices():
            self.device_combo.addItem(name, data)
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

        self.engine_combo = QComboBox()
        self.engine_combo.addItem("本地 FunASR（流式，需GPU）", "funasr")
        self.engine_combo.addItem("本地 SenseVoice（小模型，CPU可跑）", "sensevoice")
        self.engine_combo.addItem("阿里云 API（流式，任意平台）", "aliyun")
        f_recog.addRow("引擎：", self.engine_combo)

        self.engine_stack = QStackedWidget()
        # FunASR
        funasr_panel = QWidget()
        fp = QFormLayout(funasr_panel)
        self.funasr_device_combo = QComboBox()
        self.funasr_device_combo.addItem("CUDA（NVIDIA GPU）", "cuda")
        self.funasr_device_combo.addItem("CPU", "cpu")
        fp.addRow("设备：", self.funasr_device_combo)
        self.engine_stack.addWidget(funasr_panel)
        # SenseVoice
        sv_panel = QWidget()
        sp = QFormLayout(sv_panel)
        self.sv_device_combo = QComboBox()
        self.sv_device_combo.addItem("CPU（推荐）", "cpu")
        self.sv_device_combo.addItem("CUDA", "cuda")
        sp.addRow("设备：", self.sv_device_combo)
        self.sv_segment_spin = QDoubleSpinBox()
        self.sv_segment_spin.setRange(0.5, 5.0)
        self.sv_segment_spin.setSingleStep(0.5)
        self.sv_segment_spin.setSuffix(" 秒")
        sp.addRow("攒段时长：", self.sv_segment_spin)
        self.engine_stack.addWidget(sv_panel)
        # 阿里云
        aliyun_panel = QWidget()
        ap = QFormLayout(aliyun_panel)
        self.aliyun_akid_edit = QLineEdit()
        self.aliyun_akid_edit.setPlaceholderText("AccessKey ID")
        ap.addRow("AK ID：", self.aliyun_akid_edit)
        self.aliyun_aksecret_edit = QLineEdit()
        self.aliyun_aksecret_edit.setPlaceholderText("AccessKey Secret")
        self.aliyun_aksecret_edit.setEchoMode(QLineEdit.Password)
        ap.addRow("AK Secret：", self.aliyun_aksecret_edit)
        self.aliyun_appkey_edit = QLineEdit()
        self.aliyun_appkey_edit.setPlaceholderText("AppKey")
        ap.addRow("AppKey：", self.aliyun_appkey_edit)
        self.engine_stack.addWidget(aliyun_panel)

        f_recog.addRow("配置：", self.engine_stack)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        v.addWidget(g_recog)
        v.addStretch(1)
        return tab

    # ---------- 标签页2：外观 ----------
    def _build_appearance_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        tab = QWidget()
        v = QVBoxLayout(tab)

        # ---- 主题选择 ----
        g_theme = QGroupBox("主题")
        f_theme = QFormLayout(g_theme)

        theme_row = QHBoxLayout()
        self.theme_combo = QComboBox()
        for name in self._theme_mgr.get_all_themes():
            self.theme_combo.addItem(name, name)
        theme_row.addWidget(self.theme_combo, 1)
        self.theme_preview_btn = QPushButton("预览")
        self.theme_preview_btn.clicked.connect(self._on_theme_preview)
        theme_row.addWidget(self.theme_preview_btn)
        f_theme.addRow("预设主题：", theme_row)

        # 主题管理按钮
        theme_mgmt = QHBoxLayout()
        self.save_theme_btn = QPushButton("💾 保存为新主题")
        self.save_theme_btn.clicked.connect(self._on_save_theme)
        self.import_theme_btn = QPushButton("📂 导入")
        self.import_theme_btn.clicked.connect(self._on_import_theme)
        self.export_theme_btn = QPushButton("📤 导出")
        self.export_theme_btn.clicked.connect(self._on_export_theme)
        self.delete_theme_btn = QPushButton("🗑 删除")
        self.delete_theme_btn.clicked.connect(self._on_delete_theme)
        theme_mgmt.addWidget(self.save_theme_btn)
        theme_mgmt.addWidget(self.import_theme_btn)
        theme_mgmt.addWidget(self.export_theme_btn)
        theme_mgmt.addWidget(self.delete_theme_btn)
        f_theme.addRow("", theme_mgmt)
        v.addWidget(g_theme)

        # ---- 自定义颜色 ----
        g_colors = QGroupBox("自定义颜色")
        f_colors = QFormLayout(g_colors)

        self.color_buttons: dict[str, ColorButton] = {}
        color_labels = [
            ("subtitle_bg", "字幕背景"),
            ("subtitle_text", "字幕文字"),
            ("subtitle_border", "边框"),
            ("toolbar_bg", "工具栏背景"),
            ("btn_bg", "按钮背景"),
            ("btn_text", "按钮文字"),
            ("accent", "强调色"),
        ]
        for key, label in color_labels:
            btn = ColorButton("#000000")
            self.color_buttons[key] = btn
            f_colors.addRow(f"{label}：", btn)

        self.apply_colors_btn = QPushButton("🎨 应用颜色到当前主题")
        self.apply_colors_btn.clicked.connect(self._on_apply_colors)
        f_colors.addRow("", self.apply_colors_btn)
        v.addWidget(g_colors)

        # ---- 几何参数 ----
        g_geo = QGroupBox("字幕面板几何")
        f_geo = QFormLayout(g_geo)

        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 40)
        self.radius_spin.setSuffix(" px")
        f_geo.addRow("圆角：", self.radius_spin)

        pad_row = QHBoxLayout()
        self.pad_top_spin = QSpinBox()
        self.pad_top_spin.setRange(0, 50)
        self.pad_top_spin.setPrefix("上 ")
        self.pad_bottom_spin = QSpinBox()
        self.pad_bottom_spin.setRange(0, 50)
        self.pad_bottom_spin.setPrefix("下 ")
        self.pad_left_spin = QSpinBox()
        self.pad_left_spin.setRange(0, 50)
        self.pad_left_spin.setPrefix("左 ")
        self.pad_right_spin = QSpinBox()
        self.pad_right_spin.setRange(0, 50)
        self.pad_right_spin.setPrefix("右 ")
        pad_row.addWidget(self.pad_top_spin)
        pad_row.addWidget(self.pad_bottom_spin)
        pad_row.addWidget(self.pad_left_spin)
        pad_row.addWidget(self.pad_right_spin)
        f_geo.addRow("内边距：", pad_row)

        self.line_spacing_spin = QDoubleSpinBox()
        self.line_spacing_spin.setRange(1.0, 3.0)
        self.line_spacing_spin.setSingleStep(0.1)
        self.line_spacing_spin.setSuffix(" ×")
        f_geo.addRow("行间距：", self.line_spacing_spin)

        self.apply_geo_btn = QPushButton("应用几何参数")
        self.apply_geo_btn.clicked.connect(self._on_apply_geometry)
        f_geo.addRow("", self.apply_geo_btn)
        v.addWidget(g_geo)

        # ---- 字体 ----
        g_font = QGroupBox("字体")
        f_font = QFormLayout(g_font)
        self.font_combo = QFontComboBox()
        f_font.addRow("字体：", self.font_combo)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 72)
        self.font_size_spin.setSuffix(" pt")
        f_font.addRow("字号：", self.font_size_spin)
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setSuffix("%")
        f_font.addRow("背景透明度：", self.opacity_spin)
        v.addWidget(g_font)

        v.addStretch(1)
        scroll.setWidget(tab)
        return scroll

    # ---------- 标签页3：行为 ----------
    def _build_behavior_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        tab = QWidget()
        v = QVBoxLayout(tab)

        # 窗口
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
        self.topmost_check = QCheckBox("启动时窗口置顶")
        f_win.addRow(self.topmost_check)
        v.addWidget(g_win)

        # 行为
        g_behavior = QGroupBox("行为")
        f_behavior = QFormLayout(g_behavior)
        self.lock_scroll_check = QCheckBox("锁定滚动到底部")
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
        f_behavior.addRow("关闭行为：", self.close_combo)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(200, 5000)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setSuffix(" ms")
        f_behavior.addRow("工具栏隐藏延时：", self.delay_spin)
        v.addWidget(g_behavior)

        # 字幕文本
        g_sub = QGroupBox("字幕文本")
        f_sub = QFormLayout(g_sub)
        self.maxchars_spin = QSpinBox()
        self.maxchars_spin.setRange(1000, 200000)
        self.maxchars_spin.setSingleStep(1000)
        self.maxchars_spin.setSuffix(" 字符")
        f_sub.addRow("最大字符数：", self.maxchars_spin)
        clear_row = QHBoxLayout()
        self.clear_btn = QPushButton("🗑 清空当前字幕")
        self.clear_btn.clicked.connect(self._on_clear_transcript)
        clear_row.addWidget(self.clear_btn)
        clear_row.addStretch(1)
        f_sub.addRow("", clear_row)
        v.addWidget(g_sub)

        v.addStretch(1)
        scroll.setWidget(tab)
        return scroll

    # ---------- 标签页4：皮肤 ----------
    def _build_skin_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)

        g_skin = QGroupBox("桌宠皮肤")
        f_skin = QFormLayout(g_skin)

        self.skin_enable_check = QCheckBox("启用贴图皮肤")
        f_skin.addRow(self.skin_enable_check)

        skin_btns = QHBoxLayout()
        self.skin_editor_btn = QPushButton("🎨 打开皮肤编辑器")
        self.skin_editor_btn.clicked.connect(self._on_open_skin_editor)
        skin_btns.addWidget(self.skin_editor_btn)
        skin_btns.addStretch(1)
        f_skin.addRow("", skin_btns)

        self.skin_list = QListWidget()
        self.skin_list.setMaximumHeight(150)
        self.skin_list.addItem("（暂无自定义皮肤）")
        f_skin.addRow("已安装皮肤：", self.skin_list)

        v.addWidget(g_skin)

        # 动画设置
        g_anim = QGroupBox("动画")
        f_anim = QFormLayout(g_anim)
        self.anim_fps_spin = QSpinBox()
        self.anim_fps_spin.setRange(12, 60)
        self.anim_fps_spin.setSuffix(" fps")
        f_anim.addRow("帧率：", self.anim_fps_spin)
        self.anim_loop_check = QCheckBox("循环播放动画")
        f_anim.addRow(self.anim_loop_check)
        v.addWidget(g_anim)

        # 编辑器设置
        g_editor = QGroupBox("编辑器")
        f_editor = QFormLayout(g_editor)
        self.grid_snap_check = QCheckBox("网格吸附")
        f_editor.addRow(self.grid_snap_check)
        self.grid_size_spin = QSpinBox()
        self.grid_size_spin.setRange(4, 32)
        self.grid_size_spin.setSuffix(" px")
        f_editor.addRow("网格大小：", self.grid_size_spin)
        self.guides_check = QCheckBox("显示辅助线")
        f_editor.addRow(self.guides_check)
        v.addWidget(g_editor)

        v.addStretch(1)
        return tab

    # ---------- 标签页5：文稿 ----------
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
        ui = self.cfg.ui
        asr = self.cfg.asr
        skin = self.cfg.skin

        self._update_recog_buttons()
        idx = self.engine_combo.findData(asr.engine_type)
        self.engine_combo.setCurrentIndex(max(0, idx))
        self._sync_engine_panel()
        self.funasr_device_combo.setCurrentIndex(
            max(0, self.funasr_device_combo.findData(asr.device)))
        self.sv_device_combo.setCurrentIndex(
            max(0, self.sv_device_combo.findData(asr.sensevoice_device)))
        self.sv_segment_spin.setValue(asr.sensevoice_segment_seconds)
        self.aliyun_akid_edit.setText(asr.aliyun_access_key_id)
        self.aliyun_aksecret_edit.setText(asr.aliyun_access_key_secret)
        self.aliyun_appkey_edit.setText(asr.aliyun_appkey)

        # 外观
        current_theme = self.panel.get_theme()
        idx = self.theme_combo.findData(current_theme)
        self.theme_combo.setCurrentIndex(max(0, idx))
        self._sync_color_buttons()
        theme = self._theme_mgr.current
        geo = theme.geometry
        self.radius_spin.setValue(ui.border_radius if ui.border_radius is not None else geo.border_radius)
        self.pad_top_spin.setValue(ui.padding_top if ui.padding_top is not None else geo.padding_top)
        self.pad_bottom_spin.setValue(ui.padding_bottom if ui.padding_bottom is not None else geo.padding_bottom)
        self.pad_left_spin.setValue(ui.padding_left if ui.padding_left is not None else geo.padding_left)
        self.pad_right_spin.setValue(ui.padding_right if ui.padding_right is not None else geo.padding_right)
        self.line_spacing_spin.setValue(ui.line_spacing if ui.line_spacing is not None else geo.line_spacing)
        self.font_combo.setCurrentFont(QFont(self.panel.get_font_family()))
        self.font_size_spin.setValue(self.panel.get_font_size())
        self.opacity_spin.setValue(self.panel.get_opacity())

        # 行为
        w, h = self.panel.get_window_size()
        self.win_w_spin.setValue(w)
        self.win_h_spin.setValue(h)
        self.topmost_check.setChecked(self.panel.get_pin())
        self.lock_scroll_check.setChecked(self.panel.get_lock_scroll())
        self.close_combo.setCurrentIndex(max(0, self.close_combo.findData(ui.close_action)))
        self.delay_spin.setValue(ui.toolbar_hide_delay_ms)
        self.maxchars_spin.setValue(ui.max_chars)

        # 皮肤
        self.skin_enable_check.setChecked(skin.enabled)
        self.anim_fps_spin.setValue(skin.animation_fps)
        self.anim_loop_check.setChecked(skin.animation_loop)
        self.grid_snap_check.setChecked(skin.editor_grid_snap)
        self.grid_size_spin.setValue(skin.editor_grid_size)
        self.guides_check.setChecked(skin.editor_show_guides)

    def _sync_color_buttons(self):
        """从当前主题同步颜色到 ColorButton。"""
        colors = self._theme_mgr.current.colors
        for key, btn in self.color_buttons.items():
            btn.set_color(getattr(colors, key, "#000000"))

    # ---------- 事件 ----------
    def _on_engine_changed(self, _idx: int):
        self._sync_engine_panel()

    def _sync_engine_panel(self):
        etype = self.engine_combo.currentData()
        idx = {"funasr": 0, "sensevoice": 1, "aliyun": 2}.get(etype, 0)
        self.engine_stack.setCurrentIndex(idx)

    def _update_recog_buttons(self):
        running = self.panel.is_recording()
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

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

    def _refresh_transcript(self):
        self.transcript_view.setPlainText(self.panel.get_transcript())
        bar = self.transcript_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _copy_transcript(self):
        QApplication.clipboard().setText(self.panel.get_transcript())
        self.copy_btn.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("📋 复制全部"))

    def _on_tab_changed(self, idx: int):
        if idx == 4:  # 文稿
            self._refresh_transcript()

    # ---------- 主题操作 ----------
    def _on_theme_preview(self):
        name = self.theme_combo.currentData()
        if name:
            self.panel.set_theme(name)
            self._sync_color_buttons()

    def _on_apply_colors(self):
        """把 ColorButton 的颜色应用到当前主题（创建副本）。"""
        theme = self._theme_mgr.current
        colors = theme.colors
        for key, btn in self.color_buttons.items():
            setattr(colors, key, btn.get_color())
        self.panel.set_theme_obj(theme)

    def _on_save_theme(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "保存主题", "主题名称：")
        if ok and name.strip():
            theme = self._theme_mgr.current
            theme.name = name.strip()
            if self._theme_mgr.save_custom_theme(theme):
                # 刷新下拉
                self.theme_combo.clear()
                for n in self._theme_mgr.get_all_themes():
                    self.theme_combo.addItem(n, n)
                idx = self.theme_combo.findData(name.strip())
                self.theme_combo.setCurrentIndex(max(0, idx))
                QMessageBox.information(self, "成功", f"主题「{name.strip()}」已保存")
            else:
                QMessageBox.warning(self, "失败", "保存主题失败")

    def _on_import_theme(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入主题", "", "JSON (*.json)")
        if path:
            from pathlib import Path
            theme = self._theme_mgr.import_theme(Path(path))
            if theme:
                self.theme_combo.clear()
                for n in self._theme_mgr.get_all_themes():
                    self.theme_combo.addItem(n, n)
                QMessageBox.information(self, "成功", f"已导入主题「{theme.name}」")
            else:
                QMessageBox.warning(self, "失败", "导入失败，文件格式不正确")

    def _on_export_theme(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出主题", "theme.json", "JSON (*.json)")
        if path:
            from pathlib import Path
            if self._theme_mgr.export_theme(self._theme_mgr.current, Path(path)):
                QMessageBox.information(self, "成功", "主题已导出")

    def _on_delete_theme(self):
        name = self.theme_combo.currentData()
        if name in BUILTIN_THEMES:
            QMessageBox.warning(self, "提示", "内置主题不可删除")
            return
        ret = QMessageBox.question(self, "删除主题", f"确定删除「{name}」？")
        if ret == QMessageBox.Yes:
            self._theme_mgr.delete_custom_theme(name)
            self.theme_combo.clear()
            for n in self._theme_mgr.get_all_themes():
                self.theme_combo.addItem(n, n)

    def _on_apply_geometry(self):
        self.panel.set_border_radius(self.radius_spin.value())
        self.panel.set_padding(
            self.pad_top_spin.value(), self.pad_bottom_spin.value(),
            self.pad_left_spin.value(), self.pad_right_spin.value(),
        )
        self.panel.set_line_spacing(self.line_spacing_spin.value())

    def _on_open_skin_editor(self):
        """打开皮肤编辑器（由 app 层处理）。"""
        # 这里发信号给 app，由 app 创建编辑器窗口
        self.accept()
        # TODO: app 层监听并打开 SkinEditorWindow

    # ---------- 应用 ----------
    def _on_apply(self):
        p = self.panel
        asr = self.cfg.asr
        asr.engine_type = self.engine_combo.currentData()
        asr.device = self.funasr_device_combo.currentData()
        asr.sensevoice_device = self.sv_device_combo.currentData()
        asr.sensevoice_segment_seconds = self.sv_segment_spin.value()
        asr.aliyun_access_key_id = self.aliyun_akid_edit.text().strip()
        asr.aliyun_access_key_secret = self.aliyun_aksecret_edit.text().strip()
        asr.aliyun_appkey = self.aliyun_appkey_edit.text().strip()

        # 外观
        theme_name = self.theme_combo.currentData()
        if theme_name:
            p.set_theme(theme_name)
        p.set_font_family(self.font_combo.currentFont().family())
        p.set_font_size(self.font_size_spin.value())
        p.set_opacity(self.opacity_spin.value())
        p.set_border_radius(self.radius_spin.value())
        p.set_padding(
            self.pad_top_spin.value(), self.pad_bottom_spin.value(),
            self.pad_left_spin.value(), self.pad_right_spin.value(),
        )
        p.set_line_spacing(self.line_spacing_spin.value())

        # 行为
        p.set_window_size(self.win_w_spin.value(), self.win_h_spin.value())
        p.set_pin(self.topmost_check.isChecked())
        p.set_lock_scroll(self.lock_scroll_check.isChecked())
        p.set_close_action(self.close_combo.currentData())
        p.set_toolbar_hide_delay(self.delay_spin.value())
        p.set_max_chars(self.maxchars_spin.value())

        # 皮肤
        skin = self.cfg.skin
        skin.enabled = self.skin_enable_check.isChecked()
        skin.animation_fps = self.anim_fps_spin.value()
        skin.animation_loop = self.anim_loop_check.isChecked()
        skin.editor_grid_snap = self.grid_snap_check.isChecked()
        skin.editor_grid_size = self.grid_size_spin.value()
        skin.editor_show_guides = self.guides_check.isChecked()

        p._notify_geometry()
        self._update_recog_buttons()
        self.accept()
