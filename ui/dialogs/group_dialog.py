# -*- coding: utf-8 -*-
"""K1 VMS — Group Dialog (Professional: dual-panel + search + color)"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QAbstractItemView, QFrame, QGridLayout
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap

from core.group_manager import CameraGroup
from core.camera_manager import CameraManager
from core.nvr_manager import NVRManager
import config


DIALOG_STYLE = f"""
QDialog {{ background-color: {config.COLOR_BG_DARK}; }}
QLabel {{ color: {config.COLOR_TEXT_PRIMARY}; background: transparent;
         font-size: 12px; }}
QLineEdit {{
    background: {config.COLOR_BG_CARD}; color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c; border-radius: 6px;
    padding: 8px 10px; font-size: 13px; min-height: 22px;
}}
QLineEdit:focus {{ border: 1px solid {config.COLOR_ACCENT}; }}
QListWidget {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c; border-radius: 6px;
    padding: 4px; font-size: 12px;
    outline: none;
}}
QListWidget::item {{ padding: 7px 10px; border-radius: 4px; }}
QListWidget::item:hover {{ background: #2c333c; }}
QListWidget::item:selected {{ background: {config.COLOR_ACCENT};
    color: white; }}
QDialogButtonBox QPushButton {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY}; border: 1px solid #2c333c;
    border-radius: 6px; padding: 10px 24px; font-size: 13px;
    font-weight: bold; min-width: 90px; }}
QDialogButtonBox QPushButton:hover {{ background: #2c333c; }}
QPushButton {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY}; border: 1px solid #2c333c;
    border-radius: 6px; padding: 8px 16px; font-size: 12px;
    font-weight: bold; }}
QPushButton:hover {{ background: #2c333c; }}
QFrame#panel {{ background: {config.COLOR_BG_PANEL};
    border: 1px solid #2c333c; border-radius: 8px; }}
"""


# رنگ‌های آماده با نام فارسی
GROUP_COLORS = [
    ("#f39c12", "نارنجی"),
    ("#e74c3c", "قرمز"),
    ("#9b59b6", "بنفش"),
    ("#3498db", "آبی"),
    ("#1abc9c", "فیروزه‌ای"),
    ("#e67e22", "نارنجی تیره"),
    ("#2ecc71", "سبز"),
    ("#e91e63", "صورتی"),
    ("#34495e", "خاکستری"),
    ("#16a085", "سبز تیره"),
]


class GroupDialog(QDialog):
    def __init__(self, group: CameraGroup = None, parent=None):
        super().__init__(parent)
        self.is_new = group is None
        self.group = group if group else CameraGroup()
        self.cam_manager = CameraManager()
        self.nvr_manager = NVRManager.instance()

        self._selected_uids = set(self.group.camera_uids or [])

        self.setWindowTitle(
            "➕ ساخت گروه جدید" if self.is_new
            else f"✏ ویرایش گروه: {self.group.name}"
        )
        self.resize(960, 720)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()
        self._refresh_lists()

    # ============================================================
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(14)

        # ---------- Top: name + color ----------
        top = QFrame()
        top.setObjectName("panel")
        form = QFormLayout(top)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.name_edit = QLineEdit(self.group.name)
        self.name_edit.setPlaceholderText(
            "مثلاً: دوربین‌های حیاط شمالی، ورودی‌ها، پارکینگ...")
        form.addRow("نام گروه:", self.name_edit)

        # رنگ: ردیف آیکن‌های رنگی
        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        self._color_buttons = {}
        current_color = (self.group.color or "#f39c12").lower()
        for hex_color, label in GROUP_COLORS:
            btn = QPushButton()
            btn.setFixedSize(32, 32)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(label)
            btn.setProperty("hex", hex_color)
            # اگر رنگ انتخاب‌شده است، حاشیه‌ی ضخیم
            is_current = (hex_color.lower() == current_color)
            border = "3px solid white" if is_current else "1px solid #2c333c"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {hex_color};
                    border: {border};
                    border-radius: 16px;
                }}
                QPushButton:hover {{
                    border: 3px solid white;
                }}
            """)
            btn.clicked.connect(
                lambda checked=False, c=hex_color: self._set_color(c))
            color_row.addWidget(btn)
            self._color_buttons[hex_color] = btn
        color_row.addStretch(1)
        form.addRow("رنگ گروه:", color_row)

        v.addWidget(top)

        # ---------- Middle: dual panel ----------
        middle = QHBoxLayout()
        middle.setSpacing(12)

        # پنل چپ: موجود
        left = QVBoxLayout()
        left_hdr = QLabel("دوربین‌های موجود")
        left_hdr.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px; "
            f"font-weight: 700; letter-spacing: 1px;")
        left.addWidget(left_hdr)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 جستجو…")
        self.search_edit.textChanged.connect(self._refresh_lists)
        left.addWidget(self.search_edit)

        self.left_list = QListWidget()
        self.left_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.left_list.itemDoubleClicked.connect(
            lambda _: self._move_to_right())
        left.addWidget(self.left_list, 1)

        middle.addLayout(left, 1)

        # دکمه‌های وسط
        mid_btns = QVBoxLayout()
        mid_btns.addStretch(1)
        add_btn = QPushButton("▶")
        add_btn.setFixedSize(48, 48)
        add_btn.setToolTip("افزودن به گروه")
        add_btn.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_ACCENT}; color: white;
                border: none; border-radius: 24px;
                font-size: 20px; font-weight: 700; }}
            QPushButton:hover {{ background: {config.COLOR_ACCENT_HOVER}; }}
        """)
        add_btn.clicked.connect(self._move_to_right)
        mid_btns.addWidget(add_btn)

        rem_btn = QPushButton("◀")
        rem_btn.setFixedSize(48, 48)
        rem_btn.setToolTip("حذف از گروه")
        rem_btn.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_DANGER}; color: white;
                border: none; border-radius: 24px;
                font-size: 20px; font-weight: 700; }}
            QPushButton:hover {{ background: #c0392b; }}
        """)
        rem_btn.clicked.connect(self._move_to_left)
        mid_btns.addWidget(rem_btn)

        mid_btns.addStretch(1)
        middle.addLayout(mid_btns)

        # پنل راست: انتخاب‌شده
        right = QVBoxLayout()
        right_hdr = QLabel("دوربین‌های گروه")
        right_hdr.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px; "
            f"font-weight: 700; letter-spacing: 1px;")
        right.addWidget(right_hdr)

        # پلیسهولدر خالی هم‌قد search
        spacer = QLabel("")
        spacer.setFixedHeight(38)
        right.addWidget(spacer)

        self.right_list = QListWidget()
        self.right_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.right_list.itemDoubleClicked.connect(
            lambda _: self._move_to_left())
        right.addWidget(self.right_list, 1)

        middle.addLayout(right, 1)

        v.addLayout(middle, 1)

        # ---------- Bottom: count + buttons ----------
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 12px;"
        )
        v.addWidget(self.count_lbl)

        buttons = QDialogButtonBox()
        save = buttons.addButton("💾 ذخیره", QDialogButtonBox.AcceptRole)
        save.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_ACCENT}; color: white;
                padding: 10px 24px; border-radius: 6px; font-weight: bold;
                border: none; min-width: 100px; font-size: 13px; }}
            QPushButton:hover {{ background: {config.COLOR_ACCENT_HOVER}; }}
        """)
        buttons.addButton("انصراف", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    # ============================================================
    def _set_color(self, hex_color: str):
        self.group.color = hex_color
        for h, btn in self._color_buttons.items():
            is_current = (h.lower() == hex_color.lower())
            border = "3px solid white" if is_current else "1px solid #2c333c"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {h};
                    border: {border};
                    border-radius: 16px;
                }}
                QPushButton:hover {{
                    border: 3px solid white;
                }}
            """)

    def _all_cameras(self):
        """برگرداندن لیست (cam, nvr_name) برای همه‌ی دوربین‌ها."""
        result = []
        nvr_map = {n.uid: n for n in self.nvr_manager.all()}
        for c in self.cam_manager.all():
            nvr_name = ""
            if c.nvr_uid and c.nvr_uid in nvr_map:
                nvr_name = nvr_map[c.nvr_uid].name
            result.append((c, nvr_name))
        return result

    def _refresh_lists(self):
        query = (self.search_edit.text() or "").strip().lower()
        all_cams = self._all_cameras()

        self.left_list.clear()
        self.right_list.clear()

        for cam, nvr_name in all_cams:
            name = cam.name or cam.uid
            if query and query not in name.lower() \
                    and query not in nvr_name.lower():
                continue
            label = name
            if nvr_name and " دوربین " in name:
                short = "دوربین " + name.split(" دوربین ", 1)[1]
                label = f"[{nvr_name}] {short}"
            elif nvr_name:
                label = f"[{nvr_name}] {name}"

            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, cam.uid)

            if cam.uid in self._selected_uids:
                color = QColor(self.group.color or "#f39c12")
                item.setForeground(color.lighter(140))
                self.right_list.addItem(item)
            else:
                item.setForeground(QColor("#d0d8e0"))
                self.left_list.addItem(item)

        n_sel = len(self._selected_uids)
        n_total = len(all_cams)
        self.count_lbl.setText(
            f"{n_sel} از {n_total} دوربین در این گروه"
        )

    def _move_to_right(self):
        for item in self.left_list.selectedItems():
            uid = item.data(Qt.UserRole)
            if uid:
                self._selected_uids.add(uid)
        self._refresh_lists()

    def _move_to_left(self):
        for item in self.right_list.selectedItems():
            uid = item.data(Qt.UserRole)
            if uid:
                self._selected_uids.discard(uid)
        self._refresh_lists()

    # ============================================================
    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "خطا", "نام گروه را وارد کنید.")
            return
        if not self._selected_uids:
            QMessageBox.warning(self, "خطا", "حداقل یک دوربین انتخاب کنید.")
            return
        self.group.name = name
        if not self.group.color:
            self.group.color = "#f39c12"
        self.group.camera_uids = list(self._selected_uids)
        self.accept()

    def get_group(self) -> CameraGroup:
        return self.group