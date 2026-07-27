# 打包说明（exe / dmg）

本文档记录如何把 sub-title 打包成 Windows exe 与 macOS dmg。

## 两种打包模式

| 模式 | 包含依赖 | 体积 | 适用场景 |
| --- | --- | --- | --- |
| **纯 API 模式**（默认/推荐） | PySide6 / soundcard / soundfile / numpy / scipy / keyring 等 | ~200MB | 只用阿里云引擎；本地引擎在「设置→引擎管理」页按需 pip 安装 |
| 本地引擎模式 | 额外含 funasr + torch | ~600MB+ | 开箱即用 SenseVoice/FunASR，体积大、构建慢 |

当前 `sub-title.spec` 配置为**纯 API 模式**：在 `excludes` 里排除了 `torch / funasr / qwen_asr / faster_whisper / modelscope` 等重依赖。要切到本地引擎模式，注释掉 `excludes` 里对应条目并重新构建。

> 应用代码本身（`subtitle/asr/*_engine.py`）会打进包里——它们是纯 Python，torch import 延迟到 `load()` 才触发。所以「引擎管理」页能正确报告依赖缺失，用户装完依赖后无需重新打包即可使用本地引擎。

## Windows exe 构建

### 前置要求
- Windows 10/11
- Python 3.11（funasr 官方支持版本；3.13+ 未经充分验证）
- 纯 API 模式只需 `pip install -r requirements.txt` + `pip install pyinstaller==6.21.0`

### 构建步骤
```bat
:: 项目根目录
pip install pyinstaller==6.21.0
pyinstaller sub-title.spec --noconfirm --clean
```

产物：`dist/sub-title/sub-title.exe`（onedir 模式，整个 `dist/sub-title/` 文件夹是要分发的完整程序）。

### 分发
把整个 `dist/sub-title/` 文件夹压缩成 zip 分发。用户解压后双击 `sub-title.exe` 即可运行，无需安装 Python。

### 关键配置说明（`sub-title.spec`）
- **`datas`**：打包 `src/themes/*.json` 到 `themes/`（内置主题预设，`resource_dir()` 打包后指向 `_MEIPASS` 能找到它们）。
- **`hiddenimports`**：
  - `_soundfile_data` + `cffi` + `_cffi_backend`：soundfile 读 wav 必需的 libsndfile 原生库。
  - `soundcard` 全部子模块：Windows mediafoundation 音频后端。
  - `keyring` 全部子模块：Windows Credential Manager 后端（否则凭证退化到明文 json）。
  - 各 `subtitle.asr.*_engine`：让引擎管理页能探测状态。
- **`excludes`**：排除 torch/funasr 等重依赖，减小体积。
- **`console=False`**：GUI 应用无控制台（windowed 模式）。
- **`upx=False`**：UPX 压缩会触发杀软误报且对 PySide6 有兼容问题。

### 图标
`assets/app.ico` 是应用图标（字幕条风格，多尺寸 16/32/48/64/128/256）。重新生成：
```bat
python -c "from PIL import Image, ImageDraw; ..."   :: 见图标生成脚本
```

## macOS dmg 构建（待实现）

> macOS 必须在 Mac 上构建（交叉打包不可行）。以下为规划步骤，尚未在本项目验证。

```bash
# 在 Mac 上
pip install -r requirements.txt
pip install pyinstaller==6.21.0
pyinstaller sub-title.spec --noconfirm --clean
# 用 create-dmg 或 hdiutil 把 dist/sub-title/ 打成 dmg
```

macOS 注意事项：
- **系统音频捕获**：需用户另装 [BlackHole](https://existential.audio/blackhole/) 虚拟声卡（soundcard 原生不支持 Mac loopback）。应用会在找不到 loopback 时给出引导。
- **签名与公证**：分发用 dmg 需 Apple Developer 证书签名 + 公证（notarize + staple），否则用户首次打开会被 Gatekeeper 拦截。
- **字体**：默认字体按平台选择（Mac 用苹方 PingFang SC），无需额外处理。

## 验证打包结果

```bat
:: 1. 启动 exe，确认 GUI 正常出现
dist\sub-title\sub-title.exe

:: 2. 打开 设置 → 引擎管理，确认：
::    - 5 个本地引擎卡片都显示「未安装」（纯 API 模式不含它们的依赖）
::    - 每个卡片有正确的安装命令 + 复制按钮
::    - 阿里云卡片显示「无需安装」

:: 3. 打开 设置 → 识别，选「阿里云 API」，填凭证，点开始
::    确认能正常识别（验证纯 API 链路完整）
```

## 常见问题

**Q: exe 启动后立即闪退**
PyInstaller windowed 模式无控制台看不到错误。临时改 `sub-title.spec` 的 `console=False` → `console=True` 重新构建，运行时会在控制台显示 traceback。

**Q: 打包后找不到主题/报 themes 目录缺失**
确认 `datas` 里 `("src/themes", "themes")` 没被删，且 `resource_dir()`（`paths.py`）在 frozen 时返回 `sys._MEIPASS`。

**Q: soundfile 报 "libsndfile not found"**
`_soundfile_data` 包没收集。确认 spec 里 `collect_data_files("_soundfile_data")` 存在。

**Q: 凭证存不进 keyring（退化到明文）**
keyring 的 Windows 后端没收集。确认 spec 里 `collect_submodules("keyring")` 存在。
