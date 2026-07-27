"""引擎工厂 —— 根据 config.asr.engine_type 创建对应引擎。

新增引擎在这里注册即可，pipeline/app 不用改。
"""
from __future__ import annotations

from .base import AsrEngine, OnResult


def create_engine(cfg, on_result: OnResult, source: str = "system") -> AsrEngine:
    """根据 source 取对应的 AsrConfig（电脑声音/麦克风可独立配置引擎与参数），
    再按 engine_type 创建引擎实例。source 同时透传给引擎，用于回调时标记来源。

    说话人区分约束：enable_speaker_diarization=True 强制 engine_type="funasr"，
    其他引擎（sensevoice / aliyun / faster_whisper）架构上不支持流式 spk_id。
    不会改写 config，只在本次创建时降级，避免静默配置漂移。
    """
    asr_cfg = cfg.asr.for_source(source)
    engine_type = getattr(asr_cfg, "engine_type", "funasr")
    if getattr(asr_cfg, "enable_speaker_diarization", False) and engine_type != "funasr":
        print(
            f"[factory:{source}] ⚠️ 说话人区分开启但引擎类型={engine_type} 不支持流式 spk_id，"
            f"本 session 降级为 funasr（请在设置里切引擎或关闭说话人区分）"
        )
        engine_type = "funasr"

    if engine_type == "funasr":
        from .funasr_engine import FunAsrEngine
        return FunAsrEngine(asr_cfg, on_result, source=source)

    if engine_type == "sensevoice":
        from .sensevoice_engine import SenseVoiceEngine
        return SenseVoiceEngine(asr_cfg, on_result, source=source)

    if engine_type == "funasr_nano":
        # 流式模式：探测本地 realtime-server，连得上才造流式引擎；否则回退段式。
        # 降级只作用于本次 session（不重写 config），与 diarization 降级模式一致。
        mode = getattr(asr_cfg, "funasr_nano_mode", "segment")
        if mode == "streaming":
            from .funasr_nano_streaming_engine import probe
            host = getattr(asr_cfg, "funasr_nano_streaming_host", "127.0.0.1")
            port = int(getattr(asr_cfg, "funasr_nano_streaming_port", 10095))
            if probe(host, port):
                from .funasr_nano_streaming_engine import FunAsrNanoStreamingEngine
                return FunAsrNanoStreamingEngine(asr_cfg, on_result, source=source)
            print(
                f"[factory:{source}] ⚠️ Nano 流式模式连不上 {host}:{port}"
                f"（请先起 funasr-realtime-server），本次回退段式"
            )
            # 标记运行期降级，供 app.py 在状态栏提示用户（不写 config）
            asr_cfg._nano_streaming_fallback = True
        from .funasr_nano_engine import FunAsrNanoEngine
        return FunAsrNanoEngine(asr_cfg, on_result, source=source)

    if engine_type == "qwen3_asr":
        from .qwen3_asr_engine import Qwen3AsrEngine
        return Qwen3AsrEngine(asr_cfg, on_result, source=source)

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
        "未知引擎类型: " + engine_type
        + "（支持: sensevoice/funasr/funasr_nano/qwen3_asr/faster_whisper/aliyun）"
    )
