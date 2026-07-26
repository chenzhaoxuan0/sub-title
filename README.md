# sub-title · 本地实时字幕

监控电脑系统声音（浏览器、播放器、会议软件的输出），用本地 GPU 跑语音识别大模型，在屏幕上实时显示中文字幕。完全本地、零网络（API 模式除外）、个人自用优先。

支持三种识别引擎可切换：**本地 FunASR 流式**（低延迟，需 GPU）、**本地 SenseVoice 小模型**（CPU 可跑，适合 Mac/弱设备）、**阿里云 NLS API**（任意平台，免本地算力）。

## 功能特性

### 识别引擎（可切换）
- **FunASR Paraformer 流式**（`paraformer-zh-streaming`）：原生流式，RTF 0.06–0.1，延迟 < 100ms，中文准确。需要 NVIDIA GPU。
- **SenseVoice-Small**（`iic/SenseVoiceSmall`）：234M 小模型，CPU 即可流畅，适合 Mac / 无 GPU 设备。段式伪流式（VAD 切句 + 整段推理），延迟略高。
- **阿里云 NLS API**：云端流式识别，任意平台可用，免本地算力。按量计费。

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

### 滚动控制
- 智能自动滚动：贴底时跟随最新，向上翻看时不被打断
- 「锁定滚动到底部」开关：强制始终跟随
- 「立刻滚动到底部」按钮：误操作后一键回底

### 其他
- 配置持久化（引擎、窗口、主题、字体、透明度、关闭行为等全部存到 `config.yaml`）
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
- 安装 funasr / soundcard / PySide6 等依赖
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
3. 应用。首次会下载 SenseVoice 模型（约 254MB）
4. 无需额外依赖（复用 funasr + torch 的 CPU 版即可）

### 阿里云 NLS API（云端，任意平台）
1. 先装阿里云 NLS SDK（未发布到 PyPI，从 GitHub 装）：
   ```bat
   pip install git+https://github.com/aliyun/alibabacloud-nls-python-sdk.git
   ```
2. 在[阿里云智能语音交互控制台](https://nls-portal.console.aliyun.com/)开通服务，创建项目，获取 **AccessKey ID / AccessKey Secret / AppKey**
3. 设置 → 识别引擎 → 选「阿里云 API」→ 填入三项凭证
4. 应用。凭证存于本地 `config.yaml`，不上传

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

所有配置在 `config.yaml`，运行中通过设置对话框修改会自动写回。

关键字段：

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
  aliyun_access_key_id: ''
  aliyun_access_key_secret: ''
  aliyun_appkey: ''

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
```

## 项目结构

```
sub-title/
├── src/subtitle/
│   ├── app.py               # PyQt 主入口（pipeline + 面板 + 托盘）
│   ├── config.py            # 配置加载（dataclass）
│   ├── pipeline.py          # 采集 → 队列 → 引擎.feed 的事件驱动管线
│   ├── audio/
│   │   ├── capture.py       # soundcard WASAPI loopback 采集
│   │   └── resample.py      # 重采样到 16k/mono/float32
│   ├── asr/
│   │   ├── base.py          # AsrEngine 抽象接口（事件驱动）
│   │   ├── factory.py       # 引擎工厂（按 engine_type 创建）
│   │   ├── funasr_engine.py # FunASR 流式
│   │   ├── sensevoice_engine.py  # SenseVoice 段式伪流式
│   │   └── aliyun_engine.py # 阿里云 NLS API 流式
│   └── ui/
│       ├── subtitle_panel.py    # 沉浸式无边框字幕窗口
│       ├── settings_dialog.py   # 全功能设置对话框
│       ├── theme_engine.py      # 主题引擎（颜色/几何预设、自定义保存、回收站）
│       ├── trash_dialog.py      # 主题回收站对话框
│       └── tray.py              # 系统托盘 + 右键菜单
├── scripts/                 # 调试/安装脚本
├── themes/                  # 用户自定义主题（运行时生成，git 忽略）
├── config.yaml              # 运行配置
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
