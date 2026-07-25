"""主题引擎 —— 自定义颜色、皮肤预设、主题持久化。

支持：
- 完整的颜色自定义（背景、文字、工具栏、按钮、高亮等）
- 内置预设主题（dark/light/nord/tokyo-night/solarized等）
- 用户自定义主题保存/加载/导出
- 字幕面板几何自定义（圆角、内边距、字体间距）
- 跨平台适配（Win/Mac 原生风格微调）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# 主题存储目录
THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"


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
    font_family: str = "Microsoft YaHei"
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

    def __init__(self):
        self._current: Theme = BUILTIN_THEMES["Dark"]
        self._custom_themes: dict[str, Theme] = {}
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

    def save_custom_theme(self, theme: Theme) -> bool:
        """保存自定义主题到 themes/ 目录。"""
        theme.is_builtin = False
        THEMES_DIR.mkdir(parents=True, exist_ok=True)
        path = THEMES_DIR / f"{self._sanitize_name(theme.name)}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(theme.to_dict(), f, ensure_ascii=False, indent=2)
            self._custom_themes[theme.name] = theme
            return True
        except Exception as e:
            print(f"[theme] 保存主题失败: {e}")
            return False

    def delete_custom_theme(self, name: str) -> bool:
        """删除自定义主题。"""
        if name in BUILTIN_THEMES:
            return False
        path = THEMES_DIR / f"{self._sanitize_name(name)}.json"
        try:
            if path.exists():
                path.unlink()
            self._custom_themes.pop(name, None)
            return True
        except Exception:
            return False

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
        if not THEMES_DIR.exists():
            return
        for f in THEMES_DIR.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                theme = Theme.from_dict(data)
                theme.is_builtin = False
                self._custom_themes[theme.name] = theme
            except Exception:
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
