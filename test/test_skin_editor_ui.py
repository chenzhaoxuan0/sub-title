import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from subtitle.config import Config
from subtitle.skin.editor import LayerPanel, SkinEditorWindow
from subtitle.skin.editor_canvas import SkinCanvas
from subtitle.skin.model import Layer, SkinDefinition
from subtitle.skin.package import create_skin_directory
from subtitle.skin.renderer import SkinRenderer
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


if __name__ == "__main__":
    unittest.main()
