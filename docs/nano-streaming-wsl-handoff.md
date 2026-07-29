# Nano 流式（WSL2 + vLLM）实现交接

> 本文档是「在新对话中展开 Fun-ASR-Nano 流式输出（WSL2 + vLLM 方案）」所需的全部背景。
> 写于 2026-07-28，基于当时的代码库与已查证的技术事实。

## 任务目标

在本项目（`C:\Users\chenziyu\project\sub-title`）中，通过 **WSL2 + vLLM** 让 Fun-ASR-Nano 支持流式输出。这是之前因「vLLM 不支持 Windows 原生」被放弃的功能，现改为：**vLLM 服务跑在 WSL2 里，Windows 主程序作为 WebSocket 客户端连接**。

## 关键背景：为什么之前放弃了，现在又做

- **核心约束**（已查证，非推测）：Fun-ASR-Nano 是 **LLM 架构**。FunASR 源码里有：
  ```python
  # funasr/auto/auto_model_vllm.py
  _LLM_BASED_MODELS = {"FunASRNano", "LLMASR", "LLMASRNAR", "GLMASR", "QwenAudioWarp"}
  ```
  这类模型在 FunASR 里**只有 `AutoModelVLLM` 一条推理路径，无降级**。
- **Windows 原生不可用**：`pip install vllm` 会报 `ModuleNotFoundError: No module named 'vllm'`，vLLM 官方不支持 Windows 原生，需要 WSL2。
- **段式 Nano 不受影响**：走 `AutoModel(trust_remote_code)` 原生 torch，不需要 vLLM，Windows 原生可用。
- 之前做过一版「流式模式」（commit `f0e6a55`），因 Windows 跑不起来 revert 了（`17d8937`）。**这次的区别是 vLLM 服务跑在 WSL2 里**，主程序（Windows）只做 WebSocket 客户端。

## 已确认的环境事实

- 机器：**NVIDIA RTX 4060 Ti 16GB**，驱动 610.62，**WSL2 已安装**。
- 项目 conda 环境 `subtitle`：Python 3.11，`funasr==1.3.29`（已随装 `funasr-realtime-server.exe` console_script）。
- vLLM 在 WSL2 的 Ubuntu 里 `pip install vllm` 可装。

## vLLM 流式服务的真实接口

> 以下均从 FunASR 源码查证，非 README 推测。

### 服务端入口

- 命令：`funasr-realtime-server`，入口点 `funasr.bin.realtime_ws:cli_main`
- 源文件（查参数用）：`funasr/bin/realtime_ws.py`
- `main()` 是 async 且 `await asyncio.Future()` **永久阻塞** → 必须独立进程跑
- **真实参数**（从 `build_arg_parser()` 提取）：
  - `--port 10095`（默认）
  - `--model FunAudioLLM/Fun-ASR-Nano-2512`（默认）
  - `--endpoint-mode client`（客户端驱动端点，关键）
  - `--device cuda:0`（默认要 CUDA）
  - `--enable-spk`（内置说话人分离！用 `eres2netv2`，比 cam++ 新）
  - `--hub ms` / `--hub hf`
  - `--language 中文`（语言提示）
  - `--dtype bf16`、`--gpu-memory-utilization 0.8`、`--max-model-len 2048`

### WebSocket 协议（从 `funasr_wss_client.py` 源码提取）

- 端口：服务端默认 `10095`
- 连接：`ws://host:port`，`subprotocols=['binary']`
- **握手 JSON**（连接后立即发）：
  ```json
  {
    "mode": "2pass",
    "chunk_size": [5, 10, 5],
    "chunk_interval": 10,
    "encoder_chunk_look_back": 4,
    "decoder_chunk_look_back": 0,
    "wav_name": "microphone",
    "is_speaking": true,
    "hotwords": "",
    "itn": true
  }
  ```
- **音频传输**：二进制 PCM 帧（int16，16kHz），每帧 **1920 字节**（= 960 样本 = 60ms）
- **响应 JSON**：`{"text":.., "mode":"2pass-online|2pass-offline", "is_final":.., "spk_name":.., "spk_score":..}`
  - `mode` 含 `"online"` → `is_final=False` 增量中间结果
  - `mode` 含 `"offline"` → `is_final=True` 句末最终修正
- **结束信号**：发 `{"is_speaking": false}`
- **判活标志**：服务端就绪时打 `logger.info(f"Server on ws://0.0.0.0:{port}")`（`realtime_ws.py:1179`）

## 当前代码库状态（必须了解）

### Git

- 远端：`https://github.com/chenzhaoxuan0/sub-title.git`，分支 `master`
- 当前 HEAD：`c6706e3`（已 push，工作区干净）
- **流式代码已全部清理**（`funasr_nano_streaming_engine.py`、`nano_server_manager.py` 均不存在），Nano 只剩**段式**
- 历史里保留：`f0e6a55`（加流式）、`17d8937`（revert 流式），可参考但代码已不在树里

### ASR 引擎架构（`src/subtitle/asr/`）

- **接口**（`base.py`）：
  ```python
  OnResult = Callable[[str, bool, str, Optional[int]], None]
  # = (text, is_final, source, spk_id)
  ```
  `AsrEngine` ABC 有 `load() / feed(np.ndarray) / stop()`。`feed` 无返回，结果只经 `on_result` 回调流出。
- **工厂**（`factory.py:60-62`）：`funasr_nano` 分支现在是简单 2 行，直接造段式 `FunAsrNanoEngine`。**新流式引擎要在这里加分发**（参考 `f0e6a55` 做法，但连接方式从「本地子进程」改成「WSL2 网络地址」）。
- **段式 Nano 引擎**（`funasr_nano_engine.py`）：走 `AutoModel` + 写临时 wav + `generate`，**保留不动**。注意现在用了 `_device.resolve_device()`（新增的跨平台设备解析）。

### 参考先例（可大量复用）

- **`aliyun_engine.py`**：**已经是 WebSocket 客户端流式的完整先例**。`feed()` 把 float32→int16 PCM 分包 `send_audio`，服务端在回调线程吐增量结果。流式 Nano 引擎可照抄这个跨线程骨架，只是连的是 WSL2 的 `ws://<wsl-ip>:10095` 而非阿里云。
- **`funasr_engine.py`**（Paraformer-streaming）：另一个流式先例，`feed` 同步 `generate(cache=)`，从 `sentence_info` 提 `spk_id`。

### 配置（`config.py`）

- `AsrConfig` 的 Nano 字段：`funasr_nano_model / device / language / segment_seconds`（段式专属）
- 之前加过 `funasr_nano_mode / host / port` 三字段（revert 时删了），**这次需要重新加**，但 `host` 默认值应是 WSL2 的 IP（如 `172.x.x.x`），不是 `127.0.0.1`。

## 这次的架构差异（关键！）

之前（已放弃）的方案：vLLM 服务作为**子进程**跑在 Windows 上 → 装不上 vLLM → 失败。

这次的方案：**vLLM 服务跑在 WSL2 里**，Windows 主程序是**网络 WebSocket 客户端**。

这意味着：

1. **不能再用 `subprocess.Popen` 拉起服务**（那是之前 `nano_server_manager.py` 的做法；服务在 WSL 里、主程序在 Windows，`Popen` 管不到）。
2. **服务生命周期管理变了**：要么
   - (a) 只做客户端，服务由用户自己在 WSL 里手动起（`funasr-realtime-server --endpoint-mode client --port 10095`），程序只负责连；或
   - (b) 主程序通过 `wsl` 命令远程拉起 WSL 里的服务（复杂，需探测 WSL 环境）。
3. **网络层**：WSL2 默认 NAT。Windows 访问 WSL 服务用 WSL 的 IP（`wsl hostname -I` 查），或用 `localhost`（较新 Windows 版本 WSL2 支持 localhost 转发）。这点要在设计时确认。
4. **音频采集仍在 Windows 主程序**（WASAPI loopback），采集后 PCM 经 WebSocket 推给 WSL 里的 vLLM 推理，结果推回。这部分和 AliyunEngine 模式一致，音频数据跨网络但量不大（16kHz int16 = 32KB/s）。

## 用户的设计偏好（从之前对话确认）

- 用户曾明确「如果要装 WSL2 才能运行，这个功能直接放弃」→ 但现在用户主动要求尝试 WSL2 方案，态度转变，愿意接受 WSL2 门槛。
- 用户偏好：**优雅降级**（连不上服务时回退段式，不崩）—— 之前 `f0e6a55` + `factory.py` 的降级模式（`probe` 端口探测 + `_nano_streaming_fallback` 标记）可复用。
- 用户偏好：**段式/流式用户可选**（UI 下拉），段式默认。

## 建议新对话的切入点

1. 先确认 **WSL2 网络连通性**（Windows 能否通过 localhost 或 WSL IP 访问 WSL 里的 10095 端口）—— 这决定 host 配置默认值。
2. 先让用户**手动在 WSL 里起一次** `funasr-realtime-server --endpoint-mode client --port 10095`，确认服务能跑、模型能下，再谈程序集成。
3. 引擎实现照抄 `aliyun_engine.py` 的 WebSocket 客户端骨架 + `f0e6a55` 里的握手 / 响应解析逻辑（协议部分已验证正确）。
4. 服务生命周期：优先做「只连不起」的轻量版（用户手动起服务），验证可行后再考虑 `wsl` 命令远程拉起。
