"""引擎工厂 —— 根据 config.asr.engine_type 创建对应引擎。

新增引擎在这里注册即可，pipeline/app 不用改。
"""
from __future__ import annotations

import importlib.util

from .base import AsrEngine, OnResult


def _missing_dep(engine_type: str) -> "str | None":
    """返回引擎缺少依赖时的中文提示；依赖齐全返回 None。

    引擎的真实 import 发生在各自 load() 里（延迟到 worker 线程），但依赖缺失
    若在那时才暴露，用户只看到一条 traceback。这里在工厂阶段用 find_spec 探测
    （不真正 import，开销极低），给出可操作的安装提示。
    """
    if engine_type in {"funasr", "sensevoice", "funasr_nano"}:
        # 三者都基于 funasr。注意：funasr 把 torch 列为 install_requires，但用户可能
        # 手动卸了 torch 或用 --no-deps 装的 funasr——此时 `import funasr` 仍成功
        # （torch 是延迟 import），但 `from funasr import AutoModel` 会在引擎 load 时
        # 抛 ModuleNotFoundError。所以 funasr 和 torch 要分别探测，任一缺失都报。
        if importlib.util.find_spec("funasr") is None:
            return (
                f"{engine_type} 引擎需要 funasr（含 torch 依赖）。请执行：pip install funasr"
            )
        if importlib.util.find_spec("torch") is None:
            return (
                f"{engine_type} 引擎依赖 torch 但当前未安装。"
                "Windows GPU：pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121；"
                "macOS/CPU：pip install torch torchaudio"
            )
        return None
    if engine_type == "qwen3_asr":
        if importlib.util.find_spec("qwen_asr") is None:
            import platform
            hint = (
                "scripts\\install_qwen3_asr.bat"
                if platform.system() == "Windows"
                else "pip install qwen-asr"
            )
            return f"qwen3_asr 引擎未安装。请运行 {hint}（或直接 pip install qwen-asr）。"
        return None
    return None


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

    # 工厂阶段预检依赖：缺失就抛友好提示，而不是让 load() 在 worker 线程里崩成 traceback。
    missing = _missing_dep(engine_type)
    if missing:
        raise ImportError(missing)

    if engine_type == "funasr":
        from .funasr_engine import FunAsrEngine
        return FunAsrEngine(asr_cfg, on_result, source=source)

    if engine_type == "sensevoice":
        from .sensevoice_engine import SenseVoiceEngine
        return SenseVoiceEngine(asr_cfg, on_result, source=source)

    if engine_type == "funasr_nano":
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
