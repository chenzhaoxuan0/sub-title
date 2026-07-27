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

说话人区分（可选，cfg.enable_speaker_diarization 开启）：
  load 时注入 spk_model="cam++"，feed 结果里多一个 sentence_info 列表，
  每条带 spk 字段（聚类编号）。我们取最近完成句子的 spk_id 透传给 on_result。
  注意 spk_id 是"句子级"——比文字晚一两个 chunk，但字幕 UX 够用。
"""
from __future__ import annotations

from typing import Optional

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
        # 说话人区分：开 = True 时 load 会注入 spk_model="cam++"，feed 会从结果提 spk_id
        self._diarization_enabled: bool = False

    def load(self) -> None:
        from funasr import AutoModel
        self._diarization_enabled = bool(
            getattr(self.cfg, "enable_speaker_diarization", False)
        )
        model_kwargs = dict(
            model=self.cfg.model,
            device=self.cfg.device,
            disable_update=getattr(self.cfg, "disable_update", True),
        )
        if self._diarization_enabled:
            model_kwargs["spk_model"] = "cam++"
        print(
            f"[funasr] 加载模型 {self.cfg.model} (device={self.cfg.device})"
            + (" + spk_model=cam++（说话人区分）" if self._diarization_enabled else "")
            + "，首次会下载..."
        )
        self.model = AutoModel(**model_kwargs)
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
                spk_id = (
                    self._extract_latest_spk(res[0])
                    if self._diarization_enabled
                    else None
                )
                self.on_result(text, False, self.source, spk_id)
        except Exception as e:
            print(f"[funasr] feed 异常，重置 cache: {e}")
            self.cache = {}   # 异常后重置，避免半更新状态污染下一段

    def _extract_latest_spk(self, res0: dict) -> Optional[int]:
        """从 result[0] 提取最近完成句子的 spk_id。

        sentence_info 是累积的已完成的句子列表；最后一个的 spk_id 就是当前/最近说话人。
        句子级滞后于 chunk 级（一两个 chunk），但字幕 UX 上够用：
        说话人切换会反映在下一句边界处，比文字稍晚但视觉一致。
        """
        info = res0.get("sentence_info") or []
        if not info:
            return None
        last = info[-1]
        spk = last.get("spk")
        if spk is None:
            return None
        try:
            return int(spk)
        except (TypeError, ValueError):
            return None

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
