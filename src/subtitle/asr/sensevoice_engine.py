"""SenseVoice 引擎实现（事件驱动接口，段式伪流式）。

SenseVoice-Small（iic/SenseVoiceSmall）是非自回归端到端模型，不支持 chunk 流式。
用"攒段 + 整段推理"包装成伪流式：
  - feed(chunk) 累积到内部 buffer
  - 攒够 segment_seconds 或检测到静音 → 整段推理
  - 清洗 <|zh|><|HAPPY|> 等标签 → on_result(text, is_final=True)

stop 后置 _closed；feed 直接返回。
"""
from __future__ import annotations

import re

import numpy as np

from .base import AsrEngine, OnResult


_TAG_RE = re.compile(r"<\|[^|]*\|>")


def _strip_tags(text: str) -> str:
    # SenseVoice occasionally emits a trailing newline (and can emit embedded
    # line breaks).  The UI owns line wrapping, so model whitespace must not
    # create an empty subtitle row.
    return " ".join(_TAG_RE.sub("", text).split())


class SenseVoiceEngine(AsrEngine):
    def __init__(self, cfg, on_result: OnResult, source: str = "system"):
        super().__init__(cfg, on_result, source=source)
        self.model = None
        self._buf = np.zeros(0, dtype=np.float32)
        self._segment_samples = 0
        self._silence_threshold = 0.01
        self._silence_run = 0
        self._speech_samples = 0
        self._closed = False

    def load(self) -> None:
        from funasr import AutoModel
        model_id = getattr(self.cfg, "sensevoice_model", "iic/SenseVoiceSmall")
        device = getattr(self.cfg, "sensevoice_device", "cpu")
        seg_sec = getattr(self.cfg, "sensevoice_segment_seconds", 2.0)
        print(f"[sensevoice] 加载 {model_id} (device={device})，首次会下载(~254MB)...")
        self.model = AutoModel(
            model=model_id,
            trust_remote_code=True,
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            device=device,
            hub="ms",
        )
        self._segment_samples = int(seg_sec * 16000)
        print(f"[sensevoice] 就绪，段时长={seg_sec}s（CPU/段式，延迟略高于流式）")

    def feed(self, chunk: np.ndarray) -> None:
        if self._closed or self.model is None:
            return
        self._buf = np.concatenate([self._buf, chunk.astype(np.float32)])

        # 静音检测
        energy = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
        if energy < self._silence_threshold:
            self._silence_run += len(chunk)
        else:
            self._silence_run = 0
            self._speech_samples += len(chunk)

        should_infer = False
        if len(self._buf) >= self._segment_samples:
            should_infer = True
        elif len(self._buf) > 8000 and self._silence_run > 4800:
            should_infer = True

        if should_infer and len(self._buf) > 1600:
            self._infer_segment()

    def _infer_segment(self) -> None:
        audio = self._buf
        self._buf = np.zeros(0, dtype=np.float32)
        self._silence_run = 0
        speech_samples = self._speech_samples
        self._speech_samples = 0
        if speech_samples == 0:
            return
        try:
            res = self.model.generate(
                input=audio,
                language="auto",
                use_itn=True,
                merge_vad=True,
                merge_length_s=15,
            )
            if res and res[0].get("text"):
                text = _strip_tags(res[0]["text"])
                if text:
                    # SenseVoice 架构不支持说话人区分，spk_id 永远传 None
                    self.on_result(text, is_final=True, source=self.source, spk_id=None)
        except Exception as e:
            print(f"[sensevoice] 推理异常: {e}")
            # buf 已在开头清空，状态一致

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        # flush 残留 buffer
        if self.model is not None and len(self._buf) > 1600:
            self._infer_segment()
        else:
            self._buf = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._silence_run = 0
        self._speech_samples = 0
        self._closed = False
