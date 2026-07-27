"""管线总装：采集线程 → 队列 → 推理线程 → engine.feed。

线程模型（单线程所有权）：
  - 推理线程**独占**引擎的所有调用（feed + stop），避免跨线程并发崩溃
  - 主线程的 stop() 只负责：发停止信号（哨兵） + join 推理线程
  - 推理线程在 _infer_loop 的 finally 里自己调 engine.stop()，保证和 feed 同线程

采集和推理通过 queue 解耦；推理和 UI 通过 on_text 回调解耦（回调里走 Qt signal 桥接主线程）。

单源管线：一个 pipeline 实例对应一个输入源（电脑声音 / 麦克风）。app 层按需创建
1~2 个 pipeline 实例，各自独立的 capture + engine，source 标签随回调透传到 UI，
让两路字幕在同一个文档里按来源区分展示（🎤/🔊）。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Union

import numpy as np

from .audio import SystemAudioCapture, MicrophoneCapture, normalize_pcm
from .config import Config
from .asr.base import AsrEngine
from .asr.factory import create_engine

# 停止哨兵：塞进 queue 唤醒推理线程的 get，让它立刻退出循环
_STOP_SENTINEL = object()


class SubtitlePipeline:
    """串起采集与推理的控制器（单源）。

    on_text(text, is_final, source): 每次有文字时调（在推理线程或 engine 内部线程，
    UI 层需自行用 Qt signal 桥接主线程）。
    on_error(msg): 采集/引擎出错时调（可选）。
    on_audio_level(rms, peak, source): 每个音频块的电平（可选）。
    """

    def __init__(
        self,
        cfg: Config,
        engine: AsrEngine,
        on_text: Optional[Callable[[str, bool, str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_audio_level: Optional[Callable[[float, float, str], None]] = None,
        *,
        source: str = "system",
        capture_kind: str = "system",
        device_name: Optional[str] = None,
    ):
        self.cfg = cfg
        self.engine = engine
        self.on_text = on_text or (lambda t, f, s: print(t, end="", flush=True))
        self.on_error = on_error or (lambda m: print(f"[pipeline:{source}] {m}"))
        self.on_audio_level = on_audio_level or (lambda rms, peak, s: None)

        self.source = source                  # 来源标签，随回调透传（"system" / "mic"）
        self.capture_kind = capture_kind      # 采集类型（"system" / "mic"）
        self.device_name = device_name        # 设备名提示（loopback 设备 / 麦克风设备）

        self.target_sr = cfg.audio.target_sample_rate
        self.chunk_samples = int(round(cfg.audio.chunk_seconds * self.target_sr))
        self.capture_block_samples = min(self.chunk_samples, max(1, self.target_sr // 10))

        self._capture: Optional[Union[SystemAudioCapture, MicrophoneCapture]] = None
        self._infer_thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._buf = np.zeros(0, dtype=np.float32)

    def start(self) -> None:
        if self._running.is_set():
            return
        # 1) 加载引擎（在 worker 线程，避免 UI 卡顿期间暴露异常）
        self.engine.load()

        # 2) 启动采集（按 capture_kind 选 loopback 或 麦克风；recorder 已重采样到 target_sr/mono）
        if self.capture_kind == "mic":
            self._capture = MicrophoneCapture(
                target_sr=self.target_sr,
                block_samples=self.capture_block_samples,
                mic_name=self.device_name,
            )
        else:
            self._capture = SystemAudioCapture(
                target_sr=self.target_sr,
                block_samples=self.capture_block_samples,
                speaker_name=self.device_name,
            )
        self._capture.start()

        # 3) 启动推理线程（独占 engine）
        self._running.set()
        self._infer_thread = threading.Thread(target=self._infer_loop, daemon=True)
        self._infer_thread.start()

    def stop(self) -> None:
        """主线程调用：发停止信号 + 等推理线程退出。
        推理线程自己在 finally 里调 engine.stop()，保证 feed/stop 同线程。
        """
        self._running.clear()
        cap = self._capture
        # 塞哨兵唤醒可能阻塞在 queue.get 的推理线程
        if cap is not None:
            try:
                cap.queue.put_nowait(_STOP_SENTINEL)
            except Exception:
                pass
        # 等推理线程退出（它会跑完 engine.stop 再退出）
        if self._infer_thread is not None:
            self._infer_thread.join(timeout=10)
            if self._infer_thread.is_alive():
                print(f"[pipeline:{self.source}] 警告：推理线程 10s 后仍未退出（engine.stop 可能卡住）")
        # 清理本地状态
        self._buf = np.zeros(0, dtype=np.float32)

    def _infer_loop(self) -> None:
        """推理线程主循环。独占 engine：feed 和 stop 都只在这里调。"""
        cap = self._capture
        src = self.source
        try:
            # 等采集流真正打开
            wait_t0 = time.time()
            while cap.actual_sr is None and self._running.is_set():
                # 采集线程异常退出检测
                if cap.error or not cap.is_alive():
                    self.on_error(f"采集线程异常：{cap.error or '已退出'}")
                    return
                if time.time() - wait_t0 > 5:
                    self.on_error("采集流 5s 未打开")
                    return
                time.sleep(0.05)
            src_sr = cap.actual_sr or self.target_sr

            while self._running.is_set():
                try:
                    raw = cap.queue.get(timeout=0.5)
                except Exception:
                    # queue.Empty：检查采集线程是否还活着
                    if cap.error or not cap.is_alive():
                        self.on_error(f"采集线程异常：{cap.error or '已退出'}")
                        return
                    continue
                # 哨兵：主线程要停止
                if raw is _STOP_SENTINEL:
                    break
                # 归一化到 16k mono float32
                chunk = normalize_pcm(raw, src_sr=src_sr, dst_sr=self.target_sr)
                if len(chunk):
                    rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
                    peak = float(np.max(np.abs(chunk)))
                    self.on_audio_level(rms, peak, src)
                self._buf = np.concatenate([self._buf, chunk])
                # 按固定长度切块喂引擎
                while len(self._buf) >= self.chunk_samples and self._running.is_set():
                    block = self._buf[: self.chunk_samples]
                    self._buf = self._buf[self.chunk_samples:]
                    self._feed_engine(block)
        finally:
            # 关键：engine.stop 在推理线程，和 feed 同线程，无并发崩溃
            try:
                self.engine.stop()
            except Exception as e:
                print(f"[pipeline:{src}] engine.stop 异常: {e}")
            # 停采集线程并等它退出
            if cap is not None:
                cap.stop()

    def _feed_engine(self, block: np.ndarray) -> None:
        """把一个 chunk 喂给引擎。结果由引擎通过 on_result 回调推送。"""
        try:
            self.engine.feed(block)
        except Exception as e:
            print(f"[pipeline:{self.source}] engine.feed 异常: {e}")
