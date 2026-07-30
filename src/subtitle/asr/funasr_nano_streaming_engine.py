"""Fun-ASR-Nano 流式引擎（WSL2 里的 funasr-realtime-server，WebSocket 客户端）。

Fun-ASR-Nano 是 LLM 架构，FunASR 内只有 AutoModelVLLM 一条推理路径、无降级；
而 vLLM 官方不支持 Windows 原生。本方案把 funasr-realtime-server 跑在 WSL2 里，
Windows 主程序只做 WebSocket 客户端连接 WSL2 的服务。

与段式引擎（funasr_nano_engine.py，走 AutoModel）互补：
  - 段式：零依赖、攒 2s 出一段，延迟高（Windows 原生可用）
  - 流式：连 WSL2 里的 realtime-server，逐字增量输出，延迟低，但需先起服务且更吃显存

服务生命周期：本引擎只连不起 —— 用户自行在 WSL2 里手动起
`funasr-realtime-server --endpoint-mode client --port 10095`，主程序连它的
ws://<host>:10095。默认 host=localhost（WSL2 localhost 转发）；连不上时 factory
降级段式（连不上时 load() 会抛 NanoStreamingUnavailable，但工厂层已先 probe 兜底）。

架构（仿 AliyunEngine 的「WS 服务端回调」模式）：
  - load() 先用裸 socket 探测 host:port（不依赖 websockets），连不上抛 NanoStreamingUnavailable
  - 连得上 → 起守护线程跑 asyncio 事件循环：建 WS、发握手 JSON、收消息解析、发 PCM
  - feed() 在 pipeline 推理线程调用：float32→int16→按 1920 字节分包→_send_q
  - WS 回调在守护线程触发：解析 text/mode/is_final → on_result（跨线程，靠 _closed 守卫）

协议（funasr-realtime-server 1.3.30 的 realtime_ws 新协议，非旧 funasr_wss_client）：
  - 激活: 发文本 "START" → 服务端 {"event":"started"}，session.is_active=True
  - 音频: 发二进制 PCM 帧（int16/16kHz/1920 字节），服务端定期返回 partial 增量
  - 响应: {"sentences":[{text,...}], "partial":"增量", "is_final":bool}
          或 {"event":"started|stopped|error"}
  - 结束: 发 "STOP"（服务端 commit pending 出最终 + 回 stopped）
⚠ 旧 funasr_wss_client 的 JSON 握手 {"mode":"2pass",...} 在本服务端已废弃——
  handle_client 只认 START/COMMIT/STOP 文本命令，旧 JSON 无 else 分支被忽略 →
  is_active=False → 音频帧全丢、永不返回结果。

依赖（可选）：websockets（funasr 传递依赖）。未装时 import 失败 → 走 factory 段式回退。
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import socket
import threading
from typing import Optional

import numpy as np

from .base import AsrEngine, OnResult
from .funasr_nano_engine import NanoStreamingUnavailable

logger = logging.getLogger(__name__)


# 16kHz 单声道 int16 PCM：官方 client 默认每帧 1920 字节 = 960 样本 = 60ms
_PCM_FRAME_BYTES = 1920
# 端口探测超时（秒）。短一点避免拖慢启动；WSL2 服务通常本地、亚秒级响应。
_PROBE_TIMEOUT = 1.5
# stop() 等待 WS 关闭的兜底超时，与 AliyunEngine 一致
_STOP_TIMEOUT = 3.0


def probe(host: str, port: int, timeout: float = _PROBE_TIMEOUT) -> bool:
    """裸 socket 探测 WSL2 里的 realtime-server 是否在监听。

    不依赖 websockets 库 —— 即便 websockets 未安装，探测本身也能给出结论，
    让 factory 据此决定是否造流式引擎。True=服务在，False=连不上。
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError, OverflowError):
        return False


class FunAsrNanoStreamingEngine(AsrEngine):
    """Fun-ASR-Nano 流式引擎，连 WSL2 里的 funasr-realtime-server。"""

    def __init__(self, cfg, on_result: OnResult, source: str = "system"):
        super().__init__(cfg, on_result, source=source)
        self._host = getattr(cfg, "funasr_nano_streaming_host", "localhost")
        self._port = int(getattr(cfg, "funasr_nano_streaming_port", 10095))
        self._language = getattr(cfg, "funasr_nano_language", "中文")
        # 跨线程通道：推理线程 feed → _send_q → 守护线程 WS 发送
        self._send_q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        # 停止信号：stop() 置位，守护线程据此退出收发循环
        self._stop_event = threading.Event()
        # asyncio 事件循环句柄（守护线程内创建），供 stop() 投递关闭协程
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None              # websockets 连接对象（守护线程内赋值）
        self._ws_thread: Optional[threading.Thread] = None
        self._closed = False         # stop 后置 True，回调与 feed 守卫
        self._ready = False          # 守护线程握手完成后置 True
        self._sent_count = 0         # 已回调的 sentence 数（响应 sentences 累计，靠它去重）

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def load(self) -> None:
        # 1) 端口探测（已由 factory 调 probe() 做过一次，这里再确认一次兜底，
        #    防止 factory → load 之间服务被关掉这种边缘情况）。
        if not probe(self._host, self._port):
            raise NanoStreamingUnavailable(
                f"WSL2 里的 realtime-server 未监听 {self._host}:{self._port}，"
                "请先在 WSL2 启动：funasr-realtime-server --endpoint-mode client"
            )
        # 2) websockets 是可选依赖（funasr 传递依赖）。未装 → 降级。
        try:
            import websockets  # noqa: F401
        except ImportError as e:
            raise NanoStreamingUnavailable(
                f"websockets 未安装（{e}）；流式模式需要它：pip install websockets"
            )
        logger.info(f"连接 {self._host}:{self._port} ...")
        # 3) 起守护线程跑 asyncio 事件循环（WS 收发都在里面）
        self._ws_thread = threading.Thread(
            target=self._thread_main, name="nano-stream-ws", daemon=True
        )
        self._ws_thread.start()
        logger.info(f"就绪（语言={self._language}，逐字流式）")

    def feed(self, chunk: np.ndarray) -> None:
        if self._closed:
            return
        # float32 [-1,1] → int16 PCM bytes（与 AliyunEngine 同一转换）
        audio_i16 = np.clip(chunk, -1.0, 1.0)
        audio_i16 = (audio_i16 * 32767).astype(np.int16)
        pcm_bytes = audio_i16.tobytes()
        # 按官方 client 的帧长分包投递；守护线程的发送协程逐帧 send
        for i in range(0, len(pcm_bytes), _PCM_FRAME_BYTES):
            self._send_q.put(pcm_bytes[i:i + _PCM_FRAME_BYTES])

    def stop(self) -> None:
        """停止：发 {is_speaking:false} 结束流 → 等 WS 关闭 → 停事件循环。

        在 pipeline 推理线程调用（与 feed 同线程，无并发）。
        """
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        # 投递结束信号给发送协程（None 哨兵）+ 唤醒可能在 get 阻塞的发送协程
        self._send_q.put(None)
        # 在事件循环里调度「发结束 JSON + 关连接」
        if self._ws_loop is not None and not self._ws_loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._shutdown_ws(), self._ws_loop
            )
        # 等守护线程退出（有超时兜底，绝不永久阻塞，与 AliyunEngine.stop 一致）
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=_STOP_TIMEOUT)

    def reset(self) -> None:
        # 清空待发队列，重置守卫；不重连（下一次 feed 会继续用现有连接）
        while True:
            try:
                self._send_q.get_nowait()
            except queue.Empty:
                break
        self._closed = False
        self._stop_event.clear()

    # ------------------------------------------------------------------
    # 守护线程：跑 asyncio 事件循环，承载所有 WS 收发
    # ------------------------------------------------------------------
    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ws_loop = loop
        try:
            loop.run_until_complete(self._ws_session())
        except Exception as e:  # 连接级异常：打印，不向上抛（feed 仍在调，但已无害）
            logger.exception(f"WS 会话异常: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _ws_session(self) -> None:
        import websockets

        uri = f"ws://{self._host}:{self._port}"
        # subprotocols=['binary'] 与官方 client 一致，确保音频按二进制帧传输
        async with websockets.connect(uri, subprotocols=["binary"]) as ws:
            self._ws = ws
            # 新协议：发 START 激活 session。旧 funasr_wss_client 的 JSON 握手
            # {"mode":"2pass",...} 在 1.3.30 已废弃——handle_client 只认 START/COMMIT/STOP
            # 文本命令，旧 JSON 无 else 分支被忽略 → is_active=False → 后续音频帧
            # "and session.is_active" 全部丢弃，永不返回结果（实测音频发出但零转写）。
            await ws.send("START")
            self._ready = True
            # 收发并行：发送协程消费 _send_q，接收协程解析服务端消息
            sender = asyncio.ensure_future(self._sender_loop(ws))
            try:
                await self._receiver_loop(ws)
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    async def _sender_loop(self, ws) -> None:
        """消费 _send_q：把推理线程投递的 PCM 包按序发出去。

        用 run_in_executor 把阻塞的 queue.get 包成协程，避免阻塞事件循环。
        None 哨兵 = stop() 投递的结束信号，收到即退出。
        """
        loop = asyncio.get_event_loop()
        while not self._stop_event.is_set():
            try:
                pkt = await loop.run_in_executor(None, self._send_q.get, True, 0.1)
            except queue.Empty:
                continue
            if pkt is None:           # 哨兵：stop() 已发独立的 _shutdown_ws，这里直接退
                break
            await ws.send(pkt)

    async def _receiver_loop(self, ws) -> None:
        """接收服务端消息，只回调定稿句子（sentences），不回调 partial。

        响应格式（realtime_ws 新协议，endpoint-mode server）：
          {"event":"started"|"stopped"}        控制事件，日志即可
          {"event":"error","error":...}        错误，记日志
          {"sentences":[{text,start,end,spk}], 服务端 VAD 分段后的定稿句（累计，靠
           "partial":"...",                     _sent_count 去重，只回调新增）
           "is_final":bool}

        ⚠ 故意忽略 partial：partial 是当前 utterance 的完整中间文本（每次 decode 重新
        生成），而 UI 是追加模式——回调 partial 会被逐条追加导致重复堆积（实测"的目标
        呢？这个目标为什么..."循环 + nano LLM 重复生成）。server 模式下服务端 VAD 自动
        分段，sentences 即逐句最终结果，UI 追加定稿句正确不重复。代价：无逐字增量
        （句末才出，但 VAD 句末延迟 < 段式 2s）。
        """
        async for raw in ws:
            if self._closed:
                break
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(msg, dict):
                continue
            event = msg.get("event")
            if event is not None:
                if event == "error":
                    logger.warning(f"服务端错误: {msg.get('error')}")
                else:
                    logger.info(f"服务端事件: {event}")
                continue
            # 只回调新增的定稿句子（sentences 累计，靠 _sent_count 去重）
            sentences = msg.get("sentences") or []
            for sent in sentences[self._sent_count:]:
                text = (sent.get("text") or "").strip()
                if text:
                    self.on_result(text, True, self.source, spk_id=None)
            self._sent_count = len(sentences)

    async def _shutdown_ws(self) -> None:
        """stop() 投递的关闭协程：发 STOP 关闭 session（新协议替代旧 is_speaking:false）。

        服务端 STOP 会 commit pending audio 出最终 + 回 {"event":"stopped"}。注意 stop()
        已置 _closed=True，_receiver_loop 随后 break，最终结果可能不被回调（用户已停止，
        定稿最后一句意义不大；如需可在 stop 前单独发 COMMIT）。
        """
        if self._ws is None:
            return
        try:
            await self._ws.send("STOP")
        except Exception:
            pass
