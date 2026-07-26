"""Import, export and discovery for portable skin packages."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ..paths import user_data_dir
from .model import SkinDefinition


SKIN_FILE = "skin.json"
SUPPORTED_IMAGES = {".png", ".webp"}
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_FILES = 10_000


def skins_root(configured: str = "skins") -> Path:
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = user_data_dir() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\-.]+", "-", name.strip(), flags=re.UNICODE).strip("-.")
    return cleaned or "skin"


def list_skin_directories(root: Path) -> list[Path]:
    if not root.exists():
        return []
    valid = []
    for item in root.iterdir():
        if not item.is_dir() or not (item / SKIN_FILE).is_file():
            continue
        try:
            load_skin_directory(item)
        except Exception:
            continue
        valid.append(item)
    return sorted(valid, key=lambda item: item.name.lower())


def load_skin_directory(directory: Path) -> SkinDefinition:
    return SkinDefinition.load(Path(directory) / SKIN_FILE)


def create_skin_directory(root: Path, skin: SkinDefinition) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    base = safe_name(skin.name)
    candidate = root / base
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    (candidate / "assets").mkdir()
    skin.save(candidate / SKIN_FILE)
    return candidate


def _safe_relative(path_value: str) -> Path:
    normalized = PurePosixPath(path_value.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"不安全的皮肤资源路径: {path_value}")
    return Path(*normalized.parts)


def validate_assets(skin: SkinDefinition, base_dir: Path) -> list[str]:
    errors = skin.validate()
    for layer in skin.layers:
        for path_value in layer.asset_paths():
            try:
                relative = _safe_relative(path_value)
            except ValueError as error:
                errors.append(str(error))
                continue
            if relative.suffix.lower() not in SUPPORTED_IMAGES:
                errors.append(f"图层“{layer.name}”包含不支持的资源: {path_value}")
            elif not (base_dir / relative).is_file():
                errors.append(f"图层“{layer.name}”缺少资源: {path_value}")
    return errors


def export_skin_package(skin: SkinDefinition, base_dir: Path, output_path: Path) -> Path:
    errors = validate_assets(skin, base_dir)
    if errors:
        raise ValueError("\n".join(errors))
    output_path = output_path.with_suffix(".zip")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            SKIN_FILE,
            json.dumps(skin.to_dict(), ensure_ascii=False, indent=2).encode("utf-8"),
        )
        added: set[Path] = set()
        for layer in skin.layers:
            for path_value in layer.asset_paths():
                relative = _safe_relative(path_value)
                if relative not in added:
                    archive.write(base_dir / relative, relative.as_posix())
                    added.add(relative)
        thumbnail = base_dir / "thumbnail.png"
        if thumbnail.is_file():
            archive.write(thumbnail, "thumbnail.png")
    return output_path


def peek_skin_package(zip_path: Path) -> SkinDefinition:
    with zipfile.ZipFile(zip_path, "r") as archive:
        if SKIN_FILE not in archive.namelist():
            raise ValueError("皮肤包缺少 skin.json")
        return SkinDefinition.from_dict(json.loads(archive.read(SKIN_FILE).decode("utf-8")))


def import_skin_package(zip_path: Path, root: Path, overwrite: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = archive.namelist()
        if SKIN_FILE not in names:
            raise ValueError("皮肤包缺少 skin.json")
        if len(archive.infolist()) > MAX_PACKAGE_FILES:
            raise ValueError("皮肤包文件数量过多")
        if sum(item.file_size for item in archive.infolist()) > MAX_PACKAGE_BYTES:
            raise ValueError("皮肤包解压后超过 256 MB")
        for name in names:
            _safe_relative(name)
        skin = SkinDefinition.from_dict(json.loads(archive.read(SKIN_FILE).decode("utf-8")))
        destination = root / safe_name(skin.name)
        if destination.exists() and not overwrite:
            base = destination.name
            suffix = 2
            while destination.exists():
                destination = root / f"{base}-{suffix}"
                suffix += 1
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            temporary_path = Path(temporary)
            for member in archive.infolist():
                if member.is_dir():
                    continue
                relative = _safe_relative(member.filename)
                target = temporary_path / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
            errors = validate_assets(skin, temporary_path)
            if errors:
                raise ValueError("\n".join(errors))
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(temporary_path), str(destination))
    return destination
