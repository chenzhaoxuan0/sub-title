"""WASAPI loopback 系统声音采集（Windows）。

sounddevice/PortAudio 在 Windows 上不支持 loopback（输出设备 max_input_channels=0），
所以这里直接用 pycaw 调 WASAPI 的 IAudioClient，开 LOOPBACK 标志回录系统输出声音。

核心流程：
  1. 取默认渲染端点（eRender/eConsole）= 系统默认扬声器/耳机
  2. Activate IAudioClient，Initialize 时带 AUDCLNT_STREAMFLAGS_LOOPBACK
  3. GetService 拿 IAudioCaptureClient
  4. 循环 GetBuffer 读出 PCM（设备原生采样率，通常 48000，立体声）
  5. 上层用 resample.py 归一化到 16k/mono/float32 喂模型

输出格式：float32, mono（已混缩），采样率为设备原生（actual_sr），由上层重采样。
"""
from __future__ import annotations

import queue
import threading
from ctypes import POINTER, cast, c_float
from dataclasses import dataclass
from typing import Optional

import numpy as np

# WASAPI 常量（不依赖 pycaw 的枚举，直接写数值更稳）
DEVICEID_LOOPBACK_RENDER = 0  # eRender
DEVICEID_ROLE_CONSOLE = 0     # eConsole
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2

# IAudioClient / IAudioCaptureClient 的 IID
IID_IAudioClient = "{1cb9ad4c-dbfa-4c32-b178-c2f568a703b2}"
IID_IAudioCaptureClient = "{c8adbd4e-7175-4aa7-b584-5fc8458d8915}"


@dataclass
class LoopbackInfo:
    sample_rate: int
    channels: int


class WasapiLoopbackCapture(threading.Thread):
    """后台线程：用 WASAPI loopback 持续采集系统声音。

    用法：
        cap = WasapiLoopbackCapture(block_seconds=0.6)
        cap.start()
        chunk = cap.queue.get()   # np.ndarray float32, mono
        cap.stop()
    """

    def __init__(self, target_sr: int = 16000, block_seconds: float = 0.6):
        super().__init__(daemon=True)
        self.target_sr = target_sr
        self.block_seconds = block_seconds
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self.info: Optional[LoopbackInfo] = None
        self._error: Optional[str] = None

    def run(self):
        try:
            self._run_loop()
        except Exception as e:
            self._error = str(e)
            print(f"[wasapi] 采集异常: {e}")

    def stop(self):
        self._stop.set()

    def _run_loop(self):
        from ctypes import windll
        from comtypes import CoInitialize, CLSCTX_ALL, GUID
        from pycaw.pycaw import AudioUtilities  # 借它的 IMMDeviceEnumerator 封装

        CoInitialize()
        try:
            enumerator = AudioUtilities.GetDeviceEnumerator()
            device = enumerator.GetDefaultAudioEndpoint(DEVICEID_LOOPBACK_RENDER, DEVICEID_ROLE_CONSOLE)
            audio_client = device.Activate(GUID(IID_IAudioClient), CLSCTX_ALL, None)

            # 先用 GetDevicePeriod / GetMixFormat 拿到系统混音格式（最稳，不会格式转换）
            # pycaw 的 IAudioClient 包装了部分方法，但 GetMixFormat 我们用底层调用
            import ctypes
            # GetMixFormat() -> WAVEFORMATEX*
            ptr = ctypes.c_void_p()
            # IAudioClient 方法表：QueryInterface,AddRef,Release,Initialize,GetBufferSize,
            # GetStreamLatency,GetCurrentPadding,IsFormatSupported,GetDevicePeriod,
            # GetMixFormat,GetDevicePeriod...
            # 用 comtypes 的接口定义更清晰，这里直接用 pycaw 已绑定的
            mix_fmt_ptr = audio_client._GetMixFormat()  # pycaw 1.x 可能没有
            # 上面不一定可用，回退到读 GetDevicePeriod
        except Exception:
            # 上面的高级封装不可靠，改用纯 ctypes comtypes 方式
            self._run_loop_lowlevel()
            return

    def _run_loop_lowlevel(self):
        """纯 WASAPI 调用，不依赖 pycaw 的高级封装。"""
        from ctypes import wintypes
        import ctypes
        from ctypes import POINTER, byref, cast, c_float, c_void_p
        from comtypes import CoInitialize, CLSCTX_ALL, GUID, HRESULT
        from comtypes.client import GetModule, CreateObject

        # 定义/加载 mmdevapi 类型库
        try:
            import comtypes.gen.MMDeviceAPILib as MMDeviceAPILib
        except ImportError:
            GetModule("C:\\Windows\\System32\\mmdevapi.dll")
            import comtypes.gen.MMDeviceAPILib as MMDeviceAPILib

        CoInitialize()
        enumerator = CreateObject(
            MMDeviceAPILib.MMDeviceEnumerator,
            interface=MMDeviceAPILib.IMMDeviceEnumerator,
        )
        device = enumerator.GetDefaultAudioEndpoint(
            MMDeviceAPILib.eRender, MMDeviceAPILib.eConsole
        )

        audio_client = device.Activate(
            GUID(IID_IAudioClient), CLSCTX_ALL, None
        )

        # 拿 mix format
        wfx_ptr = POINTER(MMDeviceAPILib.WAVEFORMATEX)()
        audio_client.GetMixFormat(byref(wfx_ptr))
        wfx = wfx_ptr.contents
        sr = wfx.nSamplesPerSec
        channels = wfx.nChannels
        bits = wfx.wBitsPerSample
        block_align = wfx.nBlockAlign
        # mix format 通常是 float32 (IEEE), bits=32
        print(f"[wasapi] mix format: {sr}Hz {channels}ch {bits}bit")

        # 用 mix format 初始化（loopback 模式）
        # hnsBufferDuration: 纳秒(100ns 单位)，给 0.5s 缓冲
        REFTIMES_PER_SEC = 10_000_000
        buffer_duration = int(self.block_seconds * REFTIMES_PER_SEC)
        audio_client.Initialize(
            MMDeviceAPILib.AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK,
            buffer_duration,
            0,
            wfx_ptr,
            GUID("{00000000-0000-0000-0000-000000000000}"),
        )

        # 拿 capture client
        capture_client = audio_client.GetService(GUID(IID_IAudioCaptureClient))

        audio_client.Start()
        self.info = LoopbackInfo(sample_rate=sr, channels=channels)
        print(f"[wasapi] loopback 已启动，采样率={sr} ch={channels}")

        # 主循环：读 buffer
        frame_size = int(sr * self.block_seconds)
        accum = np.zeros(0, dtype=np.float32)

        try:
            while not self._stop.is_set():
                packet_size = wintypes.UINT()
                # Wait for next packet
                import time
                hr = capture_client.GetNextPacketSize(byref(packet_size))
                while packet_size.value == 0 and not self._stop.is_set():
                    time.sleep(0.01)
                    capture_client.GetNextPacketSize(byref(packet_size))

                while packet_size.value > 0 and not self._stop.is_set():
                    data_ptr = POINTER(c_float)()
                    num_frames = wintypes.UINT()
                    flags = wintypes.DWORD()
                    capture_client.GetBuffer(
                        byref(data_ptr), byref(num_frames), byref(flags),
                        None, None,
                    )
                    n = num_frames.value
                    if flags.value & AUDCLNT_BUFFERFLAGS_SILENT:
                        # 静音帧：补零
                        chunk = np.zeros(n * channels, dtype=np.float32)
                    else:
                        chunk = np.ctypeslib.as_array(data_ptr, shape=(n * channels,)).copy()
                    capture_client.ReleaseBuffer(n)

                    # 多声道混缩为 mono
                    if channels > 1:
                        chunk = chunk.reshape(-1, channels).mean(axis=1)
                    accum = np.concatenate([accum, chunk.astype(np.float32)])

                    # 攒够一个块就入队
                    while len(accum) >= frame_size:
                        block = accum[:frame_size].copy()
                        accum = accum[frame_size:]
                        try:
                            self.queue.put(block, timeout=1)
                        except queue.Full:
                            pass  # 丢掉跟不上的块

                    capture_client.GetNextPacketSize(byref(packet_size))
        finally:
            try:
                audio_client.Stop()
            except Exception:
                pass
            print("[wasapi] loopback 已停止")
