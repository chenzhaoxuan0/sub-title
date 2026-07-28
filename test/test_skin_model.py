import unittest

from subtitle.skin.model import (
    AnimationClip, Interpolation, Keyframe, Layer, PropertyTrack,
    SkinDefinition, Trigger, TriggerType,
)


class PropertyTrackTests(unittest.TestCase):
    def test_interpolation_and_replace(self):
        track = PropertyTrack("x", default_value=3)
        track.add_keyframe(Keyframe(0, 0, Interpolation.LINEAR))
        track.add_keyframe(Keyframe(1, 10, Interpolation.LINEAR))
        self.assertAlmostEqual(track.get_value_at(0.5), 5)
        track.add_keyframe(Keyframe(1, 20, Interpolation.HOLD))
        self.assertEqual(len(track.keyframes), 2)
        self.assertAlmostEqual(track.get_value_at(1), 20)

    def test_easing_and_hold(self):
        ease = PropertyTrack("x", [
            Keyframe(0, 0, Interpolation.EASE_IN), Keyframe(1, 10),
        ])
        self.assertAlmostEqual(ease.get_value_at(0.5), 2.5)
        hold = PropertyTrack("x", [
            Keyframe(0, 2, Interpolation.HOLD), Keyframe(1, 10),
        ])
        self.assertEqual(hold.get_value_at(0.75), 2)


class SkinMigrationTests(unittest.TestCase):
    def test_version_one_action_and_name_trigger_migrate(self):
        layer = Layer(name="tail")
        data = {
            "name": "cat",
            "version": 1,
            "layers": [layer.to_dict()],
            "actions": [{
                "name": "wag",
                "duration": 1,
                "keyframe_overrides": {
                    layer.id: {"rotation": [[0, 0, "linear"], [1, 20, "ease_out"]]}
                },
            }],
            "triggers": [{"name": "timer", "action_name": "wag"}],
        }
        skin = SkinDefinition.from_dict(data)
        self.assertEqual(skin.version, 2)
        self.assertEqual(len(skin.actions[0].tracks[layer.id]["rotation"].keyframes), 2)
        self.assertEqual(skin.triggers[0].action_id, skin.actions[0].id)

    def test_validation_reports_dangling_references(self):
        skin = SkinDefinition()
        clip = AnimationClip(name="broken")
        clip.get_track("missing", "x").add_keyframe(Keyframe(0, 1))
        skin.actions.append(clip)
        skin.triggers.append(Trigger(action_id="missing-action"))
        self.assertEqual(len(skin.validate()), 2)

    def test_action_playback_settings_round_trip(self):
        action = AnimationClip(
            duration=1,
            loop=True,
            loop_count=3,
            loop_forever=True,
            ping_pong=True,
            playback_duration=4,
        )

        restored = AnimationClip.from_dict(action.to_dict())

        self.assertTrue(restored.loop)
        self.assertEqual(restored.loop_count, 3)
        self.assertTrue(restored.loop_forever)
        self.assertTrue(restored.ping_pong)
        self.assertEqual(restored.playback_duration, 4)
        self.assertEqual(restored.effective_playback_duration, 4)
        self.assertEqual(AnimationClip.from_dict({"duration": 2}).effective_playback_duration, 2)

    def test_text_trigger_matching(self):
        keyword = Trigger(trigger_type=TriggerType.KEYWORD, keyword="Cat")
        regex = Trigger(trigger_type=TriggerType.REGEX, pattern=r"tail\s+move")
        self.assertTrue(keyword.matches_text("a cat appears", False))
        self.assertTrue(regex.matches_text("TAIL move", True))


if __name__ == "__main__":
    unittest.main()
