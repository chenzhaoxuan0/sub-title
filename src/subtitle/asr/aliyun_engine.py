"""阿里云 NLS 引擎实现（事件驱动接口，原生流式）。

用 NlsSpeechTranscriber（实时语音转写）：
  - load: getToken(akid, aksecret) 换 token → 创建 transcriber + start
  - feed: float32 → int16 bytes → 按 640 字节(20ms)分包 send_audio
  - 回调 on_result_changed: 中间结果 → on_result(text, is_final=False)
  - 回调 on_sentence_end: 句子最终结果 → on_result(text, is_final=True)
  - stop: tr.stop() 等 on_completed

依赖（可选，未装时其他引擎仍可用）：
  pip install git+https://github.com/aliyun/alibabacloud-nls-python-sdk.git
"""
from __future__ import annotations

import json

import numpy as np

from .base import AsrEngine, OnResult


# 阿里云 NLS 网关（cn-shanghai）
_NLS_URL = "wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1"
# send_audio 每包字节数：16k/16bit/mono 下 20ms = 640 字节
_PACKET_BYTES = 640


class AliyunEngine(AsrEngine):
    def __init__(self, cfg, on_result: OnResult):
        super().__init__(cfg, on_result)
        self._tr = None
        self._token = None
        self._started = False

    def load(self) -> None:
        import nls  # 延迟导入：未装 nls 的用户用其他引擎不受影响

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

        # 启动转写会话（开中间结果 + 标点 + ITN）
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
        if not self._started or self._tr is None:
            raise RuntimeError("未启动，先调 load()")
        # float32 [-1,1] → int16 PCM bytes
        audio_i16 = np.clip(chunk, -1.0, 1.0)
        audio_i16 = (audio_i16 * 32767).astype(np.int16)
        pcm_bytes = audio_i16.tobytes()
        # 按 640 字节(20ms)分包发送，避免流控
        for i in range(0, len(pcm_bytes), _PACKET_BYTES):
            pkt = pcm_bytes[i:i + _PACKET_BYTES]
            try:
                self._tr.send_audio(pkt)
            except Exception as e:
                print(f"[aliyun] send_audio 异常: {e}")
                break

    def stop(self) -> None:
        if self._tr is not None and self._started:
            try:
                self._tr.stop()  # 发 StopTranscription，等 on_completed
            except Exception as e:
                print(f"[aliyun] stop 异常: {e}")
            self._started = False

    def reset(self) -> None:
        # 阿里云侧自动按句切，无需客户端重置
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
        pass  # 新句开始，无需推送

    def _on_result_changed_cb(self, msg, *args):
        text = self._extract_text(msg)
        if text:
            self.on_result(text, is_final=False)

    def _on_sentence_end_cb(self, msg, *args):
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
