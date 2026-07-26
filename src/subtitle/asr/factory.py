"""引擎工厂 —— 根据 config.asr.engine_type 创建对应引擎。

新增引擎在这里注册即可，pipeline/app 不用改。
"""
from __future__ import annotations

from .base import AsrEngine, OnResult


def create_engine(cfg, on_result: OnResult) -> AsrEngine:
    """根据 cfg.asr.engine_type 创建引擎实例。"""
    engine_type = getattr(cfg.asr, "engine_type", "funasr")

    if engine_type == "funasr":
        from .funasr_engine import FunAsrEngine
        return FunAsrEngine(cfg.asr, on_result)

    if engine_type == "sensevoice":
        from .sensevoice_engine import SenseVoiceEngine
        return SenseVoiceEngine(cfg.asr, on_result)

    if engine_type == "aliyun":
        from .aliyun_engine import AliyunEngine
        return AliyunEngine(cfg.asr, on_result)

    if engine_type == "faster_whisper":
        try:
            from .faster_whisper_engine import FasterWhisperEngine
        except ImportError as e:
            raise ImportError(
                "faster-whisper 未安装。多语言/翻译引擎需要它，安装：pip install faster-whisper"
            ) from e
        return FasterWhisperEngine(cfg.asr, on_result)

    raise ValueError(
        f"未知引擎类型: {engine_type}（支持: sensevoice/funasr/faster_whisper/aliyun）"
    )
