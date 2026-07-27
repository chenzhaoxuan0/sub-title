import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from subtitle.asr.faster_whisper_engine import FasterWhisperEngine
from subtitle.asr.funasr_nano_engine import FunAsrNanoEngine
from subtitle.asr.qwen3_asr_engine import Qwen3AsrEngine
from subtitle.asr.sensevoice_engine import _strip_tags


class FakeWhisperModel:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, **kwargs):
        del audio, kwargs
        self.calls += 1
        return [], None


class FakeNanoModel:
    def __init__(self):
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return [{"text": "歌词"}]


class FakeQwenResult:
    text = "新模型字幕"


class FakeQwenModel:
    def transcribe(self, **kwargs):
        del kwargs
        return [FakeQwenResult()]


class SegmentedAsrTests(unittest.TestCase):
    def test_qwen3_loads_from_modelscope_local_snapshot(self):
        loaded_paths = []
        qwen_model = MagicMock()
        qwen_model.from_pretrained.side_effect = lambda path, **kwargs: loaded_paths.append(path)
        torch = SimpleNamespace(float32="float32", bfloat16="bfloat16")
        cfg = SimpleNamespace(
            qwen3_asr_model="Qwen/Qwen3-ASR-0.6B",
            qwen3_asr_device="cpu",
            qwen3_asr_segment_seconds=2.0,
        )
        with patch.dict("sys.modules", {
            "torch": torch,
            "qwen_asr": SimpleNamespace(Qwen3ASRModel=qwen_model),
        }), patch("subtitle.asr.qwen3_asr_engine.download_modelscope", return_value="C:/models/qwen") as download:
            Qwen3AsrEngine(cfg, lambda *args, **kwargs: None).load()
        download.assert_called_once_with("Qwen/Qwen3-ASR-0.6B", "Qwen3-ASR")
        self.assertEqual(loaded_paths, ["C:/models/qwen"])

    def test_qwen3_4bit_forwards_quantization_to_runtime(self):
        qwen_model = MagicMock()
        # 本测试前提：CUDA 可用（device=cuda + 4bit 应当成功转发到运行时）。
        # 给 mock torch 补齐 resolve_device/cuda_available 会探测的属性。
        torch = SimpleNamespace(
            float32="float32",
            bfloat16="bfloat16",
            cuda=SimpleNamespace(is_available=lambda: True),
        )
        cfg = SimpleNamespace(
            qwen3_asr_model="Qwen/Qwen3-ASR-0.6B",
            qwen3_asr_device="cuda",
            qwen3_asr_quantization="4bit",
            qwen3_asr_segment_seconds=2.0,
        )
        with patch.dict("sys.modules", {
            "torch": torch,
            "bitsandbytes": MagicMock(),
            "qwen_asr": SimpleNamespace(Qwen3ASRModel=qwen_model),
        }), patch("subtitle.asr.qwen3_asr_engine.download_modelscope", return_value="C:/models/qwen"):
            Qwen3AsrEngine(cfg, lambda *args, **kwargs: None).load()
        self.assertTrue(qwen_model.from_pretrained.call_args.kwargs["load_in_4bit"])

    def test_faster_whisper_loads_from_modelscope_local_snapshot(self):
        whisper_model = MagicMock()
        cfg = SimpleNamespace(
            faster_whisper_model="small",
            faster_whisper_device="cpu",
            faster_whisper_compute_type="int8",
            faster_whisper_segment_seconds=2.0,
            faster_whisper_language="zh",
            faster_whisper_beam_size=1,
            faster_whisper_vad_filter=True,
            faster_whisper_silence_threshold=0.01,
            faster_whisper_min_speech_seconds=0.1,
        )
        with patch.dict("sys.modules", {
            "faster_whisper": SimpleNamespace(WhisperModel=whisper_model),
        }), patch("subtitle.asr.faster_whisper_engine.download_modelscope", return_value="C:/models/fw") as download:
            FasterWhisperEngine(cfg, lambda *args, **kwargs: None).load()
        download.assert_called_once_with("Systran/faster-whisper-small", "faster-whisper")
        self.assertEqual(whisper_model.call_args.kwargs["model_size_or_path"], "C:/models/fw")

    def test_faster_whisper_legacy_turbo_uses_modelscope_large_v3(self):
        whisper_model = MagicMock()
        cfg = SimpleNamespace(
            faster_whisper_model="large-v3-turbo",
            faster_whisper_device="cpu",
            faster_whisper_compute_type="int8",
            faster_whisper_segment_seconds=2.0,
            faster_whisper_language="zh",
            faster_whisper_beam_size=1,
            faster_whisper_vad_filter=True,
            faster_whisper_silence_threshold=0.01,
            faster_whisper_min_speech_seconds=0.1,
        )
        with patch.dict("sys.modules", {
            "faster_whisper": SimpleNamespace(WhisperModel=whisper_model),
        }), patch("subtitle.asr.faster_whisper_engine.download_modelscope", return_value="C:/models/fw") as download:
            FasterWhisperEngine(cfg, lambda *args, **kwargs: None).load()
        download.assert_called_once_with("Systran/faster-whisper-large-v3", "faster-whisper")

    def test_sensevoice_text_removes_model_line_breaks(self):
        self.assertEqual(
            _strip_tags("<|zh|>第一行\n第二行\r\n"),
            "第一行 第二行",
        )

    def test_faster_whisper_skips_a_silent_segment(self):
        model = FakeWhisperModel()
        engine = FasterWhisperEngine(object(), lambda *args, **kwargs: None)
        engine.model = model
        engine._segment_samples = 3200
        engine.feed(np.zeros(3200, dtype=np.float32))
        self.assertEqual(model.calls, 0)

    def test_faster_whisper_transcribes_segment_with_speech(self):
        model = FakeWhisperModel()
        engine = FasterWhisperEngine(object(), lambda *args, **kwargs: None)
        engine.model = model
        engine._segment_samples = 3200
        engine.feed(np.full(1600, 0.02, dtype=np.float32))
        engine.feed(np.zeros(1600, dtype=np.float32))
        self.assertEqual(model.calls, 1)

    def test_funasr_nano_emits_segment_text(self):
        received = []
        engine = FunAsrNanoEngine(object(), lambda text, *args, **kwargs: received.append(text))
        engine.model = FakeNanoModel()
        engine._segment_samples = 1600
        engine.feed(np.full(1600, 0.02, dtype=np.float32))
        self.assertEqual(received, ["歌词"])
        self.assertEqual(engine.model.kwargs["batch_size"], 1)
        self.assertTrue(engine.model.kwargs["input"][0].endswith(".wav"))

    def test_qwen3_asr_emits_segment_text(self):
        received = []
        engine = Qwen3AsrEngine(object(), lambda text, *args, **kwargs: received.append(text))
        engine.model = FakeQwenModel()
        engine._segment_samples = 1600
        engine.feed(np.full(1600, 0.02, dtype=np.float32))
        self.assertEqual(received, ["新模型字幕"])


if __name__ == "__main__":
    unittest.main()
