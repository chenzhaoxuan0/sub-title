"""管线总装：采集线程 → 归一化 → 队列 → 推理线程 → engine.feed。

引擎接口是事件驱动的（feed 单向喂入，结果走 on_result 回调），
所以 pipeline 的推理线程只负责把 chunk 喂给 engine，结果回调直接调 on_text。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np

from .audio import SystemAudioCapture, normalize_pcm
from .config import Config
from .asr.base import AsrEngine
from .asr.factory import create_engine


class SubtitlePipeline:
    """串起采集与推理的控制器。

    on_text(text, is_final): 每次有文字时调（在推理线程或 engine 内部线程，
    UI 层需自行用 Qt signal 桥接主线程）。
    """

    def __init__(
        self,
        cfg: Config,
        engine: AsrEngine,
        on_text: Optional[Callable[[str, bool], None]] = None,
    ):
        self.cfg = cfg
        self.engine = engine
        self.on_text = on_text or (lambda t, f: print(t, end="", flush=True))

        self.target_sr = cfg.audio.target_sample_rate
        self.chunk_samples = int(round(cfg.audio.chunk_seconds * self.target_sr))

        self._capture: Optional[SystemAudioCapture] = None
        self._infer_thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._buf = np.zeros(0, dtype=np.float32)

    def start(self) -> None:
        if self._running.is_set():
            return
        # 1) 加载引擎（在 worker 线程，避免 UI 卡顿期间暴露异常）
        #    引擎的 on_result 回调直接调本 pipeline 的 on_text
        self.engine.load()

        # 2) 启动采集（soundcard recorder 已重采样到 target_sr/mono）
        self._capture = SystemAudioCapture(
            target_sr=self.target_sr,
            block_samples=self.chunk_samples,
            speaker_name=self.cfg.audio.input_device,
        )
        self._capture.start()

        # 3) 启动推理线程
        self._running.set()
        self._infer_thread = threading.Thread(target=self._infer_loop, daemon=True)
        self._infer_thread.start()

    def stop(self) -> None:
        self._running.clear()
        # 通知引擎停止（发 final / flush / 断连）
        try:
            self.engine.stop()
        except Exception as e:
            print(f"[pipeline] engine.stop 异常: {e}")
        if self._capture is not None:
            self._capture.stop()
        if self._infer_thread is not None:
            self._infer_thread.join(timeout=3)

    def _infer_loop(self) -> None:
        cap = self._capture
        # 等采集流真正打开
        while cap.actual_sr is None and self._running.is_set():
            time.sleep(0.05)
        src_sr = cap.actual_sr or self.target_sr

        while self._running.is_set():
            try:
                raw = cap.queue.get(timeout=0.5)
            except Exception:
                continue
            # 归一化到 16k mono float32
            chunk = normalize_pcm(raw, src_sr=src_sr, dst_sr=self.target_sr)
            self._buf = np.concatenate([self._buf, chunk])

            # 按固定长度切块喂引擎（引擎内部决定怎么用这个 chunk）
            while len(self._buf) >= self.chunk_samples:
                block = self._buf[: self.chunk_samples]
                self._buf = self._buf[self.chunk_samples:]
                self._feed_engine(block)

    def _feed_engine(self, block: np.ndarray) -> None:
        """把一个 chunk 喂给引擎。结果由引擎通过 on_result 回调推送。"""
        try:
            self.engine.feed(block)
        except Exception as e:
            print(f"[pipeline] engine.feed 异常: {e}")
