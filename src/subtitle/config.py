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

# 旧版位置（项目根 / CWD）—— 仅供迁移逻辑识别，新代码用 default_config_path()
_LEGACY_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


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
    # 注意：AccessKey ID / Secret / AppKey 不在这里 —— 它们由 credentials 模块
    # 存在系统 keyring 里（Windows Credential Manager / macOS Keychain / Linux libsecret）。
    # region 不是密钥，可以放这里。
    aliyun_region: str = "cn-shanghai"


@dataclass
class UiConfig:
    font_family: str = "Microsoft YaHei"
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
    min_win_w: int = 30
    min_win_h: int = 30
    # 自定义几何（覆盖主题默认值）
    border_radius: Optional[int] = None
    padding_top: Optional[int] = None
    padding_bottom: Optional[int] = None
    padding_left: Optional[int] = None
    padding_right: Optional[int] = None
    line_spacing: Optional[float] = None


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
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    skin: SkinConfig = field(default_factory=SkinConfig)


def _build(d: dict[str, Any]) -> Config:
    return Config(
        audio=AudioConfig(**{k: v for k, v in d.get("audio", {}).items()
                             if k in AudioConfig.__dataclass_fields__}),
        asr=AsrConfig(**{k: v for k, v in d.get("asr", {}).items()
                         if k in AsrConfig.__dataclass_fields__}),
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
