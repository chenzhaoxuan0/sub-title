"""翻译协调器 —— 接 ASR 定稿句 → 后台翻译 → 信号回主线程。

线程模型（与 pipeline 对称）：
  - feed(text, source)：主线程调，入队定稿句（去重）
  - 内部 ThreadPoolExecutor(max_workers=2) 并发翻译（Azure 无并发限制；本地引擎按算力）
  - 翻译完成 emit translation_done(orig, trans, source) —— Qt QueuedConnection 送主线程
  - start()/stop() 与识别同步生命周期

去重：nano 流式的 sentences 是累计回调（每次含已定稿的历史句），同句会被重复
喂入。按 (source, 句子文本) 维护 LRU 缓存（最近 200 句），命中则不重翻、直接回放缓存译文。
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .base import Translator, TranslatorError
from .factory import create_translator

logger = logging.getLogger(__name__)

_DEDUP_SIZE = 200   # LRU 缓存大小（条）


class TranslationWorker(QObject):
    """ASR 定稿句 → 后台翻译 → 主线程信号。

    on_error(msg)：翻译持续失败时回调（可选，让 UI 在状态栏提示一次）。
    """

    # (原文, 译文, source) —— 主线程槽消费，写进 panel 的译文区
    translation_done = Signal(str, str, str)
    # 翻译出错（透传给状态栏，不中断识别）
    error = Signal(str)

    def __init__(self, cfg, on_error=None):
        super().__init__()
        self.cfg = cfg
        self._on_error_cb = on_error
        self._translator: Optional[Translator] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()
        self._running = threading.Event()
        # LRU 去重：(source, text) -> 译文。保持插入顺序，超 _DEDDUP_SIZE 弹最旧。
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._consecutive_errors = 0   # 连续失败计数，超阈值提示一次后重置

    def start(self) -> bool:
        """创建翻译器 + 起线程池。返回 False 表示翻译未启用（上层据此跳过）。
        翻译器构造失败（如 Azure 没 key）抛 TranslatorError，由上层 catch 提示。"""
        if self._running.is_set():
            return True
        translator = create_translator(self.cfg)
        if translator is None:
            # enabled=False 或 engine=none
            return False
        self._translator = translator
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="translate")
        self._running.set()
        self._consecutive_errors = 0
        return True

    def feed(self, text: str, source: str = "system") -> None:
        """主线程调：把一条 ASR 定稿句投递给后台翻译。去重命中则直接回放缓存。"""
        if not self._running.is_set() or self._translator is None:
            return
        text = (text or "").strip()
        if not text:
            return
        key = (source, text)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                # 命中缓存：回放译文，不重翻
                hit = cached
            else:
                cached = None
        if cached is not None:
            self.translation_done.emit(text, hit, source)
            return
        # 投递到线程池异步翻译
        if self._executor is None:
            return
        self._executor.submit(self._translate_one, text, source)

    def _translate_one(self, text: str, source: str) -> None:
        """工作线程：真正调翻译器，成功则更新缓存 + emit。"""
        if self._translator is None:
            return
        try:
            result = self._translator.translate(text)
        except TranslatorError as e:
            self._handle_error(str(e))
            return
        except Exception as e:
            self._handle_error(f"翻译异常：{e}")
            return
        if not self._running.is_set():
            return
        with self._lock:
            self._cache[(source, text)] = result
            if len(self._cache) > _DEDUP_SIZE:
                self._cache.popitem(last=False)   # 弹最旧
        self._consecutive_errors = 0
        self.translation_done.emit(text, result, source)

    def _handle_error(self, msg: str) -> None:
        """连续失败累计，每 5 次提示一次（避免刷屏）。"""
        self._consecutive_errors += 1
        if self._consecutive_errors % 5 == 1:
            logger.warning("翻译失败（第 %d 次）：%s", self._consecutive_errors, msg)
            self.error.emit(f"翻译失败：{msg}（后续同类错误将静默）")
            # 重置计数窗口，让下一批 5 次再提示
            self._consecutive_errors = 0

    def stop(self) -> None:
        """停止翻译：关线程池 + 释放翻译器。清缓存（下次启动重新翻）。"""
        self._running.clear()
        ex = self._executor
        self._executor = None
        if ex is not None:
            ex.shutdown(wait=False, cancel_futures=True)
        tr = self._translator
        self._translator = None
        if tr is not None:
            try:
                tr.close()
            except Exception:
                pass
        with self._lock:
            self._cache.clear()
