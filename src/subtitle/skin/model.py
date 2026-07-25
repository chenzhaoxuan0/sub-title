"""桌宠皮肤数据模型 —— 图层、关键帧、事件、皮肤定义的序列化结构。

面向 AE/剪辑用户的关键帧动画模型：
- 每个图层有多个可动画属性（x, y, scale, rotation, opacity）
- 每个属性可以在时间轴上打关键帧
- 关键帧之间支持多种插值（线性、贝塞尔、阶梯）
- 事件系统触发动作播放（定时、识别事件等）
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


# ============================================================
# 插值类型
# ============================================================

class Interpolation(str, Enum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    BEZIER = "bezier"
    STEP = "step"          # 阶梯（直接跳到下一帧值）
    HOLD = "hold"          # 保持（保持当前值直到下一帧）


# ============================================================
# 关键帧
# ============================================================

@dataclass
class Keyframe:
    """单个关键帧。"""
    time: float                    # 时间（秒）
    value: float                   # 属性值
    interpolation: Interpolation = Interpolation.LINEAR
    # 贝塞尔控制点（仅 BEZIER 插值时使用）
    bezier_out: tuple = (0.33, 0.0)   # 出控制点 (x, y) 归一化
    bezier_in: tuple = (0.67, 1.0)    # 入控制点

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "value": self.value,
            "interpolation": self.interpolation.value,
            "bezier_out": list(self.bezier_out),
            "bezier_in": list(self.bezier_in),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Keyframe":
        return cls(
            time=d["time"],
            value=d["value"],
            interpolation=Interpolation(d.get("interpolation", "linear")),
            bezier_out=tuple(d.get("bezier_out", (0.33, 0.0))),
            bezier_in=tuple(d.get("bezier_in", (0.67, 1.0))),
        )


# ============================================================
# 属性轨道
# ============================================================

# 可动画属性列表
ANIMATABLE_PROPERTIES = ["x", "y", "scale_x", "scale_y", "rotation", "opacity"]


@dataclass
class PropertyTrack:
    """一个属性的关键帧轨道。"""
    property_name: str                         # x, y, scale_x, scale_y, rotation, opacity
    keyframes: list = field(default_factory=list)  # List[Keyframe]，按 time 排序
    default_value: float = 0.0                 # 无关键帧时的默认值

    def add_keyframe(self, kf: Keyframe):
        """添加关键帧并保持时间排序。"""
        self.keyframes.append(kf)
        self.keyframes.sort(key=lambda k: k.time)

    def remove_keyframe_at(self, time: float, tolerance: float = 0.05) -> bool:
        """删除指定时间附近的关键帧。"""
        for i, kf in enumerate(self.keyframes):
            if abs(kf.time - time) <= tolerance:
                self.keyframes.pop(i)
                return True
        return False

    def get_value_at(self, time: float) -> float:
        """插值计算指定时间的属性值。"""
        if not self.keyframes:
            return self.default_value
        if len(self.keyframes) == 1:
            return self.keyframes[0].value

        # 找到 time 所在的区间
        if time <= self.keyframes[0].time:
            return self.keyframes[0].value
        if time >= self.keyframes[-1].time:
            return self.keyframes[-1].value

        for i in range(len(self.keyframes) - 1):
            kf_a = self.keyframes[i]
            kf_b = self.keyframes[i + 1]
            if kf_a.time <= time <= kf_b.time:
                return self._interpolate(kf_a, kf_b, time)

        return self.keyframes[-1].value

    def _interpolate(self, kf_a: Keyframe, kf_b: Keyframe, time: float) -> float:
        """根据插值类型计算中间值。"""
        duration = kf_b.time - kf_a.time
        if duration <= 0:
            return kf_b.value

        t = (time - kf_a.time) / duration  # 归一化 0~1

        interp = kf_a.interpolation
        if interp == Interpolation.STEP or interp == Interpolation.HOLD:
            return kf_a.value
        elif interp == Interpolation.LINEAR:
            factor = t
        elif interp == Interpolation.EASE_IN:
            factor = t * t
        elif interp == Interpolation.EASE_OUT:
            factor = 1 - (1 - t) * (1 - t)
        elif interp == Interpolation.EASE_IN_OUT:
            factor = t * t * (3 - 2 * t)  # smoothstep
        elif interp == Interpolation.BEZIER:
            factor = self._cubic_bezier(t, kf_a.bezier_out, kf_b.bezier_in)
        else:
            factor = t

        return kf_a.value + (kf_b.value - kf_a.value) * factor

    @staticmethod
    def _cubic_bezier(t: float, p1: tuple, p2: tuple) -> float:
        """三次贝塞尔插值（简化版，用 y 分量）。"""
        # P0=(0,0), P1=p1, P2=p2, P3=(1,1)
        # 这里直接用 t 参数化 y 值（近似）
        y1, y2 = p1[1], p2[1]
        mt = 1 - t
        return 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t * t * t

    def to_dict(self) -> dict:
        return {
            "property_name": self.property_name,
            "default_value": self.default_value,
            "keyframes": [kf.to_dict() for kf in self.keyframes],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PropertyTrack":
        return cls(
            property_name=d["property_name"],
            default_value=d.get("default_value", 0.0),
            keyframes=[Keyframe.from_dict(k) for k in d.get("keyframes", [])],
        )


# ============================================================
# 图层
# ============================================================

@dataclass
class Layer:
    """一个贴图图层。"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "图层"
    image_path: str = ""               # 图片文件路径（相对于皮肤目录）
    visible: bool = True
    locked: bool = False               # 锁定后不可编辑（防误操作）

    # 静态变换（无关键帧时的基础值）
    x: float = 0.0                     # 位置 X（像素，相对字幕区左上角）
    y: float = 0.0                     # 位置 Y
    scale_x: float = 1.0              # 缩放 X
    scale_y: float = 1.0              # 缩放 Y
    rotation: float = 0.0             # 旋转角度（度）
    opacity: float = 1.0              # 不透明度 0~1

    # 锚点（变换中心，归一化 0~1）
    anchor_x: float = 0.5
    anchor_y: float = 0.5

    # 动画轨道
    tracks: dict = field(default_factory=dict)  # {property_name: PropertyTrack}

    # 图层混合模式
    blend_mode: str = "normal"         # normal, multiply, screen, overlay

    def get_track(self, prop: str) -> PropertyTrack:
        """获取或创建属性轨道。"""
        if prop not in self.tracks:
            default = getattr(self, prop, 0.0)
            self.tracks[prop] = PropertyTrack(property_name=prop, default_value=default)
        return self.tracks[prop]

    def get_animated_value(self, prop: str, time: float) -> float:
        """获取指定时间的动画值（有关键帧用插值，否则用静态值）。"""
        if prop in self.tracks and self.tracks[prop].keyframes:
            return self.tracks[prop].get_value_at(time)
        return getattr(self, prop, 0.0)

    def has_animation(self) -> bool:
        """是否有任何关键帧动画。"""
        return any(track.keyframes for track in self.tracks.values())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "image_path": self.image_path,
            "visible": self.visible,
            "locked": self.locked,
            "x": self.x, "y": self.y,
            "scale_x": self.scale_x, "scale_y": self.scale_y,
            "rotation": self.rotation, "opacity": self.opacity,
            "anchor_x": self.anchor_x, "anchor_y": self.anchor_y,
            "blend_mode": self.blend_mode,
            "tracks": {k: v.to_dict() for k, v in self.tracks.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Layer":
        layer = cls(
            id=d.get("id", str(uuid.uuid4())[:8]),
            name=d.get("name", "图层"),
            image_path=d.get("image_path", ""),
            visible=d.get("visible", True),
            locked=d.get("locked", False),
            x=d.get("x", 0.0), y=d.get("y", 0.0),
            scale_x=d.get("scale_x", 1.0), scale_y=d.get("scale_y", 1.0),
            rotation=d.get("rotation", 0.0), opacity=d.get("opacity", 1.0),
            anchor_x=d.get("anchor_x", 0.5), anchor_y=d.get("anchor_y", 0.5),
            blend_mode=d.get("blend_mode", "normal"),
        )
        for k, v in d.get("tracks", {}).items():
            layer.tracks[k] = PropertyTrack.from_dict(v)
        return layer


# ============================================================
# 事件/触发器
# ============================================================

class TriggerType(str, Enum):
    TIMER = "timer"                    # 定时触发（每隔 N 秒）
    ON_START = "on_start"              # 识别开始时
    ON_STOP = "on_stop"                # 识别停止时
    ON_TEXT = "on_text"                # 新字幕文本到达时
    ON_FINAL = "on_final"              # 一句话结束时（is_final=True）
    ON_IDLE = "on_idle"                # 空闲时（无字幕 N 秒后）
    RANDOM = "random"                  # 随机间隔触发


@dataclass
class AnimationAction:
    """一个动画动作定义。"""
    name: str = "动作"
    duration: float = 1.0              # 动画时长（秒）
    loop: bool = False                 # 是否循环
    loop_count: int = 1                # 循环次数（loop=True 时）
    # 动作关联的图层和属性变化（覆盖图层轨道中的时间段）
    # 格式：{layer_id: {prop: [(time_offset, value, interpolation), ...]}}
    keyframe_overrides: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration": self.duration,
            "loop": self.loop,
            "loop_count": self.loop_count,
            "keyframe_overrides": self.keyframe_overrides,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AnimationAction":
        return cls(
            name=d.get("name", "动作"),
            duration=d.get("duration", 1.0),
            loop=d.get("loop", False),
            loop_count=d.get("loop_count", 1),
            keyframe_overrides=d.get("keyframe_overrides", {}),
        )


@dataclass
class Trigger:
    """事件触发器。"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "触发器"
    trigger_type: TriggerType = TriggerType.TIMER
    enabled: bool = True

    # 定时参数
    interval: float = 5.0              # timer: 间隔秒数
    delay: float = 0.0                 # 首次触发延迟
    idle_timeout: float = 10.0         # on_idle: 空闲多少秒后触发

    # random 参数
    random_min: float = 3.0
    random_max: float = 8.0

    # 触发的动作
    action_name: str = ""              # 关联的 AnimationAction.name

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "trigger_type": self.trigger_type.value,
            "enabled": self.enabled,
            "interval": self.interval,
            "delay": self.delay,
            "idle_timeout": self.idle_timeout,
            "random_min": self.random_min,
            "random_max": self.random_max,
            "action_name": self.action_name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trigger":
        return cls(
            id=d.get("id", str(uuid.uuid4())[:8]),
            name=d.get("name", "触发器"),
            trigger_type=TriggerType(d.get("trigger_type", "timer")),
            enabled=d.get("enabled", True),
            interval=d.get("interval", 5.0),
            delay=d.get("delay", 0.0),
            idle_timeout=d.get("idle_timeout", 10.0),
            random_min=d.get("random_min", 3.0),
            random_max=d.get("random_max", 8.0),
            action_name=d.get("action_name", ""),
        )


# ============================================================
# 皮肤定义（完整）
# ============================================================

@dataclass
class SkinDefinition:
    """一个完整的桌宠皮肤定义。"""
    name: str = "新皮肤"
    version: int = 1
    author: str = ""
    description: str = ""

    # 图层列表（从底到顶排序）
    layers: list = field(default_factory=list)  # List[Layer]

    # 动画动作
    actions: list = field(default_factory=list)  # List[AnimationAction]

    # 触发器
    triggers: list = field(default_factory=list)  # List[Trigger]

    # 全局动画设置
    fps: int = 30
    total_duration: float = 10.0       # 时间轴总时长（秒）

    def get_layer_by_id(self, layer_id: str) -> Optional[Layer]:
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        return None

    def get_action_by_name(self, name: str) -> Optional[AnimationAction]:
        for action in self.actions:
            if action.name == name:
                return action
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "layers": [l.to_dict() for l in self.layers],
            "actions": [a.to_dict() for a in self.actions],
            "triggers": [t.to_dict() for t in self.triggers],
            "fps": self.fps,
            "total_duration": self.total_duration,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkinDefinition":
        skin = cls(
            name=d.get("name", "新皮肤"),
            version=d.get("version", 1),
            author=d.get("author", ""),
            description=d.get("description", ""),
            fps=d.get("fps", 30),
            total_duration=d.get("total_duration", 10.0),
        )
        skin.layers = [Layer.from_dict(l) for l in d.get("layers", [])]
        skin.actions = [AnimationAction.from_dict(a) for a in d.get("actions", [])]
        skin.triggers = [Trigger.from_dict(t) for t in d.get("triggers", [])]
        return skin

    def save(self, path: Path):
        """保存皮肤到 JSON 文件。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "SkinDefinition":
        """从 JSON 文件加载皮肤。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
