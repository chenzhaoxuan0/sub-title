"""faster-whisper (CTranslate2) 引擎实现（事件驱动接口，段式伪流式）。

和 SenseVoiceEngine 同构：feed 累积 16k mono float32 → 攒够段长或检测到静音
→ 整段 model.transcribe(numpy) → 拼接 segment.text → on_result(text, is_final=True)。

faster-whisper 无原生流式（transcribe 是批处理），这是社区通用做法（buffer-chunk）。
延迟由段长（默认 2s）主导，和 SenseVoice 一致。

关键参数选择：
  - device="auto"：cudaGetDeviceCount()==0 时自动回退 CPU，不崩
  - condition_on_previous_text=False：防 hallucination 滚雪球（开麦字幕关键）
  - without_timestamps=True：字幕不需要时间戳，省解码开销
  - beam_size=1：turbo 在 beam=1 鲁棒，显著降延迟
  - initial_prompt（中文）：Whisper 默认常不吐中文标点，给个带标点的短例引导输出标点，
    支持下游自动分行。实测有效（逗号可能半角，但句号 。 稳定，够断句用）。

依赖（可选，未装时 factory 选中才报错）：
  pip install faster-whisper   # 拉入 ctranslate2/tokenizers/onnxruntime，不依赖 torch
"""
from __future__ import annotations

import numpy as np

from .base import AsrEngine, OnResult


class FasterWhisperEngine(AsrEngine):
    def __init__(self, cfg, on_result: OnResult, source: str = "system"):
        super().__init__(cfg, on_result, source=source)
        self.model = None
        self._buf = np.zeros(0, dtype=np.float32)
        self._segment_samples = 0
        self._silence_threshold = 0.01
        self._silence_run = 0
        self._closed = False
        # transcribe kwargs（load 里 finalize）
        self._language: str | None = None
        self._beam_size = 1
        self._vad_filter = False
        self._vad_parameters: dict | None = None
        self._initial_prompt: str | None = None

    def load(self) -> None:
        from faster_whisper import WhisperModel
        model_name = getattr(self.cfg, "faster_whisper_model", "large-v3-turbo")
        device = getattr(self.cfg, "faster_whisper_device", "auto")
        compute_type = getattr(self.cfg, "faster_whisper_compute_type", "auto")
        seg_sec = getattr(self.cfg, "faster_whisper_segment_seconds", 2.0)
        lang = getattr(self.cfg, "faster_whisper_language", "zh")
        self._language = (lang if lang and lang != "auto" else None)  # None = 自动检测
        self._beam_size = int(getattr(self.cfg, "faster_whisper_beam_size", 1))
        self._vad_filter = bool(getattr(self.cfg, "faster_whisper_vad_filter", False))
        if self._vad_filter:
            # 短段场景把 min_silence 调小，避免把整段当非语音丢掉
            self._vad_parameters = dict(min_silence_duration_ms=500, speech_pad_ms=200)
        self._silence_threshold = float(
            getattr(self.cfg, "faster_whisper_silence_threshold", 0.01))
        # initial_prompt 引导模型输出标点（实测：不带 prompt 时 Whisper 中文常无标点，
        # 给一个带标点的短例可显著提升标点输出率，支持下游自动分行）。
        if self._language == "zh":
            self._initial_prompt = "你好，这是一个例子。第一句话；第二句话！"

        print(f"[faster_whisper] 加载 {model_name} (device={device}, "
              f"compute_type={compute_type})，首次会从 HF Hub 下载...")
        self.model = WhisperModel(
            model_size_or_path=model_name,
            device=device,
            compute_type=compute_type,
        )
        self._segment_samples = int(seg_sec * 16000)
        print(f"[faster_whisper] 就绪，段时长={seg_sec}s beam_size={self._beam_size}")

    def feed(self, chunk: np.ndarray) -> None:
        if self._closed or self.model is None:
            return
        self._buf = np.concatenate([self._buf, chunk.astype(np.float32)])

        # RMS 能量静音检测（和 SenseVoice 一致）
        energy = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
        if energy < self._silence_threshold:
            self._silence_run += len(chunk)
        else:
            self._silence_run = 0

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
        try:
            segments, _info = self.model.transcribe(
                audio,                                # 1-D float32 16k mono —— 原生支持
                language=self._language,              # None = 自动检测
                beam_size=self._beam_size,
                vad_filter=self._vad_filter,
                vad_parameters=self._vad_parameters,
                initial_prompt=self._initial_prompt,  # 引导中文标点输出
                without_timestamps=True,              # 字幕不需要时间戳，省开销
                condition_on_previous_text=False,     # 防 hallucination 滚雪球
            )
            # segment.text 已去前导空格；逐段拼成一句
            text = "".join(s.text for s in segments).strip()
            if text:
                self.on_result(text, is_final=True, source=self.source)
        except Exception as e:
            print(f"[faster_whisper] 推理异常: {e}")
            # buf 已在开头清空，状态一致；下一帧继续

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
        self._closed = False
