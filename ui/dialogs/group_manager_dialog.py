# -*- coding: utf-8 -*-
"""K1 VMS — Group Manager Dialog (list + buttons)"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QDialogButtonBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from core.group_manager import GroupManager
from core.camera_manager import CameraManager
import config


DIALOG_STYLE = f"""
QDialog {{ background-color: {config.COLOR_BG_DARK}; }}
QLabel {{ color: {config.COLOR_TEXT_PRIMARY}; background: transparent;
         font-size: 12px; }}
QListWidget {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c; border-radius: 6px;
    padding: 4px; font-size: 13px; outline: none; }}
QListWidget::item {{ padding: 10px 12px; border-radius: 5px; }}
QListWidget::item:hover {{ background: #2c333c; }}
QListWidget::item:selected {{ background: {config.COLOR_ACCENT};
    color: white; }}
QPushButton {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY}; border: 1px solid #2c333c;
    border-radius: 6px; padding: 8px 16px; font-size: 12px;
    font-weight: bold; }}
QPushButton:hover {{ background: #2c333c; }}
QDialogButtonBox QPushButton {{ padding: 8px 24px; }}
"""


class GroupManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.gm = GroupManager.instance()
        self.cam_manager = CameraManager()
        self.setWindowTitle("📁 مدیریت گروه‌ها")
        self.resize(540, 500)
        self.setStyleSheet(DIALOG_STYLE)
        self._build()
        self._refresh()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        title = QLabel("گروه‌های سفارشی")
        title.setStyleSheet(
            f"color: {config.COLOR_TEXT_PRIMARY}; font-size: 15px; "
            f"font-weight: 700;")
        v.addWidget(title)

        info = QLabel(
            "گروه‌ها به شما اجازه می‌دهند چند دوربین را با نام و رنگ "
            "دلخواه کنار هم بگذارید و در Live با یک Drag همه را پخش کنید."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px;")
        v.addWidget(info)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(
            lambda _: self._on_edit())
        v.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_btn = QPushButton("➕ گروه جدید")
        add_btn.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_ACCENT}; color: white;
                padding: 8px 18px; border-radius: 6px; font-weight: bold;
                border: none; }}
            QPushButton:hover {{ background: {config.COLOR_ACCENT_HOVER}; }}
        """)
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)

        edit_btn = QPushButton("✏ ویرایش")
        edit_btn.clicked.connect(self._on_edit)
        btn_row.addWidget(edit_btn)

        del_btn = QPushButton("🗑 حذف")
        del_btn.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_DANGER}; color: white;
                padding: 8px 18px; border-radius: 6px; font-weight: bold;
                border: none; }}
            QPushButton:hover {{ background: #c0392b; }}
        """)
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        v.addWidget(buttons)

    def _refresh(self):
        self.list_widget.clear()
        for g in self.gm.all():
            valid_cams = 0
            for u in g.camera_uids:
                if self.cam_manager.get(u) is not None:
                    valid_cams += 1
            text = f"  📁  {g.name}   —   {valid_cams} دوربین"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, g.uid)
            item.setForeground(QColor(g.color or "#f39c12"))
            self.list_widget.addItem(item)

    def _selected_uid(self):
        it = self.list_widget.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _on_add(self):
        from ui.dialogs.group_dialog import GroupDialog
        dlg = GroupDialog(None, self)
        if dlg.exec() == GroupDialog.Accepted:
            self.gm.add(dlg.get_group())
            self._refresh()

    def _on_edit(self):
        uid = self._selected_uid()
        if not uid:
            return
        from ui.dialogs.group_dialog import GroupDialog
        grp = self.gm.get(uid)
        if grp is None:
            return
        dlg = GroupDialog(grp, self)
        if dlg.exec() == GroupDialog.Accepted:
            self.gm.update(uid, dlg.get_group())
            self._refresh()

    def _on_delete(self):
        uid = self._selected_uid()
        if not uid:
            return
        from ui.dialogs import question as dlg_question
        grp = self.gm.get(uid)
        if grp is None:
            return
        if not dlg_question(self, f'حذف گروه "{grp.name}"؟', "حذف گروه"):
            return
        self.gm.delete(uid)
        self._refresh()