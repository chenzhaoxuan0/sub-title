"""Conflict-aware playback for independent skin animation clips."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .model import AnimationClip, SkinDefinition


@dataclass
class _ActiveAction:
    clip: AnimationClip
    started_at: float
    priority: int


@dataclass
class _QueuedAction:
    action_id: str
    priority: int


class ActionPlayer(QObject):
    """Play clips in parallel when they affect disjoint layer sets."""

    state_changed = Signal(object, object)
    action_started = Signal(str)
    action_finished = Signal(str)

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self._skin = skin
        self._active: dict[str, _ActiveAction] = {}
        self._queue: list[_QueuedAction] = []
        self._cooldown_until: dict[str, float] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, 1000 // max(1, skin.fps)))
        self._timer.timeout.connect(self._tick)

    @property
    def skin(self) -> SkinDefinition:
        return self._skin

    @skin.setter
    def skin(self, value: SkinDefinition) -> None:
        self.stop_all()
        self._skin = value
        self._timer.setInterval(max(1, 1000 // max(1, value.fps)))

    @property
    def active_action_ids(self) -> tuple[str, ...]:
        return tuple(self._active)

    def play(
        self, action_id: str, priority_override: Optional[int] = None,
        allow_retrigger: bool = False,
    ) -> bool:
        return self._play_at(
            action_id, priority_override, time.monotonic(), allow_retrigger=allow_retrigger
        )

    def _play_at(
        self,
        action_id: str,
        priority_override: Optional[int],
        now: float,
        allow_queue: bool = True,
        allow_retrigger: bool = False,
    ) -> bool:
        clip = self._skin.get_action_by_id(action_id)
        if clip is None:
            return False
        if action_id in self._active:
            if not allow_retrigger:
                return False
            self._active.pop(action_id)
            self.action_finished.emit(action_id)
        if now < self._cooldown_until.get(action_id, 0.0):
            return False
        priority = clip.priority if priority_override is None else int(priority_override)
        conflicts = [
            active
            for active in self._active.values()
            if active.clip.target_layer_ids & clip.target_layer_ids
        ]
        if conflicts:
            may_interrupt = all(
                active.clip.interruptible and priority > active.priority
                for active in conflicts
            )
            if may_interrupt:
                for active in conflicts:
                    self._finish(active.clip.id, now)
            elif allow_queue:
                if not any(item.action_id == action_id for item in self._queue):
                    self._queue.append(_QueuedAction(action_id, priority))
                    self._queue.sort(key=lambda item: item.priority, reverse=True)
                return False
            else:
                return False
        self._active[action_id] = _ActiveAction(clip, now, priority)
        if not self._timer.isActive():
            self._timer.start()
        self.action_started.emit(action_id)
        self._tick_at(now)
        return True

    def stop(self, action_id: str) -> None:
        now = time.monotonic()
        if action_id in self._active:
            self._finish(action_id, now)
            self._tick_at(now)
        self._queue = [item for item in self._queue if item.action_id != action_id]

    def stop_all(self) -> None:
        self._timer.stop()
        active_ids = list(self._active)
        self._active.clear()
        self._queue.clear()
        self._cooldown_until.clear()
        for action_id in active_ids:
            self.action_finished.emit(action_id)
        self.state_changed.emit({}, {})

    def _tick(self) -> None:
        self._tick_at(time.monotonic())

    def _tick_at(self, now: float) -> tuple[dict, dict]:
        finished: list[str] = []
        overrides: dict[str, dict[str, float]] = {}
        layer_times: dict[str, float] = {}
        for action_id, active in list(self._active.items()):
            clip = active.clip
            elapsed = max(0.0, now - active.started_at)
            total_loops = clip.loop_count if clip.loop else 1
            total_duration = clip.duration * max(1, total_loops)
            if elapsed >= total_duration:
                finished.append(action_id)
                continue
            local_time = elapsed % clip.duration if clip.loop else min(elapsed, clip.duration)
            for layer_id, properties in clip.tracks.items():
                layer_values = overrides.setdefault(layer_id, {})
                for property_name, track in properties.items():
                    layer_values[property_name] = track.get_value_at(local_time)
                layer_times[layer_id] = local_time
        for action_id in finished:
            self._finish(action_id, now)
        self.state_changed.emit(overrides, layer_times)
        self._start_queued(now)
        if not self._active and not self._queue:
            self._timer.stop()
        return overrides, layer_times

    def _finish(self, action_id: str, now: float) -> None:
        active = self._active.pop(action_id, None)
        if active is None:
            return
        self._cooldown_until[action_id] = now + active.clip.cooldown
        self.action_finished.emit(action_id)

    def _start_queued(self, now: float) -> None:
        if not self._queue:
            return
        remaining: list[_QueuedAction] = []
        queued = self._queue
        self._queue = []
        for item in queued:
            if not self._play_at(
                item.action_id, item.priority, now, allow_queue=False,
                allow_retrigger=False,
            ):
                remaining.append(item)
        self._queue.extend(remaining)
