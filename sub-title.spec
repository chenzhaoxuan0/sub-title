# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— sub-title 纯 API 模式 Windows exe。

纯 API 模式：只打包阿里云引擎 + 核心 UI/音频依赖，不含本地引擎的
funasr/torch/qwen_asr/faster_whisper（用户在「设置→引擎管理」页按需 pip 安装）。
这样 exe 体积小（~150-200MB）、构建快、跨 Windows 机器兼容性最好。

构建命令（项目根目录）：
    pyinstaller sub-title.spec --noconfirm

产物：dist/sub-title/sub-title.exe（onedir 模式，启动快、便于排错）。
"""
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# ============================================================
# 数据文件：内置主题 JSON（resource_dir() 打包后指向 _MEIPASS，需要这些文件）
# ============================================================
datas = [
    ("src/themes", "themes"),   # 内置主题预设（Light.json / pink.json）
]

# ============================================================
# hidden imports：PyInstaller 静态分析容易漏的动态导入
# ============================================================
hiddenimports = []

# ---- soundfile 的 libsndfile 原生库 + cffi 后端 ----
# _soundfile_data 包含 libsndfile_x64.dll，必须整体收集，否则读不了 wav。
datas += collect_data_files("_soundfile_data", include_py_files=False)
hiddenimports += ["_soundfile_data", "cffi", "_cffi_backend"]

# ---- soundcard：Windows 用 mediafoundation 后端（cffi 绑定，自带 pyinstaller 钩子）----
# soundcard 自带 __pyinstaller 钩子目录，PyInstaller 会自动收集；这里显式声明后端模块更稳。
hiddenimports += collect_submodules("soundcard")

# ---- psutil：硬件检测（CPU核数/内存/进程）。它的 import 在 try/except 里，
# PyInstaller 静态分析会当成可选跳过 → 必须显式声明。带平台原生库 _psutil_windows.pyd。
# 不收集会导致 exe 里内存永远显示 0GB（hardware.py 兜底成 0.0）。
hiddenimports += collect_submodules("psutil")
datas += collect_data_files("psutil")

# ---- keyring：Windows 用 WinVaultKeyring 后端（凭据管理器）----
# 必须收集 backends 子包，否则 keyring 找不到 Windows 后端，凭证退化到明文 json。
hiddenimports += collect_submodules("keyring")
datas += collect_data_files("keyring")

# ---- PySide6：Qt 插件（平台/图像格式/样式）由 PyInstaller 官方钩子自动收集 ----
# 无需手动配置 qwindows.dll 等。

# ---- 本应用的动态导入：各 ASR 引擎在 factory 里按 engine_type 延迟 import ----
# 纯 API 模式只保证 aliyun 可用，但 funasr/sensevoice/funasr_nano/qwen3_asr/faster_whisper
# 的引擎模块本身要能被「引擎管理页」探测状态（find_spec）和未来 import。这些模块是纯 Python，
# 不依赖 torch（torch import 延迟到 load()），所以收集进来不增加体积也不需要 torch。
hiddenimports += [
    "subtitle.asr.funasr_engine",
    "subtitle.asr.sensevoice_engine",
    "subtitle.asr.funasr_nano_engine",
    "subtitle.asr.funasr_nano_streaming_engine",   # nano 流式（WSL2+vLLM）Windows 侧客户端
    "subtitle.asr.wsl_nano_service",               # nano 流式 WSL 生命周期管理（wsl.exe 调用）
    "subtitle.asr.qwen3_asr_engine",
    "subtitle.asr.faster_whisper_engine",
    "subtitle.asr.aliyun_engine",
]
# ---- websockets：nano 流式 Windows 侧 WebSocket 客户端（load() 里延迟 import，
# PyInstaller 静态分析会漏）。纯 API 模式也要它——nano 流式靠它连 WSL 里的服务。 ----
hiddenimports += collect_submodules("websockets")

# ============================================================
# excludes：明确排除本地引擎的重依赖（纯 API 模式不打包这些）
# 减小体积 + 避免 torch 这种带巨量原生二进制的包被误拖入。
# 注意：排除后引擎模块仍可被 import（它们的 torch import 在 load() 里，延迟触发），
# 「引擎管理页」会用 find_spec 正确报告这些依赖缺失。
# ============================================================
excludes = [
    "torch", "torchaudio", "torchvision",
    "funasr",
    "qwen_asr",
    "faster_whisper", "ctranslate2",
    "bitsandbytes",
    "transformers",  # funasr/qwen 的传递依赖，纯 API 不需要
    "modelscope",    # 模型下载库，纯 API 不下载本地模型
    "librosa",       # 仅 resample 兜底用，soundfile 已够；可后续按需打开
    "IPython", "jupyter", "notebook",
    "matplotlib", "tkinter",
    "pytest", "unittest",
]


a = Analysis(
    ["scripts/package_exe.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir 模式：启动快、崩溃时便于查 _internal 日志；首次构建比 onefile 稍慢。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sub-title",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 压缩会触发杀软误报，且对 PySide6 二进制有兼容问题
    console=False,       # GUI 应用，无控制台（windowed 模式）
    disable_windowed_traceback=False,
    icon="assets/app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    a.zipfiles,
    a.scripts,
    name="sub-title",
)
