"""事件触发系统 —— 管理触发器的定时/事件驱动逻辑。

触发器类型：
- TIMER: 固定间隔触发
- ON_START: 识别开始时触发
- ON_STOP: 识别停止时触发
- ON_TEXT: 新字幕到达时触发
- ON_FINAL: 一句话结束时触发
- ON_IDLE: 空闲超时后触发
- RANDOM: 随机间隔触发
"""
from __future__ import annotations

import random
import time
from typing import Callable, Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from .model import SkinDefinition, Trigger, TriggerType, AnimationAction


class TriggerManager(QObject):
    """触发器管理器：监听事件、管理定时器、触发动作播放。"""

    # 动作触发信号：(action_name, layer_overrides)
    action_triggered = pyqtSignal(str)

    def __init__(self, skin: SkinDefinition, parent=None):
        super().__init__(parent)
        self._skin = skin
        self._timers: dict[str, QTimer] = {}
        self._last_text_time: float = 0.0
        self._idle_timer: Optional[QTimer] = None
        self._active = False

    @property
    def skin(self) -> SkinDefinition:
        return self._skin

    @skin.setter
    def skin(self, value: SkinDefinition):
        self.stop()
        self._skin = value

    def start(self):
        """启动所有触发器。"""
        self._active = True
        self._last_text_time = time.time()

        for trigger in self._skin.triggers:
            if not trigger.enabled:
                continue
            self._setup_trigger(trigger)

    def stop(self):
        """停止所有触发器。"""
        self._active = False
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()
        if self._idle_timer:
            self._idle_timer.stop()
            self._idle_timer = None

    def _setup_trigger(self, trigger: Trigger):
        """为单个触发器设置定时器。"""
        if trigger.trigger_type == TriggerType.TIMER:
            timer = QTimer(self)
            timer.setInterval(int(trigger.interval * 1000))
            timer.timeout.connect(lambda t=trigger: self._fire(t))
            # 首次延迟
            if trigger.delay > 0:
                QTimer.singleShot(int(trigger.delay * 1000), timer.start)
            else:
                timer.start()
            self._timers[trigger.id] = timer

        elif trigger.trigger_type == TriggerType.RANDOM:
            self._schedule_random(trigger)

        elif trigger.trigger_type == TriggerType.ON_IDLE:
            self._idle_timer = QTimer(self)
            self._idle_timer.setInterval(1000)  # 每秒检查
            self._idle_timer.timeout.connect(lambda t=trigger: self._check_idle(t))
            self._idle_timer.start()
            self._timers[trigger.id] = self._idle_timer

    def _schedule_random(self, trigger: Trigger):
        """随机间隔触发：每次触发后重新调度下一次。"""
        interval = random.uniform(trigger.random_min, trigger.random_max)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(int(interval * 1000))
        timer.timeout.connect(lambda: self._on_random_fire(trigger))
        timer.start()
        self._timers[trigger.id] = timer

    def _on_random_fire(self, trigger: Trigger):
        """随机触发器触发后重新调度。"""
        self._fire(trigger)
        if self._active and trigger.enabled:
            self._schedule_random(trigger)

    def _check_idle(self, trigger: Trigger):
        """检查是否空闲超时。"""
        elapsed = time.time() - self._last_text_time
        if elapsed >= trigger.idle_timeout:
            self._fire(trigger)
            self._last_text_time = time.time()  # 重置，避免连续触发

    def _fire(self, trigger: Trigger):
        """触发一个动作。"""
        if trigger.action_name:
            self.action_triggered.emit(trigger.action_name)

    # ---------- 外部事件输入 ----------
    def on_recognition_start(self):
        """识别开始事件。"""
        self._last_text_time = time.time()
        for trigger in self._skin.triggers:
            if trigger.enabled and trigger.trigger_type == TriggerType.ON_START:
                self._fire(trigger)

    def on_recognition_stop(self):
        """识别停止事件。"""
        for trigger in self._skin.triggers:
            if trigger.enabled and trigger.trigger_type == TriggerType.ON_STOP:
                self._fire(trigger)

    def on_text_received(self, is_final: bool = False):
        """新字幕文本到达。"""
        self._last_text_time = time.time()
        for trigger in self._skin.triggers:
            if not trigger.enabled:
                continue
            if trigger.trigger_type == TriggerType.ON_TEXT:
                self._fire(trigger)
            elif trigger.trigger_type == TriggerType.ON_FINAL and is_final:
                self._fire(trigger)
