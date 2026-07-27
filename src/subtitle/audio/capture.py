"""系统声音捕获 —— 用 soundcard 库做 WASAPI loopback。

soundcard 原生支持 Windows loopback（对 default_speaker 开 recorder 即回录系统输出），
比 sounddevice/PortAudio（不支持 loopback）和手写 WASAPI（pycaw comtypes 折腾）都稳。

输出：float32, mono, target_sr，直接喂 FunASR。
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

# 注意：soundcard 的 mediafoundation 后端在 import 时会调 CoInitialize/CoInitializeEx
# 初始化当前线程的 COM（_com = _COMLibrary() 是模块级全局）。
# 如果在主线程（QApplication 所在）import，会导致 PySide6 的 OleInitialize 冲突
# （RPC_E_CHANGED_MODE 0x80010106）。所以改成延迟 import——只在采集线程里 import。


@dataclass
class CaptureDeviceInfo:
    name: str
    isloopback: bool
    is_default_output: bool


def list_loopback_devices() -> list[CaptureDeviceInfo]:
    """列出可用 loopback 源。延迟 import soundcard（避免主线程 COM 初始化冲突）。"""
    import soundcard as sc
    default_spk = sc.default_speaker().name
    out: list[CaptureDeviceInfo] = []
    for m in sc.all_microphones(include_loopback=True):
        if getattr(m, "isloopback", False):
            out.append(CaptureDeviceInfo(
                name=m.name,
                isloopback=True,
                is_default_output=(m.name == default_spk),
            ))
    return out


def _find_loopback(speaker_name_hint: Optional[str] = None):
    """选定 loopback 设备：优先匹配提示名，否则用默认输出的 loopback。延迟 import。"""
    import soundcard as sc
    mics = sc.all_microphones(include_loopback=True)
    if speaker_name_hint:
        for m in mics:
            if getattr(m, "isloopback", False) and speaker_name_hint.lower() in (m.name or "").lower():
                return m
    default_spk = sc.default_speaker().name
    for m in mics:
        if getattr(m, "isloopback", False) and m.name == default_spk:
            return m
    # 兜底：任意 loopback
    for m in mics:
        if getattr(m, "isloopback", False):
            return m
    return None


def list_microphone_devices() -> list[CaptureDeviceInfo]:
    """列出可用麦克风输入设备（非 loopback）。延迟 import soundcard。"""
    import soundcard as sc
    default_mic_name: Optional[str]
    try:
        default_mic_name = sc.default_microphone().name
    except Exception:
        default_mic_name = None
    out: list[CaptureDeviceInfo] = []
    for m in sc.all_microphones(include_loopback=False):
        # 排除 loopback（all_microphones(include_loopback=False) 理论上不含，
        # 但部分驱动会把 loopback 也列出来，这里显式过滤更稳）
        if getattr(m, "isloopback", False):
            continue
        out.append(CaptureDeviceInfo(
            name=m.name,
            isloopback=False,
            is_default_output=(m.name == default_mic_name) if default_mic_name else False,
        ))
    return out


def _find_microphone(name_hint: Optional[str] = None):
    """选定麦克风设备：优先按名匹配，否则用系统默认麦克风，兜底任意麦克风。延迟 import。"""
    import soundcard as sc
    mics = [m for m in sc.all_microphones(include_loopback=False)
            if not getattr(m, "isloopback", False)]
    if name_hint:
        for m in mics:
            if name_hint.lower() in (m.name or "").lower():
                return m
    try:
        return sc.default_microphone()
    except Exception:
        pass
    # 兜底：任意真实麦克风
    for m in mics:
        return m
    return None


class SystemAudioCapture(threading.Thread):
    """后台线程：持续把系统声音的 PCM 块塞进 queue。

    用法：
        cap = SystemAudioCapture(target_sr=16000, block_samples=9600)
        cap.start()
        chunk = cap.queue.get()   # np.ndarray float32, mono, len=block_samples(近似)
        cap.stop()
    """

    def __init__(
        self,
        target_sr: int = 16000,
        block_samples: int = 9600,
        speaker_name: Optional[str] = None,
    ):
        super().__init__(daemon=True)
        self.target_sr = target_sr
        self.block_samples = block_samples
        self.speaker_name = speaker_name
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._recorder_ctx = None
        self._mic = None
        self.actual_sr: Optional[int] = None
        self.error: Optional[str] = None

    def run(self):
        try:
            self._run_loop()
        except Exception as e:
            self.error = str(e)
            print(f"[capture] 异常: {e}")

    def stop(self):
        self._stop.set()
        # 等录音流真正关闭（rec.record 阻塞 ~0.6s），避免退出时 recorder 与解释器关闭竞态
        try:
            self.join(timeout=2)
        except Exception:
            pass

    def _run_loop(self):
        mic = _find_loopback(self.speaker_name)
        if mic is None:
            raise RuntimeError("没找到 loopback 设备，确认系统有输出设备")
        self._mic = mic
        print(f"[capture] loopback 源: {mic.name}")

        # soundcard recorder 会把数据重采样到我们指定的 samplerate
        # 用 blocksize 让每次 record 返回约 block_samples 帧
        block = self.block_samples
        with mic.recorder(samplerate=self.target_sr, channels=1, blocksize=block) as rec:
            self.actual_sr = self.target_sr
            print(f"[capture] 录音流已打开 ({self.target_sr}Hz mono, block~{block})")
            while not self._stop.is_set():
                try:
                    data = rec.record(numframes=block)
                except Exception as e:
                    if self._stop.is_set():
                        break
                    print(f"[capture] record 异常: {e}")
                    time.sleep(0.05)
                    continue
                # data: (frames, 1) float32 → (frames,)
                mono = np.asarray(data, dtype=np.float32).reshape(-1)
                try:
                    self.queue.put(mono, timeout=1)
                except queue.Full:
                    pass  # 推理跟不上时丢块，保证实时性
        print("[capture] 录音流已关闭")


class MicrophoneCapture(threading.Thread):
    """后台线程：持续把麦克风输入的 PCM 块塞进 queue。

    与 SystemAudioCapture 同构（同样的 recorder/queue/stop 协议），只是设备发现
    用 _find_microphone（非 loopback 的真实麦克风）。让 pipeline 能以统一方式驱动
    电脑声音与麦克风两路独立采集。

    用法：
        cap = MicrophoneCapture(target_sr=16000, block_samples=9600)
        cap.start()
        chunk = cap.queue.get()   # np.ndarray float32, mono
        cap.stop()
    """

    def __init__(
        self,
        target_sr: int = 16000,
        block_samples: int = 9600,
        mic_name: Optional[str] = None,
    ):
        super().__init__(daemon=True)
        self.target_sr = target_sr
        self.block_samples = block_samples
        self.mic_name = mic_name
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._recorder_ctx = None
        self._mic = None
        self.actual_sr: Optional[int] = None
        self.error: Optional[str] = None

    def run(self):
        try:
            self._run_loop()
        except Exception as e:
            self.error = str(e)
            print(f"[mic-capture] 异常: {e}")

    def stop(self):
        self._stop.set()
        try:
            self.join(timeout=2)
        except Exception:
            pass

    def _run_loop(self):
        mic = _find_microphone(self.mic_name)
        if mic is None:
            raise RuntimeError("没找到麦克风设备，确认系统已连接麦克风并授权")
        self._mic = mic
        print(f"[mic-capture] 麦克风源: {mic.name}")

        block = self.block_samples
        with mic.recorder(samplerate=self.target_sr, channels=1, blocksize=block) as rec:
            self.actual_sr = self.target_sr
            print(f"[mic-capture] 录音流已打开 ({self.target_sr}Hz mono, block~{block})")
            while not self._stop.is_set():
                try:
                    data = rec.record(numframes=block)
                except Exception as e:
                    if self._stop.is_set():
                        break
                    print(f"[mic-capture] record 异常: {e}")
                    time.sleep(0.05)
                    continue
                mono = np.asarray(data, dtype=np.float32).reshape(-1)
                try:
                    self.queue.put(mono, timeout=1)
                except queue.Full:
                    pass
        print("[mic-capture] 录音流已关闭")
