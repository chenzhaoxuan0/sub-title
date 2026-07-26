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


def detect() -> dict[str, Any]:
    """检测本机硬件，返回信息字典。

    返回字段：
      os, cpu_cores, ram_gb, has_cuda, cuda_vram_gb, gpu_name, is_apple_silicon
    torch 不可用时 has_cuda=False，但 is_apple_silicon 仍按平台判断。
    """
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
    }
    try:
        import torch
        if torch.cuda.is_available():
            info["has_cuda"] = True
            info["gpu_name"] = torch.cuda.get_device_name(0)
            info["cuda_vram_gb"] = round(
                torch.cuda.get_device_properties(0).total_memory / (1024 ** 3), 1)
    except Exception:
        pass  # torch 没装或检测失败，has_cuda 保持 False
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
