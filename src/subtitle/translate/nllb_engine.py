"""NLLB-200 —— 本地离线翻译服务（备选，质量/覆盖最强，Meta 200 语言）。

服务端：thammegowda/nllb-serve（pip 安装后 `nllb-serve`，默认监听 6060）。
WSL 里跑：python -m nllb_serve --port 6060
默认模型 facebook/nllb-200-distilled-600M（约 1.2GB，CPU 可跑），200 语言覆盖。

语言码用 NLLB 特殊格式：eng_Latn / zho_Hans / jpn_Jpan。本类内置常见 ISO→NLLB 映射。
"""
from __future__ import annotations

from ._http import http_post_json
from .base import Translator, TranslatorError

# 常见 ISO/Bcp47 → NLLB 语言码映射。未命中时原样透传（让用户直接填 NLLB 码）。
_NLLB_LANG_MAP = {
    "auto": "eng_Latn",      # NLLB 不支持 auto，用 eng 兜底（auto 时通常翻中→英或英→中）
    "en": "eng_Latn",
    "en-us": "eng_Latn",
    "en-gb": "eng_Latn",
    "zh": "zho_Hans",
    "zh-hans": "zho_Hans",
    "zh-cn": "zho_Hans",
    "zh-tw": "zho_Hant",
    "zh-hant": "zho_Hant",
    "ja": "jpn_Jpan",
    "ja-jp": "jpn_Jpan",
    "ko": "kor_Hang",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "es": "spa_Latn",
    "ru": "rus_Cyrl",
    "ar": "arb_Arab",
}


def _to_nllb_code(lang: str) -> str:
    if not lang:
        return "eng_Latn"
    key = lang.lower().strip()
    return _NLLB_LANG_MAP.get(key, lang)


class NllbTranslator(Translator):
    """调本地 nllb-serve 的 POST /translate。"""

    def __init__(self, cfg, source_lang: str = "auto", target_lang: str = "zh-Hans"):
        super().__init__(cfg, source_lang, target_lang)
        host = getattr(cfg, "nllb_host", "localhost") or "localhost"
        port = int(getattr(cfg, "nllb_port", 6060) or 6060)
        self._url = f"http://{host}:{port}/translate"
        self._src_code = _to_nllb_code(source_lang)
        self._tgt_code = _to_nllb_code(target_lang)

    def _do_translate(self, text: str) -> str:
        data = http_post_json(self._url, {
            "source": [text],
            "src_lang": self._src_code,
            "_tgt_lang_param": self._tgt_code,
            "tgt_lang": self._tgt_code,
        })
        # nllb-serve 响应字段：{ "translation": ["译文"] } 或 { "result": "译文" }
        if isinstance(data, dict):
            if "translation" in data:
                t = data["translation"]
                return t[0] if isinstance(t, list) else str(t)
            if "result" in data:
                return str(data["result"])
        raise TranslatorError(f"NLLB 返回异常：{str(data)[:200]}")
