# -*- coding: utf-8 -*-
"""K1 VMS — NVR Dialog (Phase 8.1: record_folder_name field)"""
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QSpinBox,
    QComboBox, QPushButton, QDialogButtonBox, QLabel, QGroupBox,
    QListWidget, QListWidgetItem, QProgressDialog, QMessageBox,
    QAbstractItemView, QCheckBox
)
from PySide6.QtCore import Qt, QThread

from core.models import NVRDevice, NVRChannel
from core.nvr_discovery import NVRDiscoveryWorker
import config


DIALOG_STYLE = f"""
QDialog {{ background-color: {config.COLOR_BG_DARK}; }}
QLabel {{ color: {config.COLOR_TEXT_PRIMARY}; background: transparent;
         font-size: 12px; }}
QLineEdit, QSpinBox, QComboBox {{
    background: {config.COLOR_BG_CARD}; color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c; border-radius: 5px;
    padding: 6px 8px; font-size: 12px; min-height: 20px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {config.COLOR_ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {config.COLOR_BG_CARD}; color: {config.COLOR_TEXT_PRIMARY};
    selection-background-color: {config.COLOR_ACCENT};
    border: 1px solid #2c333c; outline: none; }}
QCheckBox {{ color: {config.COLOR_TEXT_PRIMARY}; font-size: 12px;
             spacing: 8px; }}
QGroupBox {{ color: {config.COLOR_TEXT_PRIMARY}; font-weight: bold;
    font-size: 12px; border: 1px solid #2c333c; border-radius: 6px;
    margin-top: 12px; padding-top: 14px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
    color: {config.COLOR_ACCENT}; }}
QListWidget {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c; border-radius: 5px;
    padding: 4px; font-size: 12px; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 4px; }}
QListWidget::item:hover {{ background: #2c333c; }}
QListWidget::item:selected {{ background: {config.COLOR_ACCENT};
    color: white; }}
QDialogButtonBox QPushButton {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY}; border: 1px solid #2c333c;
    border-radius: 5px; padding: 8px 20px; font-size: 12px;
    font-weight: bold; min-width: 80px; }}
QDialogButtonBox QPushButton:hover {{ background: #2c333c; }}
"""


def _sanitize_folder_name(text: str) -> str:
    """حذف کاراکترهای غیرمجاز — فقط A-Z a-z 0-9 _ -"""
    if not text:
        return ""
    return re.sub(r"[^a-zA-Z0-9_\-]", "", text).strip("_-")[:40]


class NVRDialog(QDialog):
    def __init__(self, nvr: NVRDevice = None, parent=None):
        super().__init__(parent)
        self.is_new = nvr is None
        self.nvr = nvr if nvr else NVRDevice()

        self._detect_thread = None
        self._detect_worker = None
        self._detect_progress = None

        self.setWindowTitle(
            "➕ افزودن NVR" if self.is_new
            else f"✏ ویرایش NVR: {self.nvr.name}"
        )
        self.resize(820, 900)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()
        self._populate_channels()

    # ============================================================
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        # ---------- Connection ----------
        grp = QGroupBox("🔌 اتصال به NVR")
        form = QFormLayout(grp)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.name_edit = QLineEdit(self.nvr.name)
        self.name_edit.setPlaceholderText("مثلاً: NVR طبقه اول")
        form.addRow("نام NVR:", self.name_edit)

        # ★ فیلد نام پوشه ضبط
        self.folder_edit = QLineEdit(
            getattr(self.nvr, "record_folder_name", "") or "")
        self.folder_edit.setPlaceholderText(
            "مثلاً: negahbani یا dakheli (حروف انگلیسی، اعداد، _ و -)")
        self.folder_edit.setMaxLength(40)
        self.folder_edit.textChanged.connect(self._on_folder_changed)
        form.addRow("نام پوشه ضبط:", self.folder_edit)

        self.folder_hint = QLabel(
            "💡 نامی که پوشه‌ی ضبط NVR و کانال‌هایش با آن ساخته می‌شود.\n"
            "   فقط حروف انگلیسی، اعداد، _ و - مجاز است.\n"
            "   مثال: negahbani → continuous/negahbani/ch_01/2026-10-10/"
        )
        self.folder_hint.setWordWrap(True)
        self.folder_hint.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 10px; "
            f"background: {config.COLOR_BG_CARD}; padding: 8px; "
            f"border-radius: 4px; border-left: 3px solid {config.COLOR_ACCENT};"
        )
        form.addRow("", self.folder_hint)

        self.brand_combo = QComboBox()
        for k, label in [
            ("auto", "🔍 تشخیص خودکار"),
            ("hikvision", "Hikvision"),
            ("dahua", "Dahua"),
            ("uniview", "Uniview"),
            ("axis", "Axis"),
            ("amcrest", "Amcrest"),
            ("kdt", "KDT"),
        ]:
            self.brand_combo.addItem(label, k)
        idx = self.brand_combo.findData(self.nvr.brand or "auto")
        if idx >= 0:
            self.brand_combo.setCurrentIndex(idx)
        form.addRow("برند:", self.brand_combo)

        self.ip_edit = QLineEdit(self.nvr.ip)
        self.ip_edit.setPlaceholderText("192.168.1.100")
        form.addRow("آدرس IP:", self.ip_edit)

        port_row = QHBoxLayout()
        self.rtsp_port_spin = QSpinBox()
        self.rtsp_port_spin.setRange(1, 65535)
        self.rtsp_port_spin.setValue(self.nvr.rtsp_port or 554)
        port_row.addWidget(QLabel("RTSP:"))
        port_row.addWidget(self.rtsp_port_spin)
        self.onvif_port_spin = QSpinBox()
        self.onvif_port_spin.setRange(1, 65535)
        self.onvif_port_spin.setValue(self.nvr.onvif_port or 80)
        port_row.addWidget(QLabel("ONVIF:"))
        port_row.addWidget(self.onvif_port_spin)
        port_row.addStretch(1)
        form.addRow("پورت:", port_row)

        self.user_edit = QLineEdit(self.nvr.user)
        form.addRow("نام کاربری:", self.user_edit)

        self.pass_edit = QLineEdit(self.nvr.password)
        self.pass_edit.setEchoMode(QLineEdit.Password)
        form.addRow("رمز عبور:", self.pass_edit)

        v.addWidget(grp)

        # ---------- Manual RTSP pattern ----------
        grp_manual = QGroupBox("🔧 الگوی RTSP دستی (برای NVRهای بدون ONVIF)")
        mv = QFormLayout(grp_manual)
        mv.setSpacing(8)

        self.custom_main_edit = QLineEdit()
        self.custom_main_edit.setPlaceholderText(
            "مثال KDT: rtsp://{user}:{pwd}@{ip}:{port}/mode=real&idc={ch}&ids=1"
        )
        mv.addRow("Main stream:", self.custom_main_edit)

        self.custom_sub_edit = QLineEdit()
        self.custom_sub_edit.setPlaceholderText(
            "مثال: rtsp://{user}:{pwd}@{ip}:{port}/mode=real&idc={ch}&ids=2"
        )
        mv.addRow("Sub stream:", self.custom_sub_edit)

        hint_m = QLabel(
            "💡 <b>متغیرها:</b> {user} {pwd} {ip} {port} {ch} {stream}\n"
            "اگر پر شود، مستقیم از این الگو استفاده می‌شود."
        )
        hint_m.setWordWrap(True)
        hint_m.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 10px;"
        )
        mv.addRow(hint_m)
        v.addWidget(grp_manual)

        # ---------- Discover button ----------
        self.discover_btn = QPushButton("🔍  تشخیص خودکار کانال‌ها")
        self.discover_btn.setMinimumHeight(42)
        self.discover_btn.setCursor(Qt.PointingHandCursor)
        self.discover_btn.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_ACCENT}; color: white;
                border: none; border-radius: 6px; font-size: 13px;
                font-weight: bold; padding: 8px 16px; }}
            QPushButton:hover {{ background: {config.COLOR_ACCENT_HOVER}; }}
            QPushButton:disabled {{ background: #2c333c; color: #6b7280; }}
        """)
        self.discover_btn.clicked.connect(self._on_discover)
        v.addWidget(self.discover_btn)

        # ---------- Channel list ----------
        info = QLabel("📡 کانال‌های کشف‌شده — موارد دلخواه را تیک بزنید:")
        info.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px;"
        )
        v.addWidget(info)

        self.channels_list = QListWidget()
        self.channels_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.channels_list.itemChanged.connect(lambda _: self._update_count())
        v.addWidget(self.channels_list, 1)

        sel_row = QHBoxLayout()
        sel_all = QPushButton("✔ انتخاب همه")
        sel_all.clicked.connect(lambda: self._set_all(True))
        sel_row.addWidget(sel_all)
        desel_all = QPushButton("✘ حذف همه")
        desel_all.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(desel_all)
        sel_row.addStretch(1)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px;"
        )
        sel_row.addWidget(self.count_lbl)
        v.addLayout(sel_row)

        # ---------- Save ----------
        buttons = QDialogButtonBox()
        save = buttons.addButton("💾 ذخیره", QDialogButtonBox.AcceptRole)
        save.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_ACCENT}; color: white;
                padding: 8px 20px; border-radius: 5px; font-weight: bold;
                border: none; min-width: 80px; }}
            QPushButton:hover {{ background: {config.COLOR_ACCENT_HOVER}; }}
        """)
        buttons.addButton("انصراف", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    # ============================================================
    def _on_folder_changed(self, text):
        """پاکسازی خودکار + نمایش پیش‌نمایش."""
        clean = _sanitize_folder_name(text)
        if clean != text:
            cur_pos = self.folder_edit.cursorPosition()
            self.folder_edit.blockSignals(True)
            self.folder_edit.setText(clean)
            self.folder_edit.setCursorPosition(min(cur_pos, len(clean)))
            self.folder_edit.blockSignals(False)

        if clean:
            preview = (f"📁 پوشه: continuous/{clean}/ch_NN/2026-10-10/"
                       f"13-14-47.ts")
        else:
            preview = ("📁 پوشه: continuous/nvr_{{uid}}/ch_NN/2026-10-10/  "
                       "(اگر خالی بماند، از uid خودکار استفاده می‌شود)")
        self.folder_hint.setText(
            "💡 نامی که پوشه‌ی ضبط NVR و کانال‌هایش با آن ساخته می‌شود.\n"
            "   فقط حروف انگلیسی، اعداد، _ و - مجاز است.\n"
            f"   {preview}"
        )

    def _populate_channels(self):
        self.channels_list.blockSignals(True)
        self.channels_list.clear()
        for ch in self.nvr.channels:
            self._add_channel_item(ch)
        self.channels_list.blockSignals(False)
        self._update_count()

    def _add_channel_item(self, ch: NVRChannel):
        res = ch.resolution or "—"
        codec = ch.codec or "—"
        has_sub = bool(ch.rtsp_url_sub)
        tag = " [M+S]" if has_sub else " [M]"
        text = f"  CH {ch.channel_id:>2}   {ch.name}   —   {res}  {codec}{tag}"
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, ch.to_dict())
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if ch.enabled else Qt.Unchecked)
        self.channels_list.addItem(item)

    def _set_all(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        self.channels_list.blockSignals(True)
        for i in range(self.channels_list.count()):
            self.channels_list.item(i).setCheckState(state)
        self.channels_list.blockSignals(False)
        self._update_count()

    def _update_count(self):
        total = self.channels_list.count()
        selected = sum(
            1 for i in range(total)
            if self.channels_list.item(i).checkState() == Qt.Checked
        )
        self.count_lbl.setText(f"{selected} از {total} انتخاب شده")

    # ============================================================
    def _on_save(self):
        name = self.name_edit.text().strip()
        ip = self.ip_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "خطا", "نام NVR را وارد کنید.")
            return
        if not ip:
            QMessageBox.warning(self, "خطا", "آدرس IP را وارد کنید.")
            return

        folder = _sanitize_folder_name(self.folder_edit.text())

        self.nvr.name = name
        self.nvr.record_folder_name = folder
        self.nvr.brand = self.brand_combo.currentData() or "auto"
        self.nvr.ip = ip
        self.nvr.rtsp_port = self.rtsp_port_spin.value()
        self.nvr.onvif_port = self.onvif_port_spin.value()
        self.nvr.user = self.user_edit.text().strip()
        self.nvr.password = self.pass_edit.text()

        selected = []
        for i in range(self.channels_list.count()):
            item = self.channels_list.item(i)
            d = item.data(Qt.UserRole) or {}
            d["enabled"] = (item.checkState() == Qt.Checked)
            selected.append(NVRChannel.from_dict(d))
        self.nvr.channels = selected
        self.accept()

    def get_nvr(self) -> NVRDevice:
        return self.nvr

    # ============================================================
    # Discovery
    # ============================================================
    def _on_discover(self):
        if self._detect_worker is not None:
            QMessageBox.information(
                self, "در حال اجرا",
                "تشخیص خودکار در حال اجراست."
            )
            return

        ip = self.ip_edit.text().strip()
        user = self.user_edit.text().strip()
        password = self.pass_edit.text()
        if not ip or not user:
            QMessageBox.warning(self, "خطا", "IP و نام کاربری الزامی است.")
            return

        custom_main = self.custom_main_edit.text().strip()
        custom_sub = self.custom_sub_edit.text().strip()

        temp = NVRDevice()
        temp.ip = ip
        temp.user = user
        temp.password = password
        temp.rtsp_port = self.rtsp_port_spin.value()
        temp.onvif_port = self.onvif_port_spin.value()
        temp.brand = self.brand_combo.currentData() or "auto"

        prog = QProgressDialog("در حال کشف…", "لغو", 0, 100, self)
        prog.setWindowTitle("تشخیص خودکار NVR")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        prog.setStyleSheet(DIALOG_STYLE)
        prog.show()

        thread = QThread(self)
        worker = NVRDiscoveryWorker(temp,
                                    custom_main=custom_main,
                                    custom_sub=custom_sub)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(lambda m: print(m))
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_ok)
        worker.finished_fail.connect(self._on_fail)
        worker.cancelled.connect(self._on_cancelled)
        prog.canceled.connect(worker.cancel)

        self._detect_thread = thread
        self._detect_worker = worker
        self._detect_progress = prog
        self.discover_btn.setEnabled(False)
        thread.start()

    def _on_progress(self, cur, total, msg):
        p = self._detect_progress
        if not p:
            return
        try:
            p.setMaximum(max(total, 1))
            p.setValue(cur)
            if msg:
                p.setLabelText(msg)
        except Exception:
            pass

    def _on_ok(self, payload):
        self._cleanup()
        self.channels_list.blockSignals(True)
        self.channels_list.clear()
        for d in (payload or []):
            ch = NVRChannel.from_dict(d)
            ch.enabled = True
            self._add_channel_item(ch)
        self.channels_list.blockSignals(False)
        self._update_count()
        n = len(payload or [])
        QMessageBox.information(
            self, "✅ موفق",
            f"{n} کانال کشف شد.\nموارد دلخواه را تیک بزنید و ذخیره کنید."
        )

    def _on_fail(self, msg):
        self._cleanup()
        QMessageBox.warning(self, "❌ کشف ناموفق", str(msg))

    def _on_cancelled(self):
        self._cleanup()

    def _cleanup(self):
        prog = self._detect_progress
        thread = self._detect_thread
        worker = self._detect_worker
        self._detect_progress = None
        self._detect_thread = None
        self._detect_worker = None

        if prog:
            try: prog.reset()
            except Exception: pass
            try: prog.close()
            except Exception: pass
            try: prog.deleteLater()
            except Exception: pass
        if worker:
            for sig in ("log", "progress", "finished_ok",
                        "finished_fail", "cancelled"):
                try: getattr(worker, sig).disconnect()
                except Exception: pass
        if thread:
            try:
                thread.quit()
                if not thread.wait(3000):
                    thread.terminate()
                    thread.wait(500)
            except Exception: pass
            try: thread.deleteLater()
            except Exception: pass
        try: self.discover_btn.setEnabled(True)
        except Exception: pass

    def closeEvent(self, event):
        if self._detect_worker is not None:
            try: self._detect_worker.cancel()
            except Exception: pass
            self._cleanup()
        super().closeEvent(event)