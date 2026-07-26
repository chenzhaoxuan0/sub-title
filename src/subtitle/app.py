"""PyQt 应用主入口 v2 —— pipeline + 字幕面板 + 系统托盘 + 主题引擎 + 皮肤系统。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from .config import load_config, DEFAULT_CONFIG_PATH
from .asr import create_engine
from .asr.base import AsrEngine
from .pipeline import SubtitlePipeline
from .ui import SubtitlePanel, TrayController, SettingsDialog, get_theme_manager

try:
    import yaml
except ImportError:
    yaml = None


class _PipelineWorker(QObject):
    started = Signal()
    failed = Signal(str)
    text = Signal(str, bool)

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
        self.app.setQuitOnLastWindowClosed(False)

        # 初始化主题引擎
        self._theme_mgr = get_theme_manager()
        theme_name = self.cfg.ui.theme or "Dark"
        self._theme_mgr.apply_theme(theme_name)

        self.panel = SubtitlePanel(
            self.cfg.ui,
            on_start=self._start,
            on_stop=self._stop,
            on_quit=None,
            on_geometry_changed=self._on_geometry_changed,
        )

        # 托盘
        self.tray = TrayController()
        self.tray.toggle_visibility_requested.connect(self.panel.toggle_visibility)
        self.tray.start_requested.connect(lambda: self.panel._on_start())
        self.tray.stop_requested.connect(lambda: self.panel._on_stop())
        self.tray.toggle_theme_requested.connect(self.panel._cycle_theme)
        self.tray.toggle_pin_requested.connect(self.panel._toggle_pin)
        self.tray.settings_requested.connect(self._open_settings)
        self.tray.quit_requested.connect(self._quit)
        self.tray.theme_switch_requested.connect(self._on_theme_switch)
        self.tray.skin_editor_requested.connect(self._open_skin_editor)

        # 主题切换时刷新托盘
        self.panel.theme_changed.connect(self._on_panel_theme_changed)

        # 窗口隐藏请求
        self.panel.hide_requested.connect(
            lambda: self.tray.notify("sub-title", "已最小化到托盘，点击托盘图标恢复")
        )
        # 真正退出请求（点✕选退出 / do_quit）→ 走完整退出流程
        self.panel.quit_requested.connect(self._quit)

        self._thread: QThread | None = None
        self._worker: _PipelineWorker | None = None
        self._starting = False        # _start 防重入标志
        self._quitting = False        # 退出流程进行中（避免重复）

    # ---------- 主题 ----------
    def _on_theme_switch(self, name: str):
        self.panel.set_theme(name)

    def _on_panel_theme_changed(self, name: str):
        self.tray.refresh_theme()

    # ---------- pipeline ----------
    def _start(self, device_name):
        # 防重入：快速连点开始时忽略后续
        if self._starting:
            return
        self._starting = True
        try:
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
        finally:
            self._starting = False

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
            self._worker.stop()    # pipeline.stop：发哨兵 + join 推理线程（推理线程内 engine.stop）
        self._cleanup_thread()
        self.panel.set_status("已停止")
        self.tray.set_running(False)

    def _cleanup_thread(self):
        if self._thread is not None:
            # 先断开旧 worker 信号，防止孤儿线程 emit 状态错乱
            if self._worker is not None:
                try:
                    self._worker.started.disconnect()
                    self._worker.failed.disconnect()
                    self._worker.text.disconnect()
                except Exception:
                    pass
            self._thread.quit()
            # 加长等待：pipeline.stop 内部 join 推理线程最多 10s，这里给足
            if not self._thread.wait(12000):
                print("[app] 警告：worker 线程 12s 后仍未退出")
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
                "skin": dataclasses.asdict(self.cfg.skin),
            }
            with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            print(f"[app] 保存配置失败: {e}")

    # ---------- 设置 ----------
    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self.panel, parent=None)
        dlg.exec_()
        self._save_config()

    # ---------- 皮肤编辑器 ----------
    def _open_skin_editor(self):
        """打开桌宠皮肤编辑器。"""
        try:
            from .skin.editor import SkinEditorWindow
            editor = SkinEditorWindow(self.cfg, self.panel)
            editor.show()
        except ImportError as e:
            self.panel.set_status(f"皮肤编辑器加载失败: {e}")
        except Exception as e:
            self.panel.set_status(f"皮肤编辑器错误: {e}")

    # ---------- 退出 ----------
    def _quit(self):
        # 防重复（托盘退出 + panel quit_requested 可能同时触发）
        if self._quitting:
            return
        self._quitting = True
        # 关键：先停 pipeline，等所有后台线程（采集/推理/nls回调）退出，避免退出崩溃
        try:
            self._stop()
        except Exception as e:
            print(f"[app] 退出时 _stop 异常: {e}")
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
