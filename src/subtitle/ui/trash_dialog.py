"""主题回收站对话框 —— 恢复 / 永久删除被软删除的自定义主题。

设计目标：
- 删除是软删除（文件移到 themes/.trash/），所以"恢复"就是把它移回去。
- 支持"恢复为"（重命名后再恢复），解决名字已被占用或想换名的情况。
- 任何时候都可以"永久删除"或"清空回收站"。
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QMessageBox, QInputDialog, QLabel,
)

from .theme_engine import get_theme_manager


class TrashDialog(QDialog):
    """主题回收站。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("主题回收站")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(560, 420)

        self._mgr = get_theme_manager()

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)

        v.addWidget(QLabel("被删除的自定义主题暂存在这里，可以恢复或永久删除。"))

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        v.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 刷新")
        self.restore_btn = QPushButton("↩️ 恢复")
        self.restore_as_btn = QPushButton("📝 恢复为…")
        self.delete_btn = QPushButton("🗑 永久删除")
        self.empty_btn = QPushButton("💥 清空回收站")
        self.close_btn = QPushButton("关闭")

        self.refresh_btn.clicked.connect(self._refresh)
        self.restore_btn.clicked.connect(self._on_restore)
        self.restore_as_btn.clicked.connect(self._on_restore_as)
        self.delete_btn.clicked.connect(self._on_delete_permanent)
        self.empty_btn.clicked.connect(self._on_empty)
        self.close_btn.clicked.connect(self.accept)

        for b in (self.refresh_btn, self.restore_btn, self.restore_as_btn,
                  self.delete_btn, self.empty_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        btn_row.addWidget(self.close_btn)
        v.addLayout(btn_row)

        self._refresh()

    # ---------- 列表 ----------
    def _refresh(self):
        self.list_widget.clear()
        items = self._mgr.list_trashed_themes()
        if not items:
            placeholder = QListWidgetItem("（回收站为空）")
            placeholder.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(placeholder)
            return
        for item in items:
            ts = datetime.fromtimestamp(item["trashed_at"]).strftime("%Y-%m-%d %H:%M:%S")
            display = f"{item['original_name']}    （删除于 {ts}）"
            li = QListWidgetItem(display)
            li.setData(Qt.UserRole, item["filename"])
            self.list_widget.addItem(li)

    def _get_selected_filename(self) -> str | None:
        items = self.list_widget.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    # ---------- 操作 ----------
    def _on_restore(self):
        filename = self._get_selected_filename()
        if not filename:
            QMessageBox.information(self, "提示", "请先选中一个主题")
            return
        result = self._mgr.restore_trashed_theme(filename)
        if result is None:
            QMessageBox.warning(
                self, "失败",
                "恢复失败。可能原因：\n"
                "• 原名已被其他主题占用（试试「📝 恢复为…」换个名字）\n"
                "• 文件已损坏",
            )
            return
        self._refresh()
        QMessageBox.information(self, "成功", f"已恢复「{result.name}」")

    def _on_restore_as(self):
        filename = self._get_selected_filename()
        if not filename:
            QMessageBox.information(self, "提示", "请先选中一个主题")
            return
        items = self._mgr.list_trashed_themes()
        original = next((i for i in items if i["filename"] == filename), None)
        if not original:
            return
        new_name, ok = QInputDialog.getText(
            self, "恢复为", "新名称：", text=original["original_name"],
        )
        if not ok or not new_name.strip():
            return
        result = self._mgr.restore_trashed_theme(filename, new_name=new_name.strip())
        if result is None:
            QMessageBox.warning(
                self, "失败",
                "恢复失败：可能是新名字与内置主题或其他自定义主题重名。",
            )
            return
        self._refresh()
        QMessageBox.information(self, "成功", f"已恢复为「{result.name}」")

    def _on_delete_permanent(self):
        filename = self._get_selected_filename()
        if not filename:
            QMessageBox.information(self, "提示", "请先选中一个主题")
            return
        ret = QMessageBox.question(
            self, "永久删除", "确定永久删除？此操作不可撤销。",
        )
        if ret == QMessageBox.Yes:
            if self._mgr.delete_trashed_theme_permanently(filename):
                self._refresh()
            else:
                QMessageBox.warning(self, "失败", "删除失败")

    def _on_empty(self):
        if not self._mgr.list_trashed_themes():
            return
        ret = QMessageBox.question(
            self, "清空回收站", "确定永久清空回收站？此操作不可撤销。",
        )
        if ret == QMessageBox.Yes:
            count = self._mgr.empty_trash()
            self._refresh()
            QMessageBox.information(self, "完成", f"已永久删除 {count} 个主题")
