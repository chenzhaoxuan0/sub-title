"""谷歌翻译（非官方免费端点）—— 备选引擎，无需 key。

调用 https://translate.google.com/translate_a/single（免费网页端），client=gtx 绕过
官方 API key 要求。桌面端 Python 直连不受浏览器 CORS 限制。

限制：单次 5000 字符；高频会 429/503。内置指数退避重试（0.5s→1s→2s，最多 3 次）。
稳定性低于 Azure，仅作"用户不想配 key"的降级方案。
"""
from __future__ import annotations

import json
import time
from urllib.parse import quote

from ._http import TranslatorError, http_get
from .base import Translator, TranslatorError

_ENDPOINT = "https://translate.google.com/translate_a/single"
_MAX_RETRIES = 3


class GoogleTranslator(Translator):
    """谷歌翻译非官方端点（client=gtx）。"""

    def _do_translate(self, text: str) -> str:
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return self._do_once(text)
            except TranslatorError as e:
                last_err = e
                msg = str(e)
                # 仅对限流/暂时性错误退避重试；HTTP 4xx（除 429）不重试
                if "HTTP 429" in msg or "HTTP 503" in msg or "网络错误" in msg:
                    time.sleep(0.5 * (2 ** attempt))   # 0.5s, 1s, 2s
                    continue
                raise
        raise TranslatorError(f"谷歌翻译重试 {_MAX_RETRIES} 次仍失败：{last_err}")

    def _do_once(self, text: str) -> str:
        sl = self.source_lang if self.source_lang and self.source_lang != "auto" else "auto"
        params = "&".join([
            "client=gtx",
            "dt=t",                       # 返回翻译片段
            f"sl={quote(sl)}",
            f"tl={quote(self.target_lang)}",
            f"q={quote(text)}",
        ])
        url = f"{_ENDPOINT}?{params}"
        raw = http_get(url)
        # 响应：[[["译文","原文",...],[...]], ...]  拼接所有片段译文
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise TranslatorError(f"谷歌返回非 JSON：{raw[:200]}") from e
        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            raise TranslatorError(f"谷歌返回结构异常：{raw[:200]}")
        chunks = []
        for seg in data[0]:
            if isinstance(seg, list) and seg:
                chunks.append(seg[0] or "")
        return "".join(chunks)
