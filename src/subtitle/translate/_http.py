"""翻译引擎共用的 HTTP 工具 —— 标准 urllib 封装，零第三方依赖。

为什么不用 requests/httpx：纯 API 打包模式（仅阿里云引擎）的 exe 体积要小，
ASR 侧也不用 requests，翻译侧若引新库会破坏"纯 API 模式零额外依赖"的承诺。
urllib.request + json 足以覆盖 4 个翻译引擎的 REST 调用。
"""
from __future__ import annotations

import json
import socket
from typing import Any, Optional
from urllib import error, request

from .base import TranslatorError

# 全局默认超时（秒）。翻译请求卡死会拖住 worker 线程池，必须设上限。
_DEFAULT_TIMEOUT = 8


def http_post_json(
    url: str,
    payload: Any,
    *,
    headers: Optional[dict] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Any:
    """POST JSON body，解析 JSON 响应返回。失败统一抛 TranslatorError（中文消息）。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=UTF-8")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise TranslatorError(f"HTTP {e.code} {e.reason}：{body}") from None
    except (error.URLError, socket.timeout) as e:
        # URLError 涵盖 DNS 失败、连不上、超时；socket.timeout 是连接后读超时
        raise TranslatorError(f"网络错误：{e}") from None
    except Exception as e:
        raise TranslatorError(f"请求失败：{e}") from None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise TranslatorError(f"响应不是合法 JSON：{raw[:200]}") from e


def http_get(url: str, *, headers: Optional[dict] = None, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """GET，返回原始文本（Google 端点返回类 JSON 文本，由调用方解析）。"""
    req = request.Request(url, method="GET")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        raise TranslatorError(f"HTTP {e.code} {e.reason}") from None
    except (error.URLError, socket.timeout) as e:
        raise TranslatorError(f"网络错误：{e}") from None
    except Exception as e:
        raise TranslatorError(f"请求失败：{e}") from None
