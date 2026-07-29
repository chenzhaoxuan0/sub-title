"""WSL2 里 funasr-realtime-server 的生命周期管理（纯逻辑，不依赖 Qt）。

Fun-ASR-Nano 流式需要的 vLLM 只能在 Linux 跑，主程序在 Windows。这里封装
「在 WSL2 里装环境 + 起/停 funasr-realtime-server」的全部操作，供设置面板的
一键按钮调用。所有 WSL 操作通过 wsl.exe -- bash -lc <script> 执行。

为什么不复用 streaming engine 的 probe：那个 probe 探「服务在不在」，本模块
也用它做端口轮询；但装环境/起进程是 WSL 专属逻辑，单独成模块更清晰。

关键约束（已验证）：
  - WSL 默认发行版 Ubuntu-26.04，系统 Python 3.14 的 pip 坏了（ensurepip 缺失），
    绝不能碰系统 Python —— 必须装独立 conda 环境（Python 3.11）。
  - 起服务必须用 nohup + & 脱离 wsl.exe 进程：wsl.exe 一退出，它派生的前台进程
    会被一起收割；nohup 让服务在 WSL 里常驻，Windows 主程序退出后仍活（除非显式 stop）。
  - 服务起来后持续占显存（gpu-memory-utilization 0.8 ≈ 13GB），程序退出时由
    app._quit() 调 stop_server() 释放。

调用约定：setup_environment / start_server 耗时几分钟到几十分钟，调用方必须在
后台线程跑（见 _wsl_worker.py 的 QThread worker），绝不能阻塞 UI 线程。
"""
from __future__ import annotations

import logging
import subprocess
from typing import Callable, Optional, Tuple

from .funasr_nano_streaming_engine import probe

logger = logging.getLogger(__name__)


# ---- 常量：WSL 里的路径与命令（用 ~ 走 HOME，已验证 WSL 解析正确）----
_CONDA_HOME = "~/miniconda3"                       # miniconda 安装路径
_ENV_NAME = "subtitle-nano"                        # conda 环境名（与 Windows 侧 subtitle 区分）
_ENV_PY = f"{_CONDA_HOME}/envs/{_ENV_NAME}/bin/python"          # 环境内 python
_ENV_FUNASR_RT = f"{_CONDA_HOME}/envs/{_ENV_NAME}/bin/funasr-realtime-server"
_CONDA_BIN = f"{_CONDA_HOME}/bin/conda"
_MINICONDA_URL = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
_MINICONDA_SH = "~/Miniconda3-latest-Linux-x86_64.sh"
_PID_FILE = "~/.nano-server.pid"                   # 服务 PID 记录，用于精确停止
_LOG_FILE = "~/nano-server.log"                    # 服务 stdout/stderr 落盘，便于排障
_DEFAULT_HOST = "localhost"                         # WSL2 localhost 转发到 WSL 端口
_STARTUP_POLL_MAX = 900                             # 轮询次数：900 × 2s = 30 分钟（首次要下模型 ~2GB + vLLM 加载，3 分钟不够）
_STARTUP_POLL_INTERVAL = 2.0

ProgressCb = Callable[[str], None]


def _wsl(script: str, timeout: Optional[float] = 120) -> Tuple[int, str, str]:
    """在默认 WSL 发行版里跑一段 bash 脚本，返回 (returncode, stdout, stderr)。

    env=None 与 asr/_install.py 一致：不继承 Windows 的环境变量（PATH 之类），
    避免污染 WSL 子进程。wsl.exe 用 -- bash -lc 让脚本走登录 shell（~ / PATH 正常）。
    """
    proc = subprocess.run(
        ["wsl.exe", "--", "bash", "-lc", script],
        capture_output=True, text=True, env=None, timeout=timeout,
    )
    # 全程留痕：执行的脚本 + 返回码/输出都落日志，方便排查 vLLM 这类黑盒
    logger.debug("[wsl] exec (rc=%s): %s", proc.returncode, script[:200])
    if proc.returncode != 0 and proc.stderr.strip():
        logger.debug("[wsl] stderr: %s", proc.stderr.strip()[:300])
    return proc.returncode, proc.stdout, proc.stderr


class WslNanoService:
    """管理 WSL2 里 funasr-realtime-server 的环境与进程。

    设计为可重复实例化（无状态，所有状态在 WSL 文件系统里：conda 环境 + PID 文件）。
    """

    # ------------------------------------------------------------------
    # 探测
    # ------------------------------------------------------------------
    def status(self) -> dict:
        """返回 {installed, running}：环境是否就绪、服务是否在跑。

        纯探测，但每次会 spawn wsl.exe（WSL 冷启动可能 1-2s）—— 调用方应在后台线程跑。
        """
        installed = self._env_ready()
        running = probe(_DEFAULT_HOST, self._port(), timeout=1.0)
        return {"installed": installed, "running": running}

    def _port(self) -> int:
        """服务端口（固定 10095；config 的 port 在引擎侧读，这里保持一致）。"""
        return 10095

    def _env_ready(self) -> bool:
        """conda 环境 subtitle-nano 是否存在且关键依赖可 import。

        import 失败（环境没建好/缺包）都算未就绪，返回 False；任何 wsl 异常也返回 False。
        """
        script = f'{_ENV_PY} -c "import funasr, vllm, websockets" 2>/dev/null'
        try:
            rc, _, _ = _wsl(script, timeout=60)
        except (subprocess.SubprocessError, OSError):
            return False
        return rc == 0

    def _conda_installed(self) -> bool:
        """miniconda 是否已装。"""
        try:
            rc, _, _ = _wsl(f"{_CONDA_BIN} --version", timeout=30)
        except (subprocess.SubprocessError, OSError):
            return False
        return rc == 0

    def _env_exists(self) -> bool:
        """conda 环境 subtitle-nano 的目录是否已存在（即便依赖没装全）。

        用于断点续装：环境建了一半（目录在但 import 失败）时，_env_ready() 返回 False，
        但这里返回 True —— 据此跳过 conda create（对已存在环境会报 CondaValueError），
        直接走 pip install 补齐缺失依赖。
        """
        script = f'[ -d "{_CONDA_HOME}/envs/{_ENV_NAME}" ]'
        try:
            rc, _, _ = _wsl(script, timeout=15)
        except (subprocess.SubprocessError, OSError):
            return False
        return rc == 0

    def _resolve_cuda_home(self) -> str:
        """在 Python 侧解析环境的 CUDA_HOME 路径（不在 bash 里用 $()/变量）。

        vLLM 的 flashinfer JIT 编译采样 kernel 需要 nvcc + CUDA 头文件。WSL 系统通常
        没装 CUDA toolkit，但 pip 装的 nvidia-cuda-nvcc 把它们放在环境 site-packages/
        nvidia/cuXX 下。这里用一次 ls 列出 cu[0-9]* 目录（排除 cudnn/cusparselt），取
        版本号最大的那个，返回其绝对路径。找不到返回空串（启动时跳过 export CUDA_HOME）。

        为什么在 Python 侧做：通过 wsl.exe -- bash -lc 执行的脚本里，$ 变量/命令替换
        会被 Windows 命令行传递层吞成空（实测 FOO=bar;echo $FOO 输出空）。在 Python 里
        算好路径再字面拼进脚本，绕开这个坑。
        """
        base = f"{_CONDA_HOME}/envs/{_ENV_NAME}/lib/python3.11/site-packages/nvidia"
        try:
            rc, out, _ = _wsl(f"ls -d {base}/cu[0-9]* 2>/dev/null", timeout=15)
        except (subprocess.SubprocessError, OSError):
            return ""
        if rc != 0 or not out.strip():
            return ""
        # 多个 cuXX 取最后一个（ls 默认字典序，cu13 > cu12）
        dirs = [d.strip() for d in out.strip().splitlines() if d.strip()]
        return dirs[-1] if dirs else ""

    def _fix_cuda_version_mismatch(self) -> None:
        """统一 nvidia-cuda-* 包版本，修 flashinfer JIT 编译时的版本冲突。

        症状：vLLM 装完后 nvidia-cuda-nvcc 是 13.3，但 runtime/cupti/nvrtc 是 13.0，
        flashinfer JIT 编译 sampling kernel 时报 "CUDA compiler and CUDA toolkit
        headers are incompatible"。这里把 runtime/nvrtc/cupti 升到和 nvcc 同一版本系列。
        幂等：已统一时 pip 跳过。
        """
        logger.info("统一 nvidia-cuda-* 包版本（修 flashinfer JIT 编译冲突）…")
        # 和 nvcc(13.3.x) 同系列的 runtime/nvrtc/cupti；版本号查证自 PyPI 可用版本
        _wsl(
            f"{_ENV_PY} -m pip install -q "
            '"nvidia-cuda-runtime==13.3.29" '
            '"nvidia-cuda-nvrtc==13.3.33" '
            '"nvidia-cuda-cupti==13.3.75"',
            timeout=600,
        )

    def _patch_flashinfer_ninja(self) -> None:
        """patch flashinfer 的 run_ninja 用绝对路径调 ninja。

        症状：vLLM spawn 的 EngineCore 子进程 PATH 没含 conda bin，flashinfer
        subprocess.run(["ninja", ...]) 找不到 ninja（FileNotFoundError: 'ninja'）。
        把 "ninja" 换成绝对路径彻底绕开 PATH 继承问题。幂等：已 patch 跳过。
        """
        target = (f"{_CONDA_HOME}/envs/{_ENV_NAME}/lib/python3.11/site-packages/"
                  "flashinfer/jit/cpp_ext.py")
        # 下载一个 patch 脚本到 WSL 执行（避免 $ 转义地狱）。脚本做：
        # 找 run_ninja 里 command = [\n        "ninja", → 换成绝对路径。
        patch_script = (
            'import shutil,re\n'
            f'tgt="{target}"\n'
            'n=shutil.which("ninja")\n'
            'if not n: exit(0)\n'
            's=open(tgt).read()\n'
            'if n in s: exit(0)\n'   # 已 patch
            'm=re.search(r"(def run_ninja.*?command = \\[\\n\\s*)\\"ninja\\"",s,re.DOTALL)\n'
            'if m:\n'
            '  s=s[:m.end()-8]+n+s[m.end():]\n'
            '  open(tgt,"w").write(s)\n'
        )
        _wsl(f'{_ENV_PY} -c \'{patch_script}\'', timeout=60)

    def _fix_flashinfer_link_libs(self, cuda_home: Optional[str] = None) -> None:
        """补 flashinfer JIT 链接缺的 dev 库 symlink（修 'cannot find -lcudart/-lcuda'）。

        症状：vLLM 0.26 + flashinfer 0.6.14 在 sampler 首次调用时 JIT 编译 sampling
        kernel，ninja 链接命令硬编码 ``-L$CUDA_HOME/lib64 -L$CUDA_HOME/lib64/stubs
        -lcudart -lcuda``。但 CUDA 13 这代 pip 包把运行时库放在 ``nvidia/cuXX/lib``
        （没有 lib64 目录），只给 ``libcudart.so.13``（无 dev symlink ``libcudart.so``），
        且整个 nvidia pip 包不提供 ``libcuda.so``（driver stub 只在 WSL 系统的
        ``/usr/lib/wsl/lib``）。结果 ld 两个库都找不到 → sampling.so 编不出 →
        EngineCore 崩 → ``Engine core initialization failed``。

        修复：在 cuXX 下建 ``lib64/libcudart.so`` → ``../lib/libcudart.so.13``，
        ``lib64/stubs/libcuda.so`` → ``/usr/lib/wsl/lib/libcuda.so``。
        幂等：``ln -sf`` 覆盖已存在 symlink，已正确时无副作用。
        """
        cuda_home = cuda_home or self._resolve_cuda_home()
        if not cuda_home:
            logger.warning("未解析到 CUDA_HOME，跳过 flashinfer 链接库修复")
            return
        lib64 = f"{cuda_home}/lib64"
        cudart_so = f"{lib64}/libcudart.so"
        stubs_dir = f"{lib64}/stubs"
        libcuda_so = f"{stubs_dir}/libcuda.so"
        # 全程字面路径（不用 bash 的 $ 变量/$()——经 wsl.exe 命令行传递会被吞空，见 _wsl 注释）。
        # ln 的 target 用相对路径：libcudart.so 在 lib64/，../lib/ 即 cuXX/lib/（实测有效）。
        script = (
            f'mkdir -p "{stubs_dir}" && '
            f'ln -sf ../lib/libcudart.so.13 "{cudart_so}" && '
            f'ln -sf /usr/lib/wsl/lib/libcuda.so "{libcuda_so}"'
        )
        try:
            rc, _, err = _wsl(script, timeout=30)
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("flashinfer 链接库修复异常: %s", e)
            return
        if rc != 0:
            logger.warning(
                "flashinfer 链接库 symlink 建立失败（rc=%s）: %s", rc, err.strip()[:200]
            )

    # ------------------------------------------------------------------
    # 装环境（耗时几十分钟）
    # ------------------------------------------------------------------
    def setup_environment(self, progress: Optional[ProgressCb] = None) -> Tuple[bool, str]:
        """一键装好 WSL 里的 conda + subtitle-nano 环境。返回 (成功, 错误信息)。

        步骤：装 miniconda → 建 Python 3.11 环境 → 装 funasr/vllm/websockets。
        每步通过 progress 回报进度文本。任一步失败立即返回。
        幂等：已装好的步骤会跳过（_conda_installed / _env_ready 探测）。
        """
        def _say(msg: str) -> None:
            if progress:
                progress(msg)

        # 1) miniconda
        if self._conda_installed():
            _say("miniconda 已安装，跳过")
        else:
            _say("下载 miniconda 安装器（约 188MB）…")
            rc, _, err = _wsl(
                f"curl -sSL -o {_MINICONDA_SH} {_MINICONDA_URL}", timeout=600
            )
            if rc != 0:
                return False, f"下载 miniconda 失败: {err.strip()}"
            _say("静默安装 miniconda…")
            rc, _, err = _wsl(f"bash {_MINICONDA_SH} -b -p {_CONDA_HOME}", timeout=600)
            if rc != 0:
                return False, f"安装 miniconda 失败: {err.strip()}"
            _say("miniconda 安装完成")

        # 2) 建环境
        if self._env_ready():
            _say(f"环境 {_ENV_NAME} 已就绪，跳过")
            return True, ""

        # 断点续装：环境目录已存在（建了一半、依赖没装全）时跳过 conda create
        # （对已存在环境 create 会报 CondaValueError: prefix already exists），直接补依赖。
        if self._env_exists():
            _say(f"环境 {_ENV_NAME} 已存在（依赖不全），跳过创建直接补依赖")
        else:
            # conda 26.x 新增 Terms of Service：默认 channel（main/r）需先 accept，
            # 否则 conda create 直接拒绝（rc=1，CondaToSNonInteractiveError）。
            # 已 accept 过会秒过，幂等。在 create 之前无条件跑一次，避免装一半卡死。
            _say("接受 conda 默认 channel 服务条款…")
            for _channel in (
                "https://repo.anaconda.com/pkgs/main",
                "https://repo.anaconda.com/pkgs/r",
            ):
                _wsl(
                    f'{_CONDA_BIN} tos accept --override-channels --channel {_channel}',
                    timeout=60,
                )
            _say(f"创建 conda 环境 {_ENV_NAME}（Python 3.11）…")
            rc, _, err = _wsl(
                f'{_CONDA_BIN} create -n {_ENV_NAME} python=3.11 -y', timeout=600
            )
            if rc != 0:
                return False, f"创建环境失败: {err.strip()}"

        # 3) 装依赖（vllm 很大，这一步最久，可能 10-20 分钟）
        _say("安装 funasr / vllm / websockets（体积大，约 10-20 分钟）…")
        rc, out, err = _wsl(
            f"{_ENV_PY} -m pip install funasr vllm websockets", timeout=2400
        )
        if rc != 0:
            return False, f"安装依赖失败: {err.strip() or out.strip()[:500]}"
        # vLLM 的 nvidia-cuda-* 包版本常不统一（nvcc 13.3 vs runtime 13.0），会导致
        # flashinfer JIT 编译失败；统一版本 + patch flashinfer 用绝对路径调 ninja
        # （spawn 子进程 PATH 不含 conda bin）。详见 _fix_cuda_version_mismatch。
        _say("修复 CUDA 版本冲突 + flashinfer ninja 路径 + JIT 链接库…")
        try:
            self._fix_cuda_version_mismatch()
            self._patch_flashinfer_ninja()
            self._fix_flashinfer_link_libs()
        except Exception as e:
            logger.exception("CUDA/flashinfer 修复步骤异常（不阻断，启动时可能再报）")
        _say("依赖安装完成，环境就绪")
        return True, ""

    # ------------------------------------------------------------------
    # 起服务（耗时 1-2 分钟，vLLM 加载模型）
    # ------------------------------------------------------------------
    def start_server(self, port: int = 10095, language: str = "中文",
                     progress: Optional[ProgressCb] = None) -> Tuple[bool, str]:
        """在 WSL 里后台起 funasr-realtime-server，轮询端口直到就绪。

        nohup + & 让服务脱离 wsl.exe 常驻；PID 写 _PID_FILE 便于精确停止。
        返回 (成功, 错误信息)。已在跑则直接返回成功。
        """
        def _say(msg: str) -> None:
            if progress:
                progress(msg)

        if probe(_DEFAULT_HOST, port, timeout=1.0):
            _say("服务已在运行")
            return True, ""

        if not self._env_ready():
            return False, "WSL 环境未就绪，请先点「安装 WSL 环境」"

        # nohup 后台起服务：立即返回（&），日志重定向，stdin 接 /dev/null（否则后台
        # 进程可能因等 stdin 阻塞）。PID 用 pgrep 抓（$! 在 wsl bash -lc 下不可靠，
        # 实测抓到空值），写入 _PID_FILE 供 stop_server 精确停止。
        # 关键环境（nohup 后台进程不继承交互 shell 的 PATH，会缺这些工具导致 vLLM 初始化失败）：
        #   - PATH 前置 conda 环境 bin + cuda bin：让 ninja / nvcc 可被 flashinfer JIT 找到
        #     （缺 ninja → "FileNotFoundError: 'ninja'"；缺 nvcc → "Could not find nvcc"）
        #   - CUDA_HOME 指向环境里的 nvidia/cuXX：flashinfer 找 CUDA 头文件
        # 全部字面拼接（不用 bash 的 $PATH/$() —— 那些经 wsl.exe 命令行传递会被吞成空）。
        _say(f"启动 funasr-realtime-server（端口 {port}）…")
        env_bin = f"{_CONDA_HOME}/envs/{_ENV_NAME}/bin"
        cuda_home = self._resolve_cuda_home()
        cuda_bin = f"{cuda_home}/bin" if cuda_home else ""
        # 启动前补 flashinfer JIT 链接库 symlink（修 'cannot find -lcudart/-lcuda'，
        # 详见 _fix_flashinfer_link_libs）。老环境（setup 时还没这步）启动时兜底补上，
        # 幂等秒过；不补会导致 EngineCore 在 sampler JIT 链接阶段崩溃。
        self._fix_flashinfer_link_libs(cuda_home)
        # 字面 PATH：环境 bin + cuda bin + 标准 PATH（不含 $PATH 展开）
        path_dirs = env_bin + (f":{cuda_bin}" if cuda_bin else "") + \
            ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env_prefix = f'export PATH="{path_dirs}"; '
        if cuda_home:
            env_prefix += f'export CUDA_HOME="{cuda_home}"; '
        script = (
            f"{env_prefix}"
            f"nohup {_ENV_FUNASR_RT} --endpoint-mode client --port {port} "
            f'--language "{language}" --dtype bf16 --gpu-memory-utilization 0.8 '
            f"> {_LOG_FILE} 2>&1 < /dev/null & "
            # 等进程名出现后用 pgrep 抓 PID（比 $! 可靠）
            f"sleep 1; pgrep -f funasr-realtime-server | head -1 > {_PID_FILE}"
        )
        try:
            rc, _, err = _wsl(script, timeout=30)
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"启动命令失败: {e}"
        if rc != 0:
            return False, f"启动失败: {err.strip()}"

        # 轮询端口（vLLM 加载模型慢；首次还需下模型 ~2GB，可能十几分钟）。
        # 关键可见性：每轮 tail 日志尾部推给 UI，让用户看到 vLLM 真实进度
        # （下载百分比 / 加载步骤 / JIT 编译），而不是干等"启动中"。
        # 致命错误识别：vLLM 崩溃会打固定标记（Engine core initialization failed /
        # Ninja build failed / cannot find -l）。一旦命中，服务进程已死，继续轮询只会
        # 死等到 30 分钟超时——立即判定失败、拉完整堆栈（root cause 在 RuntimeError
        # 上方多行，tail -n 1 会漏掉）返回，避免"一直卡在启动中"。
        _say("等待 vLLM 加载模型（首次需下载模型，可能数分钟）…")
        import time
        last_tail = ""
        fatal_markers = (
            "Engine core initialization failed",
            "Ninja build failed",
            "cannot find -l",        # 链接缺库（本次根因：-lcudart/-lcuda 找不到）
        )
        for _i in range(_STARTUP_POLL_MAX):
            if probe(_DEFAULT_HOST, port, timeout=1.0):
                _say("服务就绪")
                return True, ""
            # tail 一小段（8 行）做致命错误检测：root cause 常在 RuntimeError 上方，
            # 只读最后一行会漏判（实测：最后一行是 RuntimeError，上方才是 cannot find -l）。
            _, seg_out, _ = _wsl(f"tail -n 8 {_LOG_FILE} 2>/dev/null | tr -d '\\r'", timeout=5)
            seg = seg_out.strip()
            if any(m in seg for m in fatal_markers):
                # 服务已崩：拉完整堆栈落日志 + 返回（root cause 在上方多行）
                _, full_tail, _ = _wsl(f"tail -n 80 {_LOG_FILE} 2>/dev/null", timeout=10)
                logger.error("[wsl-server] 启动失败（致命错误），日志尾部:\n%s", full_tail.strip())
                return False, (
                    "服务启动失败（vLLM 引擎初始化崩溃）。WSL 日志尾部：\n" + full_tail.strip()
                )
            # 正常进度：推最后一行给 UI + 落日志（截断防过长行刷屏）
            tail_line = seg.splitlines()[-1] if seg else ""
            if tail_line and tail_line != last_tail:
                last_tail = tail_line
                logger.info("[wsl-server] %s", tail_line[:300])
                _say(tail_line[:120])
            time.sleep(_STARTUP_POLL_INTERVAL)
        # 超时：拉日志尾部帮排障
        _, log_tail, _ = _wsl(f"tail -n 20 {_LOG_FILE}", timeout=10)
        logger.error("[wsl-server] 启动超时，日志末尾:\n%s", log_tail.strip())
        return False, (
            "服务启动超时（端口未就绪）。WSL 日志末尾：\n" + log_tail.strip()
        )

    # ------------------------------------------------------------------
    # 停服务
    # ------------------------------------------------------------------
    def stop_server(self) -> bool:
        """停掉 WSL 里的 funasr-realtime-server，释放显存。

        优先用 PID 文件精确 kill；文件不在就 pkill 按进程名兜底。
        幂等：没在跑也返回 True。在 app._quit() 同步调用（os._exit 之前）。
        """
        # 优先精确 kill：用 xargs 把 PID 文件内容喂给 kill，避免 $(cat $PID) 的 $ 被
        # wsl.exe 命令行传递层吞空。
        script = (
            f'[ -f {_PID_FILE} ] && cat {_PID_FILE} | xargs -r kill 2>/dev/null; '
            f"rm -f {_PID_FILE}"
        )
        try:
            _wsl(script, timeout=15)
        except (subprocess.SubprocessError, OSError):
            pass
        # 兜底：按进程名清残留
        try:
            _wsl("pkill -f funasr-realtime-server", timeout=15)
        except (subprocess.SubprocessError, OSError):
            pass
        # 验证端口已关（给一点时间释放）
        import time
        for _ in range(10):
            if not probe(_DEFAULT_HOST, self._port(), timeout=0.5):
                return True
            time.sleep(0.3)
        return not probe(_DEFAULT_HOST, self._port(), timeout=0.5)
