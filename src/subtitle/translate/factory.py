"""翻译引擎工厂 —— 根据 cfg.translate.engine 创建对应翻译器。

与 asr/factory.py 同构：按字符串分发、延迟 import、缺失依赖给友好提示。
新增引擎在这里注册即可，worker/app 不用改。

engine 取值：azure / google / libretranslate / nllb / none
none（或 enabled=False）→ 返回 None，上层据此跳过翻译（零行为回归）。
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import Translator

logger = logging.getLogger(__name__)


def create_translator(cfg) -> Optional[Translator]:
    """根据 cfg.translate 创建翻译器实例。

    返回 None 表示翻译未启用（enabled=False 或 engine=none），上层应跳过。
    配置缺失（如 Azure 没 key）由引擎构造函数抛 TranslatorError，工厂不吞——
    让 worker 在 start 时捕获并在状态栏提示。
    """
    tcfg = getattr(cfg, "translate", None)
    if tcfg is None:
        return None
    if not getattr(tcfg, "enabled", False):
        return None

    engine = (getattr(tcfg, "engine", "azure") or "azure").strip().lower()
    source_lang = getattr(tcfg, "source_lang", "auto") or "auto"
    target_lang = getattr(tcfg, "target_lang", "zh-Hans") or "zh-Hans"

    if engine == "none":
        return None

    if engine == "azure":
        from .azure_engine import AzureTranslator
        return AzureTranslator(tcfg, source_lang, target_lang)

    if engine == "google":
        from .google_engine import GoogleTranslator
        return GoogleTranslator(tcfg, source_lang, target_lang)

    if engine == "libretranslate":
        from .libretranslate_engine import LibreTranslateTranslator
        return LibreTranslateTranslator(tcfg, source_lang, target_lang)

    if engine == "nllb":
        from .nllb_engine import NllbTranslator
        return NllbTranslator(tcfg, source_lang, target_lang)

    logger.warning("未知翻译引擎: %s（支持: azure/google/libretranslate/nllb）", engine)
    return None
