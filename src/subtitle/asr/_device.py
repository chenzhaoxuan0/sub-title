"""ASR 引擎设备解析助手 —— 跨平台 CUDA/CPU/MPS 兜底。

为什么需要：config 里 device 默认常为 "cuda"（Windows GPU 机器的合理默认），
但 macOS 没有 CUDA、CPU torch 也没有 CUDA 运行时。引擎直接把 "cuda" 喂给
模型会在 load 阶段硬崩。这里提供统一的"实际可用设备"解析：探测 torch 与
CUDA 可用性，cuda 不可用时按平台降级（Apple Silicon → mps，其余 → cpu），
并打印一行降级提示，避免静默行为漂移。
"""
from __future__ import annotations


def resolve_device(requested: str) -> str:
    """把用户/配置请求的 device 解析为本机真正可用的 device。

    - "cuda" 且本机 CUDA 可用 → "cuda"
    - "cuda" 但不可用（macOS / CPU torch / 无 N 卡）→ Apple Silicon 用 "mps"，否则 "cpu"
    - "mps" 且本机 MPS 可用 → "mps"；不可用 → "cpu"
    - "cpu" 或其他 → 原样返回

    永不抛异常：torch 没装时一律按"非 cuda"处理（降级到 cpu），由上层引擎在真正
    import funasr/torch 时决定是否报"依赖缺失"。
    """
    if requested == "cpu":
        return "cpu"
    try:
        import torch  # type: ignore
    except Exception:
        # torch 缺失：任何 GPU device 都跑不了，降到 cpu。
        # 引擎随后会在 import funasr/torch 时抛友好 ImportError。
        if requested != "cpu":
            print(f"[device] torch 未安装，device {requested!r} 降级为 cpu")
        return "cpu"
    if requested == "cuda":
        if torch.cuda.is_available():
            return "cuda"
        fallback = "mps" if _mps_available(torch) else "cpu"
        print(
            f"[device] CUDA 不可用（torch.cuda.is_available()=False），"
            f"device 'cuda' 降级为 {fallback!r}"
        )
        return fallback
    if requested == "mps":
        if _mps_available(torch):
            return "mps"
        print("[device] MPS 不可用，device 'mps' 降级为 'cpu'")
        return "cpu"
    return requested


def cuda_available() -> bool:
    """torch 已装且 CUDA 可用。torch 没装返回 False（不抛异常）。"""
    try:
        import torch  # type: ignore
    except Exception:
        return False
    return bool(torch.cuda.is_available())


def _mps_available(torch_module) -> bool:
    backend = getattr(getattr(torch_module, "backends", None), "mps", None)
    if backend is None:
        return False
    try:
        return bool(backend.is_available())
    except Exception:
        return False
