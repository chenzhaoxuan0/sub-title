"""ASR 引擎抽象接口 —— 让模型可插拔。

先实现 FunASR，但接口设计成换 faster-whisper / SenseVoice 时不用改 pipeline。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class AsrResult:
    """单次流式推理返回。

    text: 本 chunk 新增识别出的文字（增量）
    is_final: 是否为该段的最终结果（可用于刷新/标点）
    """
    text: str
    is_final: bool = False


class AsrEngine(ABC):
    """流式 ASR 引擎接口。"""

    @abstractmethod
    def load(self) -> None:
        """加载模型（耗时操作，在推理线程启动时调一次）。"""

    @abstractmethod
    def transcribe_chunk(
        self,
        chunk: np.ndarray,
        is_final: bool = False,
    ) -> Optional[AsrResult]:
        """喂一个 16k mono float32 的 chunk，返回增量结果。"""

    def reset(self) -> None:
        """重置内部状态（开始新的一段/清空 cache）。默认实现为空。"""
