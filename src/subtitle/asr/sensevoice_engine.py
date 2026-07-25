"""SenseVoice 引擎实现（事件驱动接口，段式伪流式）。

SenseVoice-Small（iic/SenseVoiceSmall）是非自回归端到端模型，不支持 chunk 流式。
这里用"攒段 + 整段推理"包装成伪流式：
  - feed(chunk) 累积到内部 buffer
  - 攒够 segment_seconds（默认 2s）或检测到静音（能量低于阈值）→ 整段推理
  - rich_transcription_postprocess 清洗 <|zh|><|HAPPY|> 等标签 → on_result(text, is_final=True)

CPU 可跑（适合 Mac/弱 GPU）。延迟 = 段时长 + 推理耗时，比 FunASR 流式略高。
"""
from __future__ import annotations

import re

import numpy as np

from .base import AsrEngine, OnResult


# 匹配 SenseVoice 输出的富标签 <|xxx|>
_TAG_RE = re.compile(r"<\|[^|]*\|>")


def _strip_tags(text: str) -> str:
    """去掉所有 <|...|> 标签，只保留纯文本字幕。"""
    return _TAG_RE.sub("", text).strip()


class SenseVoiceEngine(AsrEngine):
    def __init__(self, cfg, on_result: OnResult):
        super().__init__(cfg, on_result)
        self.model = None
        # 段式缓冲：攒够 segment_seconds 触发推理
        self._buf = np.zeros(0, dtype=np.float32)
        self._segment_samples = 0  # load 时根据 segment_seconds 算
        # 静音检测（提前切段，降低延迟）
        self._silence_threshold = 0.01
        self._silence_run = 0       # 连续静音帧数

    def load(self) -> None:
        from funasr import AutoModel
        model_id = getattr(self.cfg, "sensevoice_model", "iic/SenseVoiceSmall")
        device = getattr(self.cfg, "sensevoice_device", "cpu")
        seg_sec = getattr(self.cfg, "sensevoice_segment_seconds", 2.0)
        print(f"[sensevoice] 加载 {model_id} (device={device})，首次会下载(~254MB)...")
        self.model = AutoModel(
            model=model_id,
            trust_remote_code=True,
            vad_model="fsmn-vad",                          # VAD 切句
            vad_kwargs={"max_single_segment_time": 30000},
            device=device,
        )
        # 段时长（按 16k 采样率算样本数）
        self._segment_samples = int(seg_sec * 16000)
        print(f"[sensevoice] 就绪，段时长={seg_sec}s (CPU/段式，延迟略高于流式)")

    def feed(self, chunk: np.ndarray) -> None:
        if self.model is None:
            raise RuntimeError("模型未加载，先调 load()")
        self._buf = np.concatenate([self._buf, chunk.astype(np.float32)])

        # 静音检测：能量低于阈值且已累积够一定量，提前切段（降低延迟）
        energy = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
        if energy < self._silence_threshold:
            self._silence_run += len(chunk)
        else:
            self._silence_run = 0

        should_infer = False
        # 条件1：攒够 segment_samples
        if len(self._buf) >= self._segment_samples:
            should_infer = True
        # 条件2：已累积 > 0.5s 且检测到 > 0.3s 静音（句尾停顿）
        elif len(self._buf) > 8000 and self._silence_run > 4800:
            should_infer = True

        if should_infer and len(self._buf) > 1600:  # 至少 0.1s 才推
            self._infer_segment()

    def _infer_segment(self) -> None:
        """对当前 buffer 做整段推理，输出纯文本。"""
        audio = self._buf
        self._buf = np.zeros(0, dtype=np.float32)
        self._silence_run = 0
        try:
            res = self.model.generate(
                input=audio,
                language="auto",
                use_itn=True,
                merge_vad=True,
                merge_length_s=15,
            )
            if res and res[0].get("text"):
                raw = res[0]["text"]
                text = _strip_tags(raw)
                if text:
                    self.on_result(text, is_final=True)
        except Exception as e:
            print(f"[sensevoice] 推理异常: {e}")

    def stop(self) -> None:
        # flush 残留的 buffer
        if len(self._buf) > 1600:
            self._infer_segment()
        else:
            self._buf = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._silence_run = 0
