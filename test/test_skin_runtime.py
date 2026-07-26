import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from subtitle.config import Config
from subtitle.pipeline import SubtitlePipeline
from subtitle.skin.model import AnimationClip, Keyframe, Layer, SkinDefinition, Trigger, TriggerType
from subtitle.skin.runtime import SkinRuntime


APP = QApplication.instance() or QApplication([])


class FakePanel:
    def __init__(self):
        self.runtime = None
        self.updates = 0

    def set_skin_runtime(self, runtime):
        self.runtime = runtime

    def update_skin_layers(self):
        self.updates += 1


class FakeEngine:
    def load(self):
        pass

    def feed(self, block):
        pass

    def stop(self):
        pass


class SkinRuntimeTests(unittest.TestCase):
    def test_keyword_event_starts_action_through_runtime(self):
        layer = Layer(name="tail")
        action = AnimationClip(name="wag")
        action.get_track(layer.id, "rotation").add_keyframe(Keyframe(0, 10))
        trigger = Trigger(
            name="cat keyword", trigger_type=TriggerType.KEYWORD,
            keyword="cat", action_id=action.id,
        )
        skin = SkinDefinition(layers=[layer], actions=[action], triggers=[trigger])
        panel = FakePanel()
        runtime = SkinRuntime(panel)
        with tempfile.TemporaryDirectory() as temporary:
            runtime.apply_skin(skin, Path(temporary))
            runtime.on_text("a cat appeared", False)
            self.assertIn(action.id, runtime.player.active_action_ids)
            self.assertIs(panel.runtime, runtime)
            runtime.disable()
            self.assertIsNone(panel.runtime)

    def test_audio_capture_uses_ten_hertz_blocks(self):
        config = Config()
        config.audio.target_sample_rate = 16000
        config.audio.chunk_seconds = 0.6
        pipeline = SubtitlePipeline(config, FakeEngine())
        self.assertEqual(pipeline.chunk_samples, 9600)
        self.assertEqual(pipeline.capture_block_samples, 1600)


if __name__ == "__main__":
    unittest.main()
