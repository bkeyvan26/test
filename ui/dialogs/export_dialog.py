# -*- coding: utf-8 -*-
"""K1 VMS — Export dialog (High Quality only, with robust First-Frame Preview)"""
import os
import sys
import shutil
import tempfile
import datetime
import threading
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QDateTimeEdit, QPushButton, QProgressBar,
    QFileDialog, QMessageBox, QGroupBox,
    QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QDateTime, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap, QColor, QFont

from ui import theme
import config


IS_WIN = sys.platform.startswith("win")


def _spawn_kwargs():
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def _find_ffmpeg():
    try:
        from core.ffprobe_util import get_ffmpeg_exe
        exe = get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _fmt_hms(seconds):
    s = max(0, int(seconds))
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


# ============================================================
# Robust frame extraction (3 strategies)
# ============================================================
def _extract_frame_qimage(path, offset_sec):
    """Extract one frame at offset_sec. Tries 3 strategies."""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg or not os.path.isfile(path):
        return None

    offset_sec = max(0.0, float(offset_sec))

    # Strategy list: each takes tmp path, returns cmd
    def strat_fast_seek(tmp):
        return [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{offset_sec:.3f}",
            "-i", path,
            "-frames:v", "1",
            "-q:v", "3",
            "-f", "image2",
            tmp,
        ]

    def strat_accurate_seek(tmp):
        return [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", path,
            "-ss", f"{offset_sec:.3f}",
            "-frames:v", "1",
            "-q:v", "3",
            "-f", "image2",
            tmp,
        ]

    def strat_first_frame(tmp):
        return [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", path,
            "-frames:v", "1",
            "-q:v", "3",
            "-f", "image2",
            tmp,
        ]

    strategies = [
        ("fast-seek", strat_fast_seek),
        ("accurate-seek", strat_accurate_seek),
        ("first-frame", strat_first_frame),
    ]

    for name, strat in strategies:
        tmp = tempfile.mktemp(suffix=".jpg")
        try:
            cmd = strat(tmp)
            r = subprocess.run(cmd, capture_output=True, timeout=25,
                               **_spawn_kwargs())
            if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 200:
                img = QImage(tmp)
                if not img.isNull():
                    img = img.copy()
                    return img
            # Log failure
            err = (r.stderr or b"").decode("utf-8", "ignore")[:200]
            print(f"[preview] {name} failed rc={r.returncode} err={err}")
        except Exception as e:
            print(f"[preview] {name} error: {e}")
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    return None


def _find_segment_for(segments, fs):
    """Find (segment, offset) for a given time. Never returns None if
    segments is non-empty.
    """
    if not segments:
        return None, 0.0

    # 1. Exact match
    for s in segments:
        s0 = float(s.get("start", 0))
        dur = float(s.get("duration", 0))
        s1 = s0 + dur
        if s0 <= fs < s1:
            offset = fs - s0
            # clamp: never seek to the very end
            if dur > 0.5:
                offset = min(offset, max(0.0, dur - 0.2))
            return s, max(0.0, offset)

    # 2. First segment starting at or after fs
    for s in segments:
        s0 = float(s.get("start", 0))
        if s0 >= fs:
            return s, 0.0

    # 3. Last segment
    s = segments[-1]
    return s, 0.0


# ============================================================
# Export Dialog
# ============================================================
class ExportDialog(QDialog):
    """Export dialog with first-frame preview. Fixed to High Quality."""
    sig_progress = Signal(int, str)
    sig_done = Signal(bool, str, str)
    sig_preview = Signal(int, object, object)   # generation, QImage, meta

    PREVIEW_DEBOUNCE_MS = 350

    def __init__(self, camera_name, day, start_sec, end_sec,
                 segments, parent=None):
        super().__init__(parent)
        self.camera_name = camera_name
        self.day = day
        self.start_sec = int(start_sec)
        self.end_sec = int(end_sec)
        self.segments = segments or []
        self.result_path = None

        self._preview_gen = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._do_preview_worker)

        self.setWindowTitle("📤 Export Video")
        self.setMinimumSize(820, 640)
        self.setStyleSheet(self._qss())

        self.sig_progress.connect(self._on_progress)
        self.sig_done.connect(self._on_done)
        self.sig_preview.connect(self._on_preview)

        self._build()
        self._update_info()

        QTimer.singleShot(150, self._schedule_preview)

    # ============================================================
    def _qss(self):
        return f"""
            QDialog {{ background: {theme.COLOR_BG_DARK}; }}
            QLabel {{ color: {theme.COLOR_TEXT_PRIMARY}; font-size: 12px;
                      background: transparent; }}
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
            QComboBox::drop-down {{ border: none; width: 22px; }}
            QComboBox QAbstractItemView {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                selection-background-color: {theme.COLOR_ACCENT};
                border: 1px solid {theme.COLOR_BORDER};
            }}
            QGroupBox {{
                color: {theme.COLOR_TEXT_PRIMARY};
                font-weight: bold; font-size: 11px;
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 6px;
                margin-top: 12px; padding-top: 14px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px; padding: 0 6px;
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
                min-width: 80px;
            }}
            QPushButton:hover {{ background: {theme.COLOR_BG_HOVER}; }}
            QPushButton:disabled {{ color: {theme.COLOR_TEXT_MUTED}; }}
            QProgressBar {{
                background: {theme.COLOR_BG_CARD};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 5px;
                text-align: center;
                color: {theme.COLOR_TEXT_PRIMARY};
                height: 22px;
                font-size: 11px;
                font-weight: 700;
            }}
            QProgressBar::chunk {{
                background: {theme.COLOR_ACCENT};
                border-radius: 4px;
            }}
        """

    # ============================================================
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        header = QLabel("Export Video Range")
        header.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"font-size: 16px; font-weight: 700;")
        root.addWidget(header)

        info = QLabel(
            f"📷  {self.camera_name}    •    📅  {self.day.strftime('%Y-%m-%d')}")
        info.setStyleSheet(
            f"color: {theme.COLOR_ACCENT}; font-size: 12px; font-weight: 700;")
        root.addWidget(info)

        body = QHBoxLayout()
        body.setSpacing(14)

        # LEFT preview
        left = QVBoxLayout()
        left.setSpacing(8)

        prev_title = QLabel("پیش‌نمایش (فریم اول)")
        prev_title.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 11px; font-weight: 700;")
        left.addWidget(prev_title)

        self.preview_frame = QFrame()
        self.preview_frame.setStyleSheet(f"""
            QFrame {{
                background: #000000;
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 6px;
            }}
        """)
        self.preview_frame.setMinimumSize(360, 220)
        self.preview_frame.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding)

        pv = QVBoxLayout(self.preview_frame)
        pv.setContentsMargins(0, 0, 0, 0)

        self.preview_lbl = QLabel("Loading preview…")
        self.preview_lbl.setAlignment(Qt.AlignCenter)
        self.preview_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_MUTED}; "
            f"background: #000; font-size: 12px;")
        pv.addWidget(self.preview_lbl)

        left.addWidget(self.preview_frame, 1)

        self.preview_meta_lbl = QLabel("")
        self.preview_meta_lbl.setWordWrap(True)
        self.preview_meta_lbl.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_SECONDARY};
            font-size: 10px;
            font-family: Consolas, monospace;
            background: {theme.COLOR_BG_CARD};
            padding: 6px 8px;
            border-radius: 4px;
        """)
        left.addWidget(self.preview_meta_lbl)

        # RIGHT settings
        right = QVBoxLayout()
        right.setSpacing(10)

        rg = QGroupBox("Time Range")
        rf = QFormLayout(rg)
        rf.setSpacing(8)
        rf.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.from_edit = QDateTimeEdit()
        self.from_edit.setDisplayFormat("HH:mm:ss")
        self.from_edit.setDateTime(QDateTime(
            self.day.year, self.day.month, self.day.day,
            self.start_sec // 3600,
            (self.start_sec % 3600) // 60,
            self.start_sec % 60))
        self.from_edit.dateTimeChanged.connect(self._on_time_changed)
        rf.addRow("From:", self.from_edit)

        self.to_edit = QDateTimeEdit()
        self.to_edit.setDisplayFormat("HH:mm:ss")
        self.to_edit.setDateTime(QDateTime(
            self.day.year, self.day.month, self.day.day,
            self.end_sec // 3600,
            (self.end_sec % 3600) // 60,
            self.end_sec % 60))
        self.to_edit.dateTimeChanged.connect(self._on_time_changed)
        rf.addRow("To:", self.to_edit)

        self.info_lbl = QLabel("")
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setStyleSheet(
            f"color: {theme.COLOR_ACCENT}; font-size: 11px; padding: 2px;")
        rf.addRow(self.info_lbl)

        self.gap_lbl = QLabel("")
        self.gap_lbl.setWordWrap(True)
        self.gap_lbl.setStyleSheet(
            f"color: {theme.COLOR_STATUS_WARNING}; font-size: 11px; padding: 2px;")
        rf.addRow(self.gap_lbl)

        right.addWidget(rg)

        # Quality (fixed)
        qg = QGroupBox("Export Quality")
        qv = QVBoxLayout(qg)
        qv.setSpacing(4)

        quality_lbl = QLabel("💎  High Quality")
        quality_lbl.setStyleSheet(f"""
            color: {theme.COLOR_ACCENT};
            font-size: 13px;
            font-weight: 700;
            padding: 2px 4px;
        """)
        qv.addWidget(quality_lbl)

        hint_lbl = QLabel(
            "خروجی با بالاترین کیفیت ممکن، رزولوشن اصلی ضبط حفظ می‌شود.")
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 10px; padding: 2px 4px;")
        qv.addWidget(hint_lbl)

        right.addWidget(qg)

        og = QGroupBox("Output")
        of = QFormLayout(og)
        of.setSpacing(8)
        of.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(self._default_folder())
        self.folder_edit.setReadOnly(True)
        browse = QPushButton("📁")
        browse.setFixedWidth(38)
        browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        of.addRow("Folder:", folder_row)

        name = (f"{self.camera_name}_"
                f"{self.day.strftime('%Y%m%d')}_"
                f"{datetime.datetime.now().strftime('%H%M%S')}")
        self.name_edit = QLineEdit(name)
        of.addRow("Name:", self.name_edit)

        self.fmt_combo = QComboBox()
        for label, ext in [("MP4 (H.264/H.265)", "mp4"),
                           ("TS  (MPEG-TS)", "ts"),
                           ("AVI (H.264)", "avi")]:
            self.fmt_combo.addItem(label, ext)
        of.addRow("Format:", self.fmt_combo)

        right.addWidget(og)
        right.addStretch(1)

        body.addLayout(left, 3)
        body.addLayout(right, 4)
        root.addLayout(body, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        root.addWidget(self.progress)

        self.status_lbl = QLabel("آماده")
        self.status_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;")
        root.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.cancel_btn = QPushButton("انصراف")
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.export_btn = QPushButton("📤  شروع")
        self.export_btn.setStyleSheet(f"""
            QPushButton {{
                background: {theme.COLOR_ACCENT};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 24px;
                font-weight: 700;
                font-size: 12px;
                min-width: 120px;
            }}
            QPushButton:hover {{ background: {theme.COLOR_ACCENT_HOVER}; }}
            QPushButton:disabled {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_MUTED};
            }}
        """)
        self.export_btn.clicked.connect(self._do_export)
        btn_row.addWidget(self.export_btn)

        root.addLayout(btn_row)

    # ============================================================
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

    def _current_start_sec(self):
        t = self.from_edit.dateTime().toPython()
        return t.hour * 3600 + t.minute * 60 + t.second

    def _current_end_sec(self):
        t = self.to_edit.dateTime().toPython()
        return t.hour * 3600 + t.minute * 60 + t.second

    # ============================================================
    # Preview
    # ============================================================
    def _schedule_preview(self):
        self._preview_gen += 1
        self._preview_timer.start(self.PREVIEW_DEBOUNCE_MS)

    def _do_preview_worker(self):
        gen = self._preview_gen
        fs = self._current_start_sec()
        segments = list(self.segments)

        def worker():
            seg, offset = _find_segment_for(segments, fs)

            if seg is None:
                self.sig_preview.emit(gen, None, {"reason": "no segment"})
                return

            img = _extract_frame_qimage(seg["path"], offset)

            meta = {}
            try:
                from core.ffprobe_util import probe_media
                meta = probe_media(seg["path"]) or {}
            except Exception as e:
                print(f"[preview] probe error: {e}")

            self.sig_preview.emit(gen, img, meta)

        threading.Thread(target=worker, daemon=True).start()

    def _on_preview(self, gen, img, meta):
        if gen != self._preview_gen:
            return

        if img is None or img.isNull():
            self.preview_lbl.setText("— No preview —")
            self.preview_lbl.setPixmap(QPixmap())
            self.preview_meta_lbl.setText("")
            return

        target_w = max(200, self.preview_frame.width() - 4)
        target_h = max(120, self.preview_frame.height() - 4)

        scaled = img.scaled(target_w, target_h,
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation)
        self.preview_lbl.setPixmap(QPixmap.fromImage(scaled))

        parts = []
        codec = meta.get("codec")
        if codec:
            parts.append(codec.upper())
        w = meta.get("width")
        h = meta.get("height")
        if w and h:
            parts.append(f"{w}×{h}")
        fps = meta.get("fps")
        if fps:
            try:
                parts.append(f"{float(fps):.0f} fps")
            except Exception:
                pass
        dur = meta.get("duration")
        if dur:
            try:
                parts.append(f"{float(dur):.1f}s file")
            except Exception:
                pass

        self.preview_meta_lbl.setText("  •  ".join(parts) if parts else "—")

    # ============================================================
    def _on_time_changed(self):
        self._update_info()
        self._schedule_preview()

    # ============================================================
    def _update_info(self):
        try:
            fs = self._current_start_sec()
            ts = self._current_end_sec()
            dur = ts - fs
            if dur <= 0:
                self.info_lbl.setText("⚠ پایان باید بعد از شروع باشد")
                self.info_lbl.setStyleSheet(
                    f"color: {theme.COLOR_STATUS_ERROR}; "
                    f"font-size: 11px; padding: 2px;")
                self.gap_lbl.setText("")
                self.export_btn.setEnabled(False)
                return

            count = 0
            for seg in self.segments:
                seg_start = float(seg.get("start", 0))
                seg_end = seg_start + float(seg.get("duration", 0))
                if seg_end > fs and seg_start < ts:
                    count += 1

            self.info_lbl.setText(
                f"⏱  {_fmt_hms(dur)}   •   📦  {count} segments")
            self.info_lbl.setStyleSheet(
                f"color: {theme.COLOR_ACCENT}; "
                f"font-size: 11px; padding: 2px;")

            try:
                from core.export_engine import scan_gaps
                gaps = scan_gaps(self.segments, fs, ts)
                if gaps:
                    total_gap = sum(g["duration"] for g in gaps)
                    self.gap_lbl.setText(
                        f"⚠  {len(gaps)} گپ ({_fmt_hms(total_gap)}) — "
                        f"گپ‌ها حذف می‌شوند")
                else:
                    self.gap_lbl.setText("")
            except Exception:
                self.gap_lbl.setText("")

            self.export_btn.setEnabled(count > 0)
        except Exception as e:
            print(f"[preview] update_info error: {e}")

    # ============================================================
    def _do_export(self):
        folder = Path(self.folder_edit.text())
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Please enter a filename.")
            return
        name = "".join(ch for ch in name if ch.isalnum() or ch in "-_ .")[:80]
        ext = self.fmt_combo.currentData() or "mp4"
        out_path = str(folder / f"{name}.{ext}")

        fs = self._current_start_sec()
        ts = self._current_end_sec()
        if ts <= fs:
            QMessageBox.warning(self, "Error", "End must be after start.")
            return

        mode = "high_quality"

        self.export_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress.setValue(0)
        self.status_lbl.setText("آماده‌سازی…")
        self.status_lbl.setStyleSheet(
            f"color: {theme.COLOR_ACCENT}; font-size: 11px;")

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
                ok, msg = export_range(
                    files_with_start, out_path, fs, ts, mode=mode,
                    progress_cb=lambda p, m="": self.sig_progress.emit(int(p), str(m))
                )
            except Exception as e:
                import traceback
                traceback.print_exc()
                ok, msg = False, str(e)
            self.sig_done.emit(ok, msg, out_path)

        threading.Thread(target=worker, daemon=True).start()

    # ============================================================
    def _on_progress(self, pct, msg):
        try:
            self.progress.setValue(max(0, min(100, int(pct))))
            if msg:
                self.status_lbl.setText(msg)
        except Exception:
            pass

    def _on_done(self, ok, msg, out_path):
        self.cancel_btn.setEnabled(True)
        self.export_btn.setEnabled(True)

        if ok:
            self.progress.setValue(100)
            try:
                size_mb = os.path.getsize(out_path) / 1024 / 1024
            except Exception:
                size_mb = 0

            self.status_lbl.setText(f"✅ ذخیره شد: {Path(out_path).name}")
            self.status_lbl.setStyleSheet(
                f"color: {theme.COLOR_STATUS_ONLINE}; "
                f"font-size: 11px; font-weight: 700;")
            self.result_path = out_path

            QMessageBox.information(
                self, "✅ Export Complete",
                f"فایل با موفقیت ذخیره شد:\n\n{out_path}\n\n"
                f"حجم: {size_mb:.2f} MB\n"
                f"کیفیت: رزولوشن اصلی (CRF 18)"
            )
            self.accept()
        else:
            self.progress.setValue(0)
            self.status_lbl.setText(f"❌ خطا: {msg}")
            self.status_lbl.setStyleSheet(
                f"color: {theme.COLOR_STATUS_ERROR}; "
                f"font-size: 11px; font-weight: 700;")
            QMessageBox.warning(
                self, "❌ Export Failed",
                f"خطا در خروجی:\n\n{msg}"
            )