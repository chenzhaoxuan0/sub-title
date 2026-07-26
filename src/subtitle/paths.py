"""跨平台用户数据路径 —— 配置 / 缓存 / 日志的统一位置。

打包成 EXE/DMG 之后，**不能**把任何运行时数据写到 EXE 同级目录：
- Windows: `C:\\Program Files\\sub-title\\` 默认不可写（UAC）
- macOS:   `sub-title.app` 是只读签名过的，更新时被覆盖

业界标准（XDG Base Directory 规范族）：

| OS       | 配置目录                                                    |
|----------|------------------------------------------------------------|
| Windows  | `%APPDATA%\\sub-title\\`（`C:\\Users\\<user>\\AppData\\Roaming\\sub-title\\`）|
| macOS    | `~/Library/Application Support/sub-title/`                 |
| Linux    | `~/.config/sub-title/`                                     |

优先用 `platformdirs` 库（轻量、标准）；库没装时退化到 OS 自己的环境变量。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

APP_NAME = "sub-title"
APP_AUTHOR = "chenzhaoxuan0"   # Windows 上会用作 %APPDATA%/Author/AppName/ 的中间目录


def _platformdirs():
    """懒加载 platformdirs，避免 import 阶段硬依赖。"""
    try:
        import platformdirs  # type: ignore
        return platformdirs
    except ImportError:
        return None


def user_config_dir() -> Path:
    """用户配置目录（config.yaml 放这里）。"""
    pd = _platformdirs()
    if pd is not None:
        return Path(pd.user_config_dir(APP_NAME, APP_AUTHOR, roaming=False))
    # 退化（platformdirs 不可用时）
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME


def user_data_dir() -> Path:
    """用户数据目录（模型缓存、皮肤等；当前主要用 user_config_dir，保留扩展位）。"""
    pd = _platformdirs()
    if pd is not None:
        return Path(pd.user_data_dir(APP_NAME, APP_AUTHOR, roaming=False))
    return user_config_dir()  # 退化用同一目录


def user_cache_dir() -> Path:
    """用户缓存目录（临时文件、日志）。"""
    pd = _platformdirs()
    if pd is not None:
        return Path(pd.user_cache_dir(APP_NAME, APP_AUTHOR))
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_NAME


# ============================================================
# 旧 config.yaml（项目根 / CWD）→ 新位置 迁移
# ============================================================
def config_path() -> Path:
    """config.yaml 的当前位置（用户目录）。"""
    return user_config_dir() / "config.yaml"


def legacy_config_candidates() -> list[Path]:
    """旧版本可能把 config.yaml 放在这些位置，逐个检查迁移。"""
    candidates: list[Path] = []
    # 1) 当前工作目录
    cwd_yaml = Path.cwd() / "config.yaml"
    if cwd_yaml.exists():
        candidates.append(cwd_yaml)
    # 2) 项目根（开发模式：__file__ 向上两级）
    try:
        proj_root = Path(__file__).resolve().parents[2]
        proj_yaml = proj_root / "config.yaml"
        if proj_yaml.exists() and proj_yaml not in candidates:
            candidates.append(proj_yaml)
    except Exception:
        pass
    return candidates


def migrate_legacy_config(legacy: Path, new_path: Path) -> bool:
    """把旧位置 config.yaml 复制到新位置，并把老文件改名存档。

    - 新位置已有 config.yaml：跳过，不覆盖用户最新数据
    - 老文件改名加 `.migrated`（带时间戳防重名）避免下次启动再迁

    返回 True 表示执行了迁移。
    """
    if not legacy.exists() or not legacy.is_file():
        return False
    if new_path.exists():
        return False
    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        # 复制（保留 mtime 等元数据）
        import shutil
        shutil.copy2(legacy, new_path)
        # 老文件改名存档
        backup = legacy.with_name(legacy.name + ".migrated")
        if backup.exists():
            ts = int(time.time())
            backup = legacy.with_name(f"{legacy.name}.migrated.{ts}")
        legacy.rename(backup)
        return True
    except Exception as e:
        print(f"[paths] 迁移旧 config.yaml 失败: {e}")
        return False
