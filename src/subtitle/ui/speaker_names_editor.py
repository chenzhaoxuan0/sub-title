"""说话人显示名管理面板（设置页「说话人」标签内）。

- 监听 panel.spk_id_seen(source, spk_id) → 自动加一行（满足「新 spk_id 自动加入管理面板」）
- 每个 source（system / mic）独立 section
- 每行：默认名 + 当前显示名 + 可编辑输入框
- 输入框编辑完成（editingFinished，即回车/失焦）→ 调 SpeakerNameMap.set_name
- 清空输入框 = 删除该条目，display 退回「说话人 N」（满足「恢复说话人N标签」）
- 外部改名（比如另一个 editor 实例）通过 SpeakerNameMap.name_changed 自动同步显示
- 底部「清空所有命名」按钮 → 整个 source 的命名清空
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton,
)

from ..core.speaker_names import SpeakerNameMap


class _SpeakerRow(QWidget):
    """单行：默认名 → 当前显示名  +  编辑框。"""

    def __init__(self, spk_id: int, smap: SpeakerNameMap, parent=None):
        super().__init__(parent)
        self.spk_id = spk_id
        self.smap = smap
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.label = QLabel()
        self.label.setMinimumWidth(220)
        lay.addWidget(self.label)
        lay.addStretch(1)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("输入名字（清空回车恢复默认）")
        self.edit.setMaximumWidth(240)
        lay.addWidget(self.edit)
        self.edit.editingFinished.connect(self._on_edit_done)
        self._refresh()
        # 改名信号（无论是这个 editor 还是别的 editor 改的）→ 自动同步显示
        smap.name_changed.connect(self._on_map_changed)

    def _refresh(self):
        is_named = self.smap.has_name(self.spk_id)
        name = self.smap.display(self.spk_id)
        # label：默认名（说话人 N）→ 当前显示名（命名后变蓝粗体）
        if is_named:
            self.label.setText(
                f'<span style="color:#888;">说话人 {self.spk_id + 1} →</span> '
                f'<span style="color:#5aa9ff; font-weight:bold;">{name}</span>'
            )
        else:
            self.label.setText(
                f'<span style="color:#888;">说话人 {self.spk_id + 1}（未命名）</span>'
            )
        # 编辑框：仅在「无焦点 + 与现值不一致」时同步（避免打断用户输入）
        if not self.edit.hasFocus():
            cur = self.smap._map.get(self.spk_id, "") if is_named else ""
            if self.edit.text() != cur:
                self.edit.setText(cur)

    def _on_edit_done(self):
        # editingFinished：回车 / 失焦 都触发
        new = self.edit.text().strip()
        self.smap.set_name(self.spk_id, new)
        # name_changed → _on_map_changed → _refresh

    def _on_map_changed(self, changed_spk_id: int, _new: str):
        if changed_spk_id == self.spk_id:
            self._refresh()


class _SourceSection(QGroupBox):
    """一个 source（system / mic）的所有 spk_id 行 + 清空按钮。"""

    def __init__(self, source: str, smap: SpeakerNameMap, parent=None):
        title = "🔊 电脑声音（说话人）" if source == "system" else "🎤 麦克风（说话人）"
        super().__init__(title, parent)
        self.source = source
        self.smap = smap
        self._rows: dict[int, _SpeakerRow] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 18, 8, 8)
        outer.setSpacing(4)
        # 行容器（最后一个 widget 始终是 _empty_label，方便插入到它前面）
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(2)
        outer.addLayout(self._rows_layout)
        # 空状态提示
        self._empty_label = QLabel("（还没有识别到说话人——开始识别后自动出现）")
        self._empty_label.setStyleSheet("color: #888;")
        self._rows_layout.addWidget(self._empty_label)
        # 清空按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        clear_btn = QPushButton("清空所有命名")
        clear_btn.setToolTip("把所有命名都清空，display 回退「说话人 N」")
        clear_btn.clicked.connect(self._on_clear_all)
        btn_row.addWidget(clear_btn)
        outer.addLayout(btn_row)

    def add_spk_id(self, spk_id: int):
        """加一行；spk_id 已存在则 no-op。"""
        if spk_id in self._rows:
            return
        self._empty_label.hide()
        row = _SpeakerRow(spk_id, self.smap, self)
        self._rows[spk_id] = row
        # 插入到 _empty_label 之前
        idx = self._rows_layout.count() - 1
        self._rows_layout.insertWidget(idx, row)

    def has_spk_id(self, spk_id: int) -> bool:
        return spk_id in self._rows

    def _on_clear_all(self):
        self.smap.reset()  # 触发每个 _SpeakerRow 的 name_changed → 同步


class SpeakerNamesEditor(QWidget):
    """说话人显示名管理面板（设置页 tab 内容）。"""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        # 复用 panel 已创建的 SpeakerNameMap（保证 panel 渲染和 editor 显示用同一份数据）
        self._system_section = _SourceSection("system", panel._speaker_names["system"], self)
        self._mic_section = _SourceSection("mic", panel._speaker_names["mic"], self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        # 顶部说明
        info = QLabel(
            "每个说话人会被自动分配一个编号（说话人 1、说话人 2…）。\n"
            "识别出新说话人时，下面会自动加一行可编辑。\n"
            "命名后立即生效，新识别到的该说话人文字会显示你的命名。"
        )
        info.setStyleSheet("color: #888; padding: 4px;")
        info.setWordWrap(True)
        lay.addWidget(info)
        lay.addWidget(self._system_section)
        lay.addWidget(self._mic_section)
        lay.addStretch(1)
        # 监听新 spk_id → 自动加行
        self.panel.spk_id_seen.connect(self._on_spk_id_seen)
        # 初始化：把已经发现但还没显示的 spk_ids 也补上
        # （正常情况为空，但如果 editor 在识别开始后才打开也能补全）
        for source, ids in panel._seen_spk_ids.items():
            for sid in sorted(ids):
                self._on_spk_id_seen(source, sid)

    def _on_spk_id_seen(self, source: str, spk_id: int):
        section = self._system_section if source == "system" else self._mic_section
        section.add_spk_id(spk_id)
