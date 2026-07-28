import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from subtitle.config import Config
from subtitle.skin.editor import (
    ActionPanel, LayerPanel, PropertyPanel, SkinEditorWindow, TriggerPanel,
)
from subtitle.skin.editor_canvas import SkinCanvas
from subtitle.skin.editor_timeline import ActionTimeline
from subtitle.skin.model import (
    AnimationClip, HorizontalPin, Keyframe, Layer, LayerPlane, SkinDefinition,
    TriggerType, VerticalPin,
)
from subtitle.skin.package import create_skin_directory
from subtitle.skin.renderer import SkinRenderer
from subtitle.ui.settings_dialog import SettingsDialog
from subtitle.ui.subtitle_panel import SkinExtensionWindow, SubtitlePanel


APP = QApplication.instance() or QApplication([])


class SkinEditorUiTests(unittest.TestCase):
    def test_clicking_current_layer_item_selects_it_again(self):
        layer = Layer(name="cat")
        panel = LayerPanel(SkinDefinition(layers=[layer]))
        panel.resize(300, 240)
        panel.show()
        APP.processEvents()
        selected = []
        panel.selected.connect(selected.append)
        item = panel.list.item(0)
        point = panel.list.visualItemRect(item).center()
        QTest.mouseClick(panel.list.viewport(), Qt.LeftButton, pos=point)
        first_count = len(selected)
        QTest.mouseClick(panel.list.viewport(), Qt.LeftButton, pos=point)
        self.assertGreater(len(selected), first_count)
        self.assertEqual(selected[-1], layer.id)
        panel.close()

    def test_canvas_selects_and_moves_layer_outside_subtitle_viewport(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            image = QImage(40, 40, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(base_dir / "cat.png"))
            layer = Layer(name="cat", image_path="cat.png", x=-20, y=-20)
            skin = SkinDefinition(design_width=720, design_height=140, layers=[layer])
            canvas = SkinCanvas(skin, base_dir)
            canvas.resize(900, 500)
            canvas.set_viewport_size(900, 300)
            canvas.show()
            APP.processEvents()
            selected = []
            transforms = []
            canvas.layer_selected.connect(selected.append)
            canvas.transform_changed.connect(
                lambda layer_id, name, value: transforms.append((layer_id, name, value))
            )
            outside_point = canvas._from_scene(QPointF(-10, -10)).toPoint()
            self.assertFalse(canvas._preview_rect().contains(outside_point))
            QTest.mousePress(canvas, Qt.LeftButton, pos=outside_point)
            QTest.mouseMove(canvas, outside_point + QPointF(20, 12).toPoint())
            QTest.mouseRelease(canvas, Qt.LeftButton, pos=outside_point + QPointF(20, 12).toPoint())
            self.assertEqual(selected[-1], layer.id)
            self.assertTrue(any(name == "x" for _, name, _ in transforms))
            self.assertTrue(any(name == "y" for _, name, _ in transforms))
            self.assertAlmostEqual(
                canvas._preview_rect().width() / canvas._preview_rect().height(),
                3.0,
                places=2,
            )
            canvas.close()

    def test_extension_window_covers_only_outside_skin_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            image = QImage(40, 40, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(base_dir / "cat.png"))
            layer = Layer(name="cat", image_path="cat.png", x=-20, y=-20)
            skin = SkinDefinition(design_width=200, design_height=80, layers=[layer])
            renderer = SkinRenderer(skin, base_dir)
            panel = QWidget()
            panel.resize(200, 80)
            panel.container = QWidget(panel)
            panel.container.setGeometry(0, 0, 200, 80)
            panel.show()
            extension = SkinExtensionWindow(panel)
            extension.set_runtime(SimpleNamespace(renderer=renderer))
            APP.processEvents()
            extension.sync_geometry()
            content_origin = panel.container.mapToGlobal(panel.container.rect().topLeft())
            image_point = QPointF(-10, -10) + extension._paint_origin
            transparent_point = QPointF(-21, -21) + extension._paint_origin
            self.assertTrue(extension.isVisible())
            self.assertLess(extension.x(), content_origin.x())
            self.assertLess(extension.y(), content_origin.y())
            self.assertEqual(extension._layer_at_local(image_point).id, layer.id)
            self.assertIsNone(extension._layer_at_local(transparent_point))
            extension.close()
            panel.close()

    def test_extension_never_covers_toolbar_ui(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            image = QImage(40, 40, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(base_dir / "cat.png"))
            layer = Layer(name="cat", image_path="cat.png", x=0, y=-30)
            skin = SkinDefinition(design_width=200, design_height=80, layers=[layer])
            renderer = SkinRenderer(skin, base_dir)
            panel = QWidget()
            panel.resize(200, 120)
            panel.container = QWidget(panel)
            panel.container.setGeometry(0, 40, 200, 80)
            panel.show()
            extension = SkinExtensionWindow(panel)
            extension.set_runtime(SimpleNamespace(renderer=renderer))
            APP.processEvents()
            extension.sync_geometry()
            scene_point = QPointF(10, -10)
            local_point = scene_point + extension._paint_origin
            self.assertEqual(renderer.layer_at(scene_point, 200, 80).id, layer.id)
            self.assertIsNone(extension._layer_at_local(local_point))
            extension.close()
            panel.close()

    def test_extension_geometry_is_stable_across_action_positions(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            image = QImage(40, 40, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(base_dir / "cat.png"))
            layer = Layer(name="cat", image_path="cat.png", x=-30, y=20)
            action = AnimationClip(duration=1)
            x_track = action.get_track(layer.id, "x")
            x_track.add_keyframe(Keyframe(0, -30))
            x_track.add_keyframe(Keyframe(1, 230))
            renderer = SkinRenderer(
                SkinDefinition(
                    design_width=200, design_height=80,
                    layers=[layer], actions=[action],
                ),
                base_dir,
            )
            panel = QWidget()
            panel.resize(200, 80)
            panel.container = QWidget(panel)
            panel.container.setGeometry(0, 0, 200, 80)
            panel.show()
            extension = SkinExtensionWindow(panel)
            extension.set_runtime(SimpleNamespace(renderer=renderer))
            extension.sync_geometry()
            first_geometry = extension.geometry()

            renderer.set_runtime_state({layer.id: {"x": 230}}, {layer.id: 1})
            extension.sync_geometry()

            self.assertTrue(extension.isVisible())
            self.assertEqual(extension.geometry(), first_geometry)
            extension.close()
            panel.close()

    def test_programmatic_window_resize_updates_preview_dimensions(self):
        panel = SubtitlePanel(Config().ui)
        preview_updates = []
        panel.preview_state_changed.connect(lambda: preview_updates.append(True))
        panel.show()
        panel.set_window_size(880, 240)
        APP.processEvents()
        self.assertEqual(panel.get_window_size(), (880, 240))
        self.assertTrue(preview_updates)
        panel._force_quit = True
        panel.close()

    def test_editor_shows_and_switches_current_skin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "skins"
            first = create_skin_directory(root, SkinDefinition(name="猫咪"))
            second = create_skin_directory(root, SkinDefinition(name="小狗"))
            config = Config()
            config.skin.skins_dir = str(root)
            panel = SubtitlePanel(config.ui)
            panel.show()
            editor = SkinEditorWindow(config, panel)
            editor._load_editor_skin(first)
            self.assertIn("猫咪", editor.windowTitle())
            index = editor.skin_selector.findData(str(second))
            self.assertGreaterEqual(index, 0)
            editor.skin_selector.setCurrentIndex(index)
            APP.processEvents()
            self.assertEqual(editor.skin.name, "小狗")
            self.assertIn("小狗", editor.windowTitle())
            editor.close()
            panel._force_quit = True
            panel.close()

    def test_settings_skin_editor_button_requests_editor(self):
        config = Config()
        panel = SubtitlePanel(config.ui)
        dialog = SettingsDialog(config, panel)
        requested = []
        dialog.skin_editor_requested.connect(lambda: requested.append(True))

        dialog.skin_editor_btn.click()
        APP.processEvents()

        self.assertEqual(requested, [True])
        self.assertEqual(dialog.result(), QDialog.Accepted)
        dialog.close()
        panel._force_quit = True
        panel.close()

    def test_layer_placement_controls_keep_model_enums(self):
        layer = Layer(name="cat")
        properties = PropertyPanel()
        properties.set_context(layer, None, 0)

        properties.plane.setCurrentIndex(
            properties.plane.findData(LayerPlane.BELOW_TEXT)
        )
        properties.pin_x.setCurrentIndex(properties.pin_x.findData(HorizontalPin.RIGHT))
        properties.pin_y.setCurrentIndex(properties.pin_y.findData(VerticalPin.BOTTOM))

        self.assertEqual(layer.plane, LayerPlane.BELOW_TEXT)
        self.assertEqual(layer.pin_x, HorizontalPin.RIGHT)
        self.assertEqual(layer.pin_y, VerticalPin.BOTTOM)
        self.assertEqual(SkinDefinition(layers=[layer]).to_dict()["layers"][0]["plane"], "below_text")
        properties.close()

    def test_timeline_keyframe_click_switches_to_that_edit_time(self):
        layer = Layer(name="tail")
        action = AnimationClip(name="wag", duration=1)
        action.get_track(layer.id, "rotation").add_keyframe(Keyframe(0.4, 15))
        timeline = ActionTimeline()
        timeline.resize(600, 200)
        timeline.set_context(action, layer)
        timeline.show()
        APP.processEvents()
        changed_times = []
        timeline.time_changed.connect(changed_times.append)
        point = timeline._keyframe_points().__next__()[2].toPoint()

        QTest.mouseClick(timeline, Qt.LeftButton, pos=point)

        self.assertAlmostEqual(timeline.current_time, 0.4)
        self.assertAlmostEqual(changed_times[-1], 0.4)
        self.assertEqual(timeline.current_property, "rotation")
        timeline.close()

    def test_action_pose_edit_preserves_base_layer_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Config()
            config.skin.skins_dir = str(Path(temporary) / "skins")
            panel = SubtitlePanel(config.ui)
            panel.show()
            editor = SkinEditorWindow(config, panel)
            layer = Layer(name="tail", x=10, y=20)
            editor.skin = SkinDefinition(layers=[layer])
            editor._history = [editor.skin.to_dict()]
            editor._history_index = 0
            editor._saved_snapshot = editor.skin.to_dict()
            editor._refresh_all()
            action = AnimationClip(name="wag", duration=1)
            editor.skin.actions.append(action)
            editor._select_layer(layer.id)
            editor._select_action(action.id)
            editor._set_time(0.5)

            editor._set_property(layer.id, "x", 30)

            self.assertEqual(layer.x, 10)
            self.assertEqual(layer.y, 20)
            tracks = action.tracks[layer.id]
            self.assertEqual([(key.time, key.value) for key in tracks["x"].keyframes], [(0, 10), (0.5, 30)])
            self.assertEqual([(key.time, key.value) for key in tracks["y"].keyframes], [(0, 20), (0.5, 20)])
            self.assertEqual(len(tracks), 6)
            editor.close()
            panel._force_quit = True
            panel.close()

    def test_canvas_zoom_scales_scene_and_reset_restores_fit(self):
        canvas = SkinCanvas(SkinDefinition(design_width=200, design_height=80), Path("."))
        canvas.resize(600, 400)
        canvas.set_viewport_size(200, 80)
        _, fit_scale, _ = canvas._scene_mapping()

        canvas.set_zoom(2)
        _, zoomed_scale, _ = canvas._scene_mapping()
        canvas.reset_view()

        self.assertAlmostEqual(zoomed_scale, fit_scale * 2)
        self.assertEqual(canvas.zoom, 1)
        canvas.close()

    def test_canvas_rotation_uses_selected_pivot_without_moving_layer(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            image = QImage(80, 80, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(base_dir / "tail.png"))
            layer = Layer(
                name="tail", image_path="tail.png", x=60, y=20,
                anchor_x=0.2, anchor_y=0.8,
            )
            canvas = SkinCanvas(
                SkinDefinition(design_width=200, design_height=120, layers=[layer]),
                base_dir,
            )
            canvas.resize(700, 440)
            canvas.set_viewport_size(200, 120)
            canvas.select_layer(layer.id)
            canvas.show()
            APP.processEvents()
            changes = []
            canvas.transform_changed.connect(
                lambda layer_id, name, value: changes.append((layer_id, name, value))
            )
            rotate_handle = canvas._handles(canvas._preview_rect())["rotate"].toPoint()

            QTest.mousePress(canvas, Qt.LeftButton, pos=rotate_handle)
            QTest.mouseMove(canvas, rotate_handle + QPointF(35, 20).toPoint())
            QTest.mouseRelease(canvas, Qt.LeftButton, pos=rotate_handle + QPointF(35, 20).toPoint())

            self.assertEqual({name for _, name, _ in changes}, {"rotation"})
            self.assertEqual((layer.x, layer.y), (60, 20))
            canvas.close()

    def test_canvas_can_pick_rotation_pivot_from_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            image = QImage(80, 80, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(base_dir / "tail.png"))
            layer = Layer(name="tail", image_path="tail.png", x=60, y=20)
            canvas = SkinCanvas(
                SkinDefinition(design_width=200, design_height=120, layers=[layer]),
                base_dir,
            )
            canvas.resize(700, 440)
            canvas.set_viewport_size(200, 120)
            canvas.select_layer(layer.id)
            canvas.show()
            APP.processEvents()
            picked = []
            canvas.rotation_pivot_selected.connect(
                lambda layer_id, x, y: picked.append((layer_id, x, y))
            )
            pivot = canvas._handles(canvas._preview_rect())["pivot"]

            canvas.begin_rotation_pivot_pick()
            self.assertTrue(canvas._pick_rotation_pivot(pivot))

            self.assertEqual(picked[0][0], layer.id)
            self.assertAlmostEqual(picked[0][1], 0.5)
            self.assertAlmostEqual(picked[0][2], 0.5)
            canvas.close()

    def test_trigger_panel_add_and_type_change_are_serializable(self):
        skin = SkinDefinition(actions=[AnimationClip(name="wag")])
        panel = TriggerPanel(skin)
        changes = []
        panel.changed.connect(lambda: changes.append(True))

        panel._add()
        self.assertEqual(len(skin.triggers), 1)
        self.assertEqual(changes, [True])
        panel.type.setCurrentIndex(panel.type.findData(TriggerType.RANDOM))

        self.assertEqual(skin.triggers[0].trigger_type, TriggerType.RANDOM)
        self.assertEqual(skin.to_dict()["triggers"][0]["trigger_type"], "random")
        panel.close()

    def test_daily_action_creates_a_random_trigger(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Config()
            config.skin.skins_dir = str(Path(temporary) / "skins")
            panel = SubtitlePanel(config.ui)
            editor = SkinEditorWindow(config, panel)
            action = AnimationClip(name="wag")
            editor.skin = SkinDefinition(actions=[action])
            editor._history = [editor.skin.to_dict()]
            editor._history_index = 0
            editor._saved_snapshot = editor.skin.to_dict()
            editor._refresh_all()

            editor._make_daily_action(action.id)

            self.assertEqual(len(editor.skin.triggers), 1)
            trigger = editor.skin.triggers[0]
            self.assertEqual(trigger.trigger_type, TriggerType.RANDOM)
            self.assertEqual(trigger.action_id, action.id)
            self.assertEqual((trigger.random_min, trigger.random_max), (8.0, 20.0))
            editor.close()
            panel._force_quit = True
            panel.close()

    def test_action_panel_saves_loop_and_playback_options(self):
        action = AnimationClip(name="wag", duration=1)
        panel = ActionPanel(SkinDefinition(actions=[action]))
        panel.select(action.id)

        panel.loop.setChecked(True)
        panel.loop_count.setValue(3)
        panel.loop_forever.setChecked(True)
        panel.ping_pong.setChecked(True)
        panel.playback_duration.setValue(4)

        self.assertTrue(action.loop)
        self.assertEqual(action.loop_count, 3)
        self.assertTrue(action.loop_forever)
        self.assertTrue(action.ping_pong)
        self.assertEqual(action.playback_duration, 4)
        self.assertFalse(panel.loop_count.isEnabled())
        panel.close()


if __name__ == "__main__":
    unittest.main()
