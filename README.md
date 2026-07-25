# sub-title — 本地实时字幕

监控 Windows 系统声音（浏览器/播放器输出），用本地 GPU 跑 FunASR Paraformer 流式模型，
在独立 PyQt 窗口里实时滚动显示中文字幕。纯本地、零网络、个人自用。

## 硬件要求
- NVIDIA GPU（推荐 6GB+ 显存）。本项目开发机：RTX 4060 Ti 16GB。
- Windows 10/11（用 WASAPI loopback 抓系统声音）。

## 架构
```
[sounddevice WASAPI loopback]
   └采集线程→ 重采样到 16k/mono/float32
        └→ chunk 成 9600 samples (600ms)
             └→ queue.Queue → [推理线程] funasr generate(cache 维持状态)
                                   └→ Qt signal → UI 滚动字幕
```

## 安装步骤

### 1. 装 miniconda（如已有可跳过）
下载 https://docs.conda.io/en/latest/miniconda.html 的 Windows 安装包。
脚本会自动识别以下两个安装位置之一：
- `C:\ProgramData\miniconda3`（All Users 安装，推荐）
- `%USERPROFILE%\miniconda3`（Just Me 安装）

### 2. 创建环境
```bat
conda env create -f environment.yml
conda activate subtitle
```

### 3. 验证 GPU 可用（必须输出 True）
```bat
python -c "import torch; print('cuda:', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')"
```
如果输出 False：说明 torch 装成了 CPU 版（environment.yml 故意不含 torch 以避免此坑）。修复：
```bat
pip uninstall -y torch torchaudio
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```
注意必须用 `--index-url`（不是 `--extra-index-url`），否则 pip 仍会优先选 CPU 版。

### 4. 首次启动会自动下载模型（约 1GB，存到 `~/.cache/modelscope`）

## 使用
```bat
run.bat
```
或在已激活环境里：
```bat
set PYTHONPATH=src
python -m subtitle
```

## 目录
```
src/subtitle/    主代码
scripts/         调试/验证脚本（capture、模型冒烟）
config.yaml      运行配置
environment.yml  conda 环境定义
```

## 调试脚本
- `python scripts/test_capture.py --list`  列出音频设备
- `python scripts/test_capture.py --record 5`  录 5 秒系统声音存盘并回放
- `python scripts/test_capture.py --asr test.wav`  对 wav 跑流式转写
