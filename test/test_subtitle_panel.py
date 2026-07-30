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

    def test_partial_overwrites_interim(self):
        # 同 key 连续 partial 覆盖当前行，不堆积
        panel = SubtitlePanel(Config().ui)
        try:
            for t in ["你", "你好", "你好吗"]:
                panel._on_text_appended(t, False)
                panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "你好吗")
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_final_commits_and_starts_new_line(self):
        # partial → final 定稿 → 新 partial 另起一行
        panel = SubtitlePanel(Config().ui)
        try:
            panel._on_text_appended("你好", False)
            panel._flush_pending_text()
            panel._on_text_appended("你好。", True)
            panel._flush_pending_text()
            panel._on_text_appended("再见", False)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "你好。\n再见")
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_partial_with_punctuation_multiline(self):
        # partial 不过 LineBreaker：含句末标点也保持单段（避免覆盖时段数变化、位置跳动）
        panel = SubtitlePanel(Config().ui)
        try:
            panel._on_text_appended("你好。再见", False)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "你好。再见")
            panel._on_text_appended("你好。再见吗", False)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "你好。再见吗")
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_dual_source_interleave(self):
        # 双源 partial 交错：各自覆盖纠错，不互删
        panel = SubtitlePanel(Config().ui)
        try:
            panel._on_text_appended("系统", False, "system")
            panel._flush_pending_text()
            panel._on_text_appended("麦克", False, "mic")
            panel._flush_pending_text()
            panel._on_text_appended("系统音", False, "system")
            panel._flush_pending_text()
            text = panel.view.toPlainText()
            self.assertIn("系统音", text)   # system 已纠错
            self.assertIn("麦克", text)     # mic 保留
        finally:
            panel.deleteLater()
            APP.processEvents()

    # ---- 拆分模式（仅 funasr nano 流式：set_split_sources）----

    def test_split_punctuation_newline_and_append(self):
        # 句号换行；当前句 append-only 追加
        panel = SubtitlePanel(Config().ui)
        panel.set_split_sources({"system"})
        try:
            panel._on_text_appended("你好。再见", False)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "你好。\n再见")
            panel._on_text_appended("你好。再见吗", False)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "你好。\n再见吗")
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_split_multi_sentence_promote(self):
        # 多句号：当前句定稿进历史、新句起最底行
        panel = SubtitlePanel(Config().ui)
        panel.set_split_sources({"system"})
        try:
            panel._on_text_appended("A", False)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "A")
            panel._on_text_appended("A。B", False)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "A。\nB")
            panel._on_text_appended("A。B。C", False)
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "A。\nB。\nC")
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_split_history_correct_current_kept(self):
        # 历史可纠错；当前句不动
        panel = SubtitlePanel(Config().ui)
        panel.set_split_sources({"system"})
        try:
            panel._on_text_appended("你好。再见", False)
            panel._flush_pending_text()
            panel._on_text_appended("你好啊。再见", False)   # 历史"你好。"→"你好啊。"
            panel._flush_pending_text()
            text = panel.view.toPlainText()
            self.assertIn("你好啊。", text)   # 历史纠错生效
            self.assertIn("再见", text)        # 当前句保留
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_split_final_replaces_live(self):
        # final 取代活历史+当前，不重复
        panel = SubtitlePanel(Config().ui)
        panel.set_split_sources({"system"})
        try:
            panel._on_text_appended("你好。再见", False)
            panel._flush_pending_text()
            panel._on_text_appended("你好。再见。", True)    # final 定稿
            panel._flush_pending_text()
            self.assertEqual(panel.view.toPlainText(), "你好。\n再见。")
        finally:
            panel.deleteLater()
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
