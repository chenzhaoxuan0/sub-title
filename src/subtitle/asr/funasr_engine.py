"""FunASR Paraformer 流式引擎实现（事件驱动接口）。

模型：paraformer-zh-streaming（online 版）
feed 内部同步调 model.generate(cache 维持状态)，有结果就走 on_result 回调。
stop 后置 _closed，feed 直接返回；generate 异常后重置 cache。

流式标点后处理（可选，cfg.funasr_punc_enabled 开启）：
  paraformer-zh-streaming 的流式输出本身不带标点（架构决定）。
  开启后用一个独立的 realtime punc 模型（CTTransformerStreaming）给裸文本
  增量补标点：generate(input=text, cache=punc_cache) 只返回新增 token，
  已处理前缀不会漂移。punc 用独立 cache，和 ASR cache 分开。punc 异常时
  降级返回裸文本，不阻塞识别。
"""
from __future__ import annotations

import numpy as np

from .base import AsrEngine, OnResult


class FunAsrEngine(AsrEngine):
    def __init__(self, cfg, on_result: OnResult, source: str = "system"):
        super().__init__(cfg, on_result, source=source)
        self.model = None
        self.cache: dict = {}
        self._closed = False
        # 流式标点后处理（可选）
        self._punc_model = None
        self._punc_cache: dict = {}

    def load(self) -> None:
        from funasr import AutoModel
        print(f"[funasr] 加载模型 {self.cfg.model} (device={self.cfg.device})，首次会下载...")
        self.model = AutoModel(
            model=self.cfg.model,
            device=self.cfg.device,
            disable_update=getattr(self.cfg, "disable_update", True),
        )
        # 可选：流式标点后处理模型（让流式输出也带标点，支持自动分行）
        if getattr(self.cfg, "funasr_punc_enabled", False):
            punc_id = getattr(
                self.cfg, "funasr_punc_model",
                "punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727",
            )
            punc_dev = getattr(self.cfg, "funasr_punc_device", "cpu")
            print(f"[funasr] 加载流式标点模型 {punc_id} (device={punc_dev})，首次会下载...")
            self._punc_model = AutoModel(
                model=punc_id, device=punc_dev, disable_update=True,
            )
            print("[funasr] 标点模型就绪")
        print("[funasr] 模型就绪")

    def _punctuate(self, text: str) -> str:
        """增量标点：喂 delta 文本，返回补完标点的 delta（前缀不漂移）。

        realtime punc 模型内部用 cache 维持 pre_text 状态，每次只输出新增的
        带标点 token，拼接所有返回值即可重建完整带标点文本。
        """
        if not self._punc_model or not text:
            return text
        try:
            res = self._punc_model.generate(input=text, cache=self._punc_cache)
            if res and res[0].get("text"):
                return res[0]["text"]
        except Exception as e:
            print(f"[funasr] 标点异常（降级裸文本）: {e}")
        return text

    def feed(self, chunk: np.ndarray) -> None:
        if self._closed or self.model is None:
            return
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
                raw = res[0]["text"]
                text = self._punctuate(raw) if self._punc_model else raw
                self.on_result(text, False, self.source)
        except Exception as e:
            print(f"[funasr] feed 异常，重置 cache: {e}")
            self.cache = {}   # 异常后重置，避免半更新状态污染下一段

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.model is not None:
            try:
                # 喂空 final chunk 触发尾部 flush
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
            except Exception as e:
                print(f"[funasr] stop final 异常: {e}")
        self.cache = {}

    def reset(self) -> None:
        self.cache = {}
        self._punc_cache = {}
        self._closed = False
