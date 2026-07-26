"""桌宠皮肤系统 —— 贴图叠加 + 关键帧动画 + 事件触发。

模块结构：
- model.py: 数据模型（Layer, Keyframe, PropertyTrack, Trigger, SkinDefinition）
- renderer.py: 皮肤渲染器（将图层按时间绘制到 QPainter）
- events.py: 事件触发系统（定时/事件驱动触发动作）
- editor.py: 可视化皮肤编辑器（画布 + 图层面板 + 时间轴 + 属性面板）
"""
from .model import (
    SkinDefinition, Layer, Keyframe, PropertyTrack,
    AnimationAction, AnimationClip, Trigger, TriggerType, Interpolation,
    LayerPlane, HorizontalPin, VerticalPin, AssetType,
    ANIMATABLE_PROPERTIES,
)
from .renderer import SkinRenderer
from .events import TriggerManager
from .action_player import ActionPlayer
from .runtime import SkinRuntime

__all__ = [
    "SkinDefinition", "Layer", "Keyframe", "PropertyTrack",
    "AnimationAction", "AnimationClip", "Trigger", "TriggerType", "Interpolation",
    "LayerPlane", "HorizontalPin", "VerticalPin", "AssetType",
    "ANIMATABLE_PROPERTIES", "SkinRenderer", "TriggerManager", "ActionPlayer",
    "SkinRuntime",
]
