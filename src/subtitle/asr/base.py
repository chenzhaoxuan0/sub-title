"""ASR 引擎抽象接口 —— 事件驱动（feed 输入，on_result 回调输出）。

三个引擎（FunASR 流式 / SenseVoice 段式 / 阿里云 NLS API）统一用这个接口：
- feed(chunk) 单向喂入 16k mono float32 音频，无返回值
- 结果通过构造时传入的 on_result(text, is_final, source) 回调推送
- 每个引擎内部自行决定如何处理（同步/异步/攒段），对上层透明

source（来源标签）由 pipeline 在创建 engine 时传入（"system" / "mic"），
引擎本身不感知来源语义，只在回调时透传——这样双输入源（麦克风 vs 电脑声音）
各自独立的 engine 实例产出的文字能被上层按来源区分展示。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

import numpy as np


# 结果回调签名：(text, is_final, source)
OnResult = Callable[[str, bool, str], None]


class AsrEngine(ABC):
    """事件驱动的流式 ASR 引擎接口。"""

    def __init__(self, cfg, on_result: OnResult, source: str = "system"):
        self.cfg = cfg
        self.on_result = on_result   # 引擎识别出文字后调此回调
        self.source = source         # 来源标签，回调时透传（"system" / "mic"）

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
