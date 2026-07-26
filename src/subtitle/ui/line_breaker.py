"""字幕自动分行器（纯逻辑，无 Qt 依赖）。

只做一件事：识别到句末标点或引擎边界时，在文本里插入换行符。

设计要点：
  - 句末标点触发换行：遇到「。！？!?…」就在标点后插 \\n
    （含中文全角 。！？… 和英文半角 !?；英文句号 . 不触发，避免误切小数/缩写/文件名）
  - 引擎边界触发换行：is_final=True（SenseVoice 段末 / Aliyun 句末）时在末尾插 \\n
  - 无标点无边界 → 原样返回。这样 FunASR 未开流式标点时（裸文本、无 final），
    不会强行在词中间切，保持和原有连续文本一致的行为
  - 纯函数式（实例只持有 enabled 开关，无跨调用状态），插入点在
    SubtitlePanel._flush_pending_text，主线程调用，无需加锁
"""
from __future__ import annotations


# 句末标点：中文（。！？…）+ 英文（!?）。
# 不含英文句号 . （会误切 3.14 / Mr. / a.py）；不含逗号、顿号等句内停顿。
_SENTENCE_END = frozenset("。！？!?…")


class LineBreaker:
    """识别到句末标点或引擎边界时插入换行。"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def feed(self, text: str, is_final: bool) -> str:
        """处理一批增量文本，返回插入换行后的文本。

        Args:
            text: 本次 flush 的增量文本（delta）。
            is_final: 本批是否含引擎句子/段落边界（True 表示一个句子/段落结束）。

        Returns:
            插入 \\n 后的文本，交给 cursor.insertText 渲染。
        """
        if not self.enabled or not text:
            return text
        out: list[str] = []
        for ch in text:
            out.append(ch)
            if ch in _SENTENCE_END:
                out.append("\n")
        result = "".join(out)
        if is_final and not result.endswith("\n"):
            result += "\n"
        return result

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
