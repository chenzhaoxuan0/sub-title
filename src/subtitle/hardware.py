"""跨平台硬件检测 + ASR 引擎/模型推荐。

零新依赖：torch（随 funasr 装，检测 CUDA/MPS）+ psutil（随 funasr 装，检测 CPU/RAM）。
torch 不可用时降级为纯 CPU 推断（has_cuda/is_apple_silicon 按平台常理推断）。

推荐依据（来自 FunASR 官方定位 + 社区实测）：
  - 单流实时场景 GPU 优势不明显（官方 Issue #2788），CPU 即可实时（8 线程 ~20x 实时）
  - paraformer-zh-streaming 稳态 ~3GB VRAM，故 VRAM>=4GB 才推荐 GPU 路径
  - SenseVoice CPU 是跨平台兜底默认：Mac 友好、自带中文标点、官方 CPU 实时方案
"""
from __future__ import annotations

import platform
from typing import Any

# psutil 是 funasr 的传递依赖；若极端情况没装，降级用 os.cpu_count
try:
    import psutil
except ImportError:
    psutil = None

# detect() 结果缓存：硬件信息在程序生命周期内不变，缓存后多处调用只探测一次
_cached_info: Any = None


def detect(force: bool = False) -> dict[str, Any]:
    """检测本机硬件，返回信息字典。

    返回字段：
      os, cpu_cores, ram_gb, has_cuda, cuda_vram_gb, gpu_name,
      is_apple_silicon, has_mps
    torch 不可用时 has_cuda/has_mps 均为 False，但 is_apple_silicon 仍按平台判断。

    结果在程序生命周期内缓存（硬件不会在运行时变化）。多次调用——如 app 启动
    推荐 + 设置页每张 EngineConfigCard 各调一次——只探测一次，避免反复 import
    torch + 查询 GPU 拖慢设置页打开。force=True 可强制重新探测（一般用不到）。
    """
    global _cached_info
    if _cached_info is not None and not force:
        return _cached_info
    info: dict[str, Any] = {
        "os": platform.system(),
        "cpu_cores": (psutil.cpu_count(logical=True) if psutil else
                      __import__("os").cpu_count() or 0),
        "ram_gb": round(psutil.virtual_memory().total / (1024 ** 3), 1) if psutil else 0.0,
        "has_cuda": False,
        "cuda_vram_gb": 0.0,
        "gpu_name": "",
        "is_apple_silicon": (platform.system() == "Darwin"
                             and platform.machine() == "arm64"),
        "has_mps": False,   # Apple Silicon Metal Performance Shaders（torch.backends.mps）
    }
    try:
        import torch
        if torch.cuda.is_available():
            info["has_cuda"] = True
            info["gpu_name"] = torch.cuda.get_device_name(0)
            info["cuda_vram_gb"] = round(
                torch.cuda.get_device_properties(0).total_memory / (1024 ** 3), 1)
        # macOS Apple Silicon：torch CPU 轮自带 MPS 后端，is_available() 可用即 M 系列。
        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        if mps_backend is not None:
            try:
                if mps_backend.is_available():
                    info["has_mps"] = True
                    if not info["gpu_name"]:
                        info["gpu_name"] = "Apple Silicon (MPS)"
            except Exception:
                pass
    except Exception:
        pass  # torch 没装或检测失败，has_cuda/has_mps 保持 False
    _cached_info = info
    return info


def recommend_engine(info: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """根据硬件信息推荐 (engine_type, 配置覆盖字典)。

    逻辑：
      - 有 CUDA 且 VRAM>=4GB → funasr（paraformer-streaming, cuda）：低延迟流式，GPU 有意义
      - 其余（Apple Silicon / 普通 CPU / 弱机器）→ sensevoice（cpu）：跨平台兜底默认
    返回的配置覆盖字典会写进 config.yaml 的 asr 段，字段名对应 AsrConfig。
    """
    if info.get("has_cuda") and info.get("cuda_vram_gb", 0) >= 4:
        return "funasr", {"device": "cuda"}
    # sensevoice CPU 是跨平台兜底：Mac 友好、自带标点、官方 CPU 实时方案
    return "sensevoice", {"sensevoice_device": "cpu"}


def describe_recommendation(info: dict[str, Any]) -> str:
    """Return an actionable local-model recommendation for the settings UI.

    The automatic first-run choice remains latency-first. This text also names
    higher-accuracy options that the detected hardware can reasonably load.
    """
    cores = int(info.get("cpu_cores", 0) or 0)
    ram = float(info.get("ram_gb", 0) or 0)
    vram = float(info.get("cuda_vram_gb", 0) or 0)
    if not info.get("has_cuda"):
        if cores < 4 or (ram and ram < 6):
            return (
                f"检测到 {cores} 线程 / {ram:g}GB 内存且没有 CUDA。建议使用阿里云 API；"
                "本地最低可尝试 SenseVoice CPU，但实时性可能不足。"
            )
        return (
            f"检测到 {cores} 线程 / {ram:g}GB 内存且没有 CUDA。推荐 SenseVoice CPU；"
            "需要更低内存时可选 faster-whisper small + INT8。"
        )
    if vram >= 12:
        return (
            f"检测到 CUDA GPU（{vram:g}GB 显存）。实时默认推荐 FunASR Paraformer；"
            "高精度可选 Qwen3-ASR 1.7B，显存紧张时选择 Qwen3 4bit。"
        )
    if vram >= 8:
        return (
            f"检测到 CUDA GPU（{vram:g}GB 显存）。实时默认推荐 FunASR Paraformer；"
            "中文歌曲/多语种可选 Qwen3-ASR 0.6B 或 Fun-ASR-Nano。"
        )
    if vram >= 4:
        return (
            f"检测到 CUDA GPU（{vram:g}GB 显存）。推荐 FunASR Paraformer 流式；"
            "显存不足以稳定运行 Qwen3/Nano 时请改用 SenseVoice 或 API。"
        )
    return (
        f"检测到 CUDA GPU（{vram:g}GB 显存），但显存偏小。推荐 SenseVoice CPU；"
        "需要更轻量的本地方案可选 faster-whisper small + INT8。"
    )
