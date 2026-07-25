"""FunASR Paraformer 流式引擎实现。

模型：paraformer-zh-streaming（online 版）
chunk_size=[0,10,5], stride=10*960=9600 samples≈600ms
靠 cache 字典在 chunk 间维持 KV cache。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .base import AsrEngine, AsrResult
from ..config import AsrConfig


class FunAsrEngine(AsrEngine):
    def __init__(self, cfg: AsrConfig):
        self.cfg = cfg
        self.model = None
        self.cache: dict = {}

    def load(self) -> None:
        # 延迟导入：funasr import 较慢，且依赖 torch，只在真正用时加载
        from funasr import AutoModel
        kwargs = dict(
            model=self.cfg.model,
            device=self.cfg.device,
            disable_update=self.cfg.disable_update,
        )
        print(f"[funasr] 加载模型 {self.cfg.model} (device={self.cfg.device})，首次会下载...")
        self.model = AutoModel(**kwargs)
        print("[funasr] 模型就绪")

    def transcribe_chunk(
        self,
        chunk: np.ndarray,
        is_final: bool = False,
    ) -> Optional[AsrResult]:
        if self.model is None:
            raise RuntimeError("模型未加载，先调 load()")

        res = self.model.generate(
            input=chunk,
            cache=self.cache,
            is_final=is_final,
            chunk_size=self.cfg.chunk_size,
            encoder_chunk_look_back=self.cfg.encoder_chunk_look_back,
            decoder_chunk_look_back=self.cfg.decoder_chunk_look_back,
            language="zh",
            use_itn=True,
        )
        if res and res[0].get("text"):
            return AsrResult(text=res[0]["text"], is_final=is_final)
        return None

    def reset(self) -> None:
        self.cache = {}
