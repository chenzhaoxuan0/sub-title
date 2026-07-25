"""重采样 / 声道转换：把任意 PCM 归一到 16kHz / mono / float32。

FunASR 流式模型硬性要求 16k mono float32，否则结果错乱。
"""
from __future__ import annotations

import numpy as np


def to_mono(samples: np.ndarray) -> np.ndarray:
    """多声道 → 单声道（取各声道均值）。已是单声道则原样返回。"""
    if samples.ndim == 1:
        return samples.astype(np.float32, copy=False)
    # (frames, channels) → (frames,)
    return samples.mean(axis=1).astype(np.float32, copy=False)


def resample(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """重采样。优先 librosa（质量好），回退 scipy 线性插值。"""
    if src_sr == dst_sr:
        return samples.astype(np.float32, copy=False)
    try:
        import librosa
        return librosa.resample(
            np.asarray(samples, dtype=np.float32),
            orig_sr=src_sr, target_sr=dst_sr
        )
    except ImportError:
        # scipy 线性插值兜底（精度略低但够用）
        from scipy.signal import resample_poly
        import math
        g = math.gcd(int(src_sr), int(dst_sr))
        up = int(dst_sr) // g
        down = int(src_sr) // g
        return resample_poly(np.asarray(samples, dtype=np.float32), up, down).astype(np.float32)


def normalize_pcm(samples: np.ndarray, src_sr: int, dst_sr: int = 16000) -> np.ndarray:
    """一步到位：转单声道 + 重采样到 dst_sr + float32。供采集线程调用。"""
    mono = to_mono(samples)
    return resample(mono, src_sr, dst_sr)
