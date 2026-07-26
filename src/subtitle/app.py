"""PyQt 应用主入口 v2 —— pipeline + 字幕面板 + 系统托盘 + 主题引擎 + 皮肤系统。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import QApplication

from .config import load_config, DEFAULT_CONFIG_PATH, default_config_path
from .asr import create_engine
from .asr.base import AsrEngine
from .pipeline import SubtitlePipeline
from .ui import SubtitlePanel, TrayController, SettingsDialog, get_theme_manager
from . import credentials, paths

try:
    import yaml
except ImportError:
    yaml = None


# ============================================================
# 启动时一次性迁移：老 config.yaml → 用户目录 + AK 字段 → keyring
# ============================================================
def _migrate_on_startup() -> None:
    """首次启动检测：
    1. 老 config.yaml（CWD / 项目根）→ 复制到用户数据目录，老文件改 .migrated 存档
    2. 老 config.yaml（已迁移或新位置）里的 AK 字段 → 挪到系统 keyring
    3. 同步：fallback 文件（如果存在）也清理
    幂等：跑过一次就不会再迁。
    """
    new_path = paths.config_path()
    # ---- 1) 文件迁移 ----
    for legacy in paths.legacy_config_candidates():
        if paths.migrate_legacy_config(legacy, new_path):
            print(f"[startup] 迁移 {legacy} → {new_path}")
    # 还要扫描 .migrated 备份文件
    for backup in list(Path.cwd().glob("config.yaml.migrated*")):
        try:
            _extract_credentials_to_keyring(backup, clear_after=True)
        except Exception as e:
            print(f"[startup] 处理 {backup} 失败: {e}")
    # ---- 2) 当前 config.yaml 里的 AK 字段（用户从老版本升级但还没点过设置）----
    if new_path.exists():
        try:
            _extract_credentials_to_keyring(new_path, clear_after=True)
        except Exception as e:
            print(f"[startup] 检查 config.yaml 的 AK 字段失败: {e}")


def _extract_credentials_to_keyring(yaml_path: Path, *, clear_after: bool) -> None:
    """从 yaml 文件里抠出 AK 字段，写到 keyring。
    如果 clear_after=True，写完后从 yaml 里删掉这些字段（避免下次再处理）。
    """
    if not yaml_path.exists() or yaml is None:
        return
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return
    asr = data.get("asr") if isinstance(data, dict) else None
    if not isinstance(asr, dict):
        return
    ak_id = asr.pop("aliyun_access_key_id", None) or ""
    ak_secret = asr.pop("aliyun_access_key_secret", None) or ""
    appkey = asr.pop("aliyun_appkey", None) or ""
    if not (ak_id or ak_secret or appkey):
        return  # 没有任何凭证，跳过
    credentials.set_aliyun(ak_id=ak_id, ak_secret=ak_secret, appkey=appkey)
    print(f"[startup] 已将 {yaml_path.name} 里的阿里云凭证迁移到系统保险箱"
          f"（{credentials.storage_location()}）")
    if clear_after:
        try:
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            print(f"[startup] 清理 {yaml_path} 的 AK 字段失败: {e}")


class _PipelineWorker(QObject):
    started = Signal()
    failed = Signal(str)
    text = Signal(str, bool)
    audio_level = Signal(float, float)

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
                on_audio_level=lambda rms, peak: self.audio_level.emit(rms, peak),
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
        # 启动时做一次性迁移：
        # 1) 老位置的 config.yaml（项目根 / CWD）→ 用户数据目录
        # 2) 老 config.yaml 里的 AK 字段 → 系统 keyring
        _migrate_on_startup()

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
        from .skin.runtime import SkinRuntime
        self.skin_runtime = SkinRuntime(self.panel)

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
        self.tray.skin_selected.connect(self._on_skin_selected)

        # 主题切换时刷新托盘
        self.panel.theme_changed.connect(self._on_panel_theme_changed)

        # 字幕窗口任意位置右键 → 弹出托盘菜单（方便不开托盘的人也能用右键操作）
        self.panel.context_menu_requested.connect(self.tray.popup_at)

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
        self._settings_dlg: SettingsDialog | None = None  # 非模态：单例
        self._skin_editor = None
        self._load_active_skin()
        self._refresh_skin_menu()

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
            self._worker.text.connect(self.skin_runtime.on_text)
            self._worker.audio_level.connect(self.skin_runtime.on_audio_level)
            self._thread.start()
        finally:
            self._starting = False

    def _on_started(self):
        self.panel.set_status("运行中 · 实时识别")
        self.tray.set_running(True)
        self.tray.notify("sub-title", "已开始实时识别")
        self.skin_runtime.on_recognition_start()

    def _on_failed(self, msg: str):
        self.panel.set_status(f"出错：{msg}")
        self.panel._reset_buttons()
        self.tray.set_running(False)
        self._cleanup_thread()

    def _stop(self):
        was_running = self._worker is not None
        if self._worker is not None:
            self._worker.stop()    # pipeline.stop：发哨兵 + join 推理线程（推理线程内 engine.stop）
        self._cleanup_thread()
        self.panel.set_status("已停止")
        self.tray.set_running(False)
        if was_running:
            self.skin_runtime.on_recognition_stop()

    def _cleanup_thread(self):
        if self._thread is not None:
            # 先断开旧 worker 信号，防止孤儿线程 emit 状态错乱
            if self._worker is not None:
                try:
                    self._worker.started.disconnect()
                    self._worker.failed.disconnect()
                    self._worker.text.disconnect()
                    self._worker.audio_level.disconnect()
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
            from .config import default_config_path
            data = {
                "audio": dataclasses.asdict(self.cfg.audio),
                "asr": dataclasses.asdict(self.cfg.asr),
                "ui": dataclasses.asdict(self.cfg.ui),
                "skin": dataclasses.asdict(self.cfg.skin),
            }
            # 写到用户数据目录（不再是项目根 / CWD）
            path = default_config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            print(f"[app] 保存配置失败: {e}")

    # ---------- 设置 ----------
    def _open_settings(self):
        """打开设置对话框 —— **非模态**，可以一边调字幕窗口一边看设置。

        - 多次点托盘"全局设置"：已开就拉到前台，不重复创建
        - 关闭时（点 X / Apply / OK）自动保存 config
        """
        if self._settings_dlg is not None and self._settings_dlg.isVisible():
            self._settings_dlg.raise_()
            self._settings_dlg.activateWindow()
            return
        dlg = SettingsDialog(self.cfg, self.panel, parent=None)
        # 关键：非模态。要在设置开着的时候能继续操作字幕窗口。
        dlg.setAttribute(Qt.WA_DeleteOnClose, False)
        dlg.finished.connect(self._on_settings_finished)
        self._settings_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_settings_finished(self, _result):
        """设置窗口关闭（含点 Apply / OK / Cancel / X）后保存配置。"""
        self._save_config()
        self._settings_dlg = None

    # ---------- 皮肤编辑器 ----------
    def _load_active_skin(self):
        if not self.cfg.skin.active_skin:
            self.cfg.skin.enabled = False
            return
        if not self.cfg.skin.enabled:
            return
        try:
            from .skin.package import skins_root
            directory = skins_root(self.cfg.skin.skins_dir) / self.cfg.skin.active_skin
            self.skin_runtime.load_directory(directory)
        except Exception as e:
            self.cfg.skin.enabled = False
            self.panel.set_status(f"皮肤加载失败，已禁用: {e}")

    def apply_skin_directory(self, directory: Path):
        self.skin_runtime.load_directory(directory)
        self.cfg.skin.enabled = True
        self.cfg.skin.active_skin = directory.name
        self._save_config()
        self._refresh_skin_menu()

    def _refresh_skin_menu(self):
        try:
            from .skin.package import list_skin_directories, skins_root
            root = skins_root(self.cfg.skin.skins_dir)
            self.tray.set_skins(
                [directory.name for directory in list_skin_directories(root)],
                self.cfg.skin.active_skin if self.cfg.skin.enabled else "",
            )
        except Exception as e:
            print(f"[skin] 刷新皮肤菜单失败: {e}")

    def _on_skin_selected(self, name: str):
        if not name:
            self.skin_runtime.disable()
            self.cfg.skin.enabled = False
            self.cfg.skin.active_skin = ""
            self._save_config()
            self._refresh_skin_menu()
            return
        try:
            from .skin.package import skins_root
            self.apply_skin_directory(skins_root(self.cfg.skin.skins_dir) / name)
        except Exception as e:
            self.panel.set_status(f"切换皮肤失败: {e}")

    def _open_skin_editor(self):
        """打开桌宠皮肤编辑器。"""
        try:
            from .skin.editor import SkinEditorWindow
            if self._skin_editor is not None and self._skin_editor.isVisible():
                self._skin_editor.raise_()
                self._skin_editor.activateWindow()
                return
            editor = SkinEditorWindow(self.cfg, self.panel, runtime=self.skin_runtime)
            editor.setAttribute(Qt.WA_DeleteOnClose, True)
            editor.skin_saved.connect(lambda value: self.apply_skin_directory(Path(value)))
            editor.destroyed.connect(lambda: setattr(self, "_skin_editor", None))
            self._skin_editor = editor
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
        self.skin_runtime.disable()
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
