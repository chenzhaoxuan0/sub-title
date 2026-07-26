"""Application-facing controller for one active subtitle skin."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import time

from PySide6.QtCore import QObject, QPointF, QTimer, Signal

from .action_player import ActionPlayer
from .events import TriggerManager
from .model import AssetType, LayerPlane, SkinDefinition
from .renderer import SkinRenderer


class SkinRuntime(QObject):
    skin_changed = Signal(object)

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.skin: Optional[SkinDefinition] = None
        self.base_dir: Optional[Path] = None
        self.renderer: Optional[SkinRenderer] = None
        self.player: Optional[ActionPlayer] = None
        self.triggers: Optional[TriggerManager] = None
        self._sequence_started_at = time.monotonic()
        self._sequence_timer = QTimer(self)
        self._sequence_timer.timeout.connect(self._advance_sequences)

    def apply_skin(self, skin: SkinDefinition, base_dir: Path, start_triggers: bool = True) -> None:
        self.disable()
        self.skin = skin
        self.base_dir = Path(base_dir)
        self.renderer = SkinRenderer(skin, self.base_dir)
        self.player = ActionPlayer(skin, self)
        self.triggers = TriggerManager(skin, self)
        self.player.state_changed.connect(self._on_state_changed)
        self.triggers.action_triggered.connect(self.player.play)
        self.panel.set_skin_runtime(self)
        self._sequence_started_at = time.monotonic()
        if any(layer.asset_type == AssetType.SEQUENCE for layer in skin.layers):
            self._sequence_timer.setInterval(max(1, 1000 // max(1, skin.fps)))
            self._sequence_timer.start()
        if start_triggers:
            self.triggers.start()
        self.skin_changed.emit(skin)

    def load_directory(self, directory: Path, start_triggers: bool = True) -> SkinDefinition:
        skin = SkinDefinition.load(Path(directory) / "skin.json")
        self.apply_skin(skin, Path(directory), start_triggers=start_triggers)
        return skin

    def disable(self) -> None:
        self._sequence_timer.stop()
        if self.triggers is not None:
            self.triggers.stop()
        if self.player is not None:
            self.player.stop_all()
        self.skin = None
        self.base_dir = None
        self.renderer = None
        self.player = None
        self.triggers = None
        if hasattr(self.panel, "set_skin_runtime"):
            self.panel.set_skin_runtime(None)

    def refresh_definition(self) -> None:
        if self.renderer and self.skin:
            self.renderer.skin = self.skin
        if self.player and self.skin:
            self.player.skin = self.skin
        if self.triggers and self.skin:
            self.triggers.skin = self.skin
            self.triggers.start()
        self.panel.update_skin_layers()

    def _on_state_changed(self, overrides: dict, layer_times: dict) -> None:
        if self.renderer:
            self.renderer.set_runtime_state(overrides, layer_times)
            self.panel.update_skin_layers()

    def _advance_sequences(self) -> None:
        if self.renderer:
            self.renderer.set_time(time.monotonic() - self._sequence_started_at)
            self.panel.update_skin_layers()

    def hit_test(self, point: QPointF, width: int, height: int):
        if not self.renderer:
            return None
        return (
            self.renderer.layer_at(point, width, height, LayerPlane.ABOVE_TEXT)
            or self.renderer.layer_at(point, width, height, LayerPlane.BELOW_TEXT)
        )

    def play_action(self, action_id: str) -> bool:
        return bool(self.player and self.player.play(action_id))

    def on_recognition_start(self) -> None:
        if self.triggers:
            self.triggers.on_recognition_start()

    def on_recognition_stop(self) -> None:
        if self.triggers:
            self.triggers.on_recognition_stop()

    def on_text(self, text: str, is_final: bool) -> None:
        if self.triggers:
            self.triggers.on_text_received(text, is_final)

    def on_audio_level(self, rms: float, peak: float) -> None:
        if self.triggers:
            self.triggers.on_audio_level(rms, peak)

    def on_window_shown(self) -> None:
        if self.triggers:
            self.triggers.on_window_shown()

    def on_window_hidden(self) -> None:
        if self.triggers:
            self.triggers.on_window_hidden()

    def on_layer_clicked(self, layer_id: str, mouse_button: str = "left") -> None:
        if self.triggers:
            self.triggers.on_layer_clicked(layer_id, mouse_button)

    def has_click_triggers(self) -> bool:
        return bool(self.triggers and self.triggers.has_click_triggers())
