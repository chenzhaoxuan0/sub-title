"""本地引擎安装信息数据层 —— 引擎依赖清单 + 平台/硬件感知的安装命令。

供「设置 → 引擎管理」标签页和 factory 的依赖缺失提示共用，避免安装指令
散落在多处（改一处忘改另一处）。

设计原则：
  - 纯数据 + 纯查询，不执行任何 pip、不真 import 重量级依赖。
  - 用 importlib.util.find_spec 探测「装没装」（与 settings_dialog 现有
    faster_whisper 探测一致），开销极低。
  - 安装命令按 (引擎, 平台, 有无CUDA) 三维度选最合适的。
"""
from __future__ import annotations

import importlib.util
import platform
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EngineInstallInfo:
    """单个本地引擎的安装元信息。aliyun（纯 API）不需要此结构。"""

    engine_type: str               # funasr / sensevoice / funasr_nano / qwen3_asr / faster_whisper
    display_name: str              # 卡片显示名
    description: str               # 一句话用途
    deps: tuple[str, ...]          # 必需依赖（find_spec 逐个查），如 ("funasr", "torch")
    # 安装命令字典：键 = 平台场景，值 = 终端要执行的命令（可能多行）。
    # 键约定：windows_gpu / windows_cpu / macos / linux。
    commands: dict[str, str] = field(default_factory=dict)
    approx_size: str = ""          # 安装后大致占用的磁盘空间提示


# ============================================================
# 各引擎安装信息（命令与 README / scripts/install_*.bat 保持一致）
# ============================================================
_ENGINES: dict[str, EngineInstallInfo] = {
    "funasr": EngineInstallInfo(
        engine_type="funasr",
        display_name="FunASR Paraformer",
        description="流式中文字幕，低延迟，GPU 推荐（VRAM≥4GB）。",
        deps=("funasr", "torch"),
        commands={
            "windows_gpu": "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\npip install funasr",
            "windows_cpu": "pip install torch torchaudio\npip install funasr",
            "macos": "pip install torch torchaudio\npip install funasr",
            "linux": "pip install torch torchaudio\npip install funasr",
        },
        approx_size="torch ~200MB(CPU) / ~2.5GB(GPU) + funasr ~100MB + 首次模型下载",
    ),
    "sensevoice": EngineInstallInfo(
        engine_type="sensevoice",
        display_name="SenseVoice Small",
        description="段式中文字幕，CPU 可跑，自带标点。跨平台兜底默认，Mac 友好。",
        deps=("funasr", "torch"),
        commands={
            "windows_gpu": "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\npip install funasr",
            "windows_cpu": "pip install torch torchaudio\npip install funasr",
            "macos": "pip install torch torchaudio\npip install funasr",
            "linux": "pip install torch torchaudio\npip install funasr",
        },
        approx_size="torch ~200MB(CPU) + funasr ~100MB + 首次模型下载 ~254MB",
    ),
    "funasr_nano": EngineInstallInfo(
        engine_type="funasr_nano",
        display_name="Fun-ASR-Nano",
        description="新一代中文/方言/歌词识别，段式，GPU 推荐。",
        deps=("funasr", "torch"),
        commands={
            "windows_gpu": "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\npip install funasr",
            "windows_cpu": "pip install torch torchaudio\npip install funasr",
            "macos": "pip install torch torchaudio\npip install funasr",
            "linux": "pip install torch torchaudio\npip install funasr",
        },
        approx_size="torch ~200MB(CPU) + funasr ~100MB + 首次模型下载",
    ),
    "qwen3_asr": EngineInstallInfo(
        engine_type="qwen3_asr",
        display_name="Qwen3-ASR",
        description="多语种/歌曲识别，段式；4bit 量化仅 CUDA。依赖较重。",
        deps=("qwen_asr", "torch"),
        commands={
            "windows_gpu": "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\npip install qwen-asr",
            "windows_cpu": "pip install torch torchaudio\npip install qwen-asr",
            "macos": "pip install torch torchaudio\npip install qwen-asr",
            "linux": "pip install torch torchaudio\npip install qwen-asr",
        },
        approx_size="torch ~200MB(CPU) + qwen-asr + 首次模型下载",
    ),
    "faster_whisper": EngineInstallInfo(
        engine_type="faster_whisper",
        display_name="faster-whisper",
        description="多语言(99种)+翻译，CTranslate2 后端，不依赖 torch。兼容模式。",
        deps=("faster_whisper",),
        commands={
            "windows_gpu": "pip install faster-whisper",
            "windows_cpu": "pip install faster-whisper",
            "macos": "pip install faster-whisper",
            "linux": "pip install faster-whisper",
        },
        approx_size="faster-whisper + ctranslate2 ~100MB + 首次模型下载",
    ),
}


def get_engine_install_info(engine_type: str) -> EngineInstallInfo | None:
    """取引擎安装信息。aliyun / 未知引擎返回 None（无需安装）。"""
    return _ENGINES.get(engine_type)


def all_local_engines() -> list[EngineInstallInfo]:
    """所有需要本地依赖的引擎（用于引擎管理页遍历渲染卡片）。"""
    return list(_ENGINES.values())


def _dep_installed(dep: str) -> bool:
    """单个依赖是否已装。用 find_spec 不真 import（避免拖入重量级模块）。"""
    return importlib.util.find_spec(dep) is not None


def check_engine_deps(engine_type: str) -> tuple[bool, list[str]]:
    """检查引擎依赖是否齐全。

    返回 (全部就绪, 缺失依赖名列表)。未知引擎 / aliyun 返回 (True, [])。
    注意：funasr 装了但 torch 没装时（用户 --no-deps 装的）会正确报 torch 缺失，
    这是 factory 阶段预检的关键——避免 load() 时才崩成 traceback。
    """
    info = _ENGINES.get(engine_type)
    if info is None:
        return True, []
    missing = [d for d in info.deps if not _dep_installed(d)]
    return (len(missing) == 0), missing


def _platform_key() -> str:
    """当前平台对应的命令字典键。"""
    system = platform.system()
    if system == "Windows":
        return "windows_cpu"   # has_cuda 由调用方决定是否升级到 windows_gpu
    if system == "Darwin":
        return "macos"
    return "linux"


def recommended_install_command(engine_type: str, has_cuda: bool = False) -> str:
    """返回当前平台+硬件最合适的安装命令。

    Windows + CUDA → windows_gpu（从 PyTorch CUDA 索引装 GPU 轮）；
    其余（Mac / Linux / Windows 无 N 卡）→ CPU 轮（Mac 上 CPU torch 含 MPS 后端）。
    未知引擎返回空串。
    """
    info = _ENGINES.get(engine_type)
    if info is None:
        return ""
    key = _platform_key()
    if key == "windows_cpu" and has_cuda:
        key = "windows_gpu"
    return info.commands.get(key, info.commands.get("linux", ""))


def missing_dep_hint(engine_type: str, has_cuda: bool = False) -> str | None:
    """依赖缺失时的中文提示（供 factory._missing_dep 复用，保持文案一致）。

    依赖齐全返回 None。提示里含缺失依赖名 + 平台对应的安装命令。
    """
    ready, missing = check_engine_deps(engine_type)
    if ready or not missing:
        return None
    info = _ENGINES.get(engine_type)
    name = info.display_name if info else engine_type
    cmd = recommended_install_command(engine_type, has_cuda)
    deps_str = "、".join(missing)
    return f"{name} 依赖缺失：{deps_str}。请在终端执行：\n{cmd}"


# ============================================================
# 系统环境扫描：发现用户已有的 conda/Python 环境及其本地引擎依赖
#
# 用途：纯 API 模式的 exe 内部没有 torch/funasr，但用户系统里可能装了
#       Anaconda + 完整本地引擎环境。这里通过子进程调用各环境的 python.exe
#       探测依赖（独立进程，与 exe 解释器隔离，无 ABI 崩溃风险），
#       让用户知道"本地引擎环境在哪、状态如何、怎么用 run.bat 启动"。
# ============================================================


@dataclass(frozen=True)
class CondaEnvInfo:
    """一个已发现的系统 Python/conda 环境及其依赖状态。"""

    name: str               # 环境名（subtitle / base / venv 名）
    python_path: str        # python.exe 绝对路径
    python_version: str     # "3.11.15"（探测失败为空）
    has_torch: bool = False
    has_funasr: bool = False
    has_qwen_asr: bool = False
    has_faster_whisper: bool = False
    has_cuda: bool = False
    gpu_name: str = ""
    # 探测是否出错（python.exe 不存在 / 超时 / 崩溃）
    error: str = ""

    @property
    def has_any_local_engine(self) -> bool:
        """是否含至少一个本地引擎依赖（决定要不要在 UI 高亮引导）。"""
        return self.has_torch or self.has_funasr or self.has_qwen_asr or self.has_faster_whisper

    @property
    def ready_engines(self) -> list[str]:
        """此环境可用的本地引擎显示名列表（供 UI 展示）。"""
        out = []
        if self.has_funasr and self.has_torch:
            out.append("FunASR/SenseVoice/Nano")
        if self.has_qwen_asr:
            out.append("Qwen3-ASR")
        if self.has_faster_whisper:
            out.append("faster-whisper")
        return out


# 子进程探测脚本：输出 JSON，含各依赖是否存在 + CUDA 信息。
# 用 importlib.util.find_spec 探测（不真 import 重量级模块），仅 torch 在时才 import 查 CUDA。
_PROBE_SCRIPT = (
    "import json,importlib.util as u\n"
    "deps=['torch','funasr','qwen_asr','faster_whisper']\n"
    "out={d: u.find_spec(d) is not None for d in deps}\n"
    "out['cuda']=False; out['gpu']=''\n"
    "if out['torch']:\n"
    "    try:\n"
    "        import torch\n"
    "        out['cuda']=bool(torch.cuda.is_available())\n"
    "        if out['cuda']:\n"
    "            out['gpu']=torch.cuda.get_device_name(0)\n"
    "    except Exception as e:\n"
    "        out['torch_err']=str(e)[:100]\n"
    "print(json.dumps(out))\n"
)


def _candidate_env_dirs() -> list[str]:
    """枚举可能存在 conda 环境的目录（跨平台，不依赖 PATH）。

    优先级：
      1. ~/.conda/environments.txt（conda 官方维护，最可靠，记录所有 env 路径）
      2. 常见 conda 安装根的 envs 目录 + base 根目录
    """
    import os
    import sys

    env_paths: list[str] = []
    home = os.path.expanduser("~")

    # 1) ~/.conda/environments.txt：每行一个环境根目录（含 base 和各 env）
    env_txt = os.path.join(home, ".conda", "environments.txt")
    try:
        with open(env_txt, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and os.path.isabs(line):
                    env_paths.append(line)
    except (OSError, UnicodeDecodeError):
        pass

    # 2) 候选 conda 安装根（Windows / macOS / Linux 常见位置）
    candidates: list[str] = []
    if sys.platform == "win32":
        for env in ("PROGRAMDATA", "LOCALAPPDATA", "USERPROFILE"):
            base = os.environ.get(env)
            if base:
                candidates.append(os.path.join(base, "miniconda3"))
                candidates.append(os.path.join(base, "Anaconda3"))
    else:
        candidates.append(os.path.join(home, "miniconda3"))
        candidates.append(os.path.join(home, "anaconda3"))
        candidates.append("/opt/miniconda3")
        candidates.append("/opt/anaconda3")

    for root in candidates:
        # base 根本身
        if root not in env_paths:
            env_paths.append(root)
        # envs 子目录下的各环境
        envs_dir = os.path.join(root, "envs")
        try:
            for name in os.listdir(envs_dir):
                env_paths.append(os.path.join(envs_dir, name))
        except (OSError, FileNotFoundError):
            pass

    # 去重（保序）
    seen = set()
    unique = []
    for p in env_paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _python_exe_of(env_dir: str) -> str | None:
    """环境目录里的 python 可执行文件路径（Windows .exe / Unix bin/python）。"""
    import os
    import sys

    if sys.platform == "win32":
        exe = os.path.join(env_dir, "python.exe")
    else:
        exe = os.path.join(env_dir, "bin", "python")
    return exe if os.path.isfile(exe) else None


def _probe_env(python_exe: str) -> dict:
    """用子进程调 python.exe 探测依赖。返回探测结果 dict。

    子进程独立运行，与当前解释器隔离（即使 ABI 不兼容也不影响主进程）。
    单个 env 超时（10s）不阻塞整体扫描。
    """
    import subprocess

    try:
        result = subprocess.run(
            [python_exe, "-c", _PROBE_SCRIPT],
            capture_output=True, text=True, timeout=15,
            # 避免继承当前进程的环境变量导致 conda 激活冲突
            env=None,
        )
        if result.returncode != 0:
            return {"error": f"退出码 {result.returncode}: {result.stderr.strip()[:120]}"}
        # 取最后一行非空输出（避免 stderr 干扰）
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        if not lines:
            return {"error": "无输出"}
        import json
        return json.loads(lines[-1])
    except subprocess.TimeoutExpired:
        return {"error": "探测超时(>15s)"}
    except (json.JSONDecodeError, ValueError) as e:
        return {"error": f"解析失败: {e}"}
    except (OSError, FileNotFoundError) as e:
        return {"error": f"无法启动: {e}"}


def scan_conda_envs() -> list[CondaEnvInfo]:
    """扫描系统 conda/Python 环境，返回每个环境的依赖状态。

    同步阻塞函数（会逐个启动子进程），调用方应在后台线程跑。
    返回所有发现的环境（含无依赖的），按"有本地引擎依赖的优先"排序。
    """
    import os
    import subprocess

    results: list[CondaEnvInfo] = []
    for env_dir in _candidate_env_dirs():
        python_exe = _python_exe_of(env_dir)
        if python_exe is None:
            continue
        # 环境名：目录名（subtitle / base / miniconda3）
        name = os.path.basename(env_dir.rstrip(os.sep)) or env_dir

        # 先取 python 版本（快，单独一条命令避免和依赖探测耦合）
        py_ver = ""
        try:
            r = subprocess.run(
                [python_exe, "--version"], capture_output=True, text=True, timeout=8,
            )
            # "Python 3.11.15"
            ver_line = (r.stdout or r.stderr).strip()
            py_ver = ver_line.replace("Python ", "")
        except Exception:
            pass

        probe = _probe_env(python_exe)
        if "error" in probe and not any(k in probe for k in ("torch", "funasr")):
            results.append(CondaEnvInfo(
                name=name, python_path=python_exe, python_version=py_ver, error=probe["error"],
            ))
            continue

        results.append(CondaEnvInfo(
            name=name,
            python_path=python_exe,
            python_version=py_ver,
            has_torch=bool(probe.get("torch")),
            has_funasr=bool(probe.get("funasr")),
            has_qwen_asr=bool(probe.get("qwen_asr")),
            has_faster_whisper=bool(probe.get("faster_whisper")),
            has_cuda=bool(probe.get("cuda")),
            gpu_name=str(probe.get("gpu", "")),
        ))

    # 有本地引擎依赖的排前面，方便用户一眼看到
    results.sort(key=lambda e: (not e.has_any_local_engine, e.name))
    return results

