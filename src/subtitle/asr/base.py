"""ASR 引擎抽象接口 —— 事件驱动（feed 输入，on_result 回调输出）。

三个引擎（FunASR 流式 / SenseVoice 段式 / 阿里云 NLS API）统一用这个接口：
- feed(chunk) 单向喂入 16k mono float32 音频，无返回值
- 结果通过构造时传入的 on_result(text, is_final) 回调推送
- 每个引擎内部自行决定如何处理（同步/异步/攒段），对上层透明
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

import numpy as np


# 结果回调签名：(text, is_final)
OnResult = Callable[[str, bool], None]


class AsrEngine(ABC):
    """事件驱动的流式 ASR 引擎接口。"""

    def __init__(self, cfg, on_result: OnResult):
        self.cfg = cfg
        self.on_result = on_result   # 引擎识别出文字后调此回调

    @abstractmethod
    def load(self) -> None:
        """加载模型 / 建立连接（耗时，在 worker 线程启动时调一次）。"""

    @abstractmethod
    def feed(self, chunk: np.ndarray) -> None:
        """喂入一个 16k mono float32 的音频块。无返回值，结果走 on_result 回调。"""

    @abstractmethod
    def stop(self) -> None:
        """停止识别：发 final / flush 残留 / 断开连接。"""

    def reset(self) -> None:
        """重置内部状态（开始新的一段/清 cache）。默认空实现。"""
