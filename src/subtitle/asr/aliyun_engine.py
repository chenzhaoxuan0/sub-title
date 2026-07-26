"""阿里云 NLS 引擎实现（事件驱动接口，原生流式）。

用 NlsSpeechTranscriber（实时语音转写）：
  - load: getToken(akid, aksecret) 换 token → 创建 transcriber + start
  - feed: float32 → int16 bytes → 按 640 字节(20ms)分包 send_audio
  - 回调 on_result_changed: 中间结果 → on_result(text, is_final=False)
  - 回调 on_sentence_end: 句子最终结果 → on_result(text, is_final=True)
  - stop: tr.stop() 带超时兜底 shutdown，绝不永久阻塞；回调加 _closed 守卫

线程安全说明：
  - feed 和 stop 都在 pipeline 的推理线程调用（单线程所有权），无并发
  - nls SDK 的回调在它内部线程触发，通过 _closed 守卫避免 worker 析构后回调踩空

依赖（可选，未装时其他引擎仍可用）：
  pip install git+https://github.com/aliyun/alibabacloud-nls-python-sdk.git
"""
from __future__ import annotations

import json
import threading

import numpy as np

from .base import AsrEngine, OnResult


_NLS_URL = "wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1"
_PACKET_BYTES = 640


class AliyunEngine(AsrEngine):
    def __init__(self, cfg, on_result: OnResult):
        super().__init__(cfg, on_result)
        self._tr = None
        self._token = None
        self._started = False
        self._closed = False            # stop 后置 True，回调里守卫
        self._send_fail_count = 0       # 连续 send_audio 失败计数

    def load(self) -> None:
        import nls

        akid = getattr(self.cfg, "aliyun_access_key_id", "")
        aksecret = getattr(self.cfg, "aliyun_access_key_secret", "")
        appkey = getattr(self.cfg, "aliyun_appkey", "")
        if not akid or not aksecret or not appkey:
            raise ValueError(
                "阿里云凭证未配置。请在设置里填 AccessKey ID/Secret/AppKey，"
                "或手改 config.yaml 的 asr.aliyun_* 字段。"
            )

        print("[aliyun] 获取 token...")
        self._token = nls.getToken(akid, aksecret)
        if not self._token:
            raise RuntimeError("getToken 失败，请检查 AccessKey ID/Secret")

        print("[aliyun] 创建 transcriber...")
        self._tr = nls.NlsSpeechTranscriber(
            url=_NLS_URL,
            token=self._token,
            appkey=appkey,
            on_start=self._on_start_cb,
            on_sentence_begin=self._on_sentence_begin_cb,
            on_result_changed=self._on_result_changed_cb,
            on_sentence_end=self._on_sentence_end_cb,
            on_completed=self._on_completed_cb,
            on_error=self._on_error_cb,
            on_close=self._on_close_cb,
            callback_args=[],
        )
        self._tr.start(
            aformat="pcm",
            sample_rate=16000,
            enable_intermediate_result=True,
            enable_punctuation_prediction=True,
            enable_inverse_text_normalization=True,
        )
        self._started = True
        print("[aliyun] 就绪（实时流式）")

    def feed(self, chunk: np.ndarray) -> None:
        if self._closed or not self._started or self._tr is None:
            return
        # float32 [-1,1] → int16 PCM bytes
        audio_i16 = np.clip(chunk, -1.0, 1.0)
        audio_i16 = (audio_i16 * 32767).astype(np.int16)
        pcm_bytes = audio_i16.tobytes()
        for i in range(0, len(pcm_bytes), _PACKET_BYTES):
            pkt = pcm_bytes[i:i + _PACKET_BYTES]
            try:
                self._tr.send_audio(pkt)
                self._send_fail_count = 0
            except Exception as e:
                print(f"[aliyun] send_audio 异常: {e}")
                self._send_fail_count += 1
                # 连续失败 3 次：连接已断，停止后续发送
                if self._send_fail_count >= 3:
                    print("[aliyun] 连续发送失败，标记停止")
                    self._started = False
                break

    def stop(self) -> None:
        """停止：tr.stop() 带超时兜底，绝不永久阻塞。"""
        if self._closed:
            return
        self._closed = True
        self._started = False
        if self._tr is None:
            return
        # 用一个线程跑 tr.stop（它阻塞等 on_completed），主流程限时等待
        done = threading.Event()

        def _do_stop():
            try:
                self._tr.stop()
            except Exception as e:
                print(f"[aliyun] tr.stop 异常: {e}")
            finally:
                done.set()

        threading.Thread(target=_do_stop, daemon=True).start()
        if not done.wait(timeout=3):
            # 超时：强制关闭连接
            print("[aliyun] stop 超时 3s，强制 shutdown")
            try:
                self._tr.shutdown()
            except Exception as e:
                print(f"[aliyun] shutdown 异常: {e}")

    def reset(self) -> None:
        pass

    # ---------- 回调（由 nls SDK 在其内部线程调用）----------
    def _extract_text(self, msg: str) -> str:
        try:
            payload = json.loads(msg).get("payload", {})
            return payload.get("result", "") or ""
        except Exception:
            return ""

    def _on_start_cb(self, msg, *args):
        print("[aliyun] 会话已启动")

    def _on_sentence_begin_cb(self, msg, *args):
        pass

    def _on_result_changed_cb(self, msg, *args):
        if self._closed:
            return
        text = self._extract_text(msg)
        if text:
            self.on_result(text, is_final=False)

    def _on_sentence_end_cb(self, msg, *args):
        if self._closed:
            return
        text = self._extract_text(msg)
        if text:
            self.on_result(text, is_final=True)

    def _on_completed_cb(self, msg, *args):
        print("[aliyun] 会话完成")

    def _on_error_cb(self, msg, *args):
        print(f"[aliyun] 错误: {msg}")

    def _on_close_cb(self, *args):
        print("[aliyun] 连接关闭")
        self._started = False
