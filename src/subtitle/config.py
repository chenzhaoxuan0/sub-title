"""配置加载。从 config.yaml 读取，提供默认值兜底。

**config.yaml 不再存 API key**（aliyun_access_key_id / secret / appkey），
改由 `credentials.py` 写入系统 keyring（Windows Credential Manager / macOS Keychain / Linux Secret Service）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # yaml 未装时给个占位，setup 后会有
    yaml = None

# 旧版位置（项目根 / CWD）—— 仅供迁移逻辑识别，新代码用 default_config_path()。
# 打包后这个路径在只读资源根下（PyInstaller _MEIPASS），文件不存在，迁移自然跳过。
from .paths import resource_dir as _resource_dir, default_font_family as _default_font_family
_LEGACY_CONFIG_PATH = _resource_dir() / "config.yaml"


def default_config_path() -> Path:
    """当前 config.yaml 应该在的位置（用户数据目录）。

    打包后这个路径才是合法的；旧版本的"项目根"路径只用于一次性迁移。
    """
    from .paths import config_path
    return config_path()


# 向后兼容：旧代码可能直接引用这个常量。新代码请用 default_config_path()。
DEFAULT_CONFIG_PATH = default_config_path()


@dataclass
class AudioConfig:
    target_sample_rate: int = 16000
    chunk_seconds: float = 0.6
    input_device: Optional[str] = None        # 电脑声音 loopback 设备（None=默认输出）

    # ---- 双输入源 ----
    # 麦克风与电脑声音分别采集、分别识别、在字幕里按来源区分展示（🎤/🔊）。
    # 首启默认只开电脑声音（system_audio_enabled=True），保持老用户体验；
    # 麦克风需用户在工具栏/设置里手动开启。两路独立 engine 实例，双开时显存/内存翻倍。
    system_audio_enabled: bool = True         # 是否启用电脑声音（loopback）
    mic_enabled: bool = False                 # 是否启用麦克风
    mic_device: Optional[str] = None          # 麦克风设备名（None=系统默认麦克风）


@dataclass
class AsrConfig:
    # 引擎选择：sensevoice（默认，CPU 友好+自带标点）/ funasr（Paraformer 流式）/
    #           funasr_nano（新一代中文/歌词）/ qwen3_asr（新一代多语种）/
    #           faster_whisper（兼容模式，静音时可能幻觉）/ aliyun（阿里云 API）
    # 默认 sensevoice：官方单流 CPU 即可实时，Mac/Win 通用；首次启动会按硬件重写此值。
    engine_type: str = "sensevoice"

    # ---- FunASR（paraformer-zh-streaming）----
    model: str = "paraformer-zh-streaming"
    device: str = "cuda"
    chunk_size: list = field(default_factory=lambda: [0, 10, 5])
    encoder_chunk_look_back: int = 4
    decoder_chunk_look_back: int = 1
    disable_update: bool = True
    punc_model: str = "ct-punc"
    # 流式标点后处理（可选）。paraformer-zh-streaming 流式输出本身不带标点，
    # 开启后用 realtime punc 模型给裸文本增量补标点，让默认引擎也能按句分行。
    # 首次启动会下载模型（~300-700MB）。改动需停止再开始识别才生效。
    funasr_punc_enabled: bool = False
    funasr_punc_model: str = "iic/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727"
    funasr_punc_device: str = "cpu"   # CT-Transformer 轻量，CPU 即可，避免和 ASR 抢 GPU

    # ---- Fun-ASR-Nano（2025，中文/方言/歌词，段式）----
    funasr_nano_model: str = "FunAudioLLM/Fun-ASR-Nano-2512"
    funasr_nano_device: str = "cuda"
    funasr_nano_language: str = "中文"
    funasr_nano_segment_seconds: float = 2.0

    # ---- Qwen3-ASR（2026，多语种/歌曲，段式；原生流式需 vLLM）----
    qwen3_asr_model: str = "Qwen/Qwen3-ASR-0.6B"
    qwen3_asr_device: str = "cuda"
    # "none" = 原始权重；"4bit" = CUDA 上使用 bitsandbytes 运行时量化。
    qwen3_asr_quantization: str = "none"
    qwen3_asr_language: str = "Chinese"
    qwen3_asr_segment_seconds: float = 2.0

    # ---- 说话人区分（spk_id 输出）----
    # 开启后该 source（system / mic）的引擎必须为 funasr（其他引擎不支持流式 spk_id）。
    # 开启时：model 加载时自动注入 spk_model="cam++"，engine 回调追加 spk_id 字段。
    # 显示名由 ui.speaker_names_editor 管理；spk_id 仅在当前会话内稳定，重启后重置。
    # 两路（system + mic）可独立开关；双开时 2x funasr 实例、2x GPU/内存。
    enable_speaker_diarization: bool = False

    # ---- SenseVoice（段式，CPU 可跑）----
    sensevoice_model: str = "iic/SenseVoiceSmall"
    sensevoice_device: str = "cpu"          # cpu / cuda（Mac 用 cpu）
    sensevoice_segment_seconds: float = 2.0  # 攒段时长，越小延迟越低但易切词

    # ---- 阿里云 NLS API ----
    # 注意：AccessKey ID / Secret / AppKey 不在这里 —— 它们由 credentials 模块
    # 存在系统 keyring 里（Windows Credential Manager / macOS Keychain / Linux libsecret）。
    # region 不是密钥，可以放这里。
    aliyun_region: str = "cn-shanghai"

    # ---- faster-whisper（CTranslate2 后端，段式伪流式，不依赖 torch）----
    # 价值：多语言（99 语言）+ 翻译 + 轻量分发。中文准确度弱于 FunASR 系列。
    # 模型首用从 ModelScope 下载（large-v3 约 3GB）。
    faster_whisper_model: str = "large-v3"
    faster_whisper_device: str = "auto"             # cpu / cuda / auto（auto 自动回退，不崩）
    faster_whisper_compute_type: str = "auto"       # auto=GPU 用 float16 / CPU 用 int8
    faster_whisper_language: str = "zh"             # "auto" 或 None = 自动检测
    faster_whisper_beam_size: int = 1               # 1 降延迟
    faster_whisper_segment_seconds: float = 2.0     # 复用 SenseVoice 的段式策略
    faster_whisper_silence_threshold: float = 0.01
    faster_whisper_min_speech_seconds: float = 0.1  # 静音段不送入模型，避免幻觉字幕
    faster_whisper_vad_filter: bool = True          # 先过滤无语音片段，抑制静音幻觉


@dataclass
class UiConfig:
    # CJK 字幕默认字体按平台选原生字体（Mac 用苹方，避免找不到微软雅黑）。
    font_family: str = field(default_factory=_default_font_family)
    font_size: int = 22
    window_opacity: float = 0.88
    max_chars: int = 20000
    theme: str = "Dark"            # 主题名称（对应 ThemeManager 中的 key）
    always_on_top: bool = True
    # 窗口位置/尺寸记忆
    win_x: Optional[int] = None
    win_y: Optional[int] = None
    win_w: int = 720
    win_h: int = 140
    # 行为
    close_action: str = "ask"
    toolbar_hide_delay_ms: int = 800
    lock_scroll_to_bottom: bool = False
    # 自动分行：识别到句末标点（。！？!?…）或引擎句子边界（is_final）时换行。
    # 无标点且无边界的引擎（如 FunASR 未开流式标点）不会强行分行，保持连续文本。
    line_break_enabled: bool = True
    min_win_w: int = 30
    min_win_h: int = 30
    # 自定义几何（覆盖主题默认值）
    border_radius: Optional[int] = None
    padding_top: Optional[int] = None
    padding_bottom: Optional[int] = None
    padding_left: Optional[int] = None
    padding_right: Optional[int] = None
    line_spacing: Optional[float] = None
    # 麦克风字幕颜色（电脑声音用主题 subtitle_text）。走 UiConfig 而非 ThemeColors，
    # 避免改动主题 JSON 结构与现有主题迁移。
    mic_color: str = "#5aa9ff"


@dataclass
class SkinConfig:
    """桌宠/贴图皮肤配置。"""
    enabled: bool = False                    # 是否启用贴图皮肤
    active_skin: str = ""                    # 当前使用的皮肤名称
    skins_dir: str = "skins"                 # 皮肤目录；相对路径基于用户数据目录
    editor_grid_snap: bool = True            # 编辑器网格吸附
    editor_grid_size: int = 8                # 网格大小 (px)
    editor_show_guides: bool = True          # 显示辅助线
    animation_fps: int = 30                  # 动画帧率
    animation_loop: bool = True              # 动画循环播放


@dataclass
class AsrProfiles:
    """双输入源的引擎配置容器：电脑声音与麦克风各自独立的 AsrConfig。

    两路可分别选择不同引擎及参数（如 system=本地 SenseVoice，mic=阿里云 API），
    用户可按 PC 算力灵活调配。阿里云凭证（AccessKey/AppKey）存系统 keyring，
    是全局唯一的，两路共用一套——这里只承载引擎类型与参数，不含凭证。
    """
    system: AsrConfig = field(default_factory=AsrConfig)   # 🔊 电脑声音
    mic: AsrConfig = field(default_factory=AsrConfig)      # 🎤 麦克风

    def for_source(self, source: str) -> AsrConfig:
        """按来源标签取对应的 AsrConfig（factory 用此分发配置）。"""
        return self.mic if source == "mic" else self.system


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: AsrProfiles = field(default_factory=AsrProfiles)
    ui: UiConfig = field(default_factory=UiConfig)
    skin: SkinConfig = field(default_factory=SkinConfig)


def _build_asr_config(d: dict) -> AsrConfig:
    """从一个 dict 构建单个 AsrConfig（字段过滤，忽略未知键）。"""
    return AsrConfig(**{k: v for k, v in d.items()
                       if k in AsrConfig.__dataclass_fields__})


def _build(d: dict[str, Any]) -> Config:
    # asr 段支持两种格式：
    #   新格式（嵌套）：asr: {system: {...}, mic: {...}}  —— 两路独立引擎配置
    #   老格式（平铺）：asr: {engine_type: ..., sensevoice_device: ...}  —— 单一全局配置
    # 老格式自动迁移：整体作为 system 配置保留，mic 用默认 AsrConfig。
    asr_raw = d.get("asr", {})
    if isinstance(asr_raw, dict) and ("system" in asr_raw or "mic" in asr_raw):
        sys_d = asr_raw.get("system") or {}
        mic_d = asr_raw.get("mic") or {}
        asr_profiles = AsrProfiles(
            system=_build_asr_config(sys_d if isinstance(sys_d, dict) else {}),
            mic=_build_asr_config(mic_d if isinstance(mic_d, dict) else {}),
        )
    elif isinstance(asr_raw, dict) and asr_raw:
        # 老格式平铺迁移：整体 → system，mic 默认
        asr_profiles = AsrProfiles(system=_build_asr_config(asr_raw))
    else:
        asr_profiles = AsrProfiles()

    return Config(
        audio=AudioConfig(**{k: v for k, v in d.get("audio", {}).items()
                             if k in AudioConfig.__dataclass_fields__}),
        asr=asr_profiles,
        ui=UiConfig(**{k: v for k, v in d.get("ui", {}).items()
                       if k in UiConfig.__dataclass_fields__}),
        skin=SkinConfig(**{k: v for k, v in d.get("skin", {}).items()
                           if k in SkinConfig.__dataclass_fields__}),
    )


def load_config(path: Optional[Path] = None) -> Config:
    path = path or default_config_path()
    if path.exists() and yaml is not None:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return _build(data)
    return Config()


# 暴露老路径名给迁移逻辑用（避免 app.py 直接拼路径）
LEGACY_CONFIG_PATH = _LEGACY_CONFIG_PATH
