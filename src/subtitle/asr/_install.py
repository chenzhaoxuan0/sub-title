"""本地引擎安装信息数据层 —— 引擎依赖清单 + 平台/硬件感知的安装命令。

供「设置 → 引擎管理」标签页和 factory 的依赖缺失提示共用，避免安装指令
散落在多处（改一处忘改另一处）。

设计原则：
  - 纯数据 + 纯查询，不执行任何 pip、不真 import 重量级依赖。
  - 用 importlib.util.find_spec 探测「装没装」（与 settings_dialog 现有
    faster_whisper 探测一致），开销极低。
  - 安装命令按 (引擎, 平台, 有无CUDA) 三维度选最合适的。
"""
from __future__ import annotations

import importlib.util
import platform
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EngineInstallInfo:
    """单个本地引擎的安装元信息。aliyun（纯 API）不需要此结构。"""

    engine_type: str               # funasr / sensevoice / funasr_nano / qwen3_asr / faster_whisper
    display_name: str              # 卡片显示名
    description: str               # 一句话用途
    deps: tuple[str, ...]          # 必需依赖（find_spec 逐个查），如 ("funasr", "torch")
    # 安装命令字典：键 = 平台场景，值 = 终端要执行的命令（可能多行）。
    # 键约定：windows_gpu / windows_cpu / macos / linux。
    commands: dict[str, str] = field(default_factory=dict)
    approx_size: str = ""          # 安装后大致占用的磁盘空间提示


# ============================================================
# 各引擎安装信息（命令与 README / scripts/install_*.bat 保持一致）
# ============================================================
_ENGINES: dict[str, EngineInstallInfo] = {
    "funasr": EngineInstallInfo(
        engine_type="funasr",
        display_name="FunASR Paraformer",
        description="流式中文字幕，低延迟，GPU 推荐（VRAM≥4GB）。",
        deps=("funasr", "torch"),
        commands={
            "windows_gpu": "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\npip install funasr",
            "windows_cpu": "pip install torch torchaudio\npip install funasr",
            "macos": "pip install torch torchaudio\npip install funasr",
            "linux": "pip install torch torchaudio\npip install funasr",
        },
        approx_size="torch ~200MB(CPU) / ~2.5GB(GPU) + funasr ~100MB + 首次模型下载",
    ),
    "sensevoice": EngineInstallInfo(
        engine_type="sensevoice",
        display_name="SenseVoice Small",
        description="段式中文字幕，CPU 可跑，自带标点。跨平台兜底默认，Mac 友好。",
        deps=("funasr", "torch"),
        commands={
            "windows_gpu": "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\npip install funasr",
            "windows_cpu": "pip install torch torchaudio\npip install funasr",
            "macos": "pip install torch torchaudio\npip install funasr",
            "linux": "pip install torch torchaudio\npip install funasr",
        },
        approx_size="torch ~200MB(CPU) + funasr ~100MB + 首次模型下载 ~254MB",
    ),
    "funasr_nano": EngineInstallInfo(
        engine_type="funasr_nano",
        display_name="Fun-ASR-Nano",
        description="新一代中文/方言/歌词识别，段式，GPU 推荐。",
        deps=("funasr", "torch"),
        commands={
            "windows_gpu": "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\npip install funasr",
            "windows_cpu": "pip install torch torchaudio\npip install funasr",
            "macos": "pip install torch torchaudio\npip install funasr",
            "linux": "pip install torch torchaudio\npip install funasr",
        },
        approx_size="torch ~200MB(CPU) + funasr ~100MB + 首次模型下载",
    ),
    "qwen3_asr": EngineInstallInfo(
        engine_type="qwen3_asr",
        display_name="Qwen3-ASR",
        description="多语种/歌曲识别，段式；4bit 量化仅 CUDA。依赖较重。",
        deps=("qwen_asr", "torch"),
        commands={
            "windows_gpu": "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\npip install qwen-asr",
            "windows_cpu": "pip install torch torchaudio\npip install qwen-asr",
            "macos": "pip install torch torchaudio\npip install qwen-asr",
            "linux": "pip install torch torchaudio\npip install qwen-asr",
        },
        approx_size="torch ~200MB(CPU) + qwen-asr + 首次模型下载",
    ),
    "faster_whisper": EngineInstallInfo(
        engine_type="faster_whisper",
        display_name="faster-whisper",
        description="多语言(99种)+翻译，CTranslate2 后端，不依赖 torch。兼容模式。",
        deps=("faster_whisper",),
        commands={
            "windows_gpu": "pip install faster-whisper",
            "windows_cpu": "pip install faster-whisper",
            "macos": "pip install faster-whisper",
            "linux": "pip install faster-whisper",
        },
        approx_size="faster-whisper + ctranslate2 ~100MB + 首次模型下载",
    ),
}


def get_engine_install_info(engine_type: str) -> EngineInstallInfo | None:
    """取引擎安装信息。aliyun / 未知引擎返回 None（无需安装）。"""
    return _ENGINES.get(engine_type)


def all_local_engines() -> list[EngineInstallInfo]:
    """所有需要本地依赖的引擎（用于引擎管理页遍历渲染卡片）。"""
    return list(_ENGINES.values())


def _dep_installed(dep: str) -> bool:
    """单个依赖是否已装。用 find_spec 不真 import（避免拖入重量级模块）。"""
    return importlib.util.find_spec(dep) is not None


def check_engine_deps(engine_type: str) -> tuple[bool, list[str]]:
    """检查引擎依赖是否齐全。

    返回 (全部就绪, 缺失依赖名列表)。未知引擎 / aliyun 返回 (True, [])。
    注意：funasr 装了但 torch 没装时（用户 --no-deps 装的）会正确报 torch 缺失，
    这是 factory 阶段预检的关键——避免 load() 时才崩成 traceback。
    """
    info = _ENGINES.get(engine_type)
    if info is None:
        return True, []
    missing = [d for d in info.deps if not _dep_installed(d)]
    return (len(missing) == 0), missing


def _platform_key() -> str:
    """当前平台对应的命令字典键。"""
    system = platform.system()
    if system == "Windows":
        return "windows_cpu"   # has_cuda 由调用方决定是否升级到 windows_gpu
    if system == "Darwin":
        return "macos"
    return "linux"


def recommended_install_command(engine_type: str, has_cuda: bool = False) -> str:
    """返回当前平台+硬件最合适的安装命令。

    Windows + CUDA → windows_gpu（从 PyTorch CUDA 索引装 GPU 轮）；
    其余（Mac / Linux / Windows 无 N 卡）→ CPU 轮（Mac 上 CPU torch 含 MPS 后端）。
    未知引擎返回空串。
    """
    info = _ENGINES.get(engine_type)
    if info is None:
        return ""
    key = _platform_key()
    if key == "windows_cpu" and has_cuda:
        key = "windows_gpu"
    return info.commands.get(key, info.commands.get("linux", ""))


def missing_dep_hint(engine_type: str, has_cuda: bool = False) -> str | None:
    """依赖缺失时的中文提示（供 factory._missing_dep 复用，保持文案一致）。

    依赖齐全返回 None。提示里含缺失依赖名 + 平台对应的安装命令。
    """
    ready, missing = check_engine_deps(engine_type)
    if ready or not missing:
        return None
    info = _ENGINES.get(engine_type)
    name = info.display_name if info else engine_type
    cmd = recommended_install_command(engine_type, has_cuda)
    deps_str = "、".join(missing)
    return f"{name} 依赖缺失：{deps_str}。请在终端执行：\n{cmd}"
