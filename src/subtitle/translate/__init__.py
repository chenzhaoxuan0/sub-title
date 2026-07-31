"""翻译子系统 —— 与 asr/ 子包对称的引擎化翻译。

四个引擎（Azure/Google/LibreTranslate/NLLB-200）经 factory 按 config 创建，
由 TranslationWorker 在后台线程池并发翻译 ASR 定稿句，结果通过 Qt 信号回主线程。

公开 API：
  - create_translator(cfg)：工厂，返回 Translator 或 None（未启用）
  - TranslationWorker：协调器（接 ASR 定稿句 → 后台翻译 → 信号）
"""
from .base import Translator, TranslatorError
from .factory import create_translator
from .worker import TranslationWorker

__all__ = [
    "Translator",
    "TranslatorError",
    "create_translator",
    "TranslationWorker",
]
