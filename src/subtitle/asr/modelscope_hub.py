"""ModelScope-only model snapshot download helpers for optional ASR engines."""
from __future__ import annotations


def download_modelscope(model_id: str, label: str) -> str:
    """Download *model_id* from ModelScope and return its local snapshot path.

    Model runtimes receive a local directory deliberately: this prevents them
    from silently falling back to Hugging Face when a model is not cached.
    """
    try:
        from modelscope import snapshot_download
    except ImportError as error:
        raise ImportError(
            f"{label} 需要 modelscope 才能下载模型。请安装：pip install modelscope"
        ) from error

    try:
        return snapshot_download(model_id, revision="master")
    except Exception as error:
        raise RuntimeError(
            f"{label} 无法从 ModelScope 下载模型 {model_id}。"
            "请检查网络连接或稍后重试；本程序不会回退到 Hugging Face。"
        ) from error
