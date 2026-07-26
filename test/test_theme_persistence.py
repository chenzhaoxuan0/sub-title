import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from subtitle.config import Config
from subtitle.ui.settings_dialog import SettingsDialog
from subtitle.ui.subtitle_panel import SubtitlePanel
from subtitle.ui.theme_engine import Theme, ThemeManager


APP = QApplication.instance() or QApplication([])


class ThemePersistenceTests(unittest.TestCase):
    def test_legacy_theme_migrates_and_edits_persist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            destination = root / "user" / "themes"
            legacy.mkdir()
            pink = Theme(name="pink", is_builtin=False)
            (legacy / "pink.json").write_text(
                json.dumps(pink.to_dict(), ensure_ascii=False), encoding="utf-8"
            )
            manager = ThemeManager(
                themes_dir=destination,
                legacy_themes_dir=legacy,
            )
            self.assertTrue((destination / "pink.json").is_file())
            self.assertTrue(manager.apply_theme("pink"))
            manager.current.colors.subtitle_bg = "#ff69b4"
            self.assertTrue(manager.persist_custom_theme())
            reloaded = ThemeManager(
                themes_dir=destination,
                legacy_themes_dir=None,
            )
            self.assertEqual(
                reloaded.get_theme("pink").colors.subtitle_bg,
                "#ff69b4",
            )

    def test_settings_color_apply_writes_custom_theme(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            manager = ThemeManager(themes_dir=destination, legacy_themes_dir=None)
            self.assertTrue(manager.save_custom_theme(Theme(name="pink")))
            self.assertTrue(manager.apply_theme("pink"))
            config = Config()
            panel = SubtitlePanel(config.ui)
            panel._theme_mgr = manager
            dialog = SettingsDialog(config, panel)
            dialog._theme_mgr = manager
            dialog._reload_theme_combo(select="pink")
            dialog.color_buttons["subtitle_bg"].set_color("#ff1493")
            dialog._on_apply_colors()
            reloaded = ThemeManager(themes_dir=destination, legacy_themes_dir=None)
            self.assertEqual(
                reloaded.get_theme("pink").colors.subtitle_bg,
                "#ff1493",
            )
            dialog.close()
            panel._force_quit = True
            panel.close()


if __name__ == "__main__":
    unittest.main()
