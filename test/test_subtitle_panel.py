import unittest

from PySide6.QtWidgets import QApplication

from subtitle.config import Config
from subtitle.ui.subtitle_panel import SubtitlePanel


APP = QApplication.instance() or QApplication([])


class SubtitlePanelTests(unittest.TestCase):
    def test_final_subtitle_does_not_leave_a_blank_last_line(self):
        panel = SubtitlePanel(Config().ui)
        try:
            panel._on_text_appended("第一句", True)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "第一句")

            panel._on_text_appended("第二句", True)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "第一句\n第二句")
        finally:
            panel.deleteLater()
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
