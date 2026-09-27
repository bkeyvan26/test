# -*- coding: utf-8 -*-
"""K1 VMS — Export dialog (Genetec-style)"""
import datetime
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QDateTimeEdit, QPushButton, QProgressBar,
    QFileDialog, QMessageBox, QGroupBox
)
from PySide6.QtCore import Qt, QDateTime, QTimer

from ui import theme
import config


class ExportDialog(QDialog):
    """Export a time range to MP4/MKV/AVI."""

    def __init__(self, camera_name, day, start_sec, end_sec,
                 segments, parent=None):
        super().__init__(parent)
        self.camera_name = camera_name
        self.day = day
        self.start_sec = int(start_sec)
        self.end_sec = int(end_sec)
        self.segments = segments or []

        self.setWindowTitle("📤 Export Video")
        self.setMinimumWidth(520)
        self.setStyleSheet(self._qss())
        self._build()
        self._update_info()

    # ------------------------------------------------------------
    def _qss(self):
        return f"""
            QDialog {{
                background: {theme.COLOR_BG_DARK};
            }}
            QLabel {{
                color: {theme.COLOR_TEXT_PRIMARY};
                font-size: 12px;
                background: transparent;
            }}
            QLineEdit, QComboBox, QDateTimeEdit {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 5px;
                padding: 6px 8px;
                font-size: 12px;
                min-height: 20px;
            }}
            QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus {{
                border-color: {theme.COLOR_ACCENT};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 22px;
            }}
            QComboBox QAbstractItemView {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                selection-background-color: {theme.COLOR_ACCENT};
                border: 1px solid {theme.COLOR_BORDER};
            }}
            QGroupBox {{
                color: {theme.COLOR_TEXT_PRIMARY};
                font-weight: bold;
                font-size: 11px;
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 14px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {theme.COLOR_ACCENT};
            }}
            QPushButton {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 5px;
                padding: 8px 20px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_BG_HOVER};
            }}
            QProgressBar {{
                background: {theme.COLOR_BG_CARD};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 5px;
                text-align: center;
                color: {theme.COLOR_TEXT_PRIMARY};
                height: 18px;
            }}
            QProgressBar::chunk {{
                background: {theme.COLOR_ACCENT};
                border-radius: 4px;
            }}
        """

    # ------------------------------------------------------------
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        title = QLabel("Export Video Range")
        title.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 16px; font-weight: 700;")
        v.addWidget(title)

        info = QLabel(
            f"📷  {self.camera_name}    •    📅  {self.day.strftime('%Y-%m-%d')}")
        info.setStyleSheet(
            f"color: {theme.COLOR_ACCENT}; font-size: 12px; font-weight: 700;")
        v.addWidget(info)

        # ===== Range group =====
        rg = QGroupBox("Time Range")
        rf = QFormLayout(rg)
        rf.setSpacing(8)

        self.from_edit = QDateTimeEdit()
        self.from_edit.setDisplayFormat("HH:mm:ss")
        self.from_edit.setDateTime(QDateTime(
            self.day.year, self.day.month, self.day.day,
            self.start_sec // 3600,
            (self.start_sec % 3600) // 60,
            self.start_sec % 60))
        self.from_edit.dateTimeChanged.connect(self._update_info)
        rf.addRow("From:", self.from_edit)

        self.to_edit = QDateTimeEdit()
        self.to_edit.setDisplayFormat("HH:mm:ss")
        self.to_edit.setDateTime(QDateTime(
            self.day.year, self.day.month, self.day.day,
            self.end_sec // 3600,
            (self.end_sec % 3600) // 60,
            self.end_sec % 60))
        self.to_edit.dateTimeChanged.connect(self._update_info)
        rf.addRow("To:", self.to_edit)

        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px; padding: 4px;")
        rf.addRow(self.info_lbl)
        v.addWidget(rg)

        # ===== Output group =====
        og = QGroupBox("Output")
        of = QFormLayout(og)
        of.setSpacing(8)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(self._default_folder())
        self.folder_edit.setReadOnly(True)
        browse_btn = QPushButton("📁")
        browse_btn.setFixedWidth(40)
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse_btn)
        of.addRow("Folder:", folder_row)

        default_name = (f"{self.camera_name}_"
                        f"{self.day.strftime('%Y%m%d')}_"
                        f"{datetime.datetime.now().strftime('%H%M%S')}")
        self.name_edit = QLineEdit(default_name)
        of.addRow("Filename:", self.name_edit)

        self.format_combo = QComboBox()
        for label, ext in [("MP4 (H.264)", "mp4"),
                           ("MKV (H.264)", "mkv"),
                           ("AVI (H.264)", "avi")]:
            self.format_combo.addItem(label, ext)
        of.addRow("Format:", self.format_combo)

        v.addWidget(og)

        # ===== Progress =====
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        # ===== Buttons =====
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.export_btn = QPushButton("📤  Export")
        self.export_btn.setStyleSheet(f"""
            QPushButton {{
                background: {theme.COLOR_ACCENT};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 24px;
                font-weight: 700;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_ACCENT_HOVER};
            }}
        """)
        self.export_btn.clicked.connect(self._do_export)
        btn_row.addWidget(self.export_btn)

        v.addLayout(btn_row)

    # ------------------------------------------------------------
    def _default_folder(self):
        folder = config.RECORDINGS_DIR / "exports"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return str(folder)

    def _browse_folder(self):
        p = QFileDialog.getExistingDirectory(
            self, "Select export folder", self.folder_edit.text())
        if p:
            self.folder_edit.setText(p)

    def _update_info(self):
        try:
            t1 = self.from_edit.dateTime().toPython()
            t2 = self.to_edit.dateTime().toPython()
            dur = (t2 - t1).total_seconds()
            if dur <= 0:
                self.info_lbl.setText("⚠ End time must be after start time")
                self.info_lbl.setStyleSheet(
                    f"color: {theme.COLOR_STATUS_ERROR}; font-size: 11px;")
                self.export_btn.setEnabled(False)
                return
            h = int(dur) // 3600
            m = (int(dur) % 3600) // 60
            s = int(dur) % 60
            fs = t1.hour * 3600 + t1.minute * 60 + t1.second
            ts = t2.hour * 3600 + t2.minute * 60 + t2.second
            count = 0
            for seg in self.segments:
                seg_start = seg.get("start", 0)
                seg_end = seg_start + seg.get("duration", 0)
                if seg_end > fs and seg_start < ts:
                    count += 1
            self.info_lbl.setText(
                f"⏱  {h:02d}:{m:02d}:{s:02d}   •   📦  {count} segments")
            self.info_lbl.setStyleSheet(
                f"color: {theme.COLOR_ACCENT}; font-size: 11px; padding: 4px;")
            self.export_btn.setEnabled(count > 0)
        except Exception:
            pass

    def _do_export(self):
        folder = Path(self.folder_edit.text())
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Please enter a filename.")
            return
        name = "".join(ch for ch in name if ch.isalnum() or ch in "-_ .")[:80]
        ext = self.format_combo.currentData() or "mp4"
        out_path = folder / f"{name}.{ext}"

        t1 = self.from_edit.dateTime().toPython()
        t2 = self.to_edit.dateTime().toPython()
        fs = t1.hour * 3600 + t1.minute * 60 + t1.second
        ts = t2.hour * 3600 + t2.minute * 60 + t2.second
        if ts <= fs:
            QMessageBox.warning(self, "Error", "End time must be after start time.")
            return

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.export_btn.setEnabled(False)

        files_with_start = []
        for seg in self.segments:
            files_with_start.append({
                "path": seg["path"],
                "start_sec": seg["start"],
                "duration": seg["duration"],
            })

        def worker():
            try:
                from core.export_engine import export_range
                ok, msg = export_range(files_with_start, str(out_path), fs, ts,
                                       progress_cb=self._safe_progress)
            except Exception as e:
                ok, msg = False, str(e)
            QTimer.singleShot(0, lambda: self._on_done(ok, msg, out_path))

        threading.Thread(target=worker, daemon=True).start()

    def _safe_progress(self, pct):
        try:
            QTimer.singleShot(0, lambda p=int(pct): self.progress.setValue(p))
        except Exception:
            pass

    def _on_done(self, ok, msg, out_path):
        self.progress.setVisible(False)
        self.export_btn.setEnabled(True)
        if ok:
            QMessageBox.information(
                self, "Export Complete",
                f"✅ Saved:\n{out_path}")
            self.accept()
        else:
            QMessageBox.warning(
                self, "Export Failed",
                f"❌ {msg}")