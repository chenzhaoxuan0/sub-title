import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from subtitle.skin.model import AssetType, Layer, SkinDefinition
from subtitle.skin.package import export_skin_package, import_skin_package


class SkinPackageTests(unittest.TestCase):
    def test_export_and_import_static_and_sequence(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "source"
            assets = base / "assets"
            frames = assets / "tail"
            frames.mkdir(parents=True)
            (assets / "body.png").write_bytes(b"body")
            (frames / "0001.webp").write_bytes(b"one")
            (frames / "0002.webp").write_bytes(b"two")
            skin = SkinDefinition(name="cat")
            skin.layers = [
                Layer(name="body", image_path="assets/body.png"),
                Layer(
                    name="tail", image_path="assets/tail/0001.webp",
                    asset_type=AssetType.SEQUENCE,
                    sequence_frames=["assets/tail/0001.webp", "assets/tail/0002.webp"],
                ),
            ]
            archive = export_skin_package(skin, base, Path(temporary) / "cat.zip")
            destination = import_skin_package(archive, Path(temporary) / "installed")
            loaded = SkinDefinition.load(destination / "skin.json")
            self.assertEqual(loaded.name, "cat")
            self.assertTrue((destination / "assets/body.png").exists())
            self.assertEqual(len(loaded.layers[1].sequence_frames), 2)

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("skin.json", json.dumps(SkinDefinition().to_dict()))
                output.writestr("../escape.png", b"bad")
            with self.assertRaises(ValueError):
                import_skin_package(archive, Path(temporary) / "installed")


if __name__ == "__main__":
    unittest.main()
