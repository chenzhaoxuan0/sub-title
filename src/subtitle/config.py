"""配置加载。从 config.yaml 读取，提供默认值兜底。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # yaml 未装时给个占位，setup 后会有
    yaml = None


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


@dataclass
class AudioConfig:
    target_sample_rate: int = 16000
    chunk_seconds: float = 0.6
    input_device: Optional[str] = None


@dataclass
class AsrConfig:
    # 引擎选择：funasr（本地流式）/ sensevoice（本地段式小模型）/ aliyun（阿里云API）
    engine_type: str = "funasr"

    # ---- FunASR（paraformer-zh-streaming）----
    model: str = "paraformer-zh-streaming"
    device: str = "cuda"
    chunk_size: list = field(default_factory=lambda: [0, 10, 5])
    encoder_chunk_look_back: int = 4
    decoder_chunk_look_back: int = 1
    disable_update: bool = True
    punc_model: str = "ct-punc"

    # ---- SenseVoice（段式，CPU 可跑）----
    sensevoice_model: str = "iic/SenseVoiceSmall"
    sensevoice_device: str = "cpu"          # cpu / cuda（Mac 用 cpu）
    sensevoice_segment_seconds: float = 2.0  # 攒段时长，越小延迟越低但易切词

    # ---- 阿里云 NLS API ----
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_appkey: str = ""
    aliyun_region: str = "cn-shanghai"


@dataclass
class UiConfig:
    font_family: str = "Microsoft YaHei"
    font_size: int = 22
    window_opacity: float = 0.88
    max_chars: int = 20000  # 字幕文本按字符数上限，超出从头清理（与窗口大小无关）
    theme: str = "dark"          # dark=半透明黑底白字 / light=半透明白底黑字
    always_on_top: bool = True   # 打开时默认置顶
    # 窗口位置/尺寸记忆（None = 用默认值）
    win_x: Optional[int] = None
    win_y: Optional[int] = None
    win_w: int = 720
    win_h: int = 140
    # 点 ✕ 时的行为：ask=每次询问 / hide=直接隐藏到托盘 / quit=直接退出
    close_action: str = "ask"
    # 工具栏无操作后自动隐藏的延时（毫秒）
    toolbar_hide_delay_ms: int = 800
    # 锁定滚动到底部：开启后新字幕进来强制跟随，无视用户滚动位置
    lock_scroll_to_bottom: bool = False
    # 窗口最小尺寸（放大下限，避免缩太小没法用）
    min_win_w: int = 480
    min_win_h: int = 120


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    ui: UiConfig = field(default_factory=UiConfig)


def _build(d: dict[str, Any]) -> Config:
    return Config(
        audio=AudioConfig(**d.get("audio", {})),
        asr=AsrConfig(**d.get("asr", {})),
        ui=UiConfig(**d.get("ui", {})),
    )


def load_config(path: Optional[Path] = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if path.exists() and yaml is not None:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return _build(data)
    return Config()
