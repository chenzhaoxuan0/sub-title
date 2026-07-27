"""硬件检测推荐逻辑单元测试。

跑法（subtitle conda 环境，项目根目录）：
  PYTHONPATH=src python -m unittest test.test_hardware -v
（或 cd test && PYTHONPATH=../src python -m unittest test_hardware -v）
"""
import unittest

from subtitle.hardware import describe_recommendation, recommend_engine


class RecommendEngineTests(unittest.TestCase):
    def _info(self, **overrides):
        base = {
            "os": "Windows", "cpu_cores": 8, "ram_gb": 16.0,
            "has_cuda": False, "cuda_vram_gb": 0.0, "gpu_name": "",
            "is_apple_silicon": False,
        }
        base.update(overrides)
        return base

    def test_strong_cuda_gpu_recommends_funasr(self):
        """CUDA GPU 且 VRAM>=4GB → funasr (cuda)，低延迟流式。"""
        info = self._info(has_cuda=True, cuda_vram_gb=8.0, gpu_name="RTX 4060 Ti")
        engine, overrides = recommend_engine(info)
        self.assertEqual(engine, "funasr")
        self.assertEqual(overrides, {"device": "cuda"})

    def test_cuda_low_vram_falls_back_to_sensevoice(self):
        """CUDA 但 VRAM<4GB（如 2GB 老卡）→ sensevoice cpu，paraformer 稳态要 ~3GB。"""
        info = self._info(has_cuda=True, cuda_vram_gb=2.0, gpu_name="GTX 750")
        engine, overrides = recommend_engine(info)
        self.assertEqual(engine, "sensevoice")
        self.assertEqual(overrides, {"sensevoice_device": "cpu"})

    def test_cuda_exactly_4gb_recommends_funasr(self):
        """VRAM 恰好 4GB → funasr（边界含等号）。"""
        info = self._info(has_cuda=True, cuda_vram_gb=4.0)
        engine, _ = recommend_engine(info)
        self.assertEqual(engine, "funasr")

    def test_apple_silicon_recommends_sensevoice(self):
        """Apple Silicon（无 CUDA）→ sensevoice cpu，Mac 友好。"""
        info = self._info(os="Darwin", is_apple_silicon=True, cpu_cores=8, ram_gb=16.0)
        engine, overrides = recommend_engine(info)
        self.assertEqual(engine, "sensevoice")
        self.assertEqual(overrides, {"sensevoice_device": "cpu"})

    def test_modern_cpu_windows_recommends_sensevoice(self):
        """现代 Windows CPU（无 GPU）→ sensevoice cpu。"""
        info = self._info(os="Windows", cpu_cores=8, ram_gb=16.0)
        engine, overrides = recommend_engine(info)
        self.assertEqual(engine, "sensevoice")
        self.assertEqual(overrides, {"sensevoice_device": "cpu"})

    def test_weak_machine_recommends_sensevoice(self):
        """弱机器（少核/少内存，无 GPU）→ sensevoice cpu，唯一能跑的本地。"""
        info = self._info(cpu_cores=2, ram_gb=4.0)
        engine, overrides = recommend_engine(info)
        self.assertEqual(engine, "sensevoice")
        self.assertEqual(overrides, {"sensevoice_device": "cpu"})

    def test_no_cuda_field_defaults_sensevoice(self):
        """缺 has_cuda 字段 → 安全兜底 sensevoice。"""
        info = {"cpu_cores": 8, "ram_gb": 16.0}
        engine, _ = recommend_engine(info)
        self.assertEqual(engine, "sensevoice")

    def test_weak_cpu_recommendation_mentions_api(self):
        message = describe_recommendation(self._info(cpu_cores=2, ram_gb=4.0))
        self.assertIn("阿里云 API", message)

    def test_large_gpu_recommendation_mentions_qwen_quantization(self):
        message = describe_recommendation(self._info(has_cuda=True, cuda_vram_gb=12.0))
        self.assertIn("Qwen3-ASR 1.7B", message)
        self.assertIn("4bit", message)


if __name__ == "__main__":
    unittest.main()
