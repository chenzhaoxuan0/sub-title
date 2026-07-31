"""PyQt 应用主入口 v2 —— pipeline + 字幕面板 + 系统托盘 + 主题引擎 + 皮肤系统。"""
from __future__ import annotations

import logging
import os
import platform
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import load_config, DEFAULT_CONFIG_PATH, default_config_path
from .asr import create_engine
from .pipeline import SubtitlePipeline
from .translate import TranslationWorker, TranslatorError
from .ui import SubtitlePanel, TrayController, SettingsDialog, get_theme_manager
from . import credentials, paths
from . import logging_setup

logger = logging.getLogger(__name__)

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
            logger.info("迁移 %s → %s", legacy, new_path)
    # 还要扫描 .migrated 备份文件
    for backup in list(Path.cwd().glob("config.yaml.migrated*")):
        try:
            _extract_credentials_to_keyring(backup, clear_after=True)
        except Exception as e:
            logger.exception("处理 %s 失败", backup)
    # ---- 2) 当前 config.yaml 里的 AK 字段（用户从老版本升级但还没点过设置）----
    if new_path.exists():
        try:
            _extract_credentials_to_keyring(new_path, clear_after=True)
        except Exception as e:
            logger.exception("检查 config.yaml 的 AK 字段失败")


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
    logger.info("已将 %s 里的阿里云凭证迁移到系统保险箱（%s）",
                yaml_path.name, credentials.storage_location())
    if clear_after:
        try:
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            logger.exception("清理 %s 的 AK 字段失败", yaml_path)


def _apply_first_run_recommendation() -> None:
    """首次启动：检测硬件，把推荐的引擎/设备写进 config.yaml。

    判定首次启动的信号：config.yaml 不存在（load_config 在此情况下返回默认 Config）。
    非首次启动直接返回，绝不覆盖用户已选配置。

    只写 asr 段的推荐字段（engine_type + 配置覆盖），其余字段由 load_config 的
    _build 合并 dataclass 默认值。
    """
    config_file = paths.config_path()
    if config_file.exists() or yaml is None:
        return  # 非首次启动，不动

    from . import hardware
    try:
        info = hardware.detect()
        engine_type, overrides = hardware.recommend_engine(info)
    except Exception:
        logger.exception("硬件检测失败，沿用默认配置")
        return

    asr_section = {"system": {"engine_type": engine_type, **overrides}}
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump({"asr": asr_section}, f,
                           allow_unicode=True, sort_keys=False)
        gpu = f"{info['gpu_name']}({info['cuda_vram_gb']}GB)" if info["has_cuda"] else "无"
        logger.info("首次启动硬件检测：CPU=%s核 RAM=%sGB GPU=%s "
                    "AppleSilicon=%s → 推荐 %s",
                    info['cpu_cores'], info['ram_gb'], gpu,
                    info['is_apple_silicon'], engine_type)
    except Exception:
        logger.exception("写推荐配置失败")


class _PipelineWorker(QObject):
    """按启用情况创建并管理 1~2 个单源 pipeline（电脑声音 / 麦克风）。

    每个 pipeline 拥有独立的 capture + engine 实例，source 标签随结果回调透传，
    让两路字幕在 UI 里按来源区分展示。两路互不影响：一路出错只报错该路，另一路继续。
    """
    started = Signal()
    failed = Signal(str)
    # text 4 参数：(text, is_final, source, spk_id) —— spk_id 是 Optional[int]，
    # Qt Signal 不支持 Optional，用 object 兼容 None（funasr + cam++ 才有值，其他引擎 None）。
    text = Signal(str, bool, str, object)
    audio_level = Signal(float, float, str)  # (rms, peak, source)

    def __init__(self, cfg, device_name, mic_device_name,
                 sys_enabled=None, mic_enabled=None):
        super().__init__()
        self.cfg = cfg
        self.device_name = device_name
        self.mic_device_name = mic_device_name
        # None 时回落到 cfg 里的值（兼容只传设备名的调用方）
        self.sys_enabled = cfg.audio.system_audio_enabled if sys_enabled is None else bool(sys_enabled)
        self.mic_enabled = cfg.audio.mic_enabled if mic_enabled is None else bool(mic_enabled)
        self._pipelines: list[SubtitlePipeline] = []

    def _make_pipeline(self, source: str, device_name) -> SubtitlePipeline:
        """创建一个单源 pipeline。source 决定 capture_kind + engine 的来源标签。"""
        # 形参名必须叫 source（不是 src）：base.py 的 OnResult 契约是
        # (text, is_final, source, spk_id)，多个引擎（aliyun/faster_whisper/
        # sensevoice）用关键字 source=self.source 调用，名字对不上会报
        # 'unexpected keyword argument source'（合并说话人区分功能后引入）。
        def on_result(text: str, is_final: bool, source: str = source, spk_id=None):
            self.text.emit(text, is_final, source, spk_id)
        engine = create_engine(self.cfg, on_result=on_result, source=source)
        return SubtitlePipeline(
            self.cfg, engine,
            on_text=lambda t, f, s, spk: self.text.emit(t, f, s, spk),
            on_audio_level=lambda rms, peak, s: self.audio_level.emit(rms, peak, s),
            source=source,
            capture_kind=source,
            device_name=device_name,
        )

    def run(self):
        try:
            if self.device_name:
                self.cfg.audio.input_device = self.device_name
            if self.mic_device_name:
                self.cfg.audio.mic_device = self.mic_device_name
            # 同步当前启用状态回 cfg（供 _save_config 持久化）
            self.cfg.audio.system_audio_enabled = self.sys_enabled
            self.cfg.audio.mic_enabled = self.mic_enabled

            # 至少要开一路
            if not self.sys_enabled and not self.mic_enabled:
                raise RuntimeError("电脑声音和麦克风都未启用，请至少开启一路输入源")

            if self.sys_enabled:
                self._pipelines.append(self._make_pipeline("system", self.device_name))
            if self.mic_enabled:
                self._pipelines.append(self._make_pipeline("mic", self.mic_device_name))

            # 逐个启动；任一路启动失败 → 整体失败（已启动的会在外层 stop 里被回收）
            for p in self._pipelines:
                p.start()
            self.started.emit()
        except Exception as e:
            self.failed.emit(str(e))

    def stop(self):
        # 反向停止（后启动的先停），逐个 join
        for p in reversed(self._pipelines):
            try:
                p.stop()
            except Exception as e:
                logger.exception("pipeline.stop 异常")
        self._pipelines.clear()


class SubtitleApp:
    def __init__(self):
        # 启动时做一次性迁移：
        # 1) 老位置的 config.yaml（项目根 / CWD）→ 用户数据目录
        # 2) 老 config.yaml 里的 AK 字段 → 系统 keyring
        _migrate_on_startup()
        _apply_first_run_recommendation()   # 首次启动按硬件写推荐引擎/设备

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

        # 翻译协调器：接 ASR 定稿句 → 后台翻译 → 译文写进 panel 下方独立行。
        # 译文与原文各行出字、互不干扰（翻译后台线程池，不阻塞 ASR）。
        # start/stop 与识别同生命周期（_on_started / _stop 里调）。
        self._translator = TranslationWorker(
            self.cfg, on_error=lambda msg: self.panel.set_status(msg)
        )
        self._translator.translation_done.connect(
            lambda o, t, s: self.panel.emit_translation(o, t, s)
        )
        # 启动时按配置决定译文区是否显示（翻译关闭 = 单行原状，零回归）
        self.panel.set_translation_enabled(self.cfg.translate.enabled)
        # 同步译文样式镜像到 ui_cfg（panel 读 ui_cfg.translation_*）
        self.cfg.ui.translation_font_scale = self.cfg.translate.translation_font_scale
        self.cfg.ui.translation_color = self.cfg.translate.translation_color
        self.panel.set_translation_style(
            font_scale=self.cfg.translate.translation_font_scale,
            color=self.cfg.translate.translation_color or None,
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
    def _start(self, device_name, mic_device_name=None,
               sys_enabled=None, mic_enabled=None):
        """启动识别。device_name=电脑声音设备，mic_device_name=麦克风设备。
        sys_enabled / mic_enabled 控制各路是否启用（None 时用 cfg 里的当前值）。"""
        # 防重入：快速连点开始时忽略后续
        if self._starting:
            return
        if not self._check_optional_asr_dependencies(sys_enabled, mic_enabled):
            return
        self._starting = True
        try:
            self._stop()
            parts = []
            if sys_enabled if sys_enabled is not None else self.cfg.audio.system_audio_enabled:
                parts.append("电脑声音")
            if mic_enabled if mic_enabled is not None else self.cfg.audio.mic_enabled:
                parts.append("麦克风")
            label = " + ".join(parts) if parts else "识别"
            self.panel.set_status(f"加载模型中（{label}，首次较慢）……")
            self._thread = QThread()
            self._worker = _PipelineWorker(
                self.cfg, device_name, mic_device_name,
                sys_enabled=sys_enabled, mic_enabled=mic_enabled,
            )
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.started.connect(self._on_started)
            self._worker.failed.connect(self._on_failed)
            # 字幕：按 source 上色展示，spk_id 透传到 panel（panel 决定是否渲染显示名）
            self._worker.text.connect(lambda t, f, s, spk: self.panel.emit_text(t, f, s, spk))
            # 皮肤触发器：对 source 透明（合并文本触发，不破坏现有皮肤配置，不需要 spk）
            self._worker.text.connect(lambda t, f, s, spk: self.skin_runtime.on_text(t, f))
            # 翻译：仅定稿句（is_final=True）送翻译协调器（partial 不翻，避免频繁重译）。
            # 翻译后台线程池异步执行，返回后独立推进译文区，与原文互不干扰。
            if self.cfg.translate.enabled:
                self._worker.text.connect(lambda t, f, s, spk: self._translator.feed(t, s) if f else None)
            self._worker.audio_level.connect(lambda r, p, s: self.skin_runtime.on_audio_level(r, p))
            self._thread.start()
        finally:
            self._starting = False

    def _check_optional_asr_dependencies(self, sys_enabled, mic_enabled) -> bool:
        """Show an actionable prompt before a missing optional engine reaches a worker thread."""
        sources = []
        if self.cfg.audio.system_audio_enabled if sys_enabled is None else sys_enabled:
            sources.append(self.cfg.asr.system)
        if self.cfg.audio.mic_enabled if mic_enabled is None else mic_enabled:
            sources.append(self.cfg.asr.mic)
        if not any(profile.engine_type == "qwen3_asr" for profile in sources):
            return True
        from .asr.qwen3_asr_engine import qwen3_asr_available
        if qwen3_asr_available():
            return True
        import platform
        # Windows 有现成 .bat 脚本可一键装；macOS/Linux 直接给 pip 指令。
        if platform.system() == "Windows":
            script = Path(__file__).resolve().parents[2] / "scripts" / "install_qwen3_asr.bat"
            install_hint = (
                "请先关闭本程序，运行以下脚本，完成后重新打开程序：\n"
                f"{script}\n\n"
                "或在 subtitle Conda 环境中执行：pip install qwen-asr"
            )
        else:
            install_hint = (
                "请先关闭本程序，在终端执行以下命令，完成后重新打开程序：\n"
                "pip install qwen-asr"
            )
        message = QMessageBox(self.panel)
        message.setIcon(QMessageBox.Information)
        message.setWindowTitle("需要安装 Qwen3-ASR")
        message.setText("Qwen3-ASR 是可选模型，当前环境尚未安装。")
        message.setInformativeText(install_hint)
        message.exec()
        self.panel.set_status("Qwen3-ASR 未安装，请运行安装命令")
        return False

    def _on_started(self):
        # 检测 factory 是否因流式服务不可用而把 Nano 流式静默降级为段式
        # （factory 在降级时给对应 source 的 AsrConfig 打 _nano_streaming_fallback 标记，
        # 这是运行期属性，不会写进 config.yaml）。检测到就在状态栏提示一次。
        fallback_sources = [
            name for name, prof in (("🔊", self.cfg.asr.system), ("🎤", self.cfg.asr.mic))
            if getattr(prof, "_nano_streaming_fallback", False)
        ]
        if fallback_sources:
            suffix = "（" + "/".join(fallback_sources) + " 流式不可用，已降级段式）"
        else:
            suffix = ""
        # 拆分模式（句号换行 + 当前 append-only + 历史纠错）：只对 funasr nano WSL 流式
        # 且未降级的 source 生效；其他引擎走原 interim 覆盖。每次启动按当前引擎重算。
        split_sources = set()
        for name, prof in (("system", self.cfg.asr.system), ("mic", self.cfg.asr.mic)):
            if (getattr(prof, "engine_type", "") == "funasr_nano"
                    and getattr(prof, "funasr_nano_mode", "") == "streaming"
                    and not getattr(prof, "_nano_streaming_fallback", False)):
                split_sources.add(name)
        self.panel.set_split_sources(split_sources)
        self.panel.set_status(f"运行中 · 实时识别{suffix}")
        self.tray.set_running(True)
        self.tray.notify("sub-title", "已开始实时识别")
        self.skin_runtime.on_recognition_start()
        # 启动翻译器（与识别同生命周期）。翻译器构造失败（如 Azure 没 key）→ 状态栏
        # 提示一次，不中断识别（用户仍能用原文字幕）。返回 False = 翻译未启用，跳过。
        if self.cfg.translate.enabled:
            try:
                self._translator.start()
            except TranslatorError as e:
                self.panel.set_status(f"翻译未启用：{e}")
            except Exception as e:
                logger.exception("翻译器启动异常")
                self.panel.set_status(f"翻译启动异常：{e}")

    def _on_failed(self, msg: str):
        self.panel.set_status(f"出错：{msg}")
        self.panel._reset_buttons()
        self.tray.set_running(False)
        self._cleanup_thread()

    def _stop(self):
        was_running = self._worker is not None
        if self._worker is not None:
            self._worker.stop()    # pipeline.stop：发哨兵 + join 推理线程（推理线程内 engine.stop）
        # 停翻译器（关线程池，释放连接）。与 pipeline.stop 对称，避免后台线程残留。
        try:
            self._translator.stop()
        except Exception as e:
            logger.exception("翻译器停止异常")
        self._cleanup_thread()
        self.panel.set_split_sources(set())
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
                logger.warning("worker 线程 12s 后仍未退出")
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
            logger.exception("保存配置失败")

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
        dlg.skin_editor_requested.connect(self._open_skin_editor)
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
            logger.exception("刷新皮肤菜单失败")

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
            logger.exception("退出时 _stop 异常")
        # 释放被 WSL 里 nano 流式 vLLM 占住的显存（约 13GB）。必须在 _force_exit
        # (os._exit) 之前发信号，否则进程被强杀来不及清理。
        # 按配置 ui.wsl_shutdown_on_quit 选策略：
        #   True → wsl --shutdown（100% 释放，但关整个 WSL，殃及其他 WSL 程序）；
        #   False（默认）→ SIGINT 优雅退出（不关 WSL，WSL 进程异步释放 1-3 分钟），
        #                  仅在用过流式（显存 >8GB）时发，没用过不动 WSL。
        # _quit 用异步 SIGINT（不阻塞 os._exit）；同步等退出走 stop_server（设置面板停止/
        # 启动前清理用），这里不调。万一 SIGINT 没释放干净，下次 start_server 幽灵检测提示。
        try:
            from .asr.wsl_nano_service import WslNanoService
            svc = WslNanoService()
            if getattr(self.cfg.ui, "wsl_shutdown_on_quit", False):
                logger.info("退出：配置了退出时关闭 WSL，执行 wsl --shutdown")
                svc.shutdown_wsl()
            elif svc.gpu_mem_heavily_used():
                logger.info("退出：发 SIGINT 让 vLLM 优雅退出（1-3 分钟异步释放）")
                svc.request_graceful_shutdown()
        except Exception as e:
            logger.exception("退出时清理 WSL nano 异常")
        self.skin_runtime.disable()
        self._save_config()
        # 显式关闭所有顶层窗口（panel + 非模态设置/皮肤编辑器）。
        # 之前托盘退出只调 app.quit()，但 panel 没被关闭 → setQuitOnLastWindowClosed
        # 是 False，窗口和命令行黑框会一直挂着；这里强制全关，让退出彻底。
        try:
            for w in list(QApplication.topLevelWidgets()):
                if w is not None and not getattr(w, "_app_closing", False):
                    w._app_closing = True
                    w.close()
        except Exception as e:
            logger.exception("关闭顶层窗口异常")
        self.tray.tray.hide()
        self.app.quit()

    def run(self):
        self.panel.show()
        self.tray.show()
        self.tray.notify("sub-title", "已启动，右键托盘图标查看菜单")
        # Ctrl+C（SIGINT）兜底：默认 Python 收到 SIGINT 会抛 KeyboardInterrupt 直接
        # 中断，不走 _quit → 不停 WSL 里的 vLLM → 显存泄漏。这里捕获后转交 _quit
        # （含 stop_server 释放显存）。signal handler 里不能直接操作 Qt 对象，用
        # singleShot 排到事件循环主线程执行。SIGTERM 在 Windows 基本不会触发，顺带
        # 注册以防部分环境。aboutToQuit → os._exit 跳过 atexit，所以必须靠 _quit 同步清理。
        import signal
        from PySide6.QtCore import QTimer
        def _sig_to_quit(*_):
            QTimer.singleShot(0, self._quit)
        for _sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(_sig, _sig_to_quit)
            except (ValueError, OSError):
                pass  # 非主线程或信号不可注册
        # 兜底：Qt 事件循环退出后，若后台 C 扩展线程（soundcard WASAPI /
        # faster-whisper CUDA）卡住导致 sys.exit 无法正常返回，这里强制结束进程，
        # 保证「托盘退出 / 工具栏 X 退出 / Ctrl+C」都能让窗口 + 命令行黑框一起消失。
        self.app.aboutToQuit.connect(self._force_exit)
        return self.app.exec_()

    @staticmethod
    def _force_exit():
        """事件循环即将退出时强制终止进程，避免后台线程卡死导致残留。"""
        try:
            os._exit(0)
        except Exception:
            pass


def main():
    _setup_console_io()
    # 预热重库：在单线程期（Qt 未起、worker 线程未建）完成 scipy/numpy 的延迟
    # docstring 解析。这些库内部用 inspect.getsource + linecache + tokenize 解析文档，
    # 而该链路非线程安全——若推迟到 worker 线程（engine.load → funasr/librosa → scipy）
    # 首次触发，会与主线程 Qt 事件循环并发，linecache 竞态导致 Qt6Core.dll 内存访问违例
    # 崩溃（0xc0000409）。启动早期单线程预热一次，让缓存就绪，规避运行期竞态。
    _warmup_heavy_libs()
    app = SubtitleApp()
    code = app.run()
    # app.exec_() 正常返回（未被 aboutToQuit 的 os._exit 终止）时的兜底
    try:
        os._exit(code)
    except Exception:
        sys.exit(code)


def _warmup_heavy_libs() -> None:
    """单线程预热 scipy/numpy，让它们的延迟 docstring 解析（inspect.getsource 链路）
    在无并发下完成一次。失败不致命——预热只是规避竞态，库本身在 worker 里仍会正常加载。
    """
    import inspect
    try:
        import scipy  # noqa: F401
        # 触发一次会走到 _docscrape / inspect.getsource 的访问，让 linecache 缓存就绪
        for _name in ("scipy",):
            obj = sys.modules.get(_name)
            if obj is not None and getattr(obj, "__file__", None):
                # getsource 触发 linecache.updatecache（竞态源头），单线程跑一次即缓存
                try:
                    inspect.getsource(obj)
                except Exception:
                    pass
        import numpy  # noqa: F401
        obj = sys.modules.get("numpy")
        if obj is not None and getattr(obj, "__file__", None):
            try:
                inspect.getsource(obj)
            except Exception:
                pass
    except Exception as e:
        logger.debug("重库预热跳过（%s）— 不影响功能", e)


def _setup_console_io() -> None:
    """让中文 print 在所有运行形态下都不崩。

    两种会崩的情况：
      1. PyInstaller --windowed/--noconsole：sys.stdout/stderr 为 None，
         `print("中文")` 直接 AttributeError。
      2. Windows GBK 控制台（cp936）：print 含中文/emoji 的诊断信息时
         UnicodeEncodeError。
    对策：stdout/stderr 为 None 时接到空 sink；Windows 控制台强制 UTF-8。
    """
    class _NullStream:
        def write(self, _data):
            pass
        def flush(self):
            pass
        def isatty(self):
            return False
    if sys.stdout is None:
        sys.stdout = _NullStream()  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = _NullStream()  # type: ignore[assignment]
    # Windows 控制台默认 cp936，print 中文/emoji 会 UnicodeEncodeError。
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass  # 非 TextIOWrapper（如上面的 _NullStream 或重定向）就不动
    # 配置全局日志（文件 + 控制台）。在 stdout/stderr 修好后调，确保打包 --windowed
    # （stdout 是 NullStream）时 StreamHandler 也能挂上而不崩。
    log_path = logging_setup.configure()
    logging.getLogger(__name__).info(
        "sub-title 启动 | %s %s | 日志: %s",
        platform.system(), platform.release(), log_path,
    )
