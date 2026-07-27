"""说话人 ID → 显示名 映射（per source，Qt signal + JSON 持久化）

每个数据源（system / mic）独立一份 SpeakerNameMap 实例：
- mic 源的 spk_id=0（你）和 system 源的 spk_id=0（远端主播）不是同一个人，
  放同一个 map 会出现「同 ID 不同人」的张冠李戴，所以 per-source。
- 持久化按 source 分文件：speaker_names.{system|mic}.json（位于 user_data_dir）。
- 改名为空字符串 = 删除条目，display 退回默认「说话人 N」（N=spk_id+1）。
- 改名实时生效：set_name 发 name_changed(spk_id, new_name) 信号，
  UI 监听后内存重绘当前显示名（不重启引擎、不重跑识别）。

> 这类只覆盖「名字 + 持久化」，**不**碰识别结果，不存 embedding。
> 引擎给的 spk_id 在会话内稳定即可，重启会重新聚类，名字跟着新 spk_id 走。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from ..paths import user_data_dir


class SpeakerNameMap(QObject):
    """说话人 ID → 显示名 映射（per source）

    用法：
        names = SpeakerNameMap(source="system")
        text = names.display(0)                # "说话人 1"（未命名走默认）
        names.set_name(0, "张三")                # 触发 name_changed(0, "张三")
        names.set_name(0, "")                   # 触发 name_changed(0, "")，display 回退默认
        names.all_named()                       # {0: "张三", 2: "李四"}（按 spk_id 升序）

    注意：spk_id 本身是「本会话内」稳定的聚类编号。
    重启后引擎会重新聚类（可能给同一个真实人分配不同 spk_id），
    名字通过 JSON 文件跨会话保留，但 spk_id 编号本身不保证稳定。
    这是设计取舍：不存 embedding、不做 1:N 比对，换来「零隐私问题、零注册流程」。
    """

    # (spk_id, new_name) —— new_name 为 "" 表示恢复默认名（删除条目）
    name_changed = Signal(int, str)

    def __init__(self, source: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._source = source
        self._map: dict[int, str] = {}
        self._load()

    @property
    def source(self) -> str:
        return self._source

    # ============================================================
    # 读
    # ============================================================
    def display(self, spk_id: int) -> str:
        """取 spk_id 的显示名；未命名走「说话人 N」默认（N=spk_id+1）。

        这个方法没有副作用，纯函数式，可被任意线程（UI / pipeline / 推理线程）调用。
        """
        return self._map.get(spk_id) or f"说话人 {spk_id + 1}"

    def all_named(self) -> dict[int, str]:
        """返回所有已命名条目（按 spk_id 升序），供 UI 管理面板渲染。

        未命名但已识别的 spk_id 不在这里（由 UI 自己维护「见过的 spk_id」集合，
        拼上 all_named 一起渲染管理面板）。
        """
        return dict(sorted(self._map.items()))

    def has_name(self, spk_id: int) -> bool:
        return spk_id in self._map

    def named_count(self) -> int:
        return len(self._map)

    # ============================================================
    # 写
    # ============================================================
    def set_name(self, spk_id: int, name: str) -> None:
        """设置/删除名字。

        - name 为空 / 纯空白 → 删除该条目（display 回退默认），发 name_changed(spk_id, "")
        - name 与旧名相同   → no-op，不发信号（避免 UI 无意义重绘）
        - name 非空         → 写入，发 name_changed(spk_id, name)
        """
        name = name.strip()
        if not name:
            if spk_id in self._map:
                del self._map[spk_id]
                self._save()
                self.name_changed.emit(spk_id, "")
        else:
            if self._map.get(spk_id) != name:
                self._map[spk_id] = name
                self._save()
                self.name_changed.emit(spk_id, name)

    def reset(self) -> None:
        """清空所有命名（不删 JSON 文件，只清内存 + 写空文件）。"""
        if self._map:
            old = list(self._map.keys())
            self._map.clear()
            self._save()
            for spk_id in old:
                self.name_changed.emit(spk_id, "")

    # ============================================================
    # 持久化
    # ============================================================
    def _load(self) -> None:
        path = self._file_path()
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[speaker_names] 读取 {path.name} 失败：{e}，使用空映射")
            return
        # 健壮性：只保留合法 int → 非空 str
        cleaned: dict[int, str] = {}
        for k, v in raw.items():
            try:
                spk_id = int(k)
            except (TypeError, ValueError):
                continue
            name = str(v).strip()
            if name:
                cleaned[spk_id] = name
        self._map = cleaned

    def _save(self) -> None:
        path = self._file_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {str(k): v for k, v in self._map.items()},
                    f, ensure_ascii=False, indent=2,
                )
        except OSError as e:
            print(f"[speaker_names] 写入 {path.name} 失败：{e}")

    def _file_path(self) -> Path:
        return user_data_dir() / f"speaker_names.{self._source}.json"
