"""Qwen3-ASR segment engine. Native streaming is available separately through vLLM."""
from __future__ import annotations

import importlib.util

import numpy as np

from .base import AsrEngine, OnResult
from .modelscope_hub import download_modelscope


def qwen3_asr_available() -> bool:
    """Check availability without importing the heavyweight model runtime."""
    return importlib.util.find_spec("qwen_asr") is not None


class Qwen3AsrEngine(AsrEngine):
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
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as error:
            raise ImportError(
                "Qwen3-ASR 未安装。请运行 scripts\\install_qwen3_asr.bat，"
                "或在 subtitle 环境执行：pip install qwen-asr"
            ) from error
        model_id = getattr(self.cfg, "qwen3_asr_model", "Qwen/Qwen3-ASR-0.6B")
        device = getattr(self.cfg, "qwen3_asr_device", "cuda")
        seconds = float(getattr(self.cfg, "qwen3_asr_segment_seconds", 2.0))
        quantization = getattr(self.cfg, "qwen3_asr_quantization", "none")
        self._segment_samples = max(1600, int(seconds * 16000))
        if quantization not in {"none", "4bit"}:
            raise ValueError("Qwen3-ASR 量化模式仅支持 none 或 4bit")
        if quantization == "4bit":
            if device == "cpu":
                raise ValueError("Qwen3-ASR 4bit 量化仅支持 CUDA；CPU 请使用原始精度或 faster-whisper INT8。")
            try:
                import bitsandbytes  # noqa: F401
            except ImportError as error:
                raise ImportError(
                    "Qwen3-ASR 4bit 量化需要 bitsandbytes。请执行：pip install bitsandbytes"
                ) from error
        dtype = torch.float32 if device == "cpu" else torch.bfloat16
        device_map = "cpu" if device == "cpu" else f"{device}:0"
        precision = "4bit" if quantization == "4bit" else str(dtype).replace("torch.", "")
        print(f"[qwen3_asr] 从 ModelScope 下载并加载 {model_id} (device={device}, {precision})...")
        model_path = download_modelscope(model_id, "Qwen3-ASR")
        model_kwargs = dict(
            dtype=dtype, device_map=device_map,
            max_inference_batch_size=1, max_new_tokens=128,
        )
        if quantization == "4bit":
            model_kwargs["load_in_4bit"] = True
        self.model = Qwen3ASRModel.from_pretrained(
            model_path, **model_kwargs,
        )
        print(f"[qwen3_asr] 就绪，段时长={seconds}s")

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
        try:
            language = getattr(self.cfg, "qwen3_asr_language", "Chinese")
            result = self.model.transcribe(audio=(audio, 16000), language=language)
            text = result[0].text.strip() if result else ""
            if text:
                self.on_result(text, is_final=True, source=self.source, spk_id=None)
        except Exception as error:
            print(f"[qwen3_asr] 推理异常: {error}")

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
