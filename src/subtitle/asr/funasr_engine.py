"""FunASR Paraformer 流式引擎实现（事件驱动接口）。

模型：paraformer-zh-streaming（online 版）
feed 内部同步调 model.generate(cache 维持状态)，有结果就走 on_result 回调。
"""
from __future__ import annotations

import numpy as np

from .base import AsrEngine, OnResult


class FunAsrEngine(AsrEngine):
    def __init__(self, cfg, on_result: OnResult):
        super().__init__(cfg, on_result)
        self.model = None
        self.cache: dict = {}

    def load(self) -> None:
        from funasr import AutoModel
        print(f"[funasr] 加载模型 {self.cfg.model} (device={self.cfg.device})，首次会下载...")
        self.model = AutoModel(
            model=self.cfg.model,
            device=self.cfg.device,
            disable_update=getattr(self.cfg, "disable_update", True),
        )
        print("[funasr] 模型就绪")

    def feed(self, chunk: np.ndarray) -> None:
        if self.model is None:
            raise RuntimeError("模型未加载，先调 load()")
        try:
            res = self.model.generate(
                input=chunk,
                cache=self.cache,
                is_final=False,
                chunk_size=self.cfg.chunk_size,
                encoder_chunk_look_back=self.cfg.encoder_chunk_look_back,
                decoder_chunk_look_back=self.cfg.decoder_chunk_look_back,
                language="zh",
                use_itn=True,
            )
            if res and res[0].get("text"):
                self.on_result(res[0]["text"], False)
        except Exception as e:
            print(f"[funasr] feed 异常: {e}")

    def stop(self) -> None:
        # 发 final chunk 触发尾部 flush（可选，这里简单清 cache）
        try:
            if self.model is not None:
                # 喂一个空 final 触发收尾
                self.model.generate(
                    input=np.zeros(960, dtype=np.float32),
                    cache=self.cache,
                    is_final=True,
                    chunk_size=self.cfg.chunk_size,
                    encoder_chunk_look_back=self.cfg.encoder_chunk_look_back,
                    decoder_chunk_look_back=self.cfg.decoder_chunk_look_back,
                    language="zh",
                    use_itn=True,
                )
        except Exception:
            pass
        self.cache = {}

    def reset(self) -> None:
        self.cache = {}
