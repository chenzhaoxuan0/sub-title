"""崩溃诊断启动脚本 —— 捕获 Qt6Core 0xc0000409 崩溃前的 Python 调用栈。

用法（在 conda subtitle 环境）：
    cd C:\\Users\\chenziyu\\project\\sub-title
    set PYTHONPATH=src
    python scripts/run_crash_debug.py

原理：
- faulthandler：原生崩溃时 dump 所有线程的 Python 栈（写入 crash_trace.txt）
- threading 异常钩子：记录未处理异常
- 把崩溃时间戳和最近操作写入日志，便于对照

如果崩溃时 crash_trace.txt 有内容 → 能看到崩在哪个 Python 调用进入的 Qt6Core
如果 crash_trace.txt 为空 → 是纯 Qt6Core 内部自毁（与 Python 调用无关），需 WinDbg
"""
from __future__ import annotations

import faulthandler
import os
import sys
import signal
import threading
import time
from pathlib import Path

# 崩溃栈输出文件（与 subtitle.log 同目录，便于对照）
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "chenzhaoxuan0" / "sub-title" / "Cache"
TRACE_FILE = LOG_DIR / "crash_trace.txt"


def setup():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # 清掉旧的崩溃记录
    TRACE_FILE.write_text("", encoding="utf-8")
    # faulthandler：崩溃时把所有线程 Python 栈写到文件 + stderr
    fp = open(TRACE_FILE, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(fp)
    # 持续 dump：每 2 秒写一次心跳时间戳，崩后能看出崩在哪个时间点
    faulthandler.dump_traceback_later(timeout=2, repeat=True, file=fp, exit=False)
    fp.write(f"\n=== 崩溃诊断启动 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    fp.write(f"Python: {sys.version}\n")
    try:
        import PySide6
        fp.write(f"PySide6: {PySide6.__version__}\n")
    except Exception:
        pass
    fp.write(f"主线程 TID: {threading.get_ident()}\n\n")
    fp.flush()
    print(f"[crash-debug] 崩溃栈将写入: {TRACE_FILE}")
    print("[crash-debug] 现在正常操作程序，崩溃后查看该文件")
    return fp


def main():
    fp = setup()
    # 改变工作目录到项目根，让 subtitle 包能被找到
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    os.chdir(project_root)
    # 启动真正的应用
    from subtitle.app import SubtitleApp
    app = SubtitleApp()
    code = app.run()
    fp.write(f"\n=== 正常退出 code={code} {time.strftime('%H:%M:%S')} ===\n")
    fp.close()
    try:
        os._exit(code)
    except Exception:
        sys.exit(code)


if __name__ == "__main__":
    main()
