"""管线总装：采集线程 → 归一化 → 队列 → 推理线程 → 回调。

把音频捕获和 ASR 解耦：capture 只管塞 PCM，inference 只管消费。
归一化（重采样到 16k mono）在 capture 回调里就地做。

UI 层（或命令行调试）通过 on_text 回调拿结果。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np

from .audio import SystemAudioCapture, normalize_pcm
from .config import Config
from .asr.base import AsrEngine, AsrResult


class SubtitlePipeline:
    """串起采集与推理的控制器。

    on_text(text: str, is_final: bool): 每次有增量文字时调（已在推理线程，UI 层需自行 dispatch）。
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
        # 1) 加载模型（在主线程，避免 UI 卡顿期间提前暴露异常）
        self.engine.load()

        # 2) 启动采集（soundcard recorder 已做重采样到 target_sr/mono）
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

            # 按固定长度切块喂模型
            while len(self._buf) >= self.chunk_samples:
                block = self._buf[: self.chunk_samples]
                self._buf = self._buf[self.chunk_samples :]
                self._consume(block, is_final=False)

    def _consume(self, block: np.ndarray, is_final: bool) -> None:
        try:
            r: Optional[AsrResult] = self.engine.transcribe_chunk(block, is_final=is_final)
        except Exception as e:
            print(f"[pipeline] 推理异常: {e}")
            return
        if r and r.text:
            try:
                self.on_text(r.text, r.is_final)
            except Exception as e:
                print(f"[pipeline] on_text 异常: {e}")
