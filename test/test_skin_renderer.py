import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from subtitle.skin.model import AssetType, HorizontalPin, Layer, SkinDefinition
from subtitle.skin.renderer import SkinRenderer


APP = QApplication.instance() or QApplication([])


class SkinRendererTests(unittest.TestCase):
    def test_responsive_anchor_and_hit_test(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = QImage(10, 10, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(Path(temporary) / "image.png"))
            left = Layer(name="left", image_path="image.png", x=10, y=20)
            right = Layer(
                name="right", image_path="image.png", x=90, y=20,
                pin_x=HorizontalPin.RIGHT,
            )
            skin = SkinDefinition(design_width=100, design_height=100, layers=[left, right])
            renderer = SkinRenderer(skin, Path(temporary))
            self.assertEqual(renderer.layer_at(QPointF(15, 25), 200, 100).id, left.id)
            self.assertEqual(renderer.layer_at(QPointF(195, 25), 200, 100).id, right.id)

    def test_right_top_pin_stays_at_corner_when_resized(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = QImage(10, 10, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(Path(temporary) / "image.png"))
            layer = Layer(
                image_path="image.png", x=90, y=0,
                pin_x=HorizontalPin.RIGHT,
            )
            renderer = SkinRenderer(
                SkinDefinition(design_width=100, design_height=100, layers=[layer]),
                Path(temporary),
            )
            for width, height in ((200, 100), (160, 80), (60, 30)):
                bounds = renderer.get_layer_bounds(layer, canvas_w=width, canvas_h=height)
                self.assertAlmostEqual(bounds.right(), width)
                self.assertAlmostEqual(bounds.top(), 0)

    def test_sequence_frame_selection(self):
        layer = Layer(
            asset_type=AssetType.SEQUENCE,
            sequence_frames=["a.png", "b.png", "c.png"],
            sequence_fps=2,
        )
        renderer = SkinRenderer(SkinDefinition(layers=[layer]), Path("."))
        renderer.set_time(0.75)
        self.assertEqual(renderer._asset_path_at(layer), "b.png")
        renderer.set_time(2)
        self.assertEqual(renderer._asset_path_at(layer), "b.png")

    def test_skin_bounds_include_layers_outside_canvas(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = QImage(20, 20, QImage.Format_ARGB32)
            image.fill(QColor(255, 0, 0, 255))
            image.save(str(Path(temporary) / "image.png"))
            layer = Layer(name="outside", image_path="image.png", x=-10, y=-12)
            renderer = SkinRenderer(
                SkinDefinition(design_width=100, design_height=50, layers=[layer]),
                Path(temporary),
            )
            bounds = renderer.get_skin_bounds(100, 50)
            self.assertLess(bounds.left(), 0)
            self.assertLess(bounds.top(), 0)


if __name__ == "__main__":
    unittest.main()
