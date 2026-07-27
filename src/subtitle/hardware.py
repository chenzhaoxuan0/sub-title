"""跨平台硬件检测 + ASR 引擎/模型推荐。

零新依赖：torch（随 funasr 装，检测 CUDA/MPS）+ psutil（随 funasr 装，检测 CPU/RAM）。
torch 不可用时降级为纯 CPU 推断（has_cuda/is_apple_silicon 按平台常理推断）。

推荐依据（来自 FunASR 官方定位 + 社区实测）：
  - 单流实时场景 GPU 优势不明显（官方 Issue #2788），CPU 即可实时（8 线程 ~20x 实时）
  - paraformer-zh-streaming 稳态 ~3GB VRAM，故 VRAM>=4GB 才推荐 GPU 路径
  - SenseVoice CPU 是跨平台兜底默认：Mac 友好、自带中文标点、官方 CPU 实时方案
"""
from __future__ import annotations

import os
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

    # ---- 兜底：torch 不可用时（如纯 API exe 没打包 torch），用系统命令查 GPU ----
    # 否则 exe 模式下即使机器有 N 卡也会显示"无 CUDA"——torch 缺失不该导致漏报真实硬件。
    # 仅在 torch 没查出 CUDA 时才走系统命令（避免和 torch 结果冲突）。
    if not info["has_cuda"]:
        _detect_gpu_via_system(info)
    _cached_info = info
    return info


def _detect_gpu_via_system(info: dict[str, Any]) -> None:
    """torch 不可用时的 GPU 兜底检测：用 nvidia-smi / 系统命令查 NVIDIA GPU。

    只查 NVIDIA CUDA GPU（与 torch.cuda 同语义）。查到则填 has_cuda/gpu_name/cuda_vram_gb。
    非 N 卡（Intel/AMD 集显）不在此检测范围——它们本来也不能跑 CUDA 模型。
    跨平台：nvidia-smi 在 Windows/Linux 都有（随驱动安装）；macOS 无 N 卡场景。
    """
    import shutil
    import subprocess

    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        # nvidia-smi 不在 PATH（常见于某些 Windows 安装），尝试常见绝对路径
        import sys as _sys
        candidates = []
        if _sys.platform == "win32":
            for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
                base = os.environ.get(env_var)
                if base:
                    candidates.append(os.path.join(
                        base, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"))
        for c in candidates:
            if os.path.isfile(c):
                nvidia_smi = c
                break
    if nvidia_smi is None:
        return

    try:
        # 查 GPU 名 + 显存（字节）。--format=csv,noheader,nounits 给纯数值便于解析。
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return
        # 输出形如 "NVIDIA GeForce RTX 4060 Ti, 16384"
        first_line = result.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first_line.split(",")]
        if len(parts) >= 2:
            info["has_cuda"] = True
            info["gpu_name"] = parts[0]
            try:
                # memory.total 单位是 MiB，转 GB
                vram_mib = float(parts[1])
                info["cuda_vram_gb"] = round(vram_mib / 1024, 1)
            except (ValueError, IndexError):
                pass
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


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
