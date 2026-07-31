"""LibreTranslate —— 本地离线翻译服务（备选），复用 WSL 起服务的模式。

默认监听 5000，Docker 一行起：
  docker run -p 5000:5000 libretranslate/libretranslate --load-only en,zh
完全离线、免费、零 key。CPU 上每句几百 ms~1s。
"""
from __future__ import annotations

from ._http import http_post_json
from .base import Translator, TranslatorError


class LibreTranslateTranslator(Translator):
    """调本地 LibreTranslate 的 POST /translate。"""

    def __init__(self, cfg, source_lang: str = "auto", target_lang: str = "zh"):
        super().__init__(cfg, source_lang, target_lang)
        host = getattr(cfg, "libretranslate_host", "localhost") or "localhost"
        port = int(getattr(cfg, "libretranslate_port", 5000) or 5000)
        self._url = f"http://{host}:{port}/translate"
        # LibreTranslate 用 "auto" 自动检测；不支持时退化为 "en"
        self._src = source_lang if source_lang and source_lang != "auto" else "auto"

    def _do_translate(self, text: str) -> str:
        data = http_post_json(self._url, {
            "q": text,
            "source": self._src,
            "target": self.target_lang,
            "format": "text",
        })
        # 成功：{"translatedText": "..."}
        if isinstance(data, dict) and "translatedText" in data:
            return data["translatedText"]
        raise TranslatorError(f"LibreTranslate 返回异常：{str(data)[:200]}")

    def test(self) -> bool:
        # LibreTranslate 有 /languages 端点，轻量探测 + 不耗翻译算力
        from ._http import http_get
        try:
            host = getattr(self.cfg, "libretranslate_host", "localhost") or "localhost"
            port = int(getattr(self.cfg, "libretranslate_port", 5000) or 5000)
            http_get(f"http://{host}:{port}/languages", timeout=3)
            return True
        except Exception:
            return False
