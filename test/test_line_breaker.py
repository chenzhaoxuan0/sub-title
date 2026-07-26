"""LineBreaker 单元测试。

跑法（在 subtitle conda 环境，项目根目录）：
  PYTHONPATH=src python -m unittest test.test_line_breaker -v
"""
import unittest

from subtitle.ui.line_breaker import LineBreaker


class LineBreakerTests(unittest.TestCase):
    def test_disabled_passthrough(self):
        """enabled=False 时原样返回，不插任何换行。"""
        b = LineBreaker(enabled=False)
        self.assertEqual(b.feed("你好。世界！", False), "你好。世界！")
        self.assertEqual(b.feed("你好", True), "你好")

    def test_empty_text_passthrough(self):
        """空文本原样返回。"""
        b = LineBreaker()
        self.assertEqual(b.feed("", False), "")
        self.assertEqual(b.feed("", True), "")

    def test_sentence_end_punctuation(self):
        """句末标点后插换行（中文）。"""
        b = LineBreaker()
        self.assertEqual(b.feed("你好。世界", False), "你好。\n世界")
        self.assertEqual(b.feed("好吗？", False), "好吗？\n")
        self.assertEqual(b.feed("走了！", False), "走了！\n")
        self.assertEqual(b.feed("等等…", False), "等等…\n")

    def test_english_punctuation(self):
        """英文 ? ! 触发换行；句号 . 不触发（避免误切小数 3.14 / 缩写 Mr. / 文件名 a.py）。"""
        b = LineBreaker()
        self.assertEqual(b.feed("Really?", False), "Really?\n")
        self.assertEqual(b.feed("Great!", False), "Great!\n")
        # 句号 . 不换行（与中文 。 不同）
        self.assertEqual(b.feed("Hello. World", False), "Hello. World")
        self.assertEqual(b.feed("3.14 and 2.5", False), "3.14 and 2.5")

    def test_comma_no_break(self):
        """逗号、顿号等句内停顿不触发换行。"""
        b = LineBreaker()
        self.assertEqual(b.feed("第一，第二，第三", False), "第一，第二，第三")
        self.assertEqual(b.feed("a, b, c", False), "a, b, c")

    def test_is_final_forces_break(self):
        """is_final=True 在末尾强制换行（SenseVoice 段末 / Aliyun 句末）。"""
        b = LineBreaker()
        # 无标点但有 final → 末尾换行
        self.assertEqual(b.feed("一段话", True), "一段话\n")
        # 已有标点 + final → 不重复换行
        self.assertEqual(b.feed("一段话。", True), "一段话。\n")

    def test_no_punctuation_no_final_continuous(self):
        """FunASR 未开 punc 场景：无标点无 final → 原样返回，保持连续文本。"""
        b = LineBreaker()
        self.assertEqual(b.feed("裸文本没有任何标点", False), "裸文本没有任何标点")

    def test_multiple_sentences(self):
        """多句混合标点 → 每句末换行。"""
        b = LineBreaker()
        result = b.feed("第一。第二！第三？", False)
        self.assertEqual(result, "第一。\n第二！\n第三？\n")

    def test_set_enabled_toggles(self):
        """set_enabled 运行时切换开关。"""
        b = LineBreaker(enabled=False)
        self.assertEqual(b.feed("你好。", False), "你好。")
        b.set_enabled(True)
        self.assertEqual(b.feed("你好。", False), "你好。\n")
        b.set_enabled(False)
        self.assertEqual(b.feed("你好。", False), "你好。")

    def test_realistic_sensevoice_output(self):
        """模拟 SenseVoice 真实输出（实测所得）+ is_final。"""
        b = LineBreaker()
        text = "哎，那是你不对啊，本来这种想法就是不对的。第一，用功读书是为了你自己。第二，奖金是要努力过后才拿得到的。"
        result = b.feed(text, True)
        # 三句末各一个换行
        expected = (
            "哎，那是你不对啊，本来这种想法就是不对的。\n"
            "第一，用功读书是为了你自己。\n"
            "第二，奖金是要努力过后才拿得到的。\n"
        )
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
