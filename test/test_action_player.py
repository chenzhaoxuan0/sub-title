import unittest

from PySide6.QtWidgets import QApplication

from subtitle.skin.action_player import ActionPlayer
from subtitle.skin.model import AnimationClip, Keyframe, Layer, SkinDefinition


APP = QApplication.instance() or QApplication([])


def clip(name, layer_id, priority=0, duration=1.0, value=10):
    action = AnimationClip(name=name, duration=duration, priority=priority)
    track = action.get_track(layer_id, "x")
    track.add_keyframe(Keyframe(0, 0))
    track.add_keyframe(Keyframe(duration, value))
    return action


class ActionPlayerTests(unittest.TestCase):
    def setUp(self):
        self.layer_a = Layer(name="a")
        self.layer_b = Layer(name="b")
        self.skin = SkinDefinition(layers=[self.layer_a, self.layer_b])

    def test_parallel_and_restore(self):
        first = clip("first", self.layer_a.id)
        second = clip("second", self.layer_b.id)
        self.skin.actions = [first, second]
        player = ActionPlayer(self.skin)
        self.assertTrue(player._play_at(first.id, None, 10))
        self.assertTrue(player._play_at(second.id, None, 10))
        state, _ = player._tick_at(10.5)
        self.assertAlmostEqual(state[self.layer_a.id]["x"], 5)
        self.assertAlmostEqual(state[self.layer_b.id]["x"], 5)
        state, _ = player._tick_at(11.1)
        self.assertEqual(state, {})
        player.stop_all()

    def test_priority_interrupt_and_queue(self):
        low = clip("low", self.layer_a.id, priority=0, duration=2)
        high = clip("high", self.layer_a.id, priority=10, duration=1)
        queued = clip("queued", self.layer_a.id, priority=5, duration=1)
        self.skin.actions = [low, high, queued]
        player = ActionPlayer(self.skin)
        self.assertTrue(player._play_at(low.id, None, 20))
        self.assertTrue(player._play_at(high.id, None, 20.1))
        self.assertNotIn(low.id, player.active_action_ids)
        self.assertFalse(player._play_at(queued.id, None, 20.2))
        player._tick_at(21.2)
        self.assertIn(queued.id, player.active_action_ids)
        player.stop_all()

    def test_optional_retrigger_restarts_same_action(self):
        action = clip("repeat", self.layer_a.id, duration=2)
        self.skin.actions = [action]
        player = ActionPlayer(self.skin)
        self.assertTrue(player._play_at(action.id, None, 30))
        self.assertFalse(player._play_at(action.id, None, 30.5))
        self.assertTrue(player._play_at(action.id, None, 30.5, allow_retrigger=True))
        state, _ = player._tick_at(31)
        self.assertAlmostEqual(state[self.layer_a.id]["x"], 2.5)
        player.stop_all()

    def test_playback_duration_maps_real_time_to_source_timeline(self):
        action = clip("slow", self.layer_a.id, duration=1, value=10)
        action.playback_duration = 4
        self.skin.actions = [action]
        player = ActionPlayer(self.skin)

        self.assertTrue(player._play_at(action.id, None, 40))
        state, layer_times = player._tick_at(42)

        self.assertAlmostEqual(state[self.layer_a.id]["x"], 5)
        self.assertAlmostEqual(layer_times[self.layer_a.id], 0.5)
        player.stop_all()

    def test_infinite_forward_loop_remains_active_and_wraps(self):
        action = clip("loop", self.layer_a.id, duration=1, value=10)
        action.loop_forever = True
        self.skin.actions = [action]
        player = ActionPlayer(self.skin)

        self.assertTrue(player._play_at(action.id, None, 50))
        state, layer_times = player._tick_at(53.25)

        self.assertIn(action.id, player.active_action_ids)
        self.assertAlmostEqual(state[self.layer_a.id]["x"], 2.5)
        self.assertAlmostEqual(layer_times[self.layer_a.id], 0.25)
        player.stop_all()

    def test_infinite_ping_pong_loop_reverses_to_the_start(self):
        action = clip("bounce", self.layer_a.id, duration=1, value=10)
        action.loop_forever = True
        action.ping_pong = True
        action.playback_duration = 4
        self.skin.actions = [action]
        player = ActionPlayer(self.skin)

        self.assertTrue(player._play_at(action.id, None, 60))
        at_end, _ = player._tick_at(64)
        returning, _ = player._tick_at(65)
        at_restart, _ = player._tick_at(68)

        self.assertIn(action.id, player.active_action_ids)
        self.assertAlmostEqual(at_end[self.layer_a.id]["x"], 10)
        self.assertAlmostEqual(returning[self.layer_a.id]["x"], 7.5)
        self.assertAlmostEqual(at_restart[self.layer_a.id]["x"], 0)
        player.stop_all()


if __name__ == "__main__":
    unittest.main()
