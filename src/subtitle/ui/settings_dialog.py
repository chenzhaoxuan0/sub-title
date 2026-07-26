"""全局设置对话框（Fluent 风格，手写 QSS，无第三方库）。

用 SettingCard/SettingCardGroup/ToggleSwitch 等 Fluent 风格组件组织设置项。
不引入 GPLv3 的 PyQt-Fluent-Widgets，保持项目 MIT 许可证。
保留所有功能逻辑（_load_current_state/_on_apply/各回调）和 config 字段映射 1:1。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QSpinBox,
    QCheckBox, QDialogButtonBox, QLabel, QGroupBox, QTabWidget, QWidget,
    QTextEdit, QPushButton, QFontComboBox, QApplication, QMessageBox,
    QLineEdit, QStackedWidget, QDoubleSpinBox, QColorDialog, QScrollArea,
    QFrame, QSizePolicy, QFileDialog, QListWidget, QListWidgetItem,
    QAbstractItemView,
)

from ..config import Config
from .. import credentials
from .theme_engine import (
    Theme, ThemeColors, ThemeGeometry, ThemeManager,
    get_theme_manager, BUILTIN_THEMES, PROTECTED_THEMES,
)
from .fluent_widgets import (
    SettingCard, SettingCardGroup, ToggleSwitch, build_fluent_qss,
)
from .trash_dialog import TrashDialog
from .flow_layout import FlowLayout


# ------------------------------------------------------------------
# ColorButton（自定义颜色按钮，保留原实现）
# ------------------------------------------------------------------
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


# ------------------------------------------------------------------
# 辅助：把控件包进卡片的一行（标题+说明+控件），返回卡片
# ------------------------------------------------------------------
def _row(title: str, content: str, widget: QWidget) -> SettingCard:
    return SettingCard(title, content, widget)


class SettingsDialog(QDialog):
    """设置对话框（Fluent 风格）。"""

    def __init__(self, cfg: Config, panel, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.panel = panel
        self._theme_mgr = get_theme_manager()
        self.setWindowTitle("全局设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(640, 720)
        self._init_ui()
        self._load_current_state()
        self._apply_dialog_theme()
        # 初始化主题按钮可用性（Dark/Light 不可删等）
        self._refresh_theme_buttons()

    # ---------- 主题 ----------
    def _apply_dialog_theme(self):
        """套用 Fluent 风格 QSS（跟随当前主题）。"""
        colors = self._theme_mgr.current.colors
        self.setStyleSheet(build_fluent_qss(colors))
        # ToggleSwitch 用 accent 色
        accent = colors.accent
        off = colors.subtitle_border
        knob = "#ffffff"
        for sw in self.findChildren(ToggleSwitch):
            sw.set_accent(accent)
            sw.set_track_colors(off, knob)

    # ---------- UI 构建 ----------
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_recognition_tab(), "识别")
        self.tabs.addTab(self._build_appearance_tab(), "外观")
        self.tabs.addTab(self._build_behavior_tab(), "行为")
        self.tabs.addTab(self._build_skin_tab(), "皮肤")
        self.tabs.addTab(self._build_transcript_tab(), "文稿")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        self._apply_btn = btns.button(QDialogButtonBox.Apply)
        self._apply_btn.setProperty("primary", True)
        self._apply_btn.clicked.connect(self._on_apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _wrap_scroll(self, content: QWidget) -> QScrollArea:
        """把内容包进 Fluent 风格滚动区。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        return scroll

    # ---------- 标签页1：识别 ----------
    def _build_recognition_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # 输入源 + 开始停止
        self.device_combo = QComboBox()
        for name, data in self.panel.get_devices():
            self.device_combo.addItem(name, data)
        v.addWidget(_row("输入源", "选择要捕获的系统音频输出设备", self.device_combo))

        # 开始/停止（放在一行卡片）
        recog_card = SettingCard("识别控制", "开始或停止实时字幕识别", None)
        rb = QHBoxLayout()
        rb.setContentsMargins(0, 0, 0, 0)
        self.start_btn = QPushButton("▶ 开始")
        self.stop_btn = QPushButton("■ 停止")
        self.start_btn.clicked.connect(self._on_start_click)
        self.stop_btn.clicked.connect(self._on_stop_click)
        rb.addWidget(self.start_btn)
        rb.addWidget(self.stop_btn)
        wb = QWidget()
        wb.setLayout(rb)
        recog_card.set_widget(wb)
        v.addWidget(recog_card)

        # 引擎选择
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("本地 FunASR（流式，需GPU）", "funasr")
        self.engine_combo.addItem("本地 SenseVoice（小模型，CPU可跑）", "sensevoice")
        self.engine_combo.addItem("本地 Whisper（faster-whisper，多语言+翻译）", "faster_whisper")
        self.engine_combo.addItem("阿里云 API（流式，任意平台）", "aliyun")
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        v.addWidget(_row("识别引擎", "选择语音识别后端", self.engine_combo))

        # 各引擎配置（QStackedWidget）
        self.engine_stack = QStackedWidget()
        # FunASR
        funasr_panel = QWidget()
        fp = QVBoxLayout(funasr_panel)
        fp.setContentsMargins(0, 0, 0, 0)
        fp.setSpacing(4)
        self.funasr_device_combo = QComboBox()
        self.funasr_device_combo.addItem("CUDA（NVIDIA GPU）", "cuda")
        self.funasr_device_combo.addItem("CPU", "cpu")
        fp.addWidget(_row("FunASR 设备", "推理设备，GPU 更快", self.funasr_device_combo))
        self.funasr_punc_check = ToggleSwitch()
        fp.addWidget(_row("流式标点", "补标点以支持自动分行（首次下载~300-700MB，需重启识别生效）", self.funasr_punc_check))
        fp.addStretch(1)
        self.engine_stack.addWidget(funasr_panel)
        # SenseVoice
        sv_panel = QWidget()
        sp = QVBoxLayout(sv_panel)
        sp.setContentsMargins(0, 0, 0, 0)
        sp.setSpacing(4)
        self.sv_device_combo = QComboBox()
        self.sv_device_combo.addItem("CPU（推荐，Mac/弱GPU）", "cpu")
        self.sv_device_combo.addItem("CUDA（NVIDIA GPU）", "cuda")
        sp.addWidget(_row("SenseVoice 设备", "推理设备", self.sv_device_combo))
        self.sv_segment_spin = QDoubleSpinBox()
        self.sv_segment_spin.setRange(0.5, 5.0)
        self.sv_segment_spin.setSingleStep(0.5)
        self.sv_segment_spin.setSuffix(" 秒")
        sp.addWidget(_row("攒段时长", "越小延迟越低但易切词", self.sv_segment_spin))
        sp.addStretch(1)
        self.engine_stack.addWidget(sv_panel)
        # 阿里云
        aliyun_panel = QWidget()
        ap = QVBoxLayout(aliyun_panel)
        ap.setContentsMargins(0, 0, 0, 0)
        ap.setSpacing(4)
        self.aliyun_akid_edit = QLineEdit()
        self.aliyun_akid_edit.setPlaceholderText("AccessKey ID")
        ap.addWidget(_row("AccessKey ID", "阿里云控制台获取", self.aliyun_akid_edit))
        self.aliyun_aksecret_edit = QLineEdit()
        self.aliyun_aksecret_edit.setPlaceholderText("AccessKey Secret")
        self.aliyun_aksecret_edit.setEchoMode(QLineEdit.Password)
        ap.addWidget(_row("AccessKey Secret", "阿里云控制台获取", self.aliyun_aksecret_edit))
        self.aliyun_appkey_edit = QLineEdit()
        self.aliyun_appkey_edit.setPlaceholderText("AppKey")
        ap.addWidget(_row("AppKey", "智能语音交互项目 AppKey", self.aliyun_appkey_edit))
        # 提示：凭证会存到系统保险箱（不再写进 config.yaml）
        cred_location = credentials.storage_location()
        hint = QLabel(
            f"🔐 凭证存于系统保险箱（{cred_location}），不进 config.yaml。\n"
            f"卸载重装或换电脑需要重新填；需先装 nls SDK（见 README）。"
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        ap.addWidget(hint)
        ap.addStretch(1)
        self.engine_stack.addWidget(aliyun_panel)
        # faster-whisper（CTranslate2 后端，多语言+翻译，不依赖 torch）
        fw_panel = QWidget()
        wp = QVBoxLayout(fw_panel)
        wp.setContentsMargins(0, 0, 0, 0)
        wp.setSpacing(4)
        # try import 守卫：未装时置灰 + 提示
        try:
            import faster_whisper  # noqa: F401
            fw_available = True
        except ImportError:
            fw_available = False
        self.fw_model_combo = QComboBox()
        for name, label in [("large-v3-turbo", "large-v3-turbo（推荐，快+准）"),
                            ("large-v3", "large-v3（最准，慢）"),
                            ("medium", "medium（中等）"),
                            ("small", "small（最快，弱机器）"),
                            ("distil-large-v3", "distil-large-v3（仅英文，最快）")]:
            self.fw_model_combo.addItem(label, name)
        wp.addWidget(_row("Whisper 模型", "首用从 HF Hub 自动下载", self.fw_model_combo))
        self.fw_device_combo = QComboBox()
        self.fw_device_combo.addItem("auto（自动检测，推荐）", "auto")
        self.fw_device_combo.addItem("CUDA（NVIDIA GPU）", "cuda")
        self.fw_device_combo.addItem("CPU", "cpu")
        wp.addWidget(_row("设备", "auto=有GPU用GPU否则CPU，不崩", self.fw_device_combo))
        self.fw_compute_combo = QComboBox()
        for v, label in [("auto", "auto（自动）"),
                         ("float16", "float16（GPU）"),
                         ("int8", "int8（CPU 最快）"),
                         ("int8_float16", "int8_float16（省显存）")]:
            self.fw_compute_combo.addItem(label, v)
        wp.addWidget(_row("计算精度", "auto=GPU用float16/CPU用int8", self.fw_compute_combo))
        self.fw_lang_combo = QComboBox()
        self.fw_lang_combo.addItem("中文", "zh")
        self.fw_lang_combo.addItem("自动检测", "auto")
        self.fw_lang_combo.addItem("英文", "en")
        self.fw_lang_combo.addItem("日文", "ja")
        wp.addWidget(_row("语言", "影响识别准确度，中文建议指定", self.fw_lang_combo))
        self.fw_beam_spin = QSpinBox()
        self.fw_beam_spin.setRange(1, 10)
        wp.addWidget(_row("beam_size", "1 最快（turbo 鲁棒），5 默认更准", self.fw_beam_spin))
        self.fw_seg_spin = QDoubleSpinBox()
        self.fw_seg_spin.setRange(0.5, 5.0)
        self.fw_seg_spin.setSingleStep(0.5)
        self.fw_seg_spin.setSuffix(" 秒")
        wp.addWidget(_row("攒段时长", "越小延迟越低但易切词", self.fw_seg_spin))
        if not fw_available:
            hint_fw = QLabel("⚠️ faster-whisper 未安装。多语言/翻译引擎需要它：\n"
                             "pip install faster-whisper（不依赖 torch）")
            hint_fw.setStyleSheet("color: #c77; font-size: 11px;")
            hint_fw.setWordWrap(True)
            wp.addWidget(hint_fw)
            for w in (self.fw_model_combo, self.fw_device_combo, self.fw_compute_combo,
                      self.fw_lang_combo, self.fw_beam_spin, self.fw_seg_spin):
                w.setEnabled(False)
        wp.addStretch(1)
        self.engine_stack.addWidget(fw_panel)
        v.addWidget(self.engine_stack)

        v.addStretch(1)
        return self._wrap_scroll(tab)

    # ---------- 标签页2：外观 ----------
    def _build_appearance_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # 主题
        g_theme = SettingCardGroup("主题")
        theme_row = QWidget()
        tr = QHBoxLayout(theme_row)
        tr.setContentsMargins(0, 0, 0, 0)
        self.theme_combo = QComboBox()
        for name in self._theme_mgr.get_all_themes():
            self.theme_combo.addItem(name, name)
        self.theme_preview_btn = QPushButton("预览")
        self.theme_preview_btn.clicked.connect(self._on_theme_preview)
        tr.addWidget(self.theme_combo)
        tr.addWidget(self.theme_preview_btn)
        g_theme.add_card(_row("预设主题", "选择内置或自定义主题", theme_row))
        # 主题管理按钮 —— 用 FlowLayout 自动换行，避免一行 8 个按钮挤不下
        mgmt = QWidget()
        # 显式声明横竖都 Expanding：父布局（SettingCard 的 QHBoxLayout）会给我们尽量多的空间，
        # FlowLayout 才能真的把多行按钮排开
        mgmt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        flow = FlowLayout(mgmt, margin=0, h_spacing=6, v_spacing=6)
        self.new_theme_btn = QPushButton("➕ 新建")
        self.new_theme_btn.setToolTip("从空白默认值创建一个全新的自定义主题")
        self.new_theme_btn.clicked.connect(self._on_new_theme)
        self.save_theme_btn = QPushButton("💾 保存")
        self.save_theme_btn.setToolTip("把当前主题另存为自定义（深拷贝，不污染内置）")
        self.save_theme_btn.clicked.connect(self._on_save_theme)
        self.rename_theme_btn = QPushButton("✏️ 重命名")
        self.rename_theme_btn.setToolTip("修改当前主题的名字（内置会复制为新自定义）")
        self.rename_theme_btn.clicked.connect(self._on_rename_theme)
        self.reset_theme_btn = QPushButton("🔄 恢复默认")
        self.reset_theme_btn.setToolTip("把当前选中的内置主题恢复到出厂默认值（自定义主题无效）")
        self.reset_theme_btn.clicked.connect(self._on_reset_theme)
        self.import_theme_btn = QPushButton("📂 导入")
        self.import_theme_btn.clicked.connect(self._on_import_theme)
        self.export_theme_btn = QPushButton("📤 导出")
        self.export_theme_btn.clicked.connect(self._on_export_theme)
        self.delete_theme_btn = QPushButton("🗑 删除")
        self.delete_theme_btn.setToolTip("把当前自定义主题移到回收站（可恢复）")
        self.delete_theme_btn.clicked.connect(self._on_delete_theme)
        self.trash_btn = QPushButton("📦 回收站")
        self.trash_btn.setToolTip("恢复或永久删除被软删除的自定义主题")
        self.trash_btn.clicked.connect(self._on_open_trash)
        for b in (self.new_theme_btn, self.save_theme_btn, self.rename_theme_btn,
                  self.reset_theme_btn, self.import_theme_btn, self.export_theme_btn,
                  self.delete_theme_btn, self.trash_btn):
            flow.addWidget(b)
        g_theme.add_card(_row("主题管理",
                              "新建/保存/重命名/恢复默认/导入/导出/删除/回收站（基础黑白主题不可删）",
                              mgmt))
        # 主题下拉变化时刷新按钮可用性
        self.theme_combo.currentIndexChanged.connect(self._refresh_theme_buttons)
        v.addWidget(g_theme)

        # 自定义颜色
        g_colors = SettingCardGroup("自定义颜色")
        self.color_buttons: dict[str, ColorButton] = {}
        color_labels = [
            ("subtitle_bg", "字幕背景"), ("subtitle_text", "字幕文字"),
            ("subtitle_border", "边框"), ("toolbar_bg", "工具栏背景"),
            ("btn_bg", "按钮背景"), ("btn_text", "按钮文字"), ("accent", "强调色"),
        ]
        for key, label in color_labels:
            btn = ColorButton("#000000")
            self.color_buttons[key] = btn
            g_colors.add_card(_row(label, f"主题颜色：{key}", btn))
        self.apply_colors_btn = QPushButton("🎨 应用颜色")
        self.apply_colors_btn.setProperty("primary", True)
        self.apply_colors_btn.clicked.connect(self._on_apply_colors)
        g_colors.add_card(_row("应用颜色", "把以上颜色应用到当前主题", self.apply_colors_btn))
        v.addWidget(g_colors)

        # 几何参数
        g_geo = SettingCardGroup("字幕面板几何")
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 40)
        self.radius_spin.setSuffix(" px")
        g_geo.add_card(_row("圆角", "字幕区圆角半径", self.radius_spin))
        pad_row = QWidget()
        pr = QHBoxLayout(pad_row)
        pr.setContentsMargins(0, 0, 0, 0)
        self.pad_top_spin = QSpinBox(); self.pad_top_spin.setRange(0, 50); self.pad_top_spin.setPrefix("上 ")
        self.pad_bottom_spin = QSpinBox(); self.pad_bottom_spin.setRange(0, 50); self.pad_bottom_spin.setPrefix("下 ")
        self.pad_left_spin = QSpinBox(); self.pad_left_spin.setRange(0, 50); self.pad_left_spin.setPrefix("左 ")
        self.pad_right_spin = QSpinBox(); self.pad_right_spin.setRange(0, 50); self.pad_right_spin.setPrefix("右 ")
        for s in (self.pad_top_spin, self.pad_bottom_spin, self.pad_left_spin, self.pad_right_spin):
            pr.addWidget(s)
        g_geo.add_card(_row("内边距", "上/下/左/右", pad_row))
        self.line_spacing_spin = QDoubleSpinBox()
        self.line_spacing_spin.setRange(1.0, 3.0)
        self.line_spacing_spin.setSingleStep(0.1)
        self.line_spacing_spin.setSuffix(" ×")
        g_geo.add_card(_row("行间距", "字幕行距倍数", self.line_spacing_spin))
        self.apply_geo_btn = QPushButton("应用几何")
        self.apply_geo_btn.clicked.connect(self._on_apply_geometry)
        g_geo.add_card(_row("应用几何参数", "立即生效", self.apply_geo_btn))
        v.addWidget(g_geo)

        # 字体
        g_font = SettingCardGroup("字体")
        self.font_combo = QFontComboBox()
        # 关键：明确禁掉可编辑。否则对不存在的字体名（如变量字体 "MiSans VF Normal"），
        # 看起来就像一个需要手动输入的文本框，用户不知道要点 ▼ 下拉。
        self.font_combo.setEditable(False)
        self.font_combo.setToolTip("点击 ▼ 下拉选择系统已安装的字体")
        self.font_combo.setMinimumWidth(220)
        g_font.add_card(_row("字体", "字幕字体", self.font_combo))
        self.font_size_spin = QSpinBox()
        # 最小 4（与工具栏 A-/A+ 按钮的下界对齐；之前是 8）
        self.font_size_spin.setRange(4, 72)
        self.font_size_spin.setSuffix(" pt")
        g_font.add_card(_row("字号", "字体大小", self.font_size_spin))
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setSuffix("%")
        g_font.add_card(_row("背景透明度", "0=全透明 100=不透明", self.opacity_spin))
        v.addWidget(g_font)

        v.addStretch(1)
        return self._wrap_scroll(tab)

    # ---------- 标签页3：行为 ----------
    def _build_behavior_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # 窗口
        g_win = SettingCardGroup("字幕窗口")
        size_row = QWidget()
        sr = QHBoxLayout(size_row)
        sr.setContentsMargins(0, 0, 0, 0)
        self.win_w_spin = QSpinBox()
        # 下限 30：用户可以让字幕窗口缩到非常小，贴进视频网站的小角落
        self.win_w_spin.setRange(max(self.panel.minimumWidth(), 30), 4000)
        self.win_w_spin.setSuffix(" px")
        self.win_h_spin = QSpinBox()
        self.win_h_spin.setRange(max(self.panel.minimumHeight(), 30), 4000)
        self.win_h_spin.setSuffix(" px")
        sr.addWidget(self.win_w_spin)
        sr.addWidget(self.win_h_spin)
        self.apply_size_btn = QPushButton("应用")
        self.apply_size_btn.clicked.connect(self._on_apply_size)
        sr.addWidget(self.apply_size_btn)
        g_win.add_card(_row("宽 × 高", "字幕窗口尺寸", size_row))
        self.topmost_check = ToggleSwitch()
        g_win.add_card(_row("窗口置顶", "始终显示在最前", self.topmost_check))
        v.addWidget(g_win)

        # 行为
        g_behavior = SettingCardGroup("行为")
        self.lock_scroll_check = ToggleSwitch()
        g_behavior.add_card(_row("锁定滚动到底部", "新字幕强制跟随，不被滚动打断", self.lock_scroll_check))
        self.scroll_btn = QPushButton("📍 立刻滚动到底部")
        self.scroll_btn.clicked.connect(self._on_scroll_bottom)
        g_behavior.add_card(_row("立刻滚动到底部", "误操作后一键回底", self.scroll_btn))
        self.close_combo = QComboBox()
        self.close_combo.addItem("每次询问", "ask")
        self.close_combo.addItem("直接隐藏到托盘", "hide")
        self.close_combo.addItem("直接退出程序", "quit")
        g_behavior.add_card(_row("关闭行为", "点✕/Alt+F4时", self.close_combo))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(200, 5000)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setSuffix(" 毫秒")
        g_behavior.add_card(_row("工具栏隐藏延时", "鼠标离开后多久隐藏工具栏", self.delay_spin))
        v.addWidget(g_behavior)

        # 字幕文本
        g_sub = SettingCardGroup("字幕文本")
        self.maxchars_spin = QSpinBox()
        self.maxchars_spin.setRange(1000, 200000)
        self.maxchars_spin.setSingleStep(1000)
        self.maxchars_spin.setSuffix(" 字符")
        g_sub.add_card(_row("最大字符数", "超出后自动从头清理", self.maxchars_spin))
        self.line_break_check = ToggleSwitch()
        g_sub.add_card(_row("自动分行", "识别到句末标点或句子边界时换行", self.line_break_check))
        self.clear_btn = QPushButton("🗑 清空当前字幕")
        self.clear_btn.clicked.connect(self._on_clear_transcript)
        g_sub.add_card(_row("清空字幕", "清除所有已识别文本", self.clear_btn))
        v.addWidget(g_sub)

        v.addStretch(1)
        return self._wrap_scroll(tab)

    # ---------- 标签页4：皮肤 ----------
    def _build_skin_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        g_skin = SettingCardGroup("桌宠皮肤")
        self.skin_enable_check = ToggleSwitch()
        g_skin.add_card(_row("启用贴图皮肤", "开启后字幕窗口用自定义皮肤渲染", self.skin_enable_check))
        self.skin_editor_btn = QPushButton("🎨 打开皮肤编辑器")
        self.skin_editor_btn.clicked.connect(self._on_open_skin_editor)
        g_skin.add_card(_row("皮肤编辑器", "创建/编辑自定义皮肤", self.skin_editor_btn))
        v.addWidget(g_skin)

        g_anim = SettingCardGroup("动画")
        self.anim_fps_spin = QSpinBox()
        self.anim_fps_spin.setRange(12, 60)
        self.anim_fps_spin.setSuffix(" fps")
        g_anim.add_card(_row("动画帧率", "皮肤动画播放帧率", self.anim_fps_spin))
        self.anim_loop_check = ToggleSwitch()
        g_anim.add_card(_row("循环播放", "动画结束后自动重播", self.anim_loop_check))
        v.addWidget(g_anim)

        g_editor = SettingCardGroup("编辑器")
        self.grid_snap_check = ToggleSwitch()
        g_editor.add_card(_row("网格吸附", "拖动图层时吸附到网格", self.grid_snap_check))
        self.grid_size_spin = QSpinBox()
        self.grid_size_spin.setRange(4, 32)
        self.grid_size_spin.setSuffix(" px")
        g_editor.add_card(_row("网格尺寸", "吸附网格大小", self.grid_size_spin))
        self.guides_check = ToggleSwitch()
        g_editor.add_card(_row("显示辅助线", "编辑器中显示对齐辅助线", self.guides_check))
        v.addWidget(g_editor)

        v.addStretch(1)
        return self._wrap_scroll(tab)

    # ---------- 标签页5：文稿回看 ----------
    def _build_transcript_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
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
        v.addLayout(btns)
        self.transcript_view = QTextEdit()
        self.transcript_view.setReadOnly(True)
        self.transcript_view.setPlaceholderText("（暂无字幕文本）")
        v.addWidget(self.transcript_view, 1)
        return tab

    # ============================================================
    # 以下为功能逻辑方法（原样保留，仅 UI 构建部分重构）
    # ============================================================
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
        self.funasr_punc_check.setChecked(asr.funasr_punc_enabled)
        self.sv_device_combo.setCurrentIndex(
            max(0, self.sv_device_combo.findData(asr.sensevoice_device)))
        self.sv_segment_spin.setValue(asr.sensevoice_segment_seconds)
        self.aliyun_akid_edit.setText(credentials.get(credentials.KEY_ALIYUN_AK_ID) or "")
        self.aliyun_aksecret_edit.setText(credentials.get(credentials.KEY_ALIYUN_AK_SECRET) or "")
        self.aliyun_appkey_edit.setText(credentials.get(credentials.KEY_ALIYUN_APPKEY) or "")
        # faster-whisper
        self.fw_model_combo.setCurrentIndex(
            max(0, self.fw_model_combo.findData(asr.faster_whisper_model)))
        self.fw_device_combo.setCurrentIndex(
            max(0, self.fw_device_combo.findData(asr.faster_whisper_device)))
        self.fw_compute_combo.setCurrentIndex(
            max(0, self.fw_compute_combo.findData(asr.faster_whisper_compute_type)))
        self.fw_lang_combo.setCurrentIndex(
            max(0, self.fw_lang_combo.findData(asr.faster_whisper_language)))
        self.fw_beam_spin.setValue(asr.faster_whisper_beam_size)
        self.fw_seg_spin.setValue(asr.faster_whisper_segment_seconds)

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
        self.line_break_check.setChecked(self.panel.get_line_break())

        # 皮肤
        self.skin_enable_check.setChecked(skin.enabled)
        self.anim_fps_spin.setValue(skin.animation_fps)
        self.anim_loop_check.setChecked(skin.animation_loop)
        self.grid_snap_check.setChecked(skin.editor_grid_snap)
        self.grid_size_spin.setValue(skin.editor_grid_size)
        self.guides_check.setChecked(skin.editor_show_guides)

    def _sync_color_buttons(self):
        colors = self._theme_mgr.current.colors
        for key, btn in self.color_buttons.items():
            btn.set_color(getattr(colors, key, "#000000"))

    def _reload_theme_combo(self, *, select: Optional[str] = None):
        """重建主题下拉。select 为 None 时保留当前选中，否则切到指定主题。"""
        current = select if select is not None else self.theme_combo.currentData()
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for n in self._theme_mgr.get_all_themes():
            self.theme_combo.addItem(n, n)
        if current and current in self._theme_mgr.get_all_themes():
            idx = self.theme_combo.findData(current)
            self.theme_combo.setCurrentIndex(max(0, idx))
        self.theme_combo.blockSignals(False)
        self._refresh_theme_buttons()

    def _refresh_theme_buttons(self):
        """按下拉当前选中项，刷新各按钮的可用性 / tooltip。"""
        name = self.theme_combo.currentData() or ""
        is_builtin = name in BUILTIN_THEMES
        is_protected = name in PROTECTED_THEMES
        # 删除：基础主题彻底禁用，其他内置给提示，自定义正常
        if is_protected:
            self.delete_theme_btn.setEnabled(False)
            self.delete_theme_btn.setToolTip(f"「{name}」是基础主题（黑白之一），不可删除")
        else:
            self.delete_theme_btn.setEnabled(True)
            self.delete_theme_btn.setToolTip("把当前自定义主题移到回收站（可恢复）")
        # 恢复默认：仅对内置有意义
        self.reset_theme_btn.setEnabled(is_builtin)
        if not is_builtin and name:
            self.reset_theme_btn.setToolTip("「恢复默认」只对内置主题有效")
        elif is_builtin:
            self.reset_theme_btn.setToolTip("把当前选中的内置主题恢复到出厂默认值")
        # 重命名：内置和自定义都行（内置重命名会复制为新自定义）
        self.rename_theme_btn.setEnabled(bool(name))

    def _on_engine_changed(self, _idx: int):
        self._sync_engine_panel()

    def _sync_engine_panel(self):
        etype = self.engine_combo.currentData()
        idx = {"funasr": 0, "sensevoice": 1, "faster_whisper": 2, "aliyun": 3}.get(etype, 0)
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

    def _on_theme_preview(self):
        name = self.theme_combo.currentData()
        if name:
            self.panel.set_theme(name)
            self._sync_color_buttons()

    def _on_apply_colors(self):
        theme = self._theme_mgr.current
        colors = theme.colors
        for key, btn in self.color_buttons.items():
            setattr(colors, key, btn.get_color())
        self.panel.set_theme_obj(theme)

    def _on_save_theme(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "保存主题", "主题名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in BUILTIN_THEMES:
            QMessageBox.warning(self, "提示", f"「{name}」是内置主题名称，请换一个")
            return
        if self._theme_mgr.get_theme(name):
            QMessageBox.warning(self, "提示", f"已存在同名主题「{name}」")
            return
        # save_custom_theme 内部会 deep-copy，并把 _current 切到新 copy
        if self._theme_mgr.save_custom_theme(self._theme_mgr.current, new_name=name):
            self._reload_theme_combo(select=name)
            # 切到新主题，让后续"应用颜色/几何"都改在 copy 上，不再污染内置
            self.panel.set_theme(name)
            self._sync_color_buttons()
            QMessageBox.information(self, "成功", f"主题「{name}」已保存")
        else:
            QMessageBox.warning(self, "失败", "保存主题失败")

    def _on_rename_theme(self):
        from PySide6.QtWidgets import QInputDialog
        current_name = self.theme_combo.currentData()
        if not current_name:
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名主题", "新名称：", text=current_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == current_name:
            return
        if new_name in BUILTIN_THEMES:
            QMessageBox.warning(self, "提示", f"「{new_name}」是内置主题名称，请换一个")
            return
        if self._theme_mgr.get_theme(new_name):
            QMessageBox.warning(self, "提示", f"已存在同名主题「{new_name}」")
            return
        if not self._theme_mgr.rename_theme(current_name, new_name):
            QMessageBox.warning(self, "失败", "重命名失败")
            return
        self._reload_theme_combo(select=new_name)
        self.panel.set_theme(new_name)
        QMessageBox.information(
            self, "成功",
            f"已重命名为「{new_name}」"
            + ("\n（旧主题文件已移入回收站，可恢复）" if current_name not in BUILTIN_THEMES else
               "\n（内置主题未受影响，新主题是它的副本）"),
        )

    def _on_delete_theme(self):
        name = self.theme_combo.currentData()
        if not name:
            return
        if name in PROTECTED_THEMES:
            QMessageBox.warning(
                self, "禁止删除",
                f"「{name}」是基础主题（黑白之一），不可删除。\n"
                f"这是软件的兜底主题，删了就没法换回去了。",
            )
            return
        if name in BUILTIN_THEMES:
            QMessageBox.warning(
                self, "提示",
                f"「{name}」是内置主题，不可删除。\n"
                f"想要改它？用「💾 保存」另存为自定义主题后再改。",
            )
            return
        ret = QMessageBox.question(
            self, "移到回收站",
            f"确定把「{name}」移到回收站？\n（可在「📦 回收站」恢复）",
        )
        if ret != QMessageBox.Yes:
            return
        if not self._theme_mgr.delete_custom_theme(name):
            QMessageBox.warning(self, "失败", "删除失败")
            return
        self._reload_theme_combo(select="Dark")  # 删完后回退到 Dark
        self.panel.set_theme("Dark")
        self._sync_color_buttons()
        QMessageBox.information(self, "完成", f"「{name}」已移到回收站")

    def _on_open_trash(self):
        dlg = TrashDialog(self)
        dlg.exec()
        # 回收站变化可能影响 _custom_themes，刷新一下下拉
        self._reload_theme_combo(select=self.theme_combo.currentData())

    def _on_reset_theme(self):
        """把当前选中的内置主题恢复为出厂默认值。"""
        name = self.theme_combo.currentData()
        if not name:
            return
        if name not in BUILTIN_THEMES:
            QMessageBox.warning(
                self, "提示",
                "「恢复默认」仅对内置主题有效。\n"
                "自定义主题如需回到初始状态，请用「🗑 删除」后再点「➕ 新建」。",
            )
            return
        ret = QMessageBox.question(
            self, "恢复默认",
            f"确定把内置主题「{name}」恢复到出厂默认值？\n"
            f"当前对该主题的所有颜色/几何修改都会丢失。",
        )
        if ret != QMessageBox.Yes:
            return
        if not self._theme_mgr.reset_builtin(name):
            QMessageBox.warning(self, "失败", "恢复失败")
            return
        # 重新应用：让 panel 重新读取内置主题的字段
        self.panel.set_theme(name)
        self._sync_color_buttons()
        # 同步几何 spinbox 的当前值
        geo = self._theme_mgr.current.geometry
        self.radius_spin.setValue(geo.border_radius)
        self.pad_top_spin.setValue(geo.padding_top)
        self.pad_bottom_spin.setValue(geo.padding_bottom)
        self.pad_left_spin.setValue(geo.padding_left)
        self.pad_right_spin.setValue(geo.padding_right)
        self.line_spacing_spin.setValue(geo.line_spacing)
        self.font_combo.setCurrentFont(QFont(geo.font_family))
        self.font_size_spin.setValue(geo.font_size)
        self.opacity_spin.setValue(int(self._theme_mgr.current.opacity * 100))
        QMessageBox.information(self, "完成", f"「{name}」已恢复到出厂默认值")

    def _on_new_theme(self):
        """从空白默认值新建一个自定义主题（不复制当前主题的任何字段）。"""
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建主题", "新主题名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        # 检查重名（内置 + 自定义 都不能重名）
        if name in BUILTIN_THEMES:
            QMessageBox.warning(self, "提示", f"「{name}」是内置主题名称，请换一个")
            return
        if self._theme_mgr.get_theme(name):
            QMessageBox.warning(self, "提示", f"已存在同名主题「{name}」，请换一个")
            return
        new_theme = self._theme_mgr.create_blank_theme(name)
        if not self._theme_mgr.save_custom_theme(new_theme):
            QMessageBox.warning(self, "失败", "保存新主题失败")
            return
        # 刷新下拉
        self._reload_theme_combo(select=name)
        # 立即切换到这个新主题，方便用户接着编辑
        self.panel.set_theme(name)
        self._sync_color_buttons()
        QMessageBox.information(self, "成功", f"已新建主题「{name}」，可在下方自定义颜色和几何参数")

    def _on_import_theme(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入主题", "", "JSON (*.json)")
        if path:
            from pathlib import Path
            theme = self._theme_mgr.import_theme(Path(path))
            if theme:
                self._reload_theme_combo()
                QMessageBox.information(self, "成功", f"已导入主题「{theme.name}」")
            else:
                QMessageBox.warning(self, "失败", "导入失败，文件格式不正确")

    def _on_export_theme(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出主题", "theme.json", "JSON (*.json)")
        if path:
            from pathlib import Path
            if self._theme_mgr.export_theme(self._theme_mgr.current, Path(path)):
                QMessageBox.information(self, "成功", "主题已导出")

    def _on_apply_geometry(self):
        self.panel.set_border_radius(self.radius_spin.value())
        self.panel.set_padding(
            self.pad_top_spin.value(), self.pad_bottom_spin.value(),
            self.pad_left_spin.value(), self.pad_right_spin.value(),
        )
        self.panel.set_line_spacing(self.line_spacing_spin.value())

    def _on_open_skin_editor(self):
        """打开皮肤编辑器（由 app 层处理）。"""
        self.accept()

    def _on_apply(self):
        p = self.panel
        asr = self.cfg.asr
        asr.engine_type = self.engine_combo.currentData()
        asr.device = self.funasr_device_combo.currentData()
        asr.funasr_punc_enabled = self.funasr_punc_check.isChecked()
        asr.sensevoice_device = self.sv_device_combo.currentData()
        asr.sensevoice_segment_seconds = self.sv_segment_spin.value()
        # 阿里云 AccessKey ID / Secret / AppKey → 写到系统保险箱（不进 config.yaml）
        credentials.set_aliyun(
            ak_id=self.aliyun_akid_edit.text().strip(),
            ak_secret=self.aliyun_aksecret_edit.text().strip(),
            appkey=self.aliyun_appkey_edit.text().strip(),
        )
        # faster-whisper
        asr.faster_whisper_model = self.fw_model_combo.currentData()
        asr.faster_whisper_device = self.fw_device_combo.currentData()
        asr.faster_whisper_compute_type = self.fw_compute_combo.currentData()
        asr.faster_whisper_language = self.fw_lang_combo.currentData()
        asr.faster_whisper_beam_size = self.fw_beam_spin.value()
        asr.faster_whisper_segment_seconds = self.fw_seg_spin.value()

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
        p.set_line_break(self.line_break_check.isChecked())

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
