"""引擎工厂 —— 根据 config.asr.engine_type 创建对应引擎。

新增引擎在这里注册即可，pipeline/app 不用改。
"""
from __future__ import annotations

from .base import AsrEngine, OnResult


def create_engine(cfg, on_result: OnResult, source: str = "system") -> AsrEngine:
    """根据 source 取对应的 AsrConfig（电脑声音/麦克风可独立配置引擎与参数），
    再按 engine_type 创建引擎实例。source 同时透传给引擎，用于回调时标记来源。"""
    asr_cfg = cfg.asr.for_source(source)
    engine_type = getattr(asr_cfg, "engine_type", "funasr")

    if engine_type == "funasr":
        from .funasr_engine import FunAsrEngine
        return FunAsrEngine(asr_cfg, on_result, source=source)

    if engine_type == "sensevoice":
        from .sensevoice_engine import SenseVoiceEngine
        return SenseVoiceEngine(asr_cfg, on_result, source=source)

    if engine_type == "aliyun":
        from .aliyun_engine import AliyunEngine
        return AliyunEngine(asr_cfg, on_result, source=source)

    if engine_type == "faster_whisper":
        try:
            from .faster_whisper_engine import FasterWhisperEngine
        except ImportError as e:
            raise ImportError(
                "faster-whisper 未安装。多语言/翻译引擎需要它，安装：pip install faster-whisper"
            ) from e
        return FasterWhisperEngine(asr_cfg, on_result, source=source)

    raise ValueError(
        f"未知引擎类型: {engine_type}（支持: sensevoice/funasr/faster_whisper/aliyun）"
    )
