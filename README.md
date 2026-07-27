# sub-title · 本地实时字幕

监控电脑系统声音（浏览器、播放器、会议软件的输出），用本地 GPU 跑语音识别大模型，在屏幕上实时显示中文字幕。完全本地、零网络（API 模式除外）、个人自用优先。

支持本地与云端识别引擎可切换：**FunASR Paraformer 流式**（低延迟）、**SenseVoice**（CPU 可跑）、**Fun-ASR-Nano**（中文/歌词）、**Qwen3-ASR**（多语种/歌曲）与阿里云 NLS API。faster-whisper 作为兼容模式保留，但静音和音乐暂停时可能产生幻觉字幕。

**🔐 凭证安全**：AccessKey / Secret / AppKey 存到操作系统级保险箱（Windows Credential Manager / macOS Keychain / Linux Secret Service），**绝不**写进 `config.yaml`，避免被 Git 提交 / 截图 / 同步盘泄露。详见 [凭证存储与安全](#凭证存储与安全)。

## 功能特性

### 识别引擎（可切换）
- **FunASR Paraformer 流式**（`paraformer-zh-streaming`）：原生流式，RTF 0.06–0.1，延迟 < 100ms，中文准确。需要 NVIDIA GPU。
- **SenseVoice-Small**（`iic/SenseVoiceSmall`）：234M 小模型，CPU 即可流畅，适合 Mac / 无 GPU 设备。段式伪流式（VAD 切句 + 整段推理），延迟略高。
- **Fun-ASR-Nano**（`FunAudioLLM/Fun-ASR-Nano-2512`）：800M 新一代模型，面向中文、方言、歌词和音乐背景。当前为段式实时模式，推荐 NVIDIA GPU。
- **Qwen3-ASR**（[`0.6B`](https://www.modelscope.cn/models/Qwen/Qwen3-ASR-0.6B) / [`1.7B`](https://www.modelscope.cn/models/Qwen/Qwen3-ASR-1.7B)）：2026 年发布的多语种/歌曲模型；当前为段式实时模式，原生流式需要 NVIDIA GPU 与 vLLM。使用前安装 `pip install qwen-asr`，权重只从 ModelScope 下载。
- **faster-whisper**：保留给已有配置和多语种兼容用途。Whisper 在静音、音乐暂停和片尾可能输出幻觉字幕，VAD 只能缓解，中文音乐请优先使用 Fun-ASR-Nano 或 Qwen3-ASR。可用权重从 ModelScope 下载。
- **阿里云 NLS API**：云端流式识别，任意平台可用，免本地算力。按量计费。

### 本地模型下载地址

本程序的本地模型统一使用 ModelScope；不会在 ModelScope 失败后自动改从 Hugging Face 下载。

| 引擎 | ModelScope 地址 |
| --- | --- |
| FunASR Paraformer 流式 | [damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online](https://www.modelscope.cn/models/damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online) |
| SenseVoice-Small | [iic/SenseVoiceSmall](https://www.modelscope.cn/models/iic/SenseVoiceSmall) |
| Fun-ASR-Nano | [FunAudioLLM/Fun-ASR-Nano-2512](https://www.modelscope.cn/models/FunAudioLLM/Fun-ASR-Nano-2512) |
| Qwen3-ASR 0.6B | [Qwen/Qwen3-ASR-0.6B](https://www.modelscope.cn/models/Qwen/Qwen3-ASR-0.6B) |
| Qwen3-ASR 1.7B | [Qwen/Qwen3-ASR-1.7B](https://www.modelscope.cn/models/Qwen/Qwen3-ASR-1.7B) |
| faster-whisper large-v3 | [Systran/faster-whisper-large-v3](https://www.modelscope.cn/models/Systran/faster-whisper-large-v3) |
| faster-whisper medium | [Systran/faster-whisper-medium](https://www.modelscope.cn/models/Systran/faster-whisper-medium) |
| faster-whisper small | [Systran/faster-whisper-small](https://www.modelscope.cn/models/Systran/faster-whisper-small) |
| faster-whisper distil-large-v3 | [Systran/faster-distil-whisper-large-v3](https://www.modelscope.cn/models/Systran/faster-distil-whisper-large-v3) |

### 本地运行硬件参考

下表是单路实时字幕的保守建议，实际占用会受音频长度、驱动、系统后台和是否同时开启麦克风影响。双输入源会创建两套模型实例，应按约两倍内存/显存预留。模型下载大小是近似值，仓库更新后可能变化。

| 引擎 / 模型 | 下载大小约 | 推荐硬件 | CPU / 量化说明 |
| --- | ---: | --- | --- |
| FunASR Paraformer 流式 | 约 1GB | NVIDIA GPU 4GB+；或 8 线程、16GB RAM | 当前默认的低延迟本地方案；CPU 可用但应优先保证 CPU 性能。 |
| SenseVoice-Small | 约 250MB | 4 核、8GB RAM；GPU 非必需 | 最通用的 CPU/Mac 方案，不提供 GGUF 入口。 |
| Fun-ASR-Nano | 约 1.5-2GB | NVIDIA GPU 6GB+、16GB RAM | 未接入可选 GGUF/INT8 权重；CPU 可加载但通常不适合实时。 |
| Qwen3-ASR 0.6B | 约 1.9GB | NVIDIA GPU 6GB+、16GB RAM | 可在设置中选 CUDA 4-bit 运行时量化，适合显存较紧张的 GPU；4-bit 需要 `bitsandbytes`，不支持 CPU。 |
| Qwen3-ASR 1.7B | 约 4-5GB | NVIDIA GPU 12GB+、24GB RAM | 更高准确率；也可尝试 CUDA 4-bit，但实时表现取决于 GPU。 |
| faster-whisper small | 约 0.5GB | 4 核、8GB RAM | 选择 `INT8` 即可用量化 CPU 推理，是低内存本地备选。 |
| faster-whisper medium | 约 1.5GB | 6 核、12GB RAM，或 GPU 4GB+ | CPU 请选择 `INT8`；多语种兼容性较好。 |
| faster-whisper large-v3 | 约 3GB | GPU 6GB+；CPU 16GB+ 仅建议离线/较高延迟 | `INT8` 可减少运行内存，但不能根治 Whisper 的静音幻觉。 |
| 阿里云 NLS API | 无本地模型 | 任意可联网设备 | 弱 CPU、内存不足 6GB 或不希望维护本地模型时优先选择。 |

**量化与 GGUF 的边界**：本程序只展示当前引擎实际能加载的格式。faster-whisper 通过 CTranslate2 的 `INT8` 运行时量化支持 CPU；Qwen3-ASR 通过 Transformers + `bitsandbytes` 支持 CUDA 4-bit。FunASR/SenseVoice/Nano 的当前运行库以及 Qwen3 的 Python 后端都不能直接加载任意 GGUF 文件，因此没有提供会失败的 GGUF 文件选择器。将来接入 llama.cpp 或其他 GGUF ASR 后端后，才会新增独立的 GGUF 引擎选项。

### 沉浸式字幕窗口
- 无边框、半透明、窗口置顶，平时就是一个浮在屏幕上的字幕条
- 鼠标移入显示工具栏，移开自动隐藏（延时可调）
- 工具栏根据窗口宽度**渐进式精简**（缩窄时按优先级隐藏按钮，最小只留字号/透明度）
- 工具栏与字幕区**完全分离**：显隐工具栏不影响字幕位置和行数（不跳动）
- 双主题：黑底白字 / 白底黑字，背景透明度 0–100% 可调
- 字体、字号可自定义，字号支持直接输入数值
- 可拖动、右下角拖拽缩放、位置和尺寸自动记忆

### 系统托盘 + 全功能设置
- 托盘图标 + 右键菜单（显示隐藏 / 开始停止 / 主题 / 置顶 / 设置 / 退出）
- 关闭窗口默认弹窗询问（隐藏到托盘 / 退出程序），可记忆选择
- 设置对话框双标签页：
  - **设置**：识别引擎选择 + 各引擎配置（设备/凭证）、外观、窗口尺寸、滚动行为、字幕文本上限
  - **文稿回看**：完整字幕文本查看、刷新、复制、清空

### 字幕皮肤 / 桌宠贴图

> 第一次使用或不熟悉图层、关键帧和事件？请阅读 [字幕皮肤编辑器完整使用说明](docs/skin-editor-guide.md)。

- 在字幕文字上方或下方放置透明 PNG/WebP 图层，支持排序、显隐、锁定、旋转、缩放和九宫格锚定
- 支持 PNG/WebP 序列帧，可设置素材帧率和循环方式
- 每个动作拥有独立时间轴，支持逐属性关键帧、缓动、框选、多选、复制粘贴、整体拖动和时间缩放
- 动作支持优先级、可打断、冷却、重复抑制、等待队列以及不同图层并行播放
- 支持定时、随机、识别状态、字幕、关键词、正则、空闲、音量、窗口显示/隐藏和点击贴图触发
- 编辑器实时镜像当前字幕窗口；皮肤可导入、导出为包含全部素材的 ZIP 包
- 托盘右键 → **桌宠皮肤** 可打开编辑器、切换皮肤或恢复纯字幕

### 滚动控制
- 智能自动滚动：贴底时跟随最新，向上翻看时不被打断
- 「锁定滚动到底部」开关：强制始终跟随
- 「立刻滚动到底部」按钮：误操作后一键回底

### 其他
- 配置持久化（引擎、窗口、主题、字体、透明度、关闭行为等存到**用户数据目录的 `config.yaml`**；API 凭证存到系统保险箱）
- 完全离线（本地引擎模式，模型首次下载除外）

## 截图

> 字幕窗口、设置对话框、托盘菜单的截图待补充。可放在 `docs/` 目录。

## 快速开始（Windows + FunASR 本地引擎）

### 硬件要求
- NVIDIA GPU（推荐 6GB+ 显存）。开发与测试机：RTX 4060 Ti 16GB。
- Windows 10/11（音频捕获用 WASAPI loopback）。

### 1. 安装 miniconda
从 https://docs.conda.io/en/latest/miniconda.html 下载 Windows 安装包，装到默认路径（`C:\ProgramData\miniconda3` 或 `%USERPROFILE%\miniconda3`）。

### 2. 创建环境 + 安装依赖
双击项目里的 `scripts/setup_env.bat`，它会自动：
- 创建 conda 环境 `subtitle`（Python 3.11）
- 安装 funasr / soundcard / PySide6 / platformdirs / keyring 等依赖
- 安装 torch CUDA 12.1 版（约 2.5GB）

或手动执行：
```bat
conda env create -f environment.yml
conda activate subtitle
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### 3. 验证 GPU 可用
```bat
python -c "import torch; print(torch.cuda.is_available())"
```
必须输出 `True`。若为 `False` 说明 torch 装成了 CPU 版，见下方「常见问题」。

### 4. 启动
双击 `run.bat`，或：
```bat
conda activate subtitle
set PYTHONPATH=src
python -m subtitle
```
首次启动会自动下载 FunASR 模型（约 1GB，存到 `~/.cache/modelscope`）。

## 使用其他引擎

引擎在**设置对话框**（托盘右键 → ⚙ 设置）里切换，或在 `config.yaml` 改 `asr.engine_type`。

### SenseVoice（本地，CPU 可跑，适合 Mac/弱设备）
1. 设置 → 识别引擎 → 选「本地 SenseVoice」
2. 设备选 CPU（或 CUDA），攒段时长默认 2 秒
3. 应用。首次会从 ModelScope 下载 SenseVoice 模型（约 254MB）
4. 无需额外依赖（复用 funasr + torch 的 CPU 版即可）

### Fun-ASR-Nano（本地，中文/歌词）
1. 设置 → 识别引擎 → 选「本地 Fun-ASR-Nano」
2. 推荐使用 NVIDIA GPU；模型首次使用会从 ModelScope 自动下载（[模型页](https://www.modelscope.cn/models/FunAudioLLM/Fun-ASR-Nano-2512)）
3. 此模型适合中文、方言、歌词与带音乐背景的内容；当前使用段式实时推理

### Qwen3-ASR（本地，多语种/歌曲）
1. 先安装可选依赖：`pip install qwen-asr`
2. 设置 → 识别引擎 → 选「本地 Qwen3-ASR」
3. 推荐 NVIDIA GPU。模型首次只从 ModelScope 下载：[0.6B](https://www.modelscope.cn/models/Qwen/Qwen3-ASR-0.6B)、[1.7B](https://www.modelscope.cn/models/Qwen/Qwen3-ASR-1.7B)。原生流式后端依赖 vLLM；当前应用内置段式实时推理
4. 显存紧张时可在设置中选择「4-bit 运行时量化」。它需要 CUDA 和 `bitsandbytes`；可运行 `scripts\\install_qwen3_asr_4bit.bat` 一并安装。CPU 不支持该量化模式。

### 阿里云 NLS API（云端，任意平台）
1. 先装阿里云 NLS SDK（未发布到 PyPI，从 GitHub 装）：
   ```bat
   pip install git+https://github.com/aliyun/alibabacloud-nls-python-sdk.git
   ```
2. 在[阿里云智能语音交互控制台](https://nls-portal.console.aliyun.com/)开通服务，创建项目，获取 **AccessKey ID / AccessKey Secret / AppKey**
3. 设置 → 识别引擎 → 选「阿里云 API」→ 填入三项凭证
4. 点「应用」。**凭证会存到操作系统级保险箱**（Windows Credential Manager / macOS Keychain / Linux Secret Service），**不会**写进 `config.yaml`，避免被 git 提交 / 截图 / 同步盘泄露。卸载重装或换电脑需要重新填。

> 阿里云实时语音识别按语音时长计费，新用户通常有免费额度，具体以控制台为准。

## 主题管理

设置 → 外观 → 主题栏。提供 8 个动作：

- **➕ 新建**：从空白默认值（`ThemeColors()` + `ThemeGeometry()` + 0.88 不透明度）创建一个全新主题，不复制当前主题的任何字段。
- **💾 保存**：把当前主题另存为自定义。**深拷贝隔离**——内置主题（如 Dark）不会被污染；保存后 `_current` 自动切到新 copy，后续"应用颜色/几何"都改在 copy 上。
- **✏️ 重命名**：内置主题重命名 = 复制为新自定义（内置本身不动）；自定义主题重命名 = 旧文件进回收站，新名生效。
- **🔄 恢复默认**：把内置主题恢复到出厂原始值（用启动时的深拷贝快照），适合"改坏颜色回不去"时一键回退。
- **📂 导入 / 📤 导出**：JSON 文件互导。
- **🗑 删除**：自定义主题**软删除**（移到 `themes/.trash/<name>_<timestamp>.json`），可在回收站恢复。
- **📦 回收站**：列出被删除的主题，支持恢复 / 恢复为（换名）/ 永久删除 / 清空。

### 关键规则

- **基础主题 Dark / Light 不可删除**（`PROTECTED_THEMES`），按钮自动禁用并 tooltip 提示。删了就没法换回去了。
- **其他内置主题**（Nord、Tokyo Night、Solarized Dark、Catppuccin Mocha、Dracula）也不可删除，但可以通过 `💾 保存` 另存为自定义后再操作。
- **修改自定义主题的颜色/几何**直接改到该主题对象（dataclass 字段），下次打开还在。
- **修改内置主题的颜色/几何**会污染内置的内存对象——所以**改之前先点 💾 保存**另存为自定义（深拷贝），或者改完后用 🔄 恢复默认。

### 主题文件存储

```
themes/
├── My Dark.json             # 自定义主题
├── ...
└── .trash/                  # 软删除暂存（启动时不加载）
    ├── My_Dark_1700000000.json
    └── ...
```

## 配置说明

所有配置存放在**用户数据目录**的 `config.yaml`（不是项目根），运行中通过设置对话框修改会自动写回。

### 数据存储位置

| OS | 配置目录 | `config.yaml` 路径 |
|---|---|---|
| **Windows** | `%APPDATA%\sub-title\` | `C:\Users\<你>\AppData\Roaming\sub-title\config.yaml` |
| **macOS** | `~/Library/Application Support/sub-title/` | `~/Library/Application Support/sub-title/config.yaml` |
| **Linux** | `~/.config/sub-title/` | `~/.config/sub-title/config.yaml` |

> 项目根目录下的 `config.yaml`（老版本位置）**只用于一次性迁移**。打包成 EXE/DMG 后这层路径会指向不可写位置（`Program Files` / `.app` 包），所以新代码一律走用户数据目录。
>
> 首次启动时如果检测到老位置的 `config.yaml`，会自动复制到新位置并把老文件改名成 `config.yaml.migrated` 存档。

### 凭证存储与安全

**AccessKey ID / Secret / AppKey 不存进 `config.yaml`**，改由操作系统级保险箱保管：

| OS | 底层 |
|---|---|
| **Windows** | Windows Credential Manager（系统级加密，登录账号绑定） |
| **macOS** | Keychain（系统级加密，用户登录密码保护） |
| **Linux** | Secret Service / KWallet（需要 `libsecret-1-0` / `kwallet` 守护进程） |

在设置对话框填 AccessKey 后点「应用」即写入保险箱。**卸载重装或换电脑需要重新填**。

> 安全性：
> - ✅ 不会被 `git add .` 误提交
> - ✅ 截图 / 分享 `config.yaml` 不会泄露 AK
> - ✅ 同步盘 / 备份软件不会把 AK 同步走
> - ⚠️ 系统级：拿到你系统登录账号的人可以读到（这是任何本地凭证库的共性）——比明文存 `config.yaml` 安全得多，但**不能替代良好的系统访问控制**
>
> fallback：如果 `keyring` 不可用（比如 Linux 没装 libsecret 守护进程），凭证会退化到 `<用户数据目录>/credentials.json`，Unix 上自动 `chmod 600`（仅 owner 读写）。Windows 上靠 NTFS 默认用户隔离。

### 关键字段示例

```yaml
asr:
  engine_type: funasr        # funasr / sensevoice / aliyun
  # FunASR
  model: paraformer-zh-streaming
  device: cuda
  chunk_size: [0, 10, 5]
  # SenseVoice
  sensevoice_model: iic/SenseVoiceSmall
  sensevoice_device: cpu
  sensevoice_segment_seconds: 2.0
  # 阿里云
  aliyun_region: cn-shanghai   # region 不是密钥，可以放这里
  # AccessKey ID / Secret / AppKey 不在这里！
  # 三个字段在设置对话框里填，会自动存到系统保险箱。详见上文「凭证存储与安全」。

audio:
  target_sample_rate: 16000
  chunk_seconds: 0.6
  input_device: null         # null = 系统默认输出设备

ui:
  theme: dark                # dark / light
  font_family: Microsoft YaHei
  font_size: 22
  window_opacity: 0.88
  always_on_top: true
  close_action: ask          # ask / hide / quit
  lock_scroll_to_bottom: false

skin:
  enabled: false             # 启动时是否加载皮肤
  active_skin: ''            # 当前皮肤目录名
  skins_dir: skins           # 相对用户数据目录，也可填绝对路径
  editor_grid_snap: true
  editor_grid_size: 8
```

皮肤保存后会立即设为当前皮肤。默认皮肤目录位于用户数据目录下的 `skins/`。

## 项目结构

```
sub-title/
├── src/subtitle/
│   ├── app.py               # PyQt 主入口（pipeline + 面板 + 托盘 + 启动迁移）
│   ├── config.py            # 配置加载（dataclass，不含 AK 字段）
│   ├── paths.py             # 跨平台用户数据目录（%APPDATA% / ~/Library / ~/.config）
│   ├── credentials.py       # 凭证管理（系统 keyring + fallback）
│   ├── pipeline.py          # 采集 → 队列 → 引擎.feed 的事件驱动管线
│   ├── audio/
│   │   ├── capture.py       # soundcard WASAPI loopback 采集
│   │   └── resample.py      # 重采样到 16k/mono/float32
│   ├── asr/
│   │   ├── base.py          # AsrEngine 抽象接口（事件驱动）
│   │   ├── factory.py       # 引擎工厂（按 engine_type 创建）
│   │   ├── funasr_engine.py # FunASR 流式
│   │   ├── sensevoice_engine.py  # SenseVoice 段式伪流式
│   │   └── aliyun_engine.py # 阿里云 NLS API 流式（凭证从 keyring 读）
│   ├── skin/
│   │   ├── model.py         # 图层、动作、关键帧、触发器与版本迁移
│   │   ├── renderer.py      # 双层响应式渲染、序列帧和命中检测
│   │   ├── action_player.py # 动作优先级、并行、打断与等待队列
│   │   ├── events.py        # 定时/字幕/音量/窗口/点击事件
│   │   ├── runtime.py       # 应用级皮肤运行时
│   │   ├── package.py       # ZIP 皮肤包导入导出与安全检查
│   │   └── editor.py        # 可视化皮肤编辑器
│   └── ui/
│       ├── subtitle_panel.py    # 沉浸式无边框字幕窗口
│       ├── settings_dialog.py   # 全功能设置对话框
│       ├── theme_engine.py      # 主题引擎（颜色/几何预设、自定义保存、回收站）
│       ├── trash_dialog.py      # 主题回收站对话框
│       ├── flow_layout.py       # 自动换行水平布局
│       └── tray.py              # 系统托盘 + 右键菜单
├── scripts/                 # 调试/安装脚本
├── themes/                  # 用户自定义主题（运行时生成，git 忽略）
├── config.yaml.example      # 配置模板（git 入库；真 config.yaml 不入库）
├── environment.yml          # conda 环境定义
└── requirements.txt
```

## 架构

```
[soundcard WASAPI loopback, 16k mono]
   └采集线程→ queue.Queue
        └→ [推理线程] engine.feed(chunk)
                         └→ on_result(text, is_final) 回调
                              └→ Qt signal → UI 字幕追加
```

引擎接口是**事件驱动**的：`feed(chunk)` 单向喂入音频，结果通过 `on_result` 回调推送。这让三种工作方式完全不同的引擎（FunASR 同步流式、SenseVoice 段式、阿里云异步回调）能用同一个 pipeline，新增引擎只需实现接口并在工厂注册。

## 调试脚本

- `python scripts/test_loopback.py --secs 5` — 录 5 秒系统声音，验证音频捕获
- `python scripts/test_capture.py --asr auto` — 对 `test/` 下的 wav 跑流式转写（模型冒烟）
- `python scripts/live_asr.py` — 实时采集 + 当前引擎转写（无 GUI，端到端验证）

## 开发

### 环境准备
按「快速开始」装好 conda 环境和 torch。开发用 Python 3.11（funasr 不支持 3.13+）。

### 添加新引擎
1. 在 `src/subtitle/asr/` 新建 `xxx_engine.py`，继承 `AsrEngine`，实现 `load / feed / stop / reset`
2. 在 `asr/factory.py` 的 `create_engine` 注册新类型
3. 在 `config.py` 的 `AsrConfig` 加该引擎的配置字段
4. 在 `settings_dialog.py` 加对应的配置面板

pipeline 和 UI 完全不用改——这是事件驱动接口的设计目的。

## 常见问题

**Q: torch 装成了 CPU 版（cuda: False）**
`environment.yml` 故意不含 torch 以避免此坑。修复：
```bat
pip uninstall -y torch torchaudio
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```
必须用 `--index-url`（不是 `--extra-index-url`），否则 pip 会优先选 CPU 版。

**Q: 工具栏按钮看不清 / 没有背景色**
重启软件。配色改动需要完全退出（托盘 → 退出）后重新启动才生效。

**Q: 录不到系统声音**
确认声音从「系统默认输出设备」播出。脚本 `python scripts/test_loopback.py --secs 5` 可单独验证捕获，看 RMS 是否 > 0。

**Q: PowerShell 里 conda 命令找不到**
PowerShell 需先初始化 conda：
```powershell
& "C:\ProgramData\miniconda3\shell\condabin\conda-hook.ps1"
conda init powershell
```
然后重启 PowerShell。或直接用项目里的 `.bat` 脚本（走 cmd，避开此问题）。

## 技术栈

- **ASR**：[FunASR](https://github.com/modelscope/FunASR) / [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) / 阿里云 NLS
- **音频捕获**：[soundcard](https://github.com/bastibe/SoundCard)（WASAPI loopback）
- **UI**：[PySide6](https://www.qt.io/qt-for-python)（Qt 官方 Python 绑定，LGPLv3）；Fluent 风格设置 UI 为手写 QSS，未使用第三方 Fluent 组件库
- **推理**：[PyTorch](https://pytorch.org/)（CUDA 12.1）
- **跨平台路径**：[platformdirs](https://pypi.org/project/platformdirs/)
- **凭证存储**：[keyring](https://pypi.org/project/keyring/)（Windows Credential Manager / macOS Keychain / Linux Secret Service）

## License

本项目自研源代码以 **MIT License** 开源，详见 [LICENSE](LICENSE)。

第三方依赖的许可证：
- **PySide6 / Qt**：LGPLv3。本项目动态链接 PySide6（不静态链接），用户可自行替换/重新链接 Qt 库。Qt 源码获取：https://www.qt.io
- **FunASR / SenseVoice**（阿里达摩院）：Apache 2.0
- **soundcard**：BSD-3-Clause

> 为何不用 PyQt5：PyQt5 是 GPLv3/商业双协议，会通过链接传染要求整个项目也 GPLv3。改用 PySide6（LGPLv3）后，MIT 项目可安全链接而不被传染。
>
> 为何不用 PyQt-Fluent-Widgets：该库为 GPLv3，免费版会传染本项目。设置 UI 的 Fluent 风格采用手写 QSS 实现（见 `src/subtitle/ui/fluent_widgets.py`），无第三方库依赖。

本仓库不包含任何模型权重（模型由 funasr 在首次运行时从 ModelScope 自动下载）。
