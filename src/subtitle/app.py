"""PyQt 应用主入口 —— pipeline + 字幕面板 + 系统托盘。

关闭窗口默认收到托盘（不退出），从托盘菜单「退出」才真正退出。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtWidgets import QApplication

from .config import load_config, DEFAULT_CONFIG_PATH
from .asr import create_engine
from .asr.base import AsrEngine
from .pipeline import SubtitlePipeline
from .ui import SubtitlePanel, TrayController, SettingsDialog

try:
    import yaml
except ImportError:
    yaml = None


class _PipelineWorker(QObject):
    started = pyqtSignal()
    failed = pyqtSignal(str)
    text = pyqtSignal(str, bool)

    def __init__(self, cfg, device_name):
        super().__init__()
        self.cfg = cfg
        self.device_name = device_name
        self.engine: AsrEngine | None = None
        self.pipeline: SubtitlePipeline | None = None

    def run(self):
        try:
            if self.device_name:
                self.cfg.audio.input_device = self.device_name
            # 引擎的 on_result 回调 → 转 Qt signal 桥接到主线程
            def on_result(text: str, is_final: bool):
                self.text.emit(text, is_final)
            self.engine = create_engine(self.cfg, on_result=on_result)
            self.pipeline = SubtitlePipeline(
                self.cfg, self.engine,
                on_text=lambda t, f: self.text.emit(t, f),
            )
            self.pipeline.start()
            self.started.emit()
        except Exception as e:
            self.failed.emit(str(e))

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.stop()
            self.pipeline = None


class SubtitleApp:
    def __init__(self):
        self.cfg = load_config()
        self.app = QApplication(sys.argv)
        # 关闭最后一个窗口也不退出程序（靠托盘维持）
        self.app.setQuitOnLastWindowClosed(False)

        self.panel = SubtitlePanel(
            self.cfg.ui,
            on_start=self._start,
            on_stop=self._stop,
            on_quit=None,                 # 保存逻辑在 _save_config 单独调
            on_geometry_changed=self._on_geometry_changed,
        )

        # 托盘
        self.tray = TrayController()
        self.tray.toggle_visibility_requested.connect(self.panel.toggle_visibility)
        self.tray.start_requested.connect(lambda: self.panel._on_start())
        self.tray.stop_requested.connect(lambda: self.panel._on_stop())
        self.tray.toggle_theme_requested.connect(self.panel._toggle_theme)
        self.tray.toggle_pin_requested.connect(self.panel._toggle_pin)
        self.tray.settings_requested.connect(self._open_settings)
        self.tray.quit_requested.connect(self._quit)

        # 窗口隐藏请求（点 ✕ 或 Alt+F4）→ 通知托盘
        self.panel.hide_requested.connect(
            lambda: self.tray.notify("sub-title", "已最小化到托盘，点击托盘图标恢复")
        )

        self._thread: QThread | None = None
        self._worker: _PipelineWorker | None = None

    # ---------- pipeline ----------
    def _start(self, device_name):
        self._stop()
        self.panel.set_status("加载模型中（首次较慢）……")
        self._thread = QThread()
        self._worker = _PipelineWorker(self.cfg, device_name)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.started.connect(self._on_started)
        self._worker.failed.connect(self._on_failed)
        self._worker.text.connect(lambda t, f: self.panel.emit_text(t, f))
        self._thread.start()

    def _on_started(self):
        self.panel.set_status("运行中 · 实时识别")
        self.tray.set_running(True)
        self.tray.notify("sub-title", "已开始实时识别")

    def _on_failed(self, msg: str):
        self.panel.set_status(f"出错：{msg}")
        self.panel._reset_buttons()
        self.tray.set_running(False)
        self._cleanup_thread()

    def _stop(self):
        if self._worker is not None:
            self._worker.stop()
        self._cleanup_thread()
        self.panel.set_status("已停止")
        self.tray.set_running(False)

    def _cleanup_thread(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None

    # ---------- 配置持久化 ----------
    def _on_geometry_changed(self, x, y, w, h, always_on_top, theme):
        self.cfg.ui.win_x = x
        self.cfg.ui.win_y = y
        self.cfg.ui.win_w = w
        self.cfg.ui.win_h = h
        self.cfg.ui.always_on_top = always_on_top
        self.cfg.ui.theme = theme

    def _save_config(self):
        if yaml is None:
            return
        try:
            import dataclasses
            data = {
                "audio": dataclasses.asdict(self.cfg.audio),
                "asr": dataclasses.asdict(self.cfg.asr),
                "ui": dataclasses.asdict(self.cfg.ui),
            }
            with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            print(f"[app] 保存配置失败: {e}")

    # ---------- 设置 ----------
    def _open_settings(self):
        """打开全功能设置对话框。传完整 cfg（含 asr），关闭后持久化。"""
        dlg = SettingsDialog(self.cfg, self.panel, parent=None)
        dlg.exec_()
        # 对话框关闭后保存配置（应用时已通过 panel setter 改了 cfg.ui）
        self._save_config()

    # ---------- 退出 ----------
    def _quit(self):
        self._save_config()
        self.tray.tray.hide()
        self.app.quit()

    def run(self):
        self.panel.show()
        self.tray.show()
        self.tray.notify("sub-title", "已启动，右键托盘图标查看菜单")
        return self.app.exec_()


def main():
    app = SubtitleApp()
    sys.exit(app.run())
