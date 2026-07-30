"""全局日志配置。

之前全用 print()（91 处），打包后 --windowed 模式 print 到 NullStream 什么也留不下，
出了问题（如 vLLM 启动失败）只能看到一句干瘪的错误，无法回溯。这里统一用 logging：
  - 文件：RotatingFileHandler 落到 user_cache_dir()/subtitle.log，2MB×3 轮转（共 6MB 上限），
    永不撑爆磁盘；UTF-8，中文不乱码。
  - 控制台：保留开发时看终端的习惯，级别 INFO。
  - 转写文本走 on_result 回调，不经过 logger，不会产生巨量日志。

调用方：app._setup_console_io() 在修好 stdout/stderr 后调 configure() 一次。
各模块顶部 `logger = logging.getLogger(__name__)` 即可使用。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import user_cache_dir

# 单文件上限与保留份数：2MB × 3 = 6MB，足够回溯一次完整会话又不占空间
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3
_LOG_FILENAME = "subtitle.log"

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_configured = False
_log_path: Path | None = None


def log_file_path() -> Path:
    """日志文件路径（供 UI「打开日志」用）。configure() 前调用返回默认位置。"""
    global _log_path
    if _log_path is not None:
        return _log_path
    return user_cache_dir() / _LOG_FILENAME


def configure() -> Path:
    """配置 root logger：文件 + 控制台双输出。返回日志文件路径。

    幂等：重复调用只配一次（避免测试/重启时叠加 handler 重复输出）。
    """
    global _configured, _log_path
    if _configured:
        return log_file_path()

    cache_dir = user_cache_dir()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 目录建不了（权限/只读）：退化到只控制台输出，不崩
        cache_dir = Path("/tmp") if cache_dir.as_posix().startswith("/") else Path.home()
    _log_path = cache_dir / _LOG_FILENAME

    formatter = logging.Formatter(_LOG_FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)   # root 收全量；各 handler 各自过滤

    # 文件 handler：DEBUG 全量落盘（这是出问题时的主要排查依据）
    try:
        file_handler = RotatingFileHandler(
            _log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # 文件写不了（权限等）：跳过，仅控制台
        pass

    # 控制台 handler：INFO 起步，开发时看终端；打包 --windowed 时 stdout 是 NullStream，
    # handler 仍能挂上（写进去被丢弃），不报错。
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    _configured = True
    return _log_path
