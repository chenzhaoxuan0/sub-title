"""全局设置对话框（Fluent 风格，手写 QSS，无第三方库）。

用 SettingCard/SettingCardGroup/ToggleSwitch 等 Fluent 风格组件组织设置项。
不引入 GPLv3 的 PyQt-Fluent-Widgets，保持项目 MIT 许可证。
保留所有功能逻辑（_load_current_state/_on_apply/各回调）和 config 字段映射 1:1。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, QEvent, QPoint, QThread, Signal, QSize
from PySide6.QtGui import QFont, QColor, QCloseEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QSpinBox,
    QCheckBox, QDialogButtonBox, QLabel, QGroupBox, QWidget,
    QTextEdit, QPushButton, QFontComboBox, QApplication, QMessageBox,
    QLineEdit, QStackedWidget, QDoubleSpinBox, QColorDialog, QScrollArea,
    QFrame, QSizePolicy, QFileDialog, QListWidget, QListWidgetItem,
    QAbstractItemView, QToolTip, QSplitter, QListView,
)

from ..config import Config, AsrConfig
from .. import credentials, hardware
from ..asr._install import (
    all_local_engines, check_engine_deps, recommended_install_command,
    scan_conda_envs, CondaEnvInfo, find_conda_env_for_engine,
)
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
        # 用唯一 objectName + #ID 选择器限定 stylesheet 只作用于自身。
        # 若用泛化的 QPushButton 选择器，QColorDialog 以本按钮为 parent 时，
        # 该样式会级联到对话框内的 OK/Cancel/Add to Custom Colors 等按钮，
        # 选中深色时这些按钮变成深底、黑色文字看不见。
        self.setObjectName("colorButton")
        self._color = color
        self.setFixedSize(60, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()
        self.clicked.connect(self._pick_color)

    def _update_style(self):
        self.setStyleSheet(f"""
            QPushButton#colorButton {{
                background-color: {self._color};
                border: 2px solid #888;
                border-radius: 4px;
            }}
            QPushButton#colorButton:hover {{
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
def _row(title: str, content: str, widget: QWidget, vertical: bool = False) -> SettingCard:
    """快捷构造一个 SettingCard。vertical=True 时控件占满卡片宽度（用于参数密集处）。"""
    return SettingCard(title, content, widget, vertical=vertical)


class EngineConfigCard(SettingCard):
    """单路引擎配置卡片（可复用）：引擎选择 + 各引擎参数。

    电脑声音和麦克风各用一个实例，分别读写各自的 AsrConfig（cfg.asr.system / cfg.asr.mic）。
    引擎选择联动 QStackedWidget 显示对应引擎的参数面板。阿里云凭证不在此卡片内
    （凭证全局唯一、两路共用，放在识别页独立的凭证卡片）。
    """

    # 引擎下拉项：(显示名, engine_type)。顺序与 _stack 索引映射对应。
    _ENGINE_ITEMS = [
        ("本地 FunASR Paraformer（流式，需GPU）", "funasr"),
        ("本地 SenseVoice（小模型，CPU可跑）", "sensevoice"),
        ("本地 Fun-ASR-Nano（中文/歌词，需GPU）", "funasr_nano"),
        ("本地 Qwen3-ASR（多语种/歌曲，需GPU）", "qwen3_asr"),
        ("本地 Whisper（faster-whisper，兼容模式）", "faster_whisper"),
        ("阿里云 API（流式，任意平台）", "aliyun"),
    ]
    _STACK_INDEX = {
        "funasr": 0, "sensevoice": 1, "funasr_nano": 2,
        "qwen3_asr": 3, "faster_whisper": 4, "aliyun": 5,
    }

    def __init__(self, title: str, content: str, parent=None):
        # vertical=True：引擎卡片标题/说明在上，引擎选择+参数面板在下占满宽度，
        # 避免水平布局把 combo/stack 挤成右侧窄条。
        super().__init__(title, content, None, parent=parent, vertical=True)
        self._fw_available = True
        # 缓存 load_from 传入的 AsrConfig，供按钮回调取 port/language 等字段
        # （EngineConfigCard 没有 cfg；那是 SettingsDialog 的属性）。
        self._asr: AsrConfig | None = None
        # WSL 服务管理的后台 worker 引用（Setup/Server/Status 三类，同时只跑一个）
        self._wsl_worker: QThread | None = None
        self._wsl_busy = False   # 安装/启动/停止进行中（按钮禁用）
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)

        # 引擎选择（占满卡片宽度，跟随卡片拉伸）
        self.engine_combo = QComboBox()
        for label, etype in self._ENGINE_ITEMS:
            self.engine_combo.addItem(label, etype)
        self.engine_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        cl.addWidget(self.engine_combo)

        self._hardware_info = hardware.detect()
        self.hardware_hint = QLabel(hardware.describe_recommendation(self._hardware_info))
        self.hardware_hint.setStyleSheet("color: #888; font-size: 11px;")
        self.hardware_hint.setWordWrap(True)
        cl.addWidget(self.hardware_hint)
        self.apply_hardware_recommendation_btn = QPushButton("应用实时优先推荐")
        self.apply_hardware_recommendation_btn.setToolTip("根据当前硬件选择适合实时字幕的本地引擎")
        self.apply_hardware_recommendation_btn.clicked.connect(self._apply_hardware_recommendation)
        cl.addWidget(self.apply_hardware_recommendation_btn)

        # 说话人区分开关（per source 独立：电脑声音 / 麦克风各自一个实例）
        # 强制约束：开启时该 source 的引擎必须为 funasr（其他架构不支持流式 spk_id）。
        # factory.py 会在加载时打印警告并降级。
        self.diarization_check = ToggleSwitch()
        cl.addWidget(_row(
            "说话人区分",
            "开启后字幕前会显示「[说话人 N]」并支持在「说话人」标签页命名。"
            "当前 source 引擎会被强制为 FunASR（不支持流式 spk_id 的引擎会降级）。",
            self.diarization_check,
            vertical=True,
        ))
        # 不兼容引擎警告（占满卡片宽度，不和 stack 抢布局）
        self.diarization_warn = QLabel("")
        self.diarization_warn.setStyleSheet("color: #d94c4c; padding: 2px 0;")
        self.diarization_warn.setWordWrap(True)
        self.diarization_warn.hide()
        cl.addWidget(self.diarization_warn)
        # 联动：切换开关或引擎 → 刷新警告
        self.diarization_check.checkedChanged.connect(self._update_diarization_warn)
        self.engine_combo.currentIndexChanged.connect(self._update_diarization_warn)

        # 各引擎参数（QStackedWidget）
        self.stack = QStackedWidget()
        self._build_funasr_panel()
        self._build_sensevoice_panel()
        self._build_funasr_nano_panel()
        self._build_qwen3_asr_panel()
        self._build_faster_whisper_panel()
        self._build_aliyun_panel()
        cl.addWidget(self.stack)

        self.set_widget(container)

    # ---- 各引擎参数面板 ----（vertical 卡片：控件占满宽度，避免横向拥挤）
    def _build_funasr_panel(self):
        panel = QWidget()
        lp = QVBoxLayout(panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        self.funasr_device_combo = QComboBox()
        self.funasr_device_combo.addItem("CUDA（NVIDIA GPU）", "cuda")
        self.funasr_device_combo.addItem("CPU", "cpu")
        lp.addWidget(_row("FunASR 设备", "推理设备，GPU 更快", self.funasr_device_combo, vertical=True))
        self.funasr_punc_check = ToggleSwitch()
        lp.addWidget(_row("流式标点", "补标点以支持自动分行（首次下载~300-700MB）", self.funasr_punc_check, vertical=True))
        lp.addStretch(1)
        self.stack.addWidget(panel)

    def _build_sensevoice_panel(self):
        panel = QWidget()
        lp = QVBoxLayout(panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        self.sv_device_combo = QComboBox()
        self.sv_device_combo.addItem("CPU（推荐，Mac/弱GPU）", "cpu")
        self.sv_device_combo.addItem("CUDA（NVIDIA GPU）", "cuda")
        lp.addWidget(_row("SenseVoice 设备", "推理设备", self.sv_device_combo, vertical=True))
        self.sv_segment_spin = QDoubleSpinBox()
        self.sv_segment_spin.setRange(0.5, 5.0)
        self.sv_segment_spin.setSingleStep(0.5)
        self.sv_segment_spin.setSuffix(" 秒")
        lp.addWidget(_row("攒段时长", "越小延迟越低但易切词", self.sv_segment_spin, vertical=True))
        lp.addStretch(1)
        self.stack.addWidget(panel)

    @staticmethod
    def _local_device_combo() -> QComboBox:
        combo = QComboBox()
        combo.addItem("CUDA（NVIDIA GPU，推荐）", "cuda")
        combo.addItem("CPU（可运行，但延迟较高）", "cpu")
        return combo

    @staticmethod
    def _segment_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.5, 8.0)
        spin.setSingleStep(0.5)
        spin.setSuffix(" 秒")
        return spin

    def _build_funasr_nano_panel(self):
        panel = QWidget()
        lp = QVBoxLayout(panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        self.nano_device_combo = self._local_device_combo()
        lp.addWidget(_row("设备", "800M 模型；中文、方言、歌词和音乐背景识别", self.nano_device_combo, vertical=True))
        self.nano_language_combo = QComboBox()
        for label, value in (("中文", "中文"), ("英文", "English"), ("日文", "Japanese")):
            self.nano_language_combo.addItem(label, value)
        lp.addWidget(_row("语言", "指定语言可提升准确率", self.nano_language_combo, vertical=True))

        # 推理模式：段式（默认，零依赖） vs 流式（需 WSL2 里的 realtime-server）
        self.nano_mode_combo = QComboBox()
        self.nano_mode_combo.addItem("段式（默认，零依赖）", "segment")
        self.nano_mode_combo.addItem("流式（需 WSL2 realtime-server，逐字低延迟）", "streaming")
        lp.addWidget(_row(
            "推理模式",
            "段式=简单低配，攒满段长出一段；流式=逐字输出延迟低，但需先在 WSL2 起 "
            "funasr-realtime-server 且更吃显存，连不上时自动回退段式",
            self.nano_mode_combo, vertical=True,
        ))
        # 流式地址提示（仅流式模式显示）
        self.nano_stream_addr_hint = QLabel(
            "连接到 localhost:10095（funasr-realtime-server 默认端口）。\n"
            "在 WSL2 里启动服务：funasr-realtime-server --endpoint-mode client --port 10095\n"
            "旧版 Windows 若 localhost 不通，请在 config.yaml 改用 WSL IP（wsl hostname -I 查看）。"
        )
        self.nano_stream_addr_hint.setStyleSheet("color: #888; font-size: 11px;")
        self.nano_stream_addr_hint.setWordWrap(True)
        self.nano_stream_addr_hint.hide()
        lp.addWidget(self.nano_stream_addr_hint)
        # 流式 VRAM 警告（vLLM 预分配显存，低显存易 OOM）
        self.nano_stream_vram_warn = QLabel("")
        self.nano_stream_vram_warn.setStyleSheet("color: #c77; font-size: 11px;")
        self.nano_stream_vram_warn.setWordWrap(True)
        self.nano_stream_vram_warn.hide()
        lp.addWidget(self.nano_stream_vram_warn)

        # 一键起 WSL 服务：按钮负责装环境（首次约 30 分钟）+ 起/停 funasr-realtime-server。
        # 仅流式模式显示（_on_nano_mode_changed 联动）。后台 worker 跑，状态标签实时反馈。
        self.nano_wsl_status = QLabel("正在检测 WSL 服务状态…")
        self.nano_wsl_status.setStyleSheet("color: #888; font-size: 11px;")
        self.nano_wsl_status.setWordWrap(True)
        self.nano_wsl_status.hide()
        lp.addWidget(self.nano_wsl_status)
        self.nano_wsl_btn = QPushButton()
        self.nano_wsl_btn.clicked.connect(self._on_nano_wsl_btn)
        self.nano_wsl_btn.hide()
        lp.addWidget(self.nano_wsl_btn)

        self.nano_segment_spin = self._segment_spin()
        lp.addWidget(_row("攒段时长", "仅段式模式用；越小延迟越低但易切词", self.nano_segment_spin, vertical=True))
        # 联动：切模式 → 显隐相关控件 + 刷新 VRAM 警告
        self.nano_mode_combo.currentIndexChanged.connect(self._on_nano_mode_changed)
        lp.addStretch(1)
        self.stack.addWidget(panel)

    def _build_qwen3_asr_panel(self):
        panel = QWidget()
        lp = QVBoxLayout(panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        self.qwen3_model_combo = QComboBox()
        self.qwen3_model_combo.addItem("Qwen3-ASR-0.6B（推荐）", "Qwen/Qwen3-ASR-0.6B")
        self.qwen3_model_combo.addItem("Qwen3-ASR-1.7B（更高精度）", "Qwen/Qwen3-ASR-1.7B")
        lp.addWidget(_row("模型", "需安装 qwen-asr；模型首次从 ModelScope 下载", self.qwen3_model_combo, vertical=True))
        self.qwen3_device_combo = self._local_device_combo()
        lp.addWidget(_row("设备", "原生低延迟流式需 NVIDIA GPU + vLLM", self.qwen3_device_combo, vertical=True))
        self.qwen3_quant_combo = QComboBox()
        self.qwen3_quant_combo.addItem("原始精度（GPU BF16 / CPU FP32）", "none")
        self.qwen3_quant_combo.addItem("4-bit 运行时量化（CUDA，需 bitsandbytes）", "4bit")
        lp.addWidget(_row("量化", "减少 Qwen3 显存占用；不是 GGUF，CPU 不支持 4-bit", self.qwen3_quant_combo, vertical=True))
        self.qwen3_language_combo = QComboBox()
        for label, value in (("自动检测", None), ("中文", "Chinese"), ("英文", "English"), ("日文", "Japanese")):
            self.qwen3_language_combo.addItem(label, value)
        lp.addWidget(_row("语言", "自动检测支持多语种和方言", self.qwen3_language_combo, vertical=True))
        self.qwen3_segment_spin = self._segment_spin()
        lp.addWidget(_row("攒段时长", "当前内置段式模式；vLLM 原生流式将后续接入", self.qwen3_segment_spin, vertical=True))
        hint = QLabel("安装：pip install qwen-asr。该包依赖较重，不随本程序默认安装。")
        hint.setStyleSheet("color: #b87b28; font-size: 11px;")
        hint.setWordWrap(True)
        lp.addWidget(hint)
        lp.addStretch(1)
        self.stack.addWidget(panel)

    def _build_faster_whisper_panel(self):
        panel = QWidget()
        lp = QVBoxLayout(panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        warning = QLabel(
            "⚠️ 兼容模式：Whisper 在静音、音乐暂停或片尾可能产生幻觉字幕。"
            "已启用 VAD 过滤，但无法保证完全消除；中文音乐建议使用 Fun-ASR-Nano 或 Qwen3-ASR。"
        )
        warning.setStyleSheet("color: #c77; font-size: 11px;")
        warning.setWordWrap(True)
        lp.addWidget(warning)
        # 关键性能：用 find_spec 探测 faster_whisper 是否安装，**不要真正 import**。
        # import faster_whisper 会拖进 ctranslate2(~2.7s) + tokenizers，首次约 3s，
        # 而 SettingsDialog 有两张引擎卡片（电脑声音+麦克风）→ 卡 ~3s 才打开。
        # 这里只需知道「装没装」来决定是否禁用控件，实际引擎创建在 asr/factory
        # 里按需 import，与此处无关。
        import importlib.util
        self._fw_available = importlib.util.find_spec("faster_whisper") is not None
        self.fw_model_combo = QComboBox()
        for name, label in [("large-v3", "large-v3（推荐，最准）"),
                            ("medium", "medium（中等）"),
                            ("small", "small（最快，弱机器）"),
                            ("distil-large-v3", "distil-large-v3（仅英文，最快）")]:
            self.fw_model_combo.addItem(label, name)
        lp.addWidget(_row("Whisper 模型", "首用从 ModelScope 自动下载", self.fw_model_combo, vertical=True))
        self.fw_device_combo = QComboBox()
        self.fw_device_combo.addItem("auto（自动检测，推荐）", "auto")
        self.fw_device_combo.addItem("CUDA（NVIDIA GPU）", "cuda")
        self.fw_device_combo.addItem("CPU", "cpu")
        lp.addWidget(_row("设备", "auto=有GPU用GPU否则CPU，不崩", self.fw_device_combo, vertical=True))
        self.fw_compute_combo = QComboBox()
        for cv, label in [("auto", "auto（自动）"),
                          ("float16", "float16（GPU）"),
                          ("int8", "int8（CPU 最快）"),
                          ("int8_float16", "int8_float16（省显存）")]:
            self.fw_compute_combo.addItem(label, cv)
        lp.addWidget(_row("计算精度", "INT8 是 CPU/GPU 可用的量化运行模式；CPU 推荐 INT8", self.fw_compute_combo, vertical=True))
        self.fw_lang_combo = QComboBox()
        self.fw_lang_combo.addItem("中文", "zh")
        self.fw_lang_combo.addItem("自动检测", "auto")
        self.fw_lang_combo.addItem("英文", "en")
        self.fw_lang_combo.addItem("日文", "ja")
        lp.addWidget(_row("语言", "影响识别准确度，中文建议指定", self.fw_lang_combo, vertical=True))
        self.fw_beam_spin = QSpinBox()
        self.fw_beam_spin.setRange(1, 10)
        lp.addWidget(_row("beam_size", "1 最快，5 默认更准", self.fw_beam_spin, vertical=True))
        self.fw_seg_spin = QDoubleSpinBox()
        self.fw_seg_spin.setRange(0.5, 5.0)
        self.fw_seg_spin.setSingleStep(0.5)
        self.fw_seg_spin.setSuffix(" 秒")
        lp.addWidget(_row("攒段时长", "越小延迟越低但易切词", self.fw_seg_spin, vertical=True))
        self.fw_vad_check = QCheckBox("过滤无语音片段（推荐，抑制静音幻觉）")
        lp.addWidget(self.fw_vad_check)
        gguf_hint = QLabel("GGUF 提示：当前程序未集成 llama.cpp/GGUF ASR 后端，不能加载任意 GGUF 文件。CPU 请使用此处的 small/medium + INT8。")
        gguf_hint.setStyleSheet("color: #888; font-size: 11px;")
        gguf_hint.setWordWrap(True)
        lp.addWidget(gguf_hint)
        if not self._fw_available:
            hint_fw = QLabel("⚠️ faster-whisper 未安装。多语言/翻译引擎需要它：\n"
                             "pip install faster-whisper（不依赖 torch）")
            hint_fw.setStyleSheet("color: #c77; font-size: 11px;")
            hint_fw.setWordWrap(True)
            lp.addWidget(hint_fw)
            for w in (self.fw_model_combo, self.fw_device_combo, self.fw_compute_combo,
                      self.fw_lang_combo, self.fw_beam_spin, self.fw_seg_spin,
                      self.fw_vad_check):
                w.setEnabled(False)
        lp.addStretch(1)
        self.stack.addWidget(panel)

    def _build_aliyun_panel(self):
        panel = QWidget()
        lp = QVBoxLayout(panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        hint = QLabel("阿里云凭证（AccessKey ID/Secret/AppKey）全局唯一，两路共用。\n"
                      "请在下方「阿里云凭证」卡片填写，凭证存于系统保险箱。")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        lp.addWidget(hint)
        lp.addStretch(1)
        self.stack.addWidget(panel)

    # ---- 联动 ----
    def _on_engine_changed(self, _idx: int):
        self._sync_stack()
        self._update_diarization_warn()

    def _sync_stack(self):
        etype = self.engine_combo.currentData()
        self.stack.setCurrentIndex(self._STACK_INDEX.get(etype, 0))

    def _on_nano_mode_changed(self) -> None:
        """Fun-ASR-Nano 段式/流式联动：流式时禁用攒段时长、显示地址与 VRAM 警告。

        注意：此槽由 nano_mode_combo 的 currentIndexChanged 触发。load_from() 用
        blockSignals 静默设置 combo 值（不触发本槽），避免构造期间启动 WSL 后台
        worker（worker 在对话框未构造完时操作 UI 控件会导致 Qt 段错误闪退）。
        因此本槽只在**用户手动切换**模式时执行，可安全触发 WSL 状态探测。
        """
        streaming = self.nano_mode_combo.currentData() == "streaming"
        # 攒段时长仅段式有意义；流式忽略，灰掉避免误操作
        self.nano_segment_spin.setEnabled(not streaming)
        self.nano_stream_addr_hint.setVisible(streaming)
        # WSL 一键服务按钮仅流式显示；切到流式时触发一次状态探测
        self.nano_wsl_btn.setVisible(streaming)
        self.nano_wsl_status.setVisible(streaming)
        if streaming and not self._wsl_busy:
            self._refresh_nano_wsl_status()
        if not streaming:
            self.nano_stream_vram_warn.hide()
            return
        # VRAM 警告：vLLM 启动时预分配显存，低显存机器开流式易 OOM
        vram = float(self._hardware_info.get("cuda_vram_gb", 0) or 0)
        if not self._hardware_info.get("has_cuda") or vram < 6:
            self.nano_stream_vram_warn.setText(
                f"⚠️ 流式（vLLM）建议 ≥6GB 显存，当前检测到 {vram:g}GB，可能 OOM"
            )
            self.nano_stream_vram_warn.show()
        else:
            self.nano_stream_vram_warn.hide()

    def _apply_nano_mode_state(self, streaming: bool) -> None:
        """构造期恢复 UI 状态（不触发 WSL 探测）。供 load_from 调用。

        与 _on_nano_mode_changed 的区别：本方法不启动后台 worker，仅同步控件显隐，
        避免对话框构造未完成时 QThread 操作 UI 导致段错误。
        """
        self.nano_segment_spin.setEnabled(not streaming)
        self.nano_stream_addr_hint.setVisible(streaming)
        self.nano_wsl_btn.setVisible(streaming)
        self.nano_wsl_status.setVisible(streaming)
        if not streaming:
            self.nano_stream_vram_warn.hide()
            return
        vram = float(self._hardware_info.get("cuda_vram_gb", 0) or 0)
        if not self._hardware_info.get("has_cuda") or vram < 6:
            self.nano_stream_vram_warn.setText(
                f"⚠️ 流式（vLLM）建议 ≥6GB 显存，当前检测到 {vram:g}GB，可能 OOM"
            )
            self.nano_stream_vram_warn.show()
        else:
            self.nano_stream_vram_warn.hide()
        # 流式模式给个占位文案，等对话框显示后由 _kickoff_nano_wsl_probe 延迟探测
        self.nano_wsl_status.setText("（打开后自动检测 WSL 服务状态）")
        self.nano_wsl_btn.setEnabled(False)
        self.nano_wsl_btn.setText("待检测…")

    # ---- WSL 一键服务：状态探测 / 按钮点击 / 后台 worker 回调 ----
    def _refresh_nano_wsl_status(self) -> None:
        """后台探测 WSL 服务状态，据此设按钮文案（status() 会 spawn wsl.exe，不能在 UI 线程）。"""
        self.nano_wsl_status.setText("正在检测 WSL 服务状态…")
        self.nano_wsl_btn.setEnabled(False)
        self.nano_wsl_btn.setText("检测中…")
        self._wsl_worker = _WslStatusWorker()
        self._wsl_worker.finished_status.connect(self._on_nano_wsl_status)
        self._wsl_worker.start()

    def _on_nano_wsl_status(self, result: object) -> None:
        """状态探测完成：根据 installed/running 切按钮文案与可点的动作。"""
        if not isinstance(result, dict):
            result = {"installed": False, "running": False}
        installed = bool(result.get("installed"))
        running = bool(result.get("running"))
        self._wsl_worker = None
        if self._wsl_busy:
            return   # 探测期间用户已点了动作，交给动作回调去刷新
        self.nano_wsl_btn.setEnabled(True)
        if running:
            self.nano_wsl_status.setText("✅ WSL 流式服务运行中（持续占用显存，退出程序时自动关闭）")
            self.nano_wsl_btn.setText("停止 WSL 服务")
        elif installed:
            self.nano_wsl_status.setText("推理环境已就绪（funasr/vllm 已装），服务未运行")
            self.nano_wsl_btn.setText("启动 WSL 服务")
        else:
            # 这里检测的是「nano 推理环境」（conda 环境 + funasr/vllm/websockets）是否装好，
            # 不是 WSL2 本身（WSL2 已装才会显示这个面板）。文案必须说清，避免误以为 WSL2 缺失。
            self.nano_wsl_status.setText(
                "WSL2 已检测到，但 nano 推理环境（funasr/vllm）尚未安装")
            self.nano_wsl_btn.setText("安装 nano 推理环境（首次约 30 分钟）")

    def _on_nano_wsl_btn(self) -> None:
        """主按钮分发：按当前文案决定 装/起/停。"""
        text = self.nano_wsl_btn.text()
        self._wsl_busy = True
        self.nano_wsl_btn.setEnabled(False)
        if "安装" in text:
            self.nano_wsl_status.setText("开始安装 WSL 环境（下载 + 安装，请耐心等待）…")
            self.nano_wsl_btn.setText("安装中…")
            worker: QThread = _WslSetupWorker()
            worker.progress.connect(
                lambda msg: self.nano_wsl_status.setText(f"安装中：{msg}"))
            worker.finished_ok.connect(self._on_nano_wsl_setup_done)
        elif "停止" in text:
            self.nano_wsl_status.setText("正在停止 WSL 服务（释放显存）…")
            self.nano_wsl_btn.setText("停止中…")
            worker = _WslServerWorker(action="stop")
            worker.finished_ok.connect(self._on_nano_wsl_stop_done)
        else:  # 启动
            asr = self._asr
            port = int(getattr(asr, "funasr_nano_streaming_port", 10095)) if asr else 10095
            lang = getattr(asr, "funasr_nano_language", "中文") if asr else "中文"
            self.nano_wsl_status.setText("启动 WSL 服务（vLLM 加载模型，约 1-2 分钟）…")
            self.nano_wsl_btn.setText("启动中…")
            worker = _WslServerWorker(action="start", port=port, language=lang)
            worker.progress.connect(
                lambda msg: self.nano_wsl_status.setText(f"启动中：{msg}"))
            worker.finished_ok.connect(self._on_nano_wsl_start_done)
        self._wsl_worker = worker
        worker.start()

    def _on_nano_wsl_setup_done(self, ok: bool, err: str) -> None:
        self._wsl_busy = False
        self._wsl_worker = None
        if ok:
            self.nano_wsl_status.setText("✅ nano 推理环境安装完成")
            self._refresh_nano_wsl_status()
        else:
            # 失败时【不要】立即刷新（会覆盖错误信息）。显式展示失败原因 + 让按钮可重试。
            self.nano_wsl_status.setText(
                f"❌ 安装失败：{err}（可重新点击按钮重试，已完成的步骤会跳过）")
            self.nano_wsl_btn.setEnabled(True)
            self.nano_wsl_btn.setText("重试安装 nano 推理环境")

    def _on_nano_wsl_start_done(self, ok: bool, err: str) -> None:
        self._wsl_busy = False
        self._wsl_worker = None
        if not ok:
            self.nano_wsl_status.setText(
                f"❌ 启动失败：{err}（可重新点击按钮重试）")
            self.nano_wsl_btn.setEnabled(True)
            self.nano_wsl_btn.setText("启动 WSL 服务")
            return
        self._refresh_nano_wsl_status()

    def _on_nano_wsl_stop_done(self, ok: bool, err: str) -> None:
        self._wsl_busy = False
        self._wsl_worker = None
        if not ok:
            self.nano_wsl_status.setText("⚠️ 停止可能未完全生效（显存或未释放）")
        self._refresh_nano_wsl_status()

    def _apply_hardware_recommendation(self):
        engine_type, overrides = hardware.recommend_engine(self._hardware_info)
        self.engine_combo.setCurrentIndex(max(0, self.engine_combo.findData(engine_type)))
        if "device" in overrides:
            self.funasr_device_combo.setCurrentIndex(
                max(0, self.funasr_device_combo.findData(overrides["device"])))
        if "sensevoice_device" in overrides:
            self.sv_device_combo.setCurrentIndex(
                max(0, self.sv_device_combo.findData(overrides["sensevoice_device"])))

    def _update_diarization_warn(self):
        """说话人区分开关在不兼容引擎上 → 红字提示。
        
        实际加载时 factory.py 会强制降级为 funasr 并打 warning；这里只是 UI 提示。
        """
        if not self.diarization_check.isChecked():
            self.diarization_warn.hide()
            return
        etype = self.engine_combo.currentData()
        if etype == "funasr":
            self.diarization_warn.hide()
        else:
            self.diarization_warn.setText(
                f"⚠️ 当前引擎「{etype}」不支持流式 spk_id，加载时会自动降级为 FunASR。"
            )
            self.diarization_warn.show()

    # ---- 读写 AsrConfig ----
    def load_from(self, asr: AsrConfig) -> None:
        self._asr = asr   # 缓存，供 WSL 启动按钮取 port/language
        idx = self.engine_combo.findData(asr.engine_type)
        self.engine_combo.setCurrentIndex(max(0, idx))
        self._sync_stack()
        self.diarization_check.setChecked(bool(getattr(asr, "enable_speaker_diarization", False)))
        self._update_diarization_warn()
        self.funasr_device_combo.setCurrentIndex(
            max(0, self.funasr_device_combo.findData(asr.device)))
        self.funasr_punc_check.setChecked(asr.funasr_punc_enabled)
        self.sv_device_combo.setCurrentIndex(
            max(0, self.sv_device_combo.findData(asr.sensevoice_device)))
        self.sv_segment_spin.setValue(asr.sensevoice_segment_seconds)
        self.nano_device_combo.setCurrentIndex(
            max(0, self.nano_device_combo.findData(asr.funasr_nano_device)))
        self.nano_language_combo.setCurrentIndex(
            max(0, self.nano_language_combo.findData(asr.funasr_nano_language)))
        # 用 blockSignals 静默设 combo，避免触发 _on_nano_mode_changed 的 WSL 后台探测
        # （构造期间启动 QThread 操作未建好的 UI 控件会段错误闪退）。状态用
        # _apply_nano_mode_state 同步显隐，真正的 WSL 探测推迟到对话框显示后。
        self.nano_mode_combo.blockSignals(True)
        self.nano_mode_combo.setCurrentIndex(
            max(0, self.nano_mode_combo.findData(getattr(asr, "funasr_nano_mode", "segment"))))
        self.nano_mode_combo.blockSignals(False)
        self.nano_segment_spin.setValue(asr.funasr_nano_segment_seconds)
        self._apply_nano_mode_state(
            self.nano_mode_combo.currentData() == "streaming")
        self.qwen3_model_combo.setCurrentIndex(
            max(0, self.qwen3_model_combo.findData(asr.qwen3_asr_model)))
        self.qwen3_device_combo.setCurrentIndex(
            max(0, self.qwen3_device_combo.findData(asr.qwen3_asr_device)))
        self.qwen3_quant_combo.setCurrentIndex(
            max(0, self.qwen3_quant_combo.findData(asr.qwen3_asr_quantization)))
        self.qwen3_language_combo.setCurrentIndex(
            max(0, self.qwen3_language_combo.findData(asr.qwen3_asr_language)))
        self.qwen3_segment_spin.setValue(asr.qwen3_asr_segment_seconds)
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
        self.fw_vad_check.setChecked(asr.faster_whisper_vad_filter)

    def apply_to(self, asr: AsrConfig) -> None:
        asr.engine_type = self.engine_combo.currentData()
        asr.device = self.funasr_device_combo.currentData()
        asr.funasr_punc_enabled = self.funasr_punc_check.isChecked()
        asr.enable_speaker_diarization = self.diarization_check.isChecked()
        asr.sensevoice_device = self.sv_device_combo.currentData()
        asr.sensevoice_segment_seconds = self.sv_segment_spin.value()
        asr.funasr_nano_device = self.nano_device_combo.currentData()
        asr.funasr_nano_language = self.nano_language_combo.currentData()
        asr.funasr_nano_mode = self.nano_mode_combo.currentData()
        asr.funasr_nano_segment_seconds = self.nano_segment_spin.value()
        asr.qwen3_asr_model = self.qwen3_model_combo.currentData()
        asr.qwen3_asr_device = self.qwen3_device_combo.currentData()
        asr.qwen3_asr_quantization = self.qwen3_quant_combo.currentData()
        asr.qwen3_asr_language = self.qwen3_language_combo.currentData()
        asr.qwen3_asr_segment_seconds = self.qwen3_segment_spin.value()
        asr.faster_whisper_model = self.fw_model_combo.currentData()
        asr.faster_whisper_device = self.fw_device_combo.currentData()
        asr.faster_whisper_compute_type = self.fw_compute_combo.currentData()
        asr.faster_whisper_language = self.fw_lang_combo.currentData()
        asr.faster_whisper_beam_size = self.fw_beam_spin.value()
        asr.faster_whisper_segment_seconds = self.fw_seg_spin.value()
        asr.faster_whisper_vad_filter = self.fw_vad_check.isChecked()


class _CondaScanWorker(QThread):
    """后台扫描系统 conda 环境（启动多个子进程，不能阻塞 UI 线程）。

    扫描完通过 finished 信号把结果列表回主线程。参照 app.py _EngineWorker 的 QThread 模式。
    设为 daemon 线程，避免主进程退出时因 worker 还在跑子进程而卡住（测试场景尤其重要）。
    """

    finished = Signal(object)   # list[CondaEnvInfo]

    def run(self):
        try:
            envs = scan_conda_envs()
        except Exception as e:
            envs = []   # 扫描整体失败时给空列表，UI 显示"未发现"
            print(f"[conda-scan] 扫描失败: {e}")
        self.finished.emit(envs)


class _WslSetupWorker(QThread):
    """后台装 WSL 环境（耗时几十分钟，不能阻塞 UI）。

    仿 _CondaScanWorker 的 QThread 模式。progress 信号实时回报步骤文本，
    finished 信号回主线程通知成功/失败 + 错误信息。
    """

    progress = Signal(str)
    finished_ok = Signal(bool, str)   # (成功, 错误信息/空)

    def run(self):
        from ..asr.wsl_nano_service import WslNanoService
        try:
            ok, err = WslNanoService().setup_environment(self.progress.emit)
        except Exception as e:
            ok, err = False, f"安装异常: {e}"
        self.finished_ok.emit(ok, err)


class _WslServerWorker(QThread):
    """后台起/停 WSL 服务（起服务含 vLLM 模型加载，1-2 分钟）。"""

    progress = Signal(str)
    finished_ok = Signal(bool, str)   # (成功, 错误信息/空)

    def __init__(self, action: str, port: int = 10095, language: str = "中文"):
        super().__init__()
        self._action = action      # "start" / "stop"
        self._port = port
        self._language = language

    def run(self):
        from ..asr.wsl_nano_service import WslNanoService
        svc = WslNanoService()
        try:
            if self._action == "stop":
                ok, err = svc.stop_server(), ""
            else:
                ok, err = svc.start_server(self._port, self._language, self.progress.emit)
        except Exception as e:
            ok, err = False, f"操作异常: {e}"
        self.finished_ok.emit(ok, err)


class _WslStatusWorker(QThread):
    """后台查 WSL 服务状态（status() 会 spawn wsl.exe，冷启动 1-2s，避免卡 UI）。"""

    finished_status = Signal(object)   # dict {"installed", "running"}

    def run(self):
        from ..asr.wsl_nano_service import WslNanoService
        try:
            result = WslNanoService().status()
        except Exception:
            result = {"installed": False, "running": False}
        self.finished_status.emit(result)


class SettingsDialog(QDialog):
    """设置对话框（Fluent 风格）。"""

    skin_editor_requested = Signal()

    def __init__(self, cfg: Config, panel, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.panel = panel
        self._theme_mgr = get_theme_manager()
        self.setWindowTitle("全局设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        # 加大默认尺寸 + 设最小尺寸：识别页内容多，原来 640x720 偏小需放大才能看全
        self.resize(840, 780)
        self.setMinimumSize(720, 600)
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
    # 侧边栏收起态宽度（仅放一个展开箭头按钮的窄条）
    _SIDEBAR_COLLAPSED_WIDTH = 32
    # 侧边栏宽度上下限（拖拽时夹紧到这个范围）
    _SIDEBAR_MIN_WIDTH = 140
    _SIDEBAR_MAX_WIDTH = 360

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # 可折叠/可调宽侧边栏：左 QListWidget 导航 + 右 QStackedWidget 内容，
        # 由 QSplitter 承载——用户可拖动分隔条调整宽度。
        # （早先用 QTabWidget+West+HorizontalTextTabBar，但 tab bar 宽度无法拖拽、
        # 也无法整体折叠，故改为本方案。）
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)

        # ---- 左：侧边栏容器（顶部折叠按钮 + 导航列表）----
        self._nav_container = QWidget()
        self._nav_container.setObjectName("sidebarContainer")
        nav_v = QVBoxLayout(self._nav_container)
        nav_v.setContentsMargins(0, 0, 0, 0)
        nav_v.setSpacing(6)

        # 折叠/展开切换按钮
        self._collapse_btn = QPushButton("◀")
        self._collapse_btn.setObjectName("sidebarToggle")
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.setToolTip("收起侧边栏")
        self._collapse_btn.setFixedHeight(28)
        self._collapse_btn.clicked.connect(self._toggle_sidebar)
        nav_v.addWidget(self._collapse_btn)

        # 导航列表（不可拖拽重排、单选、点击切换页面）
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("settingsNav")
        self.nav_list.setMovement(QListView.Static)
        self.nav_list.setResizeMode(QListView.Adjust)
        self.nav_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.nav_list.setFocusPolicy(Qt.NoFocus)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setSpacing(2)
        nav_v.addWidget(self.nav_list, 1)

        # ---- 右：内容区 ----
        self.stack = QStackedWidget()

        # 页面定义（顺序即导航顺序，索引一一对应）
        pages = [
            ("识别", self._build_recognition_tab()),
            ("引擎管理", self._build_engine_mgmt_tab()),
            ("说话人", self._build_speaker_tab()),
            ("外观", self._build_appearance_tab()),
            ("行为", self._build_behavior_tab()),
            ("皮肤", self._build_skin_tab()),
            ("文稿", self._build_transcript_tab()),
        ]
        for title, page in pages:
            self.nav_list.addItem(title)
            self.stack.addWidget(page)
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_tab_changed)

        # 内部状态：当前展开宽度 + 是否收起（_apply_sidebar_state 会用 cfg 覆盖）
        self._sidebar_width = 200
        self._sidebar_collapsed = False

        self.splitter.addWidget(self._nav_container)
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(0, 0)   # 导航栏不抢占内容区空间
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self._sidebar_width, 640])
        # 拖动分隔条 → 实时记忆宽度（写回 cfg 对象，落盘靠关闭时 app._save_config）
        self.splitter.splitterMoved.connect(self._on_sidebar_resized)
        layout.addWidget(self.splitter, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        self._apply_btn = btns.button(QDialogButtonBox.Apply)
        self._apply_btn.setProperty("primary", True)
        self._apply_btn.clicked.connect(self._on_apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # 所有控件构建完后装滚轮防护（避免悬停滚动误改下拉框/数值框的值）
        self._install_wheel_focus_guard()

    # ---------- 滚轮误触防护 ----------
    def _install_wheel_focus_guard(self):
        """滚轮误触防护：QComboBox/QSpinBox 等控件只有被鼠标点击后，才响应滚轮。

        Qt 默认行为是鼠标悬停在控件上滚动就改值，在设置页滚动浏览时极易误触。
        早先版本用 hasFocus() 判断，但焦点可能被键盘 Tab / Qt 自动聚焦被动获得
        （用户没点过），仍会误触。现改为显式点击追踪：只有「鼠标按下」过的控件
        才放行滚轮；点了别处或焦点离开就立即收回，杜绝悬停误触。
        """
        # PySide6 的 findChildren 不接受 tuple，逐类型查找后合并
        sensitive_types = (QComboBox, QSpinBox, QDoubleSpinBox, QFontComboBox)
        targets: list = []
        for t in sensitive_types:
            targets.extend(self.findChildren(t))
        for w in targets:
            w.installEventFilter(self)
        # 当前被点击激活、允许滚轮改值的控件；None = 无（全部吃掉滚轮）
        self._wheel_active_target = None

    def eventFilter(self, obj, event):
        """滚轮防护：只有被鼠标点击过的控件才放行 Wheel；其余一律吃掉交回页面滚动。

        - MouseButtonPress：记录该控件为激活目标，滚轮放行
        - FocusOut / 鼠标点到别的控件：清除目标，滚轮重新被拦
        - Wheel：仅当 obj 是当前激活目标时放行，否则吃掉
        """
        t = event.type()
        if t == QEvent.MouseButtonPress:
            # 点击即激活（包括从别的控件点过来——按下时旧目标会被下面的 FocusOut 清）
            self._wheel_active_target = obj
        elif t == QEvent.FocusOut:
            # 焦点离开当前激活控件 → 收回滚轮权限
            if self._wheel_active_target is obj:
                self._wheel_active_target = None
        elif t == QEvent.Wheel:
            if obj is not self._wheel_active_target:
                # 不是刚点过的控件 → 吃掉滚轮，交给父滚动区翻页
                event.ignore()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        """首次显示时 splitter 才拿到真实宽度——此刻重新应用侧边栏尺寸，

        避免构造期 splitter.width() 不可靠导致内容区/侧边栏分配错误。
        """
        super().showEvent(event)
        if not getattr(self, "_sidebar_geom_applied", False):
            self._sidebar_geom_applied = True
            self._apply_sidebar_geometry()
        # 首次显示后延迟触发 WSL 服务状态探测（构造期不探测，避免 QThread 操作
        # 未建好 UI 导致闪退）。仅对处于流式模式的卡片探测；用 singleShot 排到
        # 事件循环，确保对话框已完全布局完毕。
        if not getattr(self, "_wsl_probe_kicked", False):
            self._wsl_probe_kicked = True
            QTimer.singleShot(0, self._kickoff_nano_wsl_probe)

    def _kickoff_nano_wsl_probe(self) -> None:
        """对话框显示后，对流式模式的引擎卡片触发一次 WSL 状态探测。"""
        for card in (self.system_engine_card, self.mic_engine_card):
            try:
                combo = getattr(card, "nano_mode_combo", None)
                if combo is not None and combo.currentData() == "streaming":
                    if not getattr(card, "_wsl_busy", False):
                        card._refresh_nano_wsl_status()
            except Exception as e:
                print(f"[settings] WSL 探测触发异常: {e}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """标题栏 X 与底部 Close 按钮走同一路径：reject → finished → app 保存配置。

        非模态单例（WA_DeleteOnClose=False）下，X 默认只调 done() 不一定触发
        finished 信号，导致 app 的 _on_settings_finished 不执行（配置不保存、
        单例引用不清空，下次「全局设置」打不开）。这里显式 reject() 保证一致。
        """
        self.reject()
        event.ignore()   # 真正的关闭由 reject → finished → app 链路完成

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

        # 识别控制（开始/停止）—— 顶层独立卡片
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

        # ---- 🔊 电脑声音分组：设备 + 开关 + 引擎 ----
        g_sys = SettingCardGroup("🔊 电脑声音")
        self.device_combo = QComboBox()
        for name, data in self.panel.get_devices():
            self.device_combo.addItem(name, data)
        g_sys.add_card(_row("输入源", "系统音频输出设备（扬声器/耳机）", self.device_combo))
        self.sys_enable_toggle = ToggleSwitch()
        g_sys.add_card(_row("启用电脑声音", "回录系统声音输出", self.sys_enable_toggle))
        self.system_engine_card = EngineConfigCard(
            "电脑声音引擎", "电脑声音使用的识别引擎及参数（可与麦克风不同）")
        g_sys.add_card(self.system_engine_card)
        v.addWidget(g_sys)

        # ---- 🎤 麦克风分组：设备 + 开关 + 颜色 + 引擎 ----
        g_mic = SettingCardGroup("🎤 麦克风")
        self.mic_combo = QComboBox()
        for name, data in self.panel.get_mic_devices():
            self.mic_combo.addItem(name, data)
        g_mic.add_card(_row("麦克风", "麦克风输入设备（与电脑声音分开识别）", self.mic_combo))
        self.mic_enable_toggle = ToggleSwitch()
        g_mic.add_card(_row("启用麦克风", "识别麦克风语音（双开占双倍显存/内存）", self.mic_enable_toggle))
        self.mic_color_combo = QComboBox()
        self.mic_color_combo.setEditable(True)
        for name, val in (("蓝 #5aa9ff", "#5aa9ff"), ("绿 #5ad6a6", "#5ad6a6"),
                          ("橙 #f0a830", "#f0a830"), ("粉 #f06292", "#f06292"),
                          ("紫 #b06cf0", "#b06cf0"), ("红 #f06262", "#f06262")):
            self.mic_color_combo.addItem(name, val)
        g_mic.add_card(_row("字幕颜色", "麦克风字幕文字颜色（电脑声音跟随主题）", self.mic_color_combo))
        self.mic_engine_card = EngineConfigCard(
            "麦克风引擎", "麦克风使用的识别引擎及参数（按算力灵活调配）")
        g_mic.add_card(self.mic_engine_card)
        v.addWidget(g_mic)

        # ---- 阿里云凭证（全局唯一，两路共用）----
        cred_card = SettingCard(
            "阿里云凭证", "AccessKey ID/Secret/AppKey（两路共用，存系统保险箱）", None)
        cp = QVBoxLayout()
        cp.setContentsMargins(0, 0, 0, 0)
        cp.setSpacing(4)
        self.aliyun_akid_edit = QLineEdit()
        self.aliyun_akid_edit.setPlaceholderText("AccessKey ID")
        cp.addWidget(_row("AccessKey ID", "阿里云控制台获取", self.aliyun_akid_edit, vertical=True))
        self.aliyun_aksecret_edit = QLineEdit()
        self.aliyun_aksecret_edit.setPlaceholderText("AccessKey Secret")
        self.aliyun_aksecret_edit.setEchoMode(QLineEdit.Password)
        cp.addWidget(_row("AccessKey Secret", "阿里云控制台获取", self.aliyun_aksecret_edit, vertical=True))
        self.aliyun_appkey_edit = QLineEdit()
        self.aliyun_appkey_edit.setPlaceholderText("AppKey")
        cp.addWidget(_row("AppKey", "智能语音交互项目 AppKey", self.aliyun_appkey_edit, vertical=True))
        cred_location = credentials.storage_location()
        cred_hint = QLabel(
            f"🔐 凭证存于系统保险箱（{cred_location}），不进 config.yaml。\n"
            f"卸载重装或换电脑需要重新填；需先装 nls SDK（见 README）。\n"
            f"两路同时用阿里云时会建两个连接共用此凭证。"
        )
        cred_hint.setStyleSheet("color: #888; font-size: 11px;")
        cred_hint.setWordWrap(True)
        cp.addWidget(cred_hint)
        cp_w = QWidget()
        cp_w.setLayout(cp)
        cred_card.set_widget(cp_w)
        v.addWidget(cred_card)

        v.addStretch(1)
        return self._wrap_scroll(tab)

    # ---------- 标签页：引擎管理 ----------
    def _build_engine_mgmt_tab(self) -> QWidget:
        """本地引擎依赖管理页：展示各引擎安装状态，未装时给精确安装命令+一键复制。

        纯 API（阿里云）模式无需任何安装；本地引擎按需装，装完点「重新检测」即可看到
        就绪状态（find_spec 实时探测，无需重启程序）。硬件信息也随重新检测刷新。
        """
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # ---- 顶部说明卡片（含硬件摘要 + 重新检测按钮）----
        self._hw_summary_label = QLabel()
        self._hw_summary_label.setStyleSheet("color: #888; font-size: 12px;")
        self._hw_summary_label.setWordWrap(True)

        self._rescan_btn = QPushButton("🔄 重新检测")
        self._rescan_btn.setCursor(Qt.PointingHandCursor)
        self._rescan_btn.setToolTip("安装/卸载依赖后点此按钮，刷新引擎状态和硬件信息")
        self._rescan_btn.clicked.connect(self._refresh_engine_mgmt)

        intro = QLabel(
            "🟢 阿里云 API 模式无需安装任何依赖，开箱即用（仅需在「识别」页填凭证）。\n"
            "🟡 本地引擎（SenseVoice / FunASR 等）按需安装：复制下方命令到终端执行，"
            "装完点「重新检测」即可看到就绪状态，无需重启程序。"
        )
        intro.setStyleSheet("color: #888; font-size: 12px;")
        intro.setWordWrap(True)

        # 说明 + 硬件摘要放一起，重新检测按钮单独一行靠右
        intro_card = SettingCard("引擎依赖管理", "", intro, vertical=True)
        # 在 intro_card 的内容区追加硬件摘要 + 按钮（复用 vertical 卡片的 set_widget 不便，
        # 改为直接把 intro 作为 widget 后，再单独放硬件行）
        v.addWidget(intro_card)

        # 硬件摘要行：左 label 右按钮
        hw_row = QHBoxLayout()
        hw_row.setContentsMargins(0, 0, 0, 0)
        hw_row.setSpacing(8)
        hw_row.addWidget(self._hw_summary_label, 1)
        hw_row.addWidget(self._rescan_btn)
        hw_w = QWidget()
        hw_w.setLayout(hw_row)
        v.addWidget(hw_w)

        # ---- 各本地引擎卡片（容器，便于重新检测时清空重建）----
        # 每个引擎卡片融合显示三态：exe 内部就绪 / conda 环境就绪 / 都没有。
        self._engine_cards_container = SettingCardGroup("本地引擎")
        v.addWidget(self._engine_cards_container)
        # conda 扫描状态（扫描中/完成提示），独立一行小字
        self._conda_scan_status = QLabel()
        self._conda_scan_status.setStyleSheet("color: #888; font-size: 11px; padding: 2px 0;")
        v.addWidget(self._conda_scan_status)
        # 缓存 conda 扫描结果，供引擎卡片构建时判断"conda 里有没有"
        self._conda_envs: list[CondaEnvInfo] = []
        self._conda_worker: _CondaScanWorker | None = None

        # ---- 阿里云提示卡片 ----
        api_hint = QLabel(
            "阿里云 API 是流式云端识别，不依赖 torch / funasr 等本地模型库。\n"
            "只需在「识别」标签页的「阿里云凭证」卡片填入 AccessKey/AppKey 即可。\n"
            "凭证存于系统保险箱，跨平台通用。"
        )
        api_hint.setStyleSheet("color: #888; font-size: 11px;")
        api_hint.setWordWrap(True)
        api_card = SettingCard("阿里云 API（无需安装）", "", api_hint, vertical=True)
        v.addWidget(api_card)

        v.addStretch(1)
        # 首次填充硬件摘要 + 引擎卡片（快，无副作用）。
        # conda 扫描涉及子进程（慢），延迟到用户首次切到本页时再触发（懒加载），
        # 避免构造对话框时就启动后台线程（测试场景会卡主进程退出）。
        self._refresh_engine_mgmt(skip_conda=True)
        self._conda_scan_pending = True   # 待首次切页时扫描
        return self._wrap_scroll(tab)

    def _refresh_engine_mgmt(self, skip_conda: bool = False) -> None:
        """重新检测硬件 + 刷新所有引擎卡片状态。

        用户装完依赖（pip install funasr/torch 等）后点「重新检测」，find_spec 会实时
        探测到新装的包，引擎卡片立即更新为「已就绪」。硬件信息（内存/显存）也一并刷新。
        hardware.detect() 有缓存，这里用 force=True 强制重扫。
        skip_conda=True 时跳过 conda 环境扫描（构造对话框时用，避免启动后台线程）。
        """
        # 强制重扫硬件（psutil 内存、torch CUDA 都实时查）
        hw = hardware.detect(force=True)
        has_cuda = bool(hw.get("has_cuda"))
        gpu_name = hw.get("gpu_name", "")
        gpus = gpu_name if has_cuda and gpu_name else ("无 CUDA" if not has_cuda else gpu_name)
        ram = hw.get("ram_gb", 0)
        ram_str = f"{ram:g}GB" if ram else "未知"
        self._hw_summary_label.setText(
            f"当前硬件：{hw.get('cpu_cores', 0)} 核 / {ram_str} 内存 / GPU：{gpus}"
        )

        # 清空旧卡片，按最新依赖状态重建
        layout = self._engine_cards_container._cards_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for info in all_local_engines():
            self._engine_cards_container.add_card(
                self._build_engine_install_card(info, has_cuda, self._conda_envs)
            )

        # conda 环境扫描（异步后台线程）。skip_conda=True 时不触发（构造时用）。
        if not skip_conda:
            self._start_conda_scan()

    def _start_conda_scan(self) -> None:
        """启动后台线程扫描系统 conda 环境（避免子进程阻塞 UI）。"""
        self._conda_scan_status.setText("正在扫描系统 conda 环境…")
        # 已有 worker 在跑就等它（最多 8s），避免并发扫描堆积子进程
        if self._conda_worker is not None and self._conda_worker.isRunning():
            self._conda_worker.wait(8000)
        self._conda_worker = _CondaScanWorker(self)
        self._conda_worker.finished.connect(self._on_conda_scan_done)
        self._conda_worker.start()

    def _on_conda_scan_done(self, envs: object) -> None:
        """后台扫描完成：缓存结果 + 重建引擎卡片（让 conda 里的依赖反映到各卡片状态）。"""
        self._conda_worker = None
        self._conda_envs = envs if isinstance(envs, list) else []
        local_envs = [e for e in self._conda_envs if e.has_any_local_engine]
        if local_envs:
            names = "、".join(e.name for e in local_envs)
            self._conda_scan_status.setText(
                f"已扫描系统环境：发现 {names} 含本地引擎依赖（下方各引擎卡片已更新状态）。"
            )
        elif self._conda_envs:
            self._conda_scan_status.setText(
                "已扫描系统环境：未发现含本地引擎依赖的 conda 环境。可用下方命令安装。"
            )
        else:
            self._conda_scan_status.setText(
                "未发现 conda 环境。如需本地引擎，请先安装 Anaconda/Miniconda。"
            )
        # 用最新 conda 结果重建引擎卡片
        hw = hardware.detect()
        has_cuda = bool(hw.get("has_cuda"))
        layout = self._engine_cards_container._cards_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for info in all_local_engines():
            self._engine_cards_container.add_card(
                self._build_engine_install_card(info, has_cuda, self._conda_envs)
            )

    def _build_engine_install_card(self, info, has_cuda: bool,
                                   conda_envs: list | None = None) -> SettingCard:
        """构造单个引擎卡片：三态融合显示。

        三态优先级：
          1. exe 内部就绪（find_spec 全有）→ ✅ 已就绪，可直接运行
          2. conda 环境就绪（find_conda_env_for_engine 命中）→ ✅ conda 环境「X」已含，用 run.bat 启动可运行
          3. 都没有 → ⚠️ 未安装，显示 pip 安装命令
        """
        conda_envs = conda_envs or []
        exe_ready, missing = check_engine_deps(info.engine_type)
        conda_env = find_conda_env_for_engine(info.engine_type, conda_envs)

        # ---- 决定状态文案 + 命令 ----
        if exe_ready:
            status = "✅ 已就绪（可直接运行）"
            status_color = "#4caf50"
            detail = f"{info.description}<br><span style='color:#888;font-size:11px'>体积：{info.approx_size}</span>"
            cmd = recommended_install_command(info.engine_type, has_cuda)
            cmd_label = "📋 复制安装命令"
        elif conda_env is not None:
            cuda_part = f"+ CUDA（{conda_env.gpu_name}）" if conda_env.has_cuda else "（CPU 推理）"
            status = f"✅ conda 环境「{conda_env.name}」已含依赖 {cuda_part}"
            status_color = "#4caf50"
            detail = (
                f"{info.description}<br>"
                f"<span style='color:#888;font-size:11px'>"
                f"Python {conda_env.python_version or '?'} · 此引擎依赖已在 conda 环境「{conda_env.name}」就绪。<br>"
                f"exe 无法直接调用外部 conda 的库，请用以下命令启动开发版（含本地引擎 + GPU）：</span>"
            )
            cmd = f"conda activate {conda_env.name}\npython -m subtitle"
            cmd_label = "📋 复制启动命令"
        else:
            missing_str = "、".join(missing) if missing else "依赖"
            status = f"⚠️ 未安装（缺少 {missing_str}）"
            status_color = "#d94c4c"
            detail = f"{info.description}<br><span style='color:#888;font-size:11px'>体积：{info.approx_size}</span>"
            cmd = recommended_install_command(info.engine_type, has_cuda)
            cmd_label = "📋 复制安装命令"

        title = f"{info.display_name}　·　<span style='color:{status_color}'>{status}</span>"
        content = detail

        # 命令文本框 + 复制按钮
        cmd_box = QTextEdit()
        cmd_box.setPlainText(cmd)
        cmd_box.setReadOnly(True)
        cmd_box.setFixedHeight(58 if "\n" in cmd else 34)
        cmd_box.setStyleSheet(
            "QTextEdit { background: rgba(128,128,128,0.12);"
            " border: 1px solid rgba(128,128,128,0.3); border-radius: 4px;"
            " font-family: Consolas, 'Courier New', monospace; font-size: 12px; padding: 4px; }"
        )
        copy_btn = QPushButton(cmd_label)
        copy_btn.setCursor(Qt.PointingHandCursor)
        def _copy(_checked=False, _cmd: str = cmd, _btn: QPushButton = copy_btn):
            QApplication.clipboard().setText(_cmd)
            QToolTip.showText(_btn.mapToGlobal(QPoint(0, -28)), "已复制到剪贴板", _btn)
        copy_btn.clicked.connect(_copy)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(cmd_box, 1)
        row.addWidget(copy_btn)
        cmd_w = QWidget()
        cmd_w.setLayout(row)

        card = SettingCard(title, content, cmd_w, vertical=True)
        return card

    # ---------- 标签页2：说话人管理 ----------
    def _build_speaker_tab(self) -> QWidget:
        """说话人显示名管理面板 —— 嵌入 SpeakerNamesEditor，复用 panel 的 SpeakerNameMap 实例。"""
        from .speaker_names_editor import SpeakerNamesEditor
        return SpeakerNamesEditor(self.panel, self)

    # ---------- 标签页3：外观 ----------
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
        self.wsl_shutdown_check = ToggleSwitch()
        g_behavior.add_card(_row("退出时关闭 WSL",
            "勾选后退出程序直接 wsl --shutdown 释放全部显存（会关闭所有 WSL 程序）",
            self.wsl_shutdown_check))
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
        audio = self.cfg.audio
        skin = self.cfg.skin

        self._update_recog_buttons()
        # 双输入源：电脑声音 + 麦克风（默认与面板工具栏开关同步）
        self.sys_enable_toggle.setChecked(audio.system_audio_enabled)
        self.mic_enable_toggle.setChecked(audio.mic_enabled)
        idx = self.mic_combo.findData(audio.mic_device)
        self.mic_combo.setCurrentIndex(max(0, idx))
        # 麦克风字幕颜色：优先匹配预设，否则作为自定义文本
        cidx = self.mic_color_combo.findData(ui.mic_color)
        if cidx >= 0:
            self.mic_color_combo.setCurrentIndex(cidx)
        else:
            self.mic_color_combo.setEditText(ui.mic_color)
        # 两路独立引擎配置（各自读各自的 AsrConfig）
        self.system_engine_card.load_from(self.cfg.asr.system)
        self.mic_engine_card.load_from(self.cfg.asr.mic)
        # 阿里云凭证（全局唯一，从系统保险箱读）
        self.aliyun_akid_edit.setText(credentials.get(credentials.KEY_ALIYUN_AK_ID) or "")
        self.aliyun_aksecret_edit.setText(credentials.get(credentials.KEY_ALIYUN_AK_SECRET) or "")
        self.aliyun_appkey_edit.setText(credentials.get(credentials.KEY_ALIYUN_APPKEY) or "")

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
        self.wsl_shutdown_check.setChecked(getattr(ui, "wsl_shutdown_on_quit", False))
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

        # 侧边栏宽度 / 展开状态（最后应用，依赖 splitter 已布局）
        self._apply_sidebar_state()

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
        # 按标题字符串判断页面（不依赖索引），导航顺序变化也不破坏懒加载逻辑。
        item = self.nav_list.item(idx)
        text = item.text() if item is not None else ""
        # 切换内容区（QListWidget 选中 → QStackedWidget 切页）
        if 0 <= idx < self.stack.count():
            self.stack.setCurrentIndex(idx)
        if text == "文稿":
            self._refresh_transcript()
        # 首次切到「引擎管理」页时触发 conda 环境扫描（懒加载，避免构造时启动后台线程）。
        if text == "引擎管理" and getattr(self, "_conda_scan_pending", False):
            self._conda_scan_pending = False
            self._start_conda_scan()

    # ---------- 可折叠/可调宽侧边栏 ----------
    # 设计要点：宽度由 splitter.setSizes() 唯一控制，绝不给 nav_container 调
    # setFixedWidth/setMinimumWidth/setMaximumWidth（那样会锁死宽度、splitter
    # handle 拖不动，且与 splitter 抢控制权）。收起态用 setSizes 把侧边栏压到
    # 窄条宽度，剩余空间全部分给内容区——内容区自然左移延伸。
    def _toggle_sidebar(self):
        """展开 ↔ 收起侧边栏。收起后只剩窄条 + 展开箭头。"""
        self._sidebar_collapsed = not self._sidebar_collapsed
        self._apply_sidebar_geometry()
        self.cfg.ui.settings_sidebar_collapsed = self._sidebar_collapsed

    def _apply_sidebar_geometry(self):
        """根据当前 _sidebar_collapsed / _sidebar_width 应用侧边栏尺寸。

        通过 splitter.setSizes 重分配：侧边栏取目标宽度，剩余全部分给内容区，
        这样收起时内容区会自动左移延伸填满。展开/收起切换 handle 是否可拖。
        """
        total = self.splitter.width() - self.splitter.handleWidth()
        if self._sidebar_collapsed:
            # 收起：隐藏导航文字，侧边栏压到窄条宽度，内容区吃掉让出的空间。
            self.nav_list.setVisible(False)
            self._collapse_btn.setText("▶")
            self._collapse_btn.setToolTip("展开侧边栏")
            target = self._SIDEBAR_COLLAPSED_WIDTH
            # 收起态不允许再拖 handle（避免拖出半开不开的状态）
            self._nav_container.setMinimumWidth(self._SIDEBAR_COLLAPSED_WIDTH)
            self._nav_container.setMaximumWidth(self._SIDEBAR_COLLAPSED_WIDTH)
        else:
            self.nav_list.setVisible(True)
            self._collapse_btn.setText("◀")
            self._collapse_btn.setToolTip("收起侧边栏")
            target = max(self._SIDEBAR_MIN_WIDTH,
                         min(self._sidebar_width, self._SIDEBAR_MAX_WIDTH))
            # 展开态：解除 fixed 约束，仅保留宽松上下限，由 splitter sizes 控宽（可拖拽）。
            # 注意：minimum/maximum 必须给 splitter 留出活动范围，否则 handle 拖不动。
            self._nav_container.setMinimumWidth(self._SIDEBAR_MIN_WIDTH)
            self._nav_container.setMaximumWidth(self._SIDEBAR_MAX_WIDTH)
        # 剩余空间全给内容区（total - target，下限 0）
        rest = max(0, total - target)
        self.splitter.setSizes([target, rest])

    def _on_sidebar_resized(self, _pos: int, _index: int):
        """用户拖动 splitter → 记忆新宽度（仅展开态）。"""
        if self._sidebar_collapsed:
            return
        new_w = self.splitter.sizes()[0] if self.splitter.sizes() else self._sidebar_width
        new_w = max(self._SIDEBAR_MIN_WIDTH, min(new_w, self._SIDEBAR_MAX_WIDTH))
        self._sidebar_width = new_w
        self.cfg.ui.settings_sidebar_width = new_w

    def _apply_sidebar_state(self):
        """从 cfg 恢复侧边栏宽度和展开/收起状态（_load_current_state 末尾调用）。"""
        self._sidebar_width = max(
            self._SIDEBAR_MIN_WIDTH,
            min(int(self.cfg.ui.settings_sidebar_width), self._SIDEBAR_MAX_WIDTH),
        )
        self._sidebar_collapsed = bool(self.cfg.ui.settings_sidebar_collapsed)
        self._apply_sidebar_geometry()

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
        if theme.name not in BUILTIN_THEMES:
            if not self._theme_mgr.persist_custom_theme(theme):
                QMessageBox.warning(self, "保存失败", f"主题“{theme.name}”的颜色未能写入磁盘")

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
        theme = self._theme_mgr.current
        geometry = theme.geometry
        geometry.border_radius = self.radius_spin.value()
        geometry.padding_top = self.pad_top_spin.value()
        geometry.padding_bottom = self.pad_bottom_spin.value()
        geometry.padding_left = self.pad_left_spin.value()
        geometry.padding_right = self.pad_right_spin.value()
        geometry.line_spacing = self.line_spacing_spin.value()
        self.panel.set_border_radius(self.radius_spin.value())
        self.panel.set_padding(
            self.pad_top_spin.value(), self.pad_bottom_spin.value(),
            self.pad_left_spin.value(), self.pad_right_spin.value(),
        )
        self.panel.set_line_spacing(self.line_spacing_spin.value())
        if theme.name not in BUILTIN_THEMES:
            if not self._theme_mgr.persist_custom_theme(theme):
                QMessageBox.warning(self, "保存失败", f"主题“{theme.name}”的几何参数未能写入磁盘")

    def _on_open_skin_editor(self):
        """打开皮肤编辑器（由 app 层处理）。"""
        self.skin_editor_requested.emit()
        self.accept()

    def _on_apply(self):
        p = self.panel
        audio = self.cfg.audio
        ui = self.cfg.ui
        # 双输入源开关与设备
        audio.system_audio_enabled = self.sys_enable_toggle.isChecked()
        audio.mic_enabled = self.mic_enable_toggle.isChecked()
        audio.mic_device = self.mic_combo.currentData()
        ui.mic_color = self.mic_color_combo.currentData() or self.mic_color_combo.currentText() or "#5aa9ff"
        # 同步面板工具栏开关（让下次开始识别时两路状态一致）
        p.set_source_states(audio.system_audio_enabled, audio.mic_enabled)
        # 两路独立引擎配置（各自写各自的 AsrConfig）
        self.system_engine_card.apply_to(self.cfg.asr.system)
        self.mic_engine_card.apply_to(self.cfg.asr.mic)
        # 阿里云 AccessKey ID / Secret / AppKey → 写到系统保险箱（不进 config.yaml）
        # 凭证全局唯一，两路共用一套
        credentials.set_aliyun(
            ak_id=self.aliyun_akid_edit.text().strip(),
            ak_secret=self.aliyun_aksecret_edit.text().strip(),
            appkey=self.aliyun_appkey_edit.text().strip(),
        )

        # 外观
        theme_name = self.theme_combo.currentData()
        editing_current_theme = theme_name == self._theme_mgr.current.name
        if editing_current_theme:
            colors = self._theme_mgr.current.colors
            for key, button in self.color_buttons.items():
                setattr(colors, key, button.get_color())
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
        if editing_current_theme and theme_name not in BUILTIN_THEMES:
            theme = self._theme_mgr.current
            theme.geometry.border_radius = self.radius_spin.value()
            theme.geometry.padding_top = self.pad_top_spin.value()
            theme.geometry.padding_bottom = self.pad_bottom_spin.value()
            theme.geometry.padding_left = self.pad_left_spin.value()
            theme.geometry.padding_right = self.pad_right_spin.value()
            theme.geometry.line_spacing = self.line_spacing_spin.value()
            theme.geometry.font_family = self.font_combo.currentFont().family()
            theme.geometry.font_size = self.font_size_spin.value()
            theme.opacity = self.opacity_spin.value() / 100.0
            self._theme_mgr.persist_custom_theme(theme)

        # 行为
        p.set_window_size(self.win_w_spin.value(), self.win_h_spin.value())
        p.set_pin(self.topmost_check.isChecked())
        p.set_lock_scroll(self.lock_scroll_check.isChecked())
        p.set_close_action(self.close_combo.currentData())
        self.cfg.ui.wsl_shutdown_on_quit = self.wsl_shutdown_check.isChecked()
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
