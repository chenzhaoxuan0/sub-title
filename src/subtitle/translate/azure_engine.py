"""Azure Translator（微软翻译）—— 主力引擎，官方免费层 F0。

API：POST https://api.cognitive.microsofttranslator.com/translate?api-version=3.0
鉴权：两个请求头 Ocp-Apim-Subscription-Key + Ocp-Apim-Subscription-Region
免费额度：F0 = 每小时 200 万字符（滑动窗口刷新），无并发数限制。
延迟：100 字符内 150-300ms 返回，适合每秒一句的实时字幕。

key/region 从 credentials.py（系统 keyring）读，绝不进 config.yaml。
"""
from __future__ import annotations

from urllib.parse import quote

from .. import credentials
from ._http import http_post_json
from .base import Translator, TranslatorError

_ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"


class AzureTranslator(Translator):
    """Azure Translator Text API v3。"""

    def __init__(self, cfg, source_lang: str = "auto", target_lang: str = "zh-Hans"):
        super().__init__(cfg, source_lang, target_lang)
        cred = credentials.get_azure_translate()
        self._key = cred.get(credentials.KEY_AZURE_TRANSLATE_KEY, "") or ""
        self._region = cred.get(credentials.KEY_AZURE_TRANSLATE_REGION, "") or ""
        if not self._key:
            raise TranslatorError(
                "未配置 Azure 翻译 key，请在「设置 → 翻译」填入（存系统保险箱）。"
            )

    def _do_translate(self, text: str) -> str:
        # 查询串：api-version + from(可选，auto 时不传让 Azure 自动检测) + to
        params = ["api-version=3.0", "to=" + quote(self.target_lang)]
        if self.source_lang and self.source_lang != "auto":
            params.append("from=" + quote(self.source_lang))
        url = f"{_ENDPOINT}?" + "&".join(params)
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Ocp-Apim-Subscription-Region": self._region,
        }
        # body 字段名是 "Text"（首字母大写），不是 "text" —— v3 常见坑
        data = http_post_json(url, [{"Text": text}], headers=headers)
        # 成功：[{"translations":[{"text":"...","to":"zh-Hans"}]}]
        # 失败：{"error":{"code":..., "message":...}}
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise TranslatorError(f"Azure 错误：{err.get('message', err)}")
        if not isinstance(data, list) or not data:
            raise TranslatorError(f"Azure 返回结构异常：{str(data)[:200]}")
        item = data[0]
        translations = item.get("translations")
        if not translations:
            # 可能是整段翻译失败（如 source 字符不合法）
            raise TranslatorError(f"Azure 无译文：{str(item)[:200]}")
        return translations[0].get("text", "")

    def test(self) -> bool:
        # 用 /languages 端点轻量探测 + key 有效性（不发翻译请求，不耗配额）
        # 这里仍用一次最小翻译请求，因为 /languages 不验证 key
        return super().test()
