"""主题引擎 —— 自定义颜色、皮肤预设、主题持久化。

支持：
- 完整的颜色自定义（背景、文字、工具栏、按钮、高亮等）
- 内置预设主题（dark/light/nord/tokyo-night/solarized等）
- 用户自定义主题保存/加载/导出
- 字幕面板几何自定义（圆角、内边距、字体间距）
- 跨平台适配（Win/Mac 原生风格微调）
"""
from __future__ import annotations

import copy
import json
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from ..paths import user_data_dir, resource_dir, default_font_family


# 主题存储目录
# 打包后内嵌的只读主题预设放在资源根下；用户主题在 user_data_dir 下（可写）。
LEGACY_THEMES_DIR = resource_dir() / "themes"
THEMES_DIR = user_data_dir() / "themes"
# 回收站：软删除的自定义主题暂存这里，文件名加时间戳，可恢复
TRASH_DIR = THEMES_DIR / ".trash"

# 基础主题（黑/白）—— 绝对不可删除，作为兜底主题
PROTECTED_THEMES = frozenset({"Dark", "Light"})


@dataclass
class ThemeColors:
    """一套完整的颜色方案。"""
    # 字幕区
    subtitle_bg: str = "#1a1a1a"
    subtitle_text: str = "#f2f2f2"
    subtitle_border: str = "#333333"

    # 工具栏
    toolbar_bg: str = "#2d2d2d"
    toolbar_text: str = "#e0e0e0"

    # 按钮
    btn_bg: str = "#d8d8d8"
    btn_text: str = "#1a1a1a"
    btn_border: str = "#b0b0b0"
    btn_hover: str = "#ffffff"
    btn_disabled_bg: str = "#555555"
    btn_disabled_text: str = "#999999"

    # 下拉框
    combo_bg: str = "#2a2a2a"
    combo_text: str = "#f0f0f0"
    combo_selected: str = "#3a6ea5"

    # 强调色（滑块、选中态、高亮）
    accent: str = "#3a6ea5"
    accent_hover: str = "#4a8ec5"

    # 托盘菜单
    tray_bg: str = "#2a2a2a"
    tray_text: str = "#f0f0f0"
    tray_hover: str = "#3a6ea5"


@dataclass
class ThemeGeometry:
    """字幕面板几何参数。"""
    border_radius: int = 12          # 字幕区圆角 (px)
    padding_top: int = 8             # 内边距
    padding_bottom: int = 8
    padding_left: int = 16
    padding_right: int = 16
    toolbar_radius: int = 8          # 工具栏圆角
    btn_radius: int = 5              # 按钮圆角
    line_spacing: float = 1.4        # 行间距倍数
    font_family: str = field(default_factory=default_font_family)
    font_size: int = 22
    font_weight: int = 400           # 400=normal, 700=bold


@dataclass
class Theme:
    """一个完整的主题定义。"""
    name: str = "Dark"
    is_builtin: bool = True
    colors: ThemeColors = field(default_factory=ThemeColors)
    geometry: ThemeGeometry = field(default_factory=ThemeGeometry)
    opacity: float = 0.88            # 背景不透明度 0~1

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "is_builtin": self.is_builtin,
            "colors": asdict(self.colors),
            "geometry": asdict(self.geometry),
            "opacity": self.opacity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Theme":
        colors = ThemeColors(**{k: v for k, v in d.get("colors", {}).items()
                                if k in ThemeColors.__dataclass_fields__})
        geometry = ThemeGeometry(**{k: v for k, v in d.get("geometry", {}).items()
                                    if k in ThemeGeometry.__dataclass_fields__})
        return cls(
            name=d.get("name", "Custom"),
            is_builtin=d.get("is_builtin", False),
            colors=colors,
            geometry=geometry,
            opacity=d.get("opacity", 0.88),
        )


# ============================================================
# 内置预设主题
# ============================================================

BUILTIN_THEMES: dict[str, Theme] = {}


def _register(theme: Theme):
    BUILTIN_THEMES[theme.name] = theme


_register(Theme(
    name="Dark",
    colors=ThemeColors(),
    geometry=ThemeGeometry(),
))

_register(Theme(
    name="Light",
    colors=ThemeColors(
        subtitle_bg="#f5f5f5",
        subtitle_text="#1a1a1a",
        subtitle_border="#dddddd",
        toolbar_bg="#e8e8e8",
        toolbar_text="#333333",
        btn_bg="#3a3a3a",
        btn_text="#ffffff",
        btn_border="#555555",
        btn_hover="#1a1a1a",
        btn_disabled_bg="#bbbbbb",
        btn_disabled_text="#888888",
        combo_bg="#ffffff",
        combo_text="#1a1a1a",
        combo_selected="#b8d4f0",
        accent="#2979ff",
        accent_hover="#448aff",
        tray_bg="#f5f5f5",
        tray_text="#333333",
        tray_hover="#e0e0e0",
    ),
    geometry=ThemeGeometry(),
    opacity=0.92,
))

_register(Theme(
    name="Nord",
    colors=ThemeColors(
        subtitle_bg="#2e3440",
        subtitle_text="#eceff4",
        subtitle_border="#3b4252",
        toolbar_bg="#3b4252",
        toolbar_text="#d8dee9",
        btn_bg="#4c566a",
        btn_text="#eceff4",
        btn_border="#4c566a",
        btn_hover="#5e6779",
        btn_disabled_bg="#3b4252",
        btn_disabled_text="#616e88",
        combo_bg="#3b4252",
        combo_text="#eceff4",
        combo_selected="#5e81ac",
        accent="#88c0d0",
        accent_hover="#8fbcbb",
        tray_bg="#2e3440",
        tray_text="#eceff4",
        tray_hover="#4c566a",
    ),
    geometry=ThemeGeometry(border_radius=10, btn_radius=6),
    opacity=0.92,
))

_register(Theme(
    name="Tokyo Night",
    colors=ThemeColors(
        subtitle_bg="#1a1b26",
        subtitle_text="#c0caf5",
        subtitle_border="#292e42",
        toolbar_bg="#24283b",
        toolbar_text="#a9b1d6",
        btn_bg="#414868",
        btn_text="#c0caf5",
        btn_border="#565f89",
        btn_hover="#565f89",
        btn_disabled_bg="#292e42",
        btn_disabled_text="#565f89",
        combo_bg="#24283b",
        combo_text="#c0caf5",
        combo_selected="#7aa2f7",
        accent="#7aa2f7",
        accent_hover="#89b4fa",
        tray_bg="#1a1b26",
        tray_text="#c0caf5",
        tray_hover="#414868",
    ),
    geometry=ThemeGeometry(border_radius=14, btn_radius=7),
    opacity=0.90,
))

_register(Theme(
    name="Solarized Dark",
    colors=ThemeColors(
        subtitle_bg="#002b36",
        subtitle_text="#93a1a1",
        subtitle_border="#073642",
        toolbar_bg="#073642",
        toolbar_text="#93a1a1",
        btn_bg="#586e75",
        btn_text="#fdf6e3",
        btn_border="#657b83",
        btn_hover="#657b83",
        btn_disabled_bg="#073642",
        btn_disabled_text="#586e75",
        combo_bg="#073642",
        combo_text="#93a1a1",
        combo_selected="#268bd2",
        accent="#268bd2",
        accent_hover="#2aa198",
        tray_bg="#002b36",
        tray_text="#93a1a1",
        tray_hover="#073642",
    ),
    geometry=ThemeGeometry(border_radius=8),
    opacity=0.93,
))

_register(Theme(
    name="Catppuccin Mocha",
    colors=ThemeColors(
        subtitle_bg="#1e1e2e",
        subtitle_text="#cdd6f4",
        subtitle_border="#313244",
        toolbar_bg="#181825",
        toolbar_text="#bac2de",
        btn_bg="#45475a",
        btn_text="#cdd6f4",
        btn_border="#585b70",
        btn_hover="#585b70",
        btn_disabled_bg="#313244",
        btn_disabled_text="#6c7086",
        combo_bg="#181825",
        combo_text="#cdd6f4",
        combo_selected="#89b4fa",
        accent="#89b4fa",
        accent_hover="#b4befe",
        tray_bg="#1e1e2e",
        tray_text="#cdd6f4",
        tray_hover="#45475a",
    ),
    geometry=ThemeGeometry(border_radius=16, btn_radius=8),
    opacity=0.91,
))

_register(Theme(
    name="Dracula",
    colors=ThemeColors(
        subtitle_bg="#282a36",
        subtitle_text="#f8f8f2",
        subtitle_border="#44475a",
        toolbar_bg="#21222c",
        toolbar_text="#f8f8f2",
        btn_bg="#44475a",
        btn_text="#f8f8f2",
        btn_border="#6272a4",
        btn_hover="#6272a4",
        btn_disabled_bg="#282a36",
        btn_disabled_text="#6272a4",
        combo_bg="#21222c",
        combo_text="#f8f8f2",
        combo_selected="#bd93f9",
        accent="#bd93f9",
        accent_hover="#ff79c6",
        tray_bg="#282a36",
        tray_text="#f8f8f2",
        tray_hover="#44475a",
    ),
    geometry=ThemeGeometry(border_radius=12, btn_radius=6),
    opacity=0.92,
))


# ============================================================
# 主题管理器
# ============================================================

class ThemeManager:
    """管理主题的加载、保存、切换。"""

    def __init__(
        self,
        themes_dir: Optional[Path] = None,
        legacy_themes_dir: Optional[Path] = LEGACY_THEMES_DIR,
    ):
        self._themes_dir = Path(themes_dir) if themes_dir is not None else THEMES_DIR
        self._trash_dir = self._themes_dir / ".trash"
        self._legacy_themes_dir = (
            Path(legacy_themes_dir) if legacy_themes_dir is not None else None
        )
        self._current: Theme = BUILTIN_THEMES["Dark"]
        self._custom_themes: dict[str, Theme] = {}
        # 内置主题的"原始快照" —— 启动时深拷贝一份，
        # 用来支持"恢复内置默认值"，避免用户改完 Dark 后回不去。
        self._builtin_snapshots: dict[str, Theme] = {
            name: copy.deepcopy(t) for name, t in BUILTIN_THEMES.items()
        }
        self._migrate_legacy_themes()
        self._load_custom_themes()

    @property
    def current(self) -> Theme:
        return self._current

    def get_all_themes(self) -> dict[str, Theme]:
        """返回所有可用主题（内置 + 自定义）。"""
        all_themes = dict(BUILTIN_THEMES)
        all_themes.update(self._custom_themes)
        return all_themes

    def get_theme(self, name: str) -> Optional[Theme]:
        return self.get_all_themes().get(name)

    def apply_theme(self, name: str) -> bool:
        """切换到指定主题。"""
        theme = self.get_theme(name)
        if theme:
            self._current = theme
            return True
        return False

    def apply_theme_obj(self, theme: Theme):
        """直接应用一个 Theme 对象。"""
        self._current = theme

    def reset_builtin(self, name: str) -> bool:
        """把内置主题恢复为出厂默认值。

        适用场景：用户改了 Dark 的颜色/几何后想回到最初的暗色风格。
        自定义主题不能 reset（请用 delete_custom_theme 后再 create_blank_theme 重建）。
        """
        if name not in BUILTIN_THEMES:
            return False
        if name not in self._builtin_snapshots:
            return False
        fresh = copy.deepcopy(self._builtin_snapshots[name])
        # 记下旧引用，因为赋值后 BUILTIN_THEMES[name] 就是 fresh 了
        old_ref = BUILTIN_THEMES[name]
        BUILTIN_THEMES[name] = fresh
        # 如果用户当前正用着这个被污染的内置，也一并刷新
        if self._current is old_ref:
            self._current = fresh
        return True

    def create_blank_theme(self, name: str) -> Theme:
        """从空白默认值创建一个新主题（不复用当前主题的任何字段）。

        适用场景：用户想做一个完全独立的新风格，而不是"基于当前主题改"。
        返回的主题尚未保存到磁盘，需要调用 save_custom_theme。
        """
        # 用 "Dark" 默认 ThemeColors/ThemeGeometry 作为基线（保留原始配色，
        # 方便用户直接调亮/调暗得到新风格；想从纯黑纯白开始也可以手动改）
        return Theme(
            name=name,
            is_builtin=False,
            colors=ThemeColors(),
            geometry=ThemeGeometry(),
            opacity=0.88,
        )

    def save_custom_theme(self, theme: Theme, *, new_name: Optional[str] = None) -> bool:
        """保存主题为自定义（深拷贝，不污染原对象）。

        关键：无论传入的是内置主题还是自定义主题，函数内部都会 deep-copy 一份再写盘。
        这样调用方继续修改原对象时不会影响磁盘 / 内置主题 / _current 指向的对象。

        如果 new_name 不为空，用它作为保存后的主题名（调用方无需自己改 theme.name）。
        如果传入的 theme 就是当前 _current，会把 _current 切到新 copy，
        之后所有"应用颜色/几何"都会改在 copy 上，不会再污染内置。
        """
        copy_theme = copy.deepcopy(theme)
        if new_name:
            new_name = new_name.strip()
            if not new_name:
                return False
            copy_theme.name = new_name
        if not copy_theme.name:
            return False
        if copy_theme.name in BUILTIN_THEMES:
            return False  # 不允许用内置主题的名字
        copy_theme.is_builtin = False
        self._themes_dir.mkdir(parents=True, exist_ok=True)
        if not self._write_theme_file(copy_theme):
            print(f"[theme] 保存主题失败: {copy_theme.name}")
            return False
        # 替换自定义 dict（如果同名旧版本存在，覆盖）
        self._custom_themes[copy_theme.name] = copy_theme
        # 如果是当前主题的覆写，把 _current 也切到新 copy
        if self._current is theme:
            self._current = copy_theme
        return True

    def persist_custom_theme(self, theme: Optional[Theme] = None) -> bool:
        """Persist edits made to an existing custom theme."""
        theme = theme or self._current
        if not theme.name or theme.name in BUILTIN_THEMES:
            return False
        theme.is_builtin = False
        self._themes_dir.mkdir(parents=True, exist_ok=True)
        if not self._write_theme_file(theme):
            return False
        self._custom_themes[theme.name] = theme
        return True

    def rename_theme(self, old_name: str, new_name: str) -> bool:
        """重命名主题。

        - 内置主题 → 复制为新的自定义主题（新名），内置本身保持不变。
        - 自定义主题 → 文件名/字段/dict 全部更新；旧名对应的文件进回收站（可恢复）。
        - 基础主题（Dark/Light）虽然是内置，不影响重命名逻辑（重命名内置 = 复制为新自定义）。
        """
        new_name = new_name.strip()
        if not new_name or old_name == new_name:
            return False
        if new_name in BUILTIN_THEMES:
            return False
        if new_name in self._custom_themes:
            return False
        if old_name in BUILTIN_THEMES:
            # 内置：拷贝一份按新名存为自定义；内置不动
            return self.save_custom_theme(BUILTIN_THEMES[old_name], new_name=new_name)
        if old_name in self._custom_themes:
            old_theme = self._custom_themes[old_name]
            was_current = self._current is old_theme
            # 旧名软删除进回收站（保底可恢复）
            self._custom_themes.pop(old_name, None)
            old_path = self._themes_dir / f"{self._sanitize_name(old_name)}.json"
            if old_path.exists():
                if not self._move_to_trash(old_path):
                    # 软删除失败，回滚 dict
                    self._custom_themes[old_name] = old_theme
                    return False
            # 写新名
            new_theme = copy.deepcopy(old_theme)
            new_theme.name = new_name
            new_theme.is_builtin = False
            if not self._write_theme_file(new_theme):
                # 新文件写失败：回滚（把旧主题从回收站放回）
                self._custom_themes[old_name] = old_theme
                return False
            self._custom_themes[new_name] = new_theme
            if was_current:
                self._current = new_theme
            return True
        return False

    def delete_custom_theme(self, name: str) -> bool:
        """软删除自定义主题（文件移到 themes/.trash/，可从回收站恢复）。

        内置主题（包括基础 Dark/Light）一律不可删除。
        """
        if name in BUILTIN_THEMES:
            return False
        self._custom_themes.pop(name, None)
        path = self._themes_dir / f"{self._sanitize_name(name)}.json"
        if not path.exists():
            return True  # 内存已清，文件本来就没有，视为成功
        if not self._move_to_trash(path):
            return False
        return True

    # ---------- 回收站 ----------
    def list_trashed_themes(self) -> list[dict]:
        """列出回收站里的主题（按删除时间倒序）。

        返回每项: {"filename", "original_name", "trashed_at", "data"}
        """
        if not self._trash_dir.exists():
            return []
        result: list[dict] = []
        for f in self._trash_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                result.append({
                    "filename": f.name,
                    "original_name": data.get("name", f.stem),
                    "trashed_at": f.stat().st_mtime,
                    "data": data,
                })
            except Exception:
                continue
        result.sort(key=lambda x: x["trashed_at"], reverse=True)
        return result

    def restore_trashed_theme(self, filename: str, *, new_name: Optional[str] = None) -> Optional[Theme]:
        """从回收站恢复一个主题。

        - new_name 为空：用原始名恢复（如果该名已被占用则失败）。
        - new_name 不为空：用新名恢复（也用于重命名后还想换名的情况）。
        """
        src = self._trash_dir / filename
        if not src.exists():
            return None
        try:
            with open(src, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            theme = Theme.from_dict(data)
            if new_name:
                theme.name = new_name.strip()
            if not theme.name:
                return None
            if theme.name in BUILTIN_THEMES:
                return None
            if theme.name in self._custom_themes:
                return None  # 重名
            # 移回 themes/
            self._themes_dir.mkdir(parents=True, exist_ok=True)
            dst = self._themes_dir / f"{self._sanitize_name(theme.name)}.json"
            src.rename(dst)
            theme.is_builtin = False
            self._custom_themes[theme.name] = theme
            return theme
        except Exception:
            return None

    def delete_trashed_theme_permanently(self, filename: str) -> bool:
        """从回收站永久删除一个主题（不可恢复）。"""
        path = self._trash_dir / filename
        try:
            if path.exists():
                path.unlink()
            return True
        except Exception:
            return False

    def empty_trash(self) -> int:
        """永久清空回收站，返回被删的数量。"""
        if not self._trash_dir.exists():
            return 0
        count = 0
        for f in self._trash_dir.glob("*.json"):
            try:
                f.unlink()
                count += 1
            except Exception:
                continue
        return count

    def export_theme(self, theme: Theme, path: Path) -> bool:
        """导出主题到指定路径。"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(theme.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def import_theme(self, path: Path) -> Optional[Theme]:
        """从文件导入主题。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            theme = Theme.from_dict(data)
            theme.is_builtin = False
            self.save_custom_theme(theme)
            return theme
        except Exception as e:
            print(f"[theme] 导入主题失败: {e}")
            return None

    def _load_custom_themes(self):
        """启动时加载 themes/ 目录下的自定义主题。"""
        if not self._themes_dir.exists():
            return
        for f in self._themes_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                theme = Theme.from_dict(data)
                theme.is_builtin = False
                self._custom_themes[theme.name] = theme
            except Exception:
                continue

    def _write_theme_file(self, theme: Theme) -> bool:
        """把 theme 写到 themes/<name>.json。"""
        path = self._themes_dir / f"{self._sanitize_name(theme.name)}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(theme.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"[theme] 写文件失败: {e}")
            return False

    def _move_to_trash(self, path: Path) -> bool:
        """把 themes/ 下的文件移到 themes/.trash/，文件名加时间戳防冲突。"""
        try:
            self._trash_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            dst = self._trash_dir / f"{path.stem}_{ts}{path.suffix}"
            # 极端情况下同一秒冲突
            while dst.exists():
                ts += 1
                dst = self._trash_dir / f"{path.stem}_{ts}{path.suffix}"
            path.rename(dst)
            return True
        except Exception as e:
            print(f"[theme] 移到回收站失败: {e}")
            return False

    def _migrate_legacy_themes(self) -> None:
        source = self._legacy_themes_dir
        if source is None or not source.exists():
            return
        try:
            if source.resolve() == self._themes_dir.resolve():
                return
        except OSError:
            return
        self._themes_dir.mkdir(parents=True, exist_ok=True)
        for path in source.glob("*.json"):
            destination = self._themes_dir / path.name
            if destination.exists():
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    Theme.from_dict(json.load(handle))
                shutil.copy2(path, destination)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """文件名安全化。"""
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


# 全局单例
_theme_manager: Optional[ThemeManager] = None


def get_theme_manager() -> ThemeManager:
    global _theme_manager
    if _theme_manager is None:
        _theme_manager = ThemeManager()
    return _theme_manager
