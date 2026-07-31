"""翻译引擎抽象接口 —— 与 asr/base.py 同构（事件驱动）。

四个引擎（Azure / Google / LibreTranslate / NLLB-200）统一用这个接口：
- translate(text) 同步翻译单句，返回译文（耗时，调用方应在后台线程调）
- test() 探测连通性（设置页『测试连接』按钮用）
- close() 释放资源

与 ASR 的区别：翻译是"请求-响应"模型而非"流式 feed"，所以不做 ABC 强约束，
而是约定 Translator 子类实现 translate。配置由各自子类从 TranslationConfig 读取。
"""
from __future__ import annotations

from typing import Optional


class TranslatorError(Exception):
    """翻译失败的统一异常。message 用中文，直接给用户看。"""


class Translator:
    """翻译器基类。子类实现 _do_translate(text) -> str。

    所有方法在调用方线程同步执行。translate() 可能因网络/模型耗时至秒级，
    调用方（TranslationWorker）负责把调用放到后台线程池，结果用 Qt 信号回主线程。
    """

    def __init__(self, cfg, source_lang: str = "auto", target_lang: str = "zh-Hans"):
        self.cfg = cfg
        self.source_lang = source_lang   # "auto" = 自动检测（Azure 支持，本地引擎需具体码）
        self.target_lang = target_lang

    def translate(self, text: str) -> str:
        """翻译单句。空串/纯空白直接返回空（不浪费配额）。失败抛 TranslatorError。"""
        if not text or not text.strip():
            return ""
        return self._do_translate(text)

    def _do_translate(self, text: str) -> str:
        """子类实现：真正发请求、解析、返回译文。失败抛 TranslatorError。"""
        raise NotImplementedError

    def test(self) -> bool:
        """探测连通性。默认用一句固定测试文本跑一次 translate，成功返回 True。
        子类可覆盖为更轻量的 ping（如 Azure 的 /languages 端点）。"""
        try:
            self.translate("hello")
            return True
        except Exception:
            return False

    def close(self) -> None:
        """释放资源（连接池等）。默认空，子类按需覆盖。"""
        return None
