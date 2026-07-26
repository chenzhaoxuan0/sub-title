"""Timer and application-event triggers for skin animation clips."""
from __future__ import annotations

import random
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .model import SkinDefinition, Trigger, TriggerType


class TriggerManager(QObject):
    action_triggered = Signal(str, object, bool)

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self._skin = skin
        self._timers: dict[str, QTimer] = {}
        self._last_text_time = time.monotonic()
        self._last_fire: dict[str, float] = {}
        self._fire_counts: dict[str, int] = {}
        self._volume_since: dict[str, float] = {}
        self._active = False

    @property
    def skin(self) -> SkinDefinition:
        return self._skin

    @skin.setter
    def skin(self, value: SkinDefinition) -> None:
        was_active = self._active
        self.stop()
        self._skin = value
        if was_active:
            self.start()

    def start(self) -> None:
        self.stop()
        self._active = True
        self._last_text_time = time.monotonic()
        for trigger in self._skin.triggers:
            if trigger.enabled:
                self._setup_trigger(trigger)

    def stop(self) -> None:
        self._active = False
        for timer in self._timers.values():
            timer.stop()
            timer.deleteLater()
        self._timers.clear()
        self._volume_since.clear()

    def refresh(self) -> None:
        if self._active:
            self.start()

    def fire_for_test(self, trigger_id: str) -> bool:
        trigger = next((item for item in self._skin.triggers if item.id == trigger_id), None)
        return self._fire(trigger, bypass_limits=True) if trigger else False

    def _setup_trigger(self, trigger: Trigger) -> None:
        if trigger.trigger_type == TriggerType.TIMER:
            timer = QTimer(self)
            timer.setInterval(max(50, int(trigger.interval * 1000)))
            timer.timeout.connect(lambda current=trigger: self._fire(current))
            self._timers[trigger.id] = timer
            if trigger.delay:
                QTimer.singleShot(int(trigger.delay * 1000), lambda: self._start_timer(trigger.id))
            else:
                timer.start()
        elif trigger.trigger_type == TriggerType.RANDOM:
            self._schedule_random(trigger)
        elif trigger.trigger_type == TriggerType.ON_IDLE:
            timer = QTimer(self)
            timer.setInterval(250)
            timer.timeout.connect(lambda current=trigger: self._check_idle(current))
            timer.start()
            self._timers[trigger.id] = timer

    def _start_timer(self, trigger_id: str) -> None:
        if self._active and trigger_id in self._timers:
            self._timers[trigger_id].start()

    def _schedule_random(self, trigger: Trigger) -> None:
        if not self._active:
            return
        low, high = sorted((trigger.random_min, trigger.random_max))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(max(50, int(random.uniform(low, high) * 1000)))
        timer.timeout.connect(lambda current=trigger: self._random_fired(current))
        timer.start()
        old = self._timers.pop(trigger.id, None)
        if old is not None:
            old.deleteLater()
        self._timers[trigger.id] = timer

    def _random_fired(self, trigger: Trigger) -> None:
        self._fire(trigger)
        if self._active and trigger.enabled:
            self._schedule_random(trigger)

    def _check_idle(self, trigger: Trigger) -> None:
        if time.monotonic() - self._last_text_time >= trigger.idle_timeout:
            if self._fire(trigger):
                self._last_text_time = time.monotonic()

    def _fire(self, trigger: Trigger, bypass_limits: bool = False) -> bool:
        if not trigger.enabled or not trigger.action_id:
            return False
        now = time.monotonic()
        if not bypass_limits:
            if trigger.max_fires and self._fire_counts.get(trigger.id, 0) >= trigger.max_fires:
                return False
            if now - self._last_fire.get(trigger.id, float("-inf")) < trigger.cooldown:
                return False
            if random.random() > trigger.probability:
                return False
        self._last_fire[trigger.id] = now
        self._fire_counts[trigger.id] = self._fire_counts.get(trigger.id, 0) + 1
        self.action_triggered.emit(
            trigger.action_id, trigger.priority_override, trigger.allow_retrigger
        )
        return True

    def _for_type(self, trigger_type: TriggerType):
        return (
            trigger
            for trigger in self._skin.triggers
            if trigger.enabled and trigger.trigger_type == trigger_type
        )

    def on_recognition_start(self) -> None:
        self._last_text_time = time.monotonic()
        for trigger in self._for_type(TriggerType.ON_START):
            self._fire(trigger)

    def on_recognition_stop(self) -> None:
        for trigger in self._for_type(TriggerType.ON_STOP):
            self._fire(trigger)

    def on_text_received(self, text: str = "", is_final: bool = False) -> None:
        self._last_text_time = time.monotonic()
        text_types = {
            TriggerType.ON_TEXT,
            TriggerType.ON_PARTIAL,
            TriggerType.ON_FINAL,
            TriggerType.KEYWORD,
            TriggerType.REGEX,
        }
        for trigger in self._skin.triggers:
            if trigger.enabled and trigger.trigger_type in text_types and trigger.matches_text(text, is_final):
                self._fire(trigger)

    def on_audio_level(self, rms: float, peak: float = 0.0) -> None:
        del peak
        now = time.monotonic()
        for trigger in self._skin.triggers:
            if not trigger.enabled or trigger.trigger_type not in (
                TriggerType.VOLUME_ABOVE,
                TriggerType.VOLUME_BELOW,
            ):
                continue
            matched = (
                rms >= trigger.volume_threshold
                if trigger.trigger_type == TriggerType.VOLUME_ABOVE
                else rms <= trigger.volume_threshold
            )
            if not matched:
                self._volume_since.pop(trigger.id, None)
                continue
            started = self._volume_since.setdefault(trigger.id, now)
            if now - started >= trigger.hold_seconds and self._fire(trigger):
                self._volume_since[trigger.id] = now

    def on_window_shown(self) -> None:
        for trigger in self._for_type(TriggerType.WINDOW_SHOW):
            self._fire(trigger)

    def on_window_hidden(self) -> None:
        for trigger in self._for_type(TriggerType.WINDOW_HIDE):
            self._fire(trigger)

    def on_layer_clicked(self, layer_id: str, mouse_button: str = "left") -> None:
        for trigger in self._for_type(TriggerType.ON_CLICK):
            if trigger.mouse_button == mouse_button and (
                not trigger.target_layer_id or trigger.target_layer_id == layer_id
            ):
                self._fire(trigger)

    def has_click_triggers(self) -> bool:
        return any(
            trigger.enabled and trigger.trigger_type == TriggerType.ON_CLICK
            for trigger in self._skin.triggers
        )
