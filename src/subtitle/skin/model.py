"""Data model and version migration for subtitle decoration skins."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


def _id() -> str:
    return str(uuid.uuid4())[:8]


class Interpolation(str, Enum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    BEZIER = "bezier"
    STEP = "step"
    HOLD = "hold"


class LayerPlane(str, Enum):
    BELOW_TEXT = "below_text"
    ABOVE_TEXT = "above_text"


class HorizontalPin(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class VerticalPin(str, Enum):
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"


class AssetType(str, Enum):
    STATIC = "static"
    SEQUENCE = "sequence"


class TriggerType(str, Enum):
    TIMER = "timer"
    RANDOM = "random"
    ON_START = "on_start"
    ON_STOP = "on_stop"
    ON_TEXT = "on_text"
    ON_PARTIAL = "on_partial"
    ON_FINAL = "on_final"
    ON_IDLE = "on_idle"
    KEYWORD = "keyword"
    REGEX = "regex"
    VOLUME_ABOVE = "volume_above"
    VOLUME_BELOW = "volume_below"
    WINDOW_SHOW = "window_show"
    WINDOW_HIDE = "window_hide"
    ON_CLICK = "on_click"


ANIMATABLE_PROPERTIES = ["x", "y", "scale_x", "scale_y", "rotation", "opacity"]


@dataclass
class Keyframe:
    time: float
    value: float
    interpolation: Interpolation = Interpolation.LINEAR
    bezier_out: tuple[float, float] = (0.33, 0.0)
    bezier_in: tuple[float, float] = (0.67, 1.0)

    def to_dict(self) -> dict:
        return {
            "time": float(self.time),
            "value": float(self.value),
            "interpolation": self.interpolation.value,
            "bezier_out": list(self.bezier_out),
            "bezier_in": list(self.bezier_in),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Keyframe":
        return cls(
            time=float(data["time"]),
            value=float(data["value"]),
            interpolation=Interpolation(data.get("interpolation", "linear")),
            bezier_out=tuple(data.get("bezier_out", (0.33, 0.0))),
            bezier_in=tuple(data.get("bezier_in", (0.67, 1.0))),
        )


@dataclass
class PropertyTrack:
    property_name: str
    keyframes: list[Keyframe] = field(default_factory=list)
    default_value: float = 0.0

    def add_keyframe(self, keyframe: Keyframe, tolerance: float = 0.001) -> None:
        """Insert or replace a keyframe at the same timestamp."""
        for index, existing in enumerate(self.keyframes):
            if abs(existing.time - keyframe.time) <= tolerance:
                self.keyframes[index] = keyframe
                break
        else:
            self.keyframes.append(keyframe)
        self.keyframes.sort(key=lambda item: item.time)

    def remove_keyframe_at(self, time_value: float, tolerance: float = 0.05) -> bool:
        for index, keyframe in enumerate(self.keyframes):
            if abs(keyframe.time - time_value) <= tolerance:
                self.keyframes.pop(index)
                return True
        return False

    def keyframe_at(self, time_value: float, tolerance: float = 0.05) -> Optional[Keyframe]:
        return next(
            (keyframe for keyframe in self.keyframes if abs(keyframe.time - time_value) <= tolerance),
            None,
        )

    def get_value_at(self, time_value: float) -> float:
        if not self.keyframes:
            return self.default_value
        if len(self.keyframes) == 1 or time_value <= self.keyframes[0].time:
            return self.keyframes[0].value
        if time_value >= self.keyframes[-1].time:
            return self.keyframes[-1].value
        for keyframe_a, keyframe_b in zip(self.keyframes, self.keyframes[1:]):
            if keyframe_a.time <= time_value <= keyframe_b.time:
                return self._interpolate(keyframe_a, keyframe_b, time_value)
        return self.keyframes[-1].value

    @staticmethod
    def _interpolate(keyframe_a: Keyframe, keyframe_b: Keyframe, time_value: float) -> float:
        duration = keyframe_b.time - keyframe_a.time
        if duration <= 0:
            return keyframe_b.value
        progress = (time_value - keyframe_a.time) / duration
        interpolation = keyframe_a.interpolation
        if interpolation in (Interpolation.STEP, Interpolation.HOLD):
            factor = 0.0
        elif interpolation == Interpolation.EASE_IN:
            factor = progress * progress
        elif interpolation == Interpolation.EASE_OUT:
            factor = 1 - (1 - progress) ** 2
        elif interpolation == Interpolation.EASE_IN_OUT:
            factor = progress * progress * (3 - 2 * progress)
        elif interpolation == Interpolation.BEZIER:
            y1 = keyframe_a.bezier_out[1]
            y2 = keyframe_b.bezier_in[1]
            inverse = 1 - progress
            factor = (
                3 * inverse * inverse * progress * y1
                + 3 * inverse * progress * progress * y2
                + progress ** 3
            )
        else:
            factor = progress
        return keyframe_a.value + (keyframe_b.value - keyframe_a.value) * factor

    def to_dict(self) -> dict:
        return {
            "property_name": self.property_name,
            "default_value": float(self.default_value),
            "keyframes": [keyframe.to_dict() for keyframe in self.keyframes],
        }

    @classmethod
    def from_dict(cls, data: dict, property_name: str = "") -> "PropertyTrack":
        return cls(
            property_name=data.get("property_name", property_name),
            default_value=float(data.get("default_value", 0.0)),
            keyframes=[Keyframe.from_dict(item) for item in data.get("keyframes", [])],
        )


@dataclass
class Layer:
    id: str = field(default_factory=_id)
    name: str = "图层"
    image_path: str = ""
    asset_type: AssetType = AssetType.STATIC
    sequence_frames: list[str] = field(default_factory=list)
    sequence_fps: float = 12.0
    sequence_loop: bool = True
    visible: bool = True
    locked: bool = False
    plane: LayerPlane = LayerPlane.ABOVE_TEXT
    x: float = 0.0
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    pin_x: HorizontalPin = HorizontalPin.LEFT
    pin_y: VerticalPin = VerticalPin.TOP
    tracks: dict[str, PropertyTrack] = field(default_factory=dict)
    blend_mode: str = "normal"

    def get_track(self, property_name: str) -> PropertyTrack:
        if property_name not in self.tracks:
            self.tracks[property_name] = PropertyTrack(
                property_name=property_name,
                default_value=float(getattr(self, property_name, 0.0)),
            )
        return self.tracks[property_name]

    def get_animated_value(self, property_name: str, time_value: float) -> float:
        track = self.tracks.get(property_name)
        if track and track.keyframes:
            return track.get_value_at(time_value)
        return float(getattr(self, property_name, 0.0))

    def has_animation(self) -> bool:
        return any(track.keyframes for track in self.tracks.values())

    def asset_paths(self) -> list[str]:
        if self.asset_type == AssetType.SEQUENCE:
            return list(self.sequence_frames)
        return [self.image_path] if self.image_path else []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "image_path": self.image_path,
            "asset_type": self.asset_type.value,
            "sequence_frames": self.sequence_frames,
            "sequence_fps": self.sequence_fps,
            "sequence_loop": self.sequence_loop,
            "visible": self.visible,
            "locked": self.locked,
            "plane": self.plane.value,
            "x": self.x,
            "y": self.y,
            "scale_x": self.scale_x,
            "scale_y": self.scale_y,
            "rotation": self.rotation,
            "opacity": self.opacity,
            "anchor_x": self.anchor_x,
            "anchor_y": self.anchor_y,
            "pin_x": self.pin_x.value,
            "pin_y": self.pin_y.value,
            "blend_mode": self.blend_mode,
            "tracks": {name: track.to_dict() for name, track in self.tracks.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Layer":
        asset_type = data.get("asset_type", "sequence" if data.get("sequence_frames") else "static")
        layer = cls(
            id=data.get("id", _id()),
            name=data.get("name", "图层"),
            image_path=data.get("image_path", ""),
            asset_type=AssetType(asset_type),
            sequence_frames=list(data.get("sequence_frames", [])),
            sequence_fps=float(data.get("sequence_fps", 12.0)),
            sequence_loop=bool(data.get("sequence_loop", True)),
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            plane=LayerPlane(data.get("plane", "above_text")),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            scale_x=float(data.get("scale_x", 1.0)),
            scale_y=float(data.get("scale_y", 1.0)),
            rotation=float(data.get("rotation", 0.0)),
            opacity=float(data.get("opacity", 1.0)),
            anchor_x=float(data.get("anchor_x", 0.5)),
            anchor_y=float(data.get("anchor_y", 0.5)),
            pin_x=HorizontalPin(data.get("pin_x", "left")),
            pin_y=VerticalPin(data.get("pin_y", "top")),
            blend_mode=data.get("blend_mode", "normal"),
        )
        layer.tracks = {
            name: PropertyTrack.from_dict(track, name)
            for name, track in data.get("tracks", {}).items()
        }
        return layer


@dataclass
class AnimationClip:
    id: str = field(default_factory=_id)
    name: str = "动作"
    duration: float = 1.0
    loop: bool = False
    loop_count: int = 1
    priority: int = 0
    interruptible: bool = True
    cooldown: float = 0.0
    restore_to_base: bool = True
    tracks: dict[str, dict[str, PropertyTrack]] = field(default_factory=dict)

    @property
    def target_layer_ids(self) -> set[str]:
        return {layer_id for layer_id, tracks in self.tracks.items() if tracks}

    @property
    def keyframe_overrides(self) -> dict:
        """Compatibility view for version-one skins."""
        result: dict = {}
        for layer_id, properties in self.tracks.items():
            result[layer_id] = {}
            for property_name, track in properties.items():
                result[layer_id][property_name] = [
                    (item.time, item.value, item.interpolation.value)
                    for item in track.keyframes
                ]
        return result

    def get_track(self, layer_id: str, property_name: str, default_value: float = 0.0) -> PropertyTrack:
        layer_tracks = self.tracks.setdefault(layer_id, {})
        if property_name not in layer_tracks:
            layer_tracks[property_name] = PropertyTrack(property_name, default_value=default_value)
        return layer_tracks[property_name]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "loop": self.loop,
            "loop_count": self.loop_count,
            "priority": self.priority,
            "interruptible": self.interruptible,
            "cooldown": self.cooldown,
            "restore_to_base": self.restore_to_base,
            "tracks": {
                layer_id: {name: track.to_dict() for name, track in properties.items()}
                for layer_id, properties in self.tracks.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnimationClip":
        clip = cls(
            id=data.get("id", _id()),
            name=data.get("name", "动作"),
            duration=max(0.01, float(data.get("duration", 1.0))),
            loop=bool(data.get("loop", False)),
            loop_count=max(1, int(data.get("loop_count", 1))),
            priority=int(data.get("priority", 0)),
            interruptible=bool(data.get("interruptible", True)),
            cooldown=max(0.0, float(data.get("cooldown", 0.0))),
            restore_to_base=bool(data.get("restore_to_base", True)),
        )
        if "tracks" in data:
            clip.tracks = {
                layer_id: {
                    name: PropertyTrack.from_dict(track, name)
                    for name, track in properties.items()
                }
                for layer_id, properties in data.get("tracks", {}).items()
            }
        else:
            for layer_id, properties in data.get("keyframe_overrides", {}).items():
                for property_name, keyframes in properties.items():
                    track = clip.get_track(layer_id, property_name)
                    for raw in keyframes:
                        if isinstance(raw, dict):
                            track.add_keyframe(Keyframe.from_dict(raw))
                        else:
                            time_value, value, *rest = raw
                            interpolation = Interpolation(rest[0] if rest else "linear")
                            track.add_keyframe(Keyframe(float(time_value), float(value), interpolation))
        return clip


AnimationAction = AnimationClip


@dataclass
class Trigger:
    id: str = field(default_factory=_id)
    name: str = "触发器"
    trigger_type: TriggerType = TriggerType.TIMER
    enabled: bool = True
    action_id: str = ""
    action_name: str = ""
    interval: float = 5.0
    delay: float = 0.0
    idle_timeout: float = 10.0
    random_min: float = 3.0
    random_max: float = 8.0
    keyword: str = ""
    pattern: str = ""
    case_sensitive: bool = False
    volume_threshold: float = 0.2
    hold_seconds: float = 0.0
    target_layer_id: str = ""
    mouse_button: str = "left"
    cooldown: float = 0.0
    allow_retrigger: bool = False
    probability: float = 1.0
    max_fires: int = 0
    priority_override: Optional[int] = None

    def matches_text(self, text: str, is_final: bool) -> bool:
        if self.trigger_type == TriggerType.ON_TEXT:
            return bool(text)
        if self.trigger_type == TriggerType.ON_PARTIAL:
            return bool(text) and not is_final
        if self.trigger_type == TriggerType.ON_FINAL:
            return bool(text) and is_final
        if self.trigger_type == TriggerType.KEYWORD:
            source = text if self.case_sensitive else text.lower()
            keyword = self.keyword if self.case_sensitive else self.keyword.lower()
            return bool(keyword) and keyword in source
        if self.trigger_type == TriggerType.REGEX:
            if not self.pattern:
                return False
            flags = 0 if self.case_sensitive else re.IGNORECASE
            try:
                return re.search(self.pattern, text, flags) is not None
            except re.error:
                return False
        return False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "trigger_type": self.trigger_type.value,
            "enabled": self.enabled,
            "action_id": self.action_id,
            "action_name": self.action_name,
            "interval": self.interval,
            "delay": self.delay,
            "idle_timeout": self.idle_timeout,
            "random_min": self.random_min,
            "random_max": self.random_max,
            "keyword": self.keyword,
            "pattern": self.pattern,
            "case_sensitive": self.case_sensitive,
            "volume_threshold": self.volume_threshold,
            "hold_seconds": self.hold_seconds,
            "target_layer_id": self.target_layer_id,
            "mouse_button": self.mouse_button,
            "cooldown": self.cooldown,
            "allow_retrigger": self.allow_retrigger,
            "probability": self.probability,
            "max_fires": self.max_fires,
            "priority_override": self.priority_override,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Trigger":
        return cls(
            id=data.get("id", _id()),
            name=data.get("name", "触发器"),
            trigger_type=TriggerType(data.get("trigger_type", "timer")),
            enabled=bool(data.get("enabled", True)),
            action_id=data.get("action_id", ""),
            action_name=data.get("action_name", ""),
            interval=max(0.05, float(data.get("interval", 5.0))),
            delay=max(0.0, float(data.get("delay", 0.0))),
            idle_timeout=max(0.05, float(data.get("idle_timeout", 10.0))),
            random_min=max(0.05, float(data.get("random_min", 3.0))),
            random_max=max(0.05, float(data.get("random_max", 8.0))),
            keyword=data.get("keyword", ""),
            pattern=data.get("pattern", ""),
            case_sensitive=bool(data.get("case_sensitive", False)),
            volume_threshold=max(0.0, float(data.get("volume_threshold", 0.2))),
            hold_seconds=max(0.0, float(data.get("hold_seconds", 0.0))),
            target_layer_id=data.get("target_layer_id", ""),
            mouse_button=data.get("mouse_button", "left"),
            cooldown=max(0.0, float(data.get("cooldown", 0.0))),
            allow_retrigger=bool(data.get("allow_retrigger", False)),
            probability=min(1.0, max(0.0, float(data.get("probability", 1.0)))),
            max_fires=max(0, int(data.get("max_fires", 0))),
            priority_override=data.get("priority_override"),
        )


@dataclass
class SkinDefinition:
    name: str = "新皮肤"
    version: int = 2
    author: str = ""
    description: str = ""
    id: str = field(default_factory=_id)
    design_width: int = 720
    design_height: int = 140
    layers: list[Layer] = field(default_factory=list)
    actions: list[AnimationClip] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    fps: int = 30
    total_duration: float = 10.0

    def get_layer_by_id(self, layer_id: str) -> Optional[Layer]:
        return next((layer for layer in self.layers if layer.id == layer_id), None)

    def get_action_by_id(self, action_id: str) -> Optional[AnimationClip]:
        return next((action for action in self.actions if action.id == action_id), None)

    def get_action_by_name(self, name: str) -> Optional[AnimationClip]:
        return next((action for action in self.actions if action.name == name), None)

    def resolve_trigger_actions(self) -> None:
        for trigger in self.triggers:
            if not trigger.action_id and trigger.action_name:
                action = self.get_action_by_name(trigger.action_name)
                if action:
                    trigger.action_id = action.id

    def validate(self) -> list[str]:
        errors: list[str] = []
        layer_ids = {layer.id for layer in self.layers}
        action_ids = {action.id for action in self.actions}
        for action in self.actions:
            missing = action.target_layer_ids - layer_ids
            if missing:
                errors.append(f"动作“{action.name}”引用了不存在的图层: {', '.join(sorted(missing))}")
        for trigger in self.triggers:
            if trigger.action_id and trigger.action_id not in action_ids:
                errors.append(f"触发器“{trigger.name}”引用了不存在的动作")
            if trigger.target_layer_id and trigger.target_layer_id not in layer_ids:
                errors.append(f"触发器“{trigger.name}”引用了不存在的点击图层")
        return errors

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": 2,
            "author": self.author,
            "description": self.description,
            "id": self.id,
            "design_width": self.design_width,
            "design_height": self.design_height,
            "layers": [layer.to_dict() for layer in self.layers],
            "actions": [action.to_dict() for action in self.actions],
            "triggers": [trigger.to_dict() for trigger in self.triggers],
            "fps": self.fps,
            "total_duration": self.total_duration,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkinDefinition":
        skin = cls(
            name=data.get("name", "新皮肤"),
            version=2,
            author=data.get("author", ""),
            description=data.get("description", ""),
            id=data.get("id", _id()),
            design_width=max(1, int(data.get("design_width", 720))),
            design_height=max(1, int(data.get("design_height", 140))),
            fps=max(1, int(data.get("fps", 30))),
            total_duration=max(0.01, float(data.get("total_duration", 10.0))),
        )
        skin.layers = [Layer.from_dict(item) for item in data.get("layers", [])]
        skin.actions = [AnimationClip.from_dict(item) for item in data.get("actions", [])]
        skin.triggers = [Trigger.from_dict(item) for item in data.get("triggers", [])]
        skin.resolve_trigger_actions()
        return skin

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "SkinDefinition":
        with path.open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))
