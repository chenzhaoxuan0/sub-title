import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from subtitle.skin.events import TriggerManager
from subtitle.skin.model import AnimationClip, SkinDefinition, Trigger, TriggerType


APP = QApplication.instance() or QApplication([])


class TriggerManagerTests(unittest.TestCase):
    def setUp(self):
        self.action = AnimationClip(name="wag")

    def manager(self, trigger):
        trigger.action_id = self.action.id
        skin = SkinDefinition(actions=[self.action], triggers=[trigger])
        manager = TriggerManager(skin)
        fired = []
        manager.action_triggered.connect(
            lambda action_id, priority, retrigger: fired.append(action_id)
        )
        return manager, fired

    def test_text_keyword_regex_and_final(self):
        cases = [
            (Trigger(trigger_type=TriggerType.KEYWORD, keyword="cat"), "Cat here", False),
            (Trigger(trigger_type=TriggerType.REGEX, pattern=r"tail\d"), "TAIL2", False),
            (Trigger(trigger_type=TriggerType.ON_FINAL), "done", True),
            (Trigger(trigger_type=TriggerType.ON_PARTIAL), "part", False),
        ]
        for trigger, text, final in cases:
            manager, fired = self.manager(trigger)
            manager.on_text_received(text, final)
            self.assertEqual(fired, [self.action.id])

    def test_window_click_and_volume(self):
        trigger = Trigger(trigger_type=TriggerType.ON_CLICK, target_layer_id="tail")
        manager, fired = self.manager(trigger)
        manager.on_layer_clicked("body")
        manager.on_layer_clicked("tail")
        self.assertEqual(fired, [self.action.id])
        volume = Trigger(
            trigger_type=TriggerType.VOLUME_ABOVE, volume_threshold=0.5, hold_seconds=0
        )
        manager, fired = self.manager(volume)
        manager.on_audio_level(0.6)
        self.assertEqual(fired, [self.action.id])

    def test_cooldown_and_probability(self):
        trigger = Trigger(trigger_type=TriggerType.ON_START, cooldown=10, probability=1)
        manager, fired = self.manager(trigger)
        with patch("subtitle.skin.events.time.monotonic", side_effect=[10, 10, 11, 11]):
            manager.on_recognition_start()
            manager.on_recognition_start()
        self.assertEqual(fired, [self.action.id])


if __name__ == "__main__":
    unittest.main()
