"""Fun-ASR-Nano segment engine for local Chinese, dialect, and lyric subtitles."""
from __future__ import annotations

import logging
import os
import tempfile
import traceback

import numpy as np
import soundfile as sf

from .base import AsrEngine, OnResult

logger = logging.getLogger(__name__)


class NanoStreamingUnavailable(RuntimeError):
    """流式服务不可用（连不上 / websockets 缺失），工厂据此降级段式。"""


class FunAsrNanoEngine(AsrEngine):
    """Use the current FunASR runtime without making vLLM a required dependency."""

    def __init__(self, cfg, on_result: OnResult, source: str = "system"):
        super().__init__(cfg, on_result, source=source)
        self.model = None
        self._buf = np.zeros(0, dtype=np.float32)
        self._segment_samples = 32000
        self._silence_threshold = 0.01
        self._silence_run = 0
        self._speech_samples = 0
        self._closed = False

    def load(self) -> None:
        from funasr import AutoModel

        model_id = getattr(self.cfg, "funasr_nano_model", "FunAudioLLM/Fun-ASR-Nano-2512")
        # 跨平台设备解析：cuda 不可用（macOS/CPU torch）时降级，避免硬崩。
        from ._device import resolve_device
        device = resolve_device(getattr(self.cfg, "funasr_nano_device", "cuda"))
        seconds = float(getattr(self.cfg, "funasr_nano_segment_seconds", 2.0))
        self._segment_samples = max(1600, int(seconds * 16000))
        logger.info(f"从 ModelScope 加载 {model_id} (device={device})...")
        self.model = AutoModel(model=model_id, device=device, disable_update=True, hub="ms")
        logger.info(f"就绪，段时长={seconds}s")

    def feed(self, chunk: np.ndarray) -> None:
        if self._closed or self.model is None:
            return
        chunk = chunk.astype(np.float32, copy=False)
        self._buf = np.concatenate([self._buf, chunk])
        energy = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
        if energy < self._silence_threshold:
            self._silence_run += len(chunk)
        else:
            self._silence_run = 0
            self._speech_samples += len(chunk)
        if len(self._buf) >= self._segment_samples or (
            len(self._buf) > 8000 and self._silence_run > 4800
        ):
            self._infer_segment()

    def _infer_segment(self) -> None:
        audio, speech_samples = self._buf, self._speech_samples
        self._buf = np.zeros(0, dtype=np.float32)
        self._silence_run = self._speech_samples = 0
        if speech_samples == 0:
            return
        path = None
        try:
            # AutoModel accepts audio paths. Passing an ndarray is supported by
            # the vLLM API but not by this standard FunASR runtime.
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as file:
                path = file.name
            sf.write(path, audio, 16000, subtype="PCM_16")
            language = getattr(self.cfg, "funasr_nano_language", "中文")
            result = self.model.generate(
                input=[path], cache={}, batch_size=1,
                language=language, itn=True,
            )
            text = result[0].get("text", "").strip() if result else ""
            if text:
                self.on_result(text, is_final=True, source=self.source, spk_id=None)
        except Exception as error:
            logger.exception(f"推理异常: {error}")
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.model is not None and len(self._buf) > 1600:
            self._infer_segment()
        else:
            self._buf = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._silence_run = self._speech_samples = 0
        self._closed = False
