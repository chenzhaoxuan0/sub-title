"""PyInstaller 打包入口脚本（纯 API 模式）。

单独建一个入口而不是用 `python -m subtitle`，是为了让 PyInstaller 的依赖分析
更稳定（模块入口明确，不会被 -m 的隐式导入分析困扰）。

纯 API 模式：只打包阿里云引擎所需依赖，不含 funasr/torch/qwen_asr/faster_whisper。
用户要用本地引擎时，在「设置 → 引擎管理」页按提示 pip 安装。
"""
import os
import sys

# 让 PyInstaller 打包后能找到 src/subtitle 包。
# 开发模式：src 在项目根；打包后：PyInstaller 会把 subtitle 包冻进 _MEIPASS。
if not getattr(sys, "frozen", False):
    # 开发/源码模式：把 src 加入路径（仅运行此入口时需要）
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from subtitle.app import main

if __name__ == "__main__":
    main()
