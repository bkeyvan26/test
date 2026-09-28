# -*- coding: utf-8 -*-
"""K1 VMS — Playback page (Phase C: seek debounce + gap handling)"""
import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QWidget, QSlider
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ui import theme
from ui.icons import make_icon
from ui.pages import BasePage
from ui.dialogs import info as dlg_info, warning as dlg_warning
from ui.dialogs.export_dialog import ExportDialog
from ui.widgets.timeline import TimelineWidget
from ui.widgets.video_display import VideoDisplay
from ui.widgets.persian_calendar import (
    PersianCalendar, gregorian_to_jalali, PERSIAN_MONTHS
)
from core.camera_manager import CameraManager
from core.playback_engine import PlaybackEngine
from core.nvr_engine import _camera_path_name
from core.recording_indexer import RecordingIndexer, STATE_WRITING
from core import settings_manager
import config


def _fmt_hms(seconds):
    s = max(0, int(seconds))
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


class PlaybackPage(BasePage):
    PAGE_KEY = "playback"
    PAGE_TITLE = "Playback"
    PANEL_TITLE = "تشخیص حرکت"

    def __init__(self, nvr_engine=None, parent=None):
        super().__init__(parent)
        self.cam_manager = CameraManager()
        self.nvr = nvr_engine
        self._current_cam_uid = None
        self.indexer = RecordingIndexer.instance(nvr_engine)

        self._cut_in = None
        self._cut_out = None

        self.engine = PlaybackEngine(self)
        self.engine.frame_ready.connect(self._on_frame)
        self.engine.position_changed.connect(self._on_position)
        self.engine.state_changed.connect(self._on_state)

        self._build()
        self._load_cameras()

        if self.cam_list.count() > 0:
            self.cam_list.setCurrentRow(0)

    # ============================================================
    # UI Construction
    # ============================================================
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_left_panel())
        body.addWidget(self._build_center_panel(), 1)
        body.addWidget(self._build_right_panel())
        root.addLayout(body, 1)

        root.addWidget(self._build_bottom())

    # ------------------------------------------------------------
    def _build_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f"""
            QFrame {{
                background: {theme.COLOR_BG_HEADER};
                border-bottom: 1px solid {theme.COLOR_BORDER};
            }}
        """)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)

        title = QLabel("🎬  Playback")
        title.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"font-size: 14px; font-weight: 700;")
        h.addWidget(title)
        h.addSpacing(20)

        self.prev_day_btn = QPushButton("‹")
        self.prev_day_btn.setFixedSize(28, 26)
        self.prev_day_btn.setCursor(Qt.PointingHandCursor)
        self.prev_day_btn.clicked.connect(lambda: self._shift_day(-1))
        self.prev_day_btn.setStyleSheet(self._nav_qss())
        h.addWidget(self.prev_day_btn)

        self.date_lbl = QLabel("—")
        self.date_lbl.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_PRIMARY};
            font-size: 12px; font-weight: 700;
            background: {theme.COLOR_BG_CARD};
            border-radius: 5px; padding: 4px 14px;
        """)
        h.addWidget(self.date_lbl)

        self.next_day_btn = QPushButton("›")
        self.next_day_btn.setFixedSize(28, 26)
        self.next_day_btn.setCursor(Qt.PointingHandCursor)
        self.next_day_btn.clicked.connect(lambda: self._shift_day(1))
        self.next_day_btn.setStyleSheet(self._nav_qss())
        h.addWidget(self.next_day_btn)

        h.addSpacing(6)

        self.today_btn = QPushButton("Today")
        self.today_btn.setFixedHeight(26)
        self.today_btn.setCursor(Qt.PointingHandCursor)
        self.today_btn.clicked.connect(self._go_today)
        self.today_btn.setStyleSheet(f"""
            QPushButton {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 5px; padding: 0 12px;
                font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {theme.COLOR_BG_HOVER}; }}
        """)
        h.addWidget(self.today_btn)

        h.addStretch(1)

        self.header_time_lbl = QLabel("--:--:-- / --:--:--")
        self.header_time_lbl.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_PRIMARY};
            font-family: Consolas, monospace;
            font-size: 12px; font-weight: 700;
            background: {theme.COLOR_BG_CARD};
            padding: 4px 12px; border-radius: 5px;
        """)
        h.addWidget(self.header_time_lbl)
        return bar

    def _build_left_panel(self):
        panel = QFrame()
        panel.setFixedWidth(180)
        panel.setStyleSheet(f"""
            QFrame {{
                background: {theme.COLOR_BG_PANEL};
                border-right: 1px solid {theme.COLOR_BORDER};
            }}
        """)
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        title = QLabel("CAMERAS")
        title.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_SECONDARY};
            font-size: 10px; font-weight: 700;
            letter-spacing: 1.5px; padding: 4px;
        """)
        v.addWidget(title)

        self.cam_list = QListWidget()
        self.cam_list.setStyleSheet(f"""
            QListWidget {{
                background: {theme.COLOR_BG_DARK};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 6px; padding: 3px; font-size: 12px;
            }}
            QListWidget::item {{ padding: 9px 10px; border-radius: 4px; }}
            QListWidget::item:hover {{ background: {theme.COLOR_BG_HOVER}; }}
            QListWidget::item:selected {{
                background: {theme.COLOR_ACCENT}; color: white;
            }}
        """)
        self.cam_list.currentItemChanged.connect(self._on_camera_selected)
        v.addWidget(self.cam_list, 1)
        return panel

    def _build_center_panel(self):
        center = QFrame()
        center.setStyleSheet("background: #000000;")
        v = QVBoxLayout(center)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.video = VideoDisplay()
        self.video.set_placeholder("Select a camera to begin")
        self.video.zoom_changed.connect(self._on_video_zoom_changed)
        v.addWidget(self.video, 1)

        # --- Toolbar ---
        toolbar = QFrame()
        toolbar.setFixedHeight(42)
        toolbar.setStyleSheet(f"""
            QFrame {{
                background: {theme.COLOR_BG_CARD};
                border-top: 1px solid {theme.COLOR_BORDER};
            }}
        """)
        th = QHBoxLayout(toolbar)
        th.setContentsMargins(8, 4, 8, 4)
        th.setSpacing(4)

        self.btn_layout = self._small_btn("grid", "چیدمان شبکه")
        self.btn_layout.clicked.connect(self._cycle_layout)
        th.addWidget(self.btn_layout)

        self.btn_close = self._small_btn("x", "بستن دوربین فعال")
        self.btn_close.clicked.connect(self._close_active)
        th.addWidget(self.btn_close)

        self.btn_snap_toolbar = self._small_btn("camera", "عکس فوری")
        self.btn_snap_toolbar.clicked.connect(self._on_snapshot)
        th.addWidget(self.btn_snap_toolbar)

        th.addSpacing(8)
        th.addWidget(self._vsep())
        th.addSpacing(8)

        self.btn_prev_seg = self._small_btn("skip-back", "قطعه قبلی")
        self.btn_prev_seg.clicked.connect(self._prev_segment)
        th.addWidget(self.btn_prev_seg)

        self.btn_step_back = self._small_btn("chevron-left", "یک فریم عقب")
        self.btn_step_back.clicked.connect(lambda: self._step(-1))
        th.addWidget(self.btn_step_back)

        self.btn_play = QPushButton()
        self.btn_play.setIcon(make_icon("play-circle", "white", 20))
        self.btn_play.setFixedSize(40, 32)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.setToolTip("پخش / توقف  (Space)")
        self.btn_play.clicked.connect(self._toggle)
        self.btn_play.setStyleSheet(f"""
            QPushButton {{
                background: {theme.COLOR_ACCENT};
                border: none; border-radius: 8px;
            }}
            QPushButton:hover {{ background: {theme.COLOR_ACCENT_HOVER}; }}
        """)
        th.addWidget(self.btn_play)

        self.btn_stop = self._small_btn("x", "توقف")
        self.btn_stop.clicked.connect(self._stop)
        th.addWidget(self.btn_stop)

        self.btn_step_fwd = self._small_btn("chevron-right", "یک فریم جلو")
        self.btn_step_fwd.clicked.connect(lambda: self._step(1))
        th.addWidget(self.btn_step_fwd)

        self.btn_next_seg = self._small_btn("skip-forward", "قطعه بعدی")
        self.btn_next_seg.clicked.connect(self._next_segment)
        th.addWidget(self.btn_next_seg)

        th.addSpacing(8)
        th.addWidget(self._vsep())
        th.addSpacing(8)

        sp_lbl = QLabel("⚡")
        sp_lbl.setStyleSheet("color: #8899a6; font-size: 12px;")
        th.addWidget(sp_lbl)

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(0, 4)
        self.speed_slider.setValue(1)
        self.speed_slider.setFixedWidth(90)
        self.speed_slider.setCursor(Qt.PointingHandCursor)
        self.speed_slider.valueChanged.connect(self._on_speed_slider)
        self.speed_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: #2c333c; border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {theme.COLOR_ACCENT}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: white; width: 12px; height: 12px;
                margin: -4px 0; border-radius: 6px;
            }}
        """)
        th.addWidget(self.speed_slider)

        self.speed_lbl = QLabel("1x")
        self.speed_lbl.setStyleSheet(
            f"color: {theme.COLOR_ACCENT}; font-size: 11px; "
            f"font-weight: 700; min-width: 24px;")
        th.addWidget(self.speed_lbl)

        th.addSpacing(8)
        th.addWidget(self._vsep())
        th.addSpacing(8)

        self.btn_cut = self._small_btn("edit", "برش: کلیک ۱ = شروع، کلیک ۲ = پایان")
        self.btn_cut.clicked.connect(self._on_cut_clicked)
        th.addWidget(self.btn_cut)

        self.range_lbl = QLabel("")
        self.range_lbl.setStyleSheet(
            f"color: {theme.COLOR_ACCENT}; font-size: 11px; font-weight: 700;")
        th.addWidget(self.range_lbl)

        th.addStretch(1)

        self.btn_export = self._small_btn("save", "خروجی")
        self.btn_export.clicked.connect(self._open_export_dialog)
        th.addWidget(self.btn_export)

        self.btn_fs = self._small_btn("maximize", "تمام‌صفحه")
        self.btn_fs.clicked.connect(self._toggle_fullscreen)
        th.addWidget(self.btn_fs)

        v.addWidget(toolbar)
        return center

    def _build_right_panel(self):
        panel = QFrame()
        panel.setFixedWidth(240)
        panel.setStyleSheet(f"""
            QFrame {{
                background: {theme.COLOR_BG_PANEL};
                border-left: 1px solid {theme.COLOR_BORDER};
            }}
        """)
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(10)

        cal_title = QLabel("تقویم ضبط")
        cal_title.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_SECONDARY};
            font-size: 10px; font-weight: 700; letter-spacing: 1px;
        """)
        v.addWidget(cal_title)

        cal_wrap = QFrame()
        cal_wrap.setStyleSheet(f"""
            QFrame {{
                background: {theme.COLOR_BG_CARD};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 6px;
            }}
        """)
        cw = QVBoxLayout(cal_wrap)
        cw.setContentsMargins(2, 2, 2, 2)
        self.calendar = PersianCalendar()
        self.calendar.date_selected.connect(self._on_calendar_date_selected)
        cw.addWidget(self.calendar)
        v.addWidget(cal_wrap)

        legend = QHBoxLayout()
        legend.setSpacing(6)
        for color, label in [
            ("#e74c3c", "ضبط‌شده"),
            ("#f1c40f", "حرکت"),
            ("#0a1a2a", "بدون حرکت"),
            ("#000000", "بدون ضبط"),
        ]:
            w = QWidget()
            lh = QHBoxLayout(w)
            lh.setContentsMargins(0, 0, 0, 0); lh.setSpacing(3)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 11px;")
            lh.addWidget(dot)
            txt = QLabel(label)
            txt.setStyleSheet(
                f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 9px;")
            lh.addWidget(txt)
            legend.addWidget(w)
        legend.addStretch(1)
        v.addLayout(legend)
        v.addSpacing(4)

        info_title = QLabel("اطلاعات")
        info_title.setStyleSheet(cal_title.styleSheet())
        v.addWidget(info_title)

        self.info_rows = {}
        for key, label in [
            ("camera", "دوربین"),
            ("date", "تاریخ"),
            ("time", "زمان"),
            ("count", "تعداد قطعات"),
        ]:
            v.addWidget(self._info_row(key, label))
        v.addStretch(1)
        return panel

    def _info_row(self, key, label):
        w = QWidget()
        h = QVBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(1)
        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 9px; font-weight: 700;")
        val = QLabel("—")
        val.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 11px; font-weight: 600;")
        val.setWordWrap(True)
        h.addWidget(lbl); h.addWidget(val)
        self.info_rows[key] = val
        return w

    def _build_bottom(self):
        wrap = QFrame()
        wrap.setStyleSheet(f"""
            QFrame {{
                background: {theme.COLOR_BG_PANEL};
                border-top: 1px solid {theme.COLOR_BORDER};
            }}
        """)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(10, 6, 10, 8)
        v.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        hdr.addWidget(self._lbl("Timeline"))
        hdr.addStretch(1)
        self.time_lbl = QLabel("--:--:-- / --:--:--")
        self.time_lbl.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_PRIMARY};
            font-family: Consolas, monospace;
            font-size: 12px; font-weight: 700;
        """)
        hdr.addWidget(self.time_lbl)
        v.addLayout(hdr)

        self.timeline = TimelineWidget()
        self.timeline.seek_requested.connect(self._on_seek)
        v.addWidget(self.timeline)
        return wrap

    # ============================================================
    # Helpers
    # ============================================================
    def _nav_qss(self):
        return f"""
            QPushButton {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 5px;
                font-size: 14px; font-weight: 700;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_ACCENT}; color: white;
            }}
        """

    def _small_btn(self, icon, tip):
        b = QPushButton()
        b.setIcon(make_icon(icon, theme.COLOR_TEXT_PRIMARY, 14))
        b.setFixedSize(30, 30)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {theme.COLOR_BG_PANEL};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 6px;
            }}
            QPushButton:hover {{ background: {theme.COLOR_BG_HOVER}; }}
        """)
        return b

    def _vsep(self):
        s = QFrame()
        s.setFixedSize(1, 22)
        s.setStyleSheet(f"background: {theme.COLOR_BORDER};")
        return s

    def _lbl(self, text):
        l = QLabel(text)
        l.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 10px; "
            f"font-weight: 700; letter-spacing: 1px;")
        return l

    # ============================================================
    # Cameras
    # ============================================================
    def _load_cameras(self):
        self.cam_list.blockSignals(True)
        self.cam_list.clear()
        for c in self.cam_manager.all():
            item = QListWidgetItem(f"📷  {c.name or c.uid}")
            item.setData(Qt.UserRole, c.uid)
            self.cam_list.addItem(item)
        self.cam_list.blockSignals(False)

    # ============================================================
    # Scanning
    # ============================================================
    def _scan_recording_dates(self, cam_uid):
        cam = self.cam_manager.get(cam_uid)
        if cam is None:
            return set()
        name = _camera_path_name(cam)
        try:
            return self.indexer.get_recording_dates(name)
        except Exception as e:
            print(f"[playback] dates scan error: {e}")
            return set()

    def _scan_segments(self, cam_uid, day):
        """Use RecordingIndexer for accurate, cached segment discovery."""
        cam = self.cam_manager.get(cam_uid)
        if cam is None:
            return []
        name = _camera_path_name(cam)

        try:
            day_index = self.indexer.get_day_index(name, day)
        except Exception as e:
            print(f"[playback] segment scan error: {e}")
            return []

        now = datetime.datetime.now()
        now_sec = now.hour * 3600 + now.minute * 60 + now.second

        out = []
        for seg in day_index.segments:
            dur = seg.duration

            # WRITING segments: growing bar up to current time
            if seg.state == STATE_WRITING:
                if seg.start_sec <= now_sec:
                    dur = float(now_sec - seg.start_sec)
                else:
                    dur = float(86400 - seg.start_sec + now_sec)

            if dur is None or dur <= 0:
                continue

            out.append({
                "path": seg.file_path,
                "start": float(seg.start_sec),
                "duration": float(dur),
                "type": "continuous",
                "state": seg.state,
                "codec": seg.codec,
                "width": seg.width,
                "height": seg.height,
                "fps": seg.fps,
            })

        return out

    # ============================================================
    # Selection
    # ============================================================
    def _on_camera_selected(self, current, previous):
        if current is None:
            return
        self._current_cam_uid = current.data(Qt.UserRole)
        cam = self.cam_manager.get(self._current_cam_uid)
        dates = self._scan_recording_dates(self._current_cam_uid)
        self.calendar.set_recording_dates(dates)
        self.info_rows["camera"].setText(cam.name if cam else "—")
        self._reload_for_date()

    def _reload_for_date(self):
        uid = self._current_cam_uid
        if uid is None:
            return
        day = self.calendar.selected_date()

        jy, jm, jd = gregorian_to_jalali(day.year, day.month, day.day)
        self.date_lbl.setText(
            f"{day.strftime('%Y-%m-%d')}   —   "
            f"{jd} {PERSIAN_MONTHS[jm-1]} {jy}"
        )

        self.info_rows["date"].setText(day.strftime("%Y-%m-%d"))

        segments = self._scan_segments(uid, day)
        self.timeline.set_segments(segments)

        self.engine.stop()
        self.engine.set_segments(segments)

        self.info_rows["count"].setText(f"{len(segments)} قطعه")

        if not segments:
            self.video.set_placeholder(f"No recordings on {day}")
            self.header_time_lbl.setText("--:--:-- / --:--:--")
            self.time_lbl.setText("--:--:-- / --:--:--")
            self.info_rows["time"].setText("—")
        else:
            first = segments[0]["start"]
            self.engine.seek(first)

        self._update_panel_segments()

    def _on_calendar_date_selected(self, date_obj):
        self._reload_for_date()

    def _shift_day(self, delta):
        d = self.calendar.selected_date() + datetime.timedelta(days=delta)
        self.calendar.set_selected_date(d)
        self._reload_for_date()

    def _go_today(self):
        self.calendar.set_selected_date(datetime.date.today())
        self._reload_for_date()

    # ============================================================
    # Player controls
    # ============================================================
    def _toggle(self):
        if not self.engine.has_content():
            return
        self.engine.toggle()

    def _stop(self):
        self.engine.stop()
        self.video.set_placeholder("Stopped")

    def _step(self, direction):
        self.engine.pause()
        self.engine.step_frame(direction)

    def _prev_segment(self):
        idx = self.engine._segment_idx
        if idx > 0:
            segs = self.engine.get_segments()
            self.engine.seek(segs[idx - 1]["start"])

    def _next_segment(self):
        idx = self.engine._segment_idx
        segs = self.engine.get_segments()
        if idx < len(segs) - 1:
            self.engine.seek(segs[idx + 1]["start"])

    def _on_speed_slider(self, value):
        speed_map = {0: 0.5, 1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}
        s = speed_map.get(value, 1.0)
        self.speed_lbl.setText(f"{s:g}x")
        self.engine.set_speed(s)

    def _on_seek(self, seconds):
        """Called continuously during timeline drag → debounced."""
        self.engine.request_seek(seconds)

    def _on_frame(self, rgb):
        if rgb is None:
            self.video.set_placeholder("— NO RECORDING —")
            return
        self.video.set_frame(rgb)

    def _on_position(self, seconds):
        self.timeline.set_playhead(seconds)
        total = self.engine.get_total_duration()
        txt = f"{_fmt_hms(seconds)} / {_fmt_hms(total)}"
        self.time_lbl.setText(txt)
        self.header_time_lbl.setText(txt)
        self.info_rows["time"].setText(_fmt_hms(seconds))

    def _on_state(self, state):
        if state == "playing":
            self.btn_play.setIcon(make_icon("pause", "white", 20))
            # در حال پخش: اگر placeholder قبلی بود، پاک شود
            if getattr(self.video, "_image", None) is None:
                self.video.set_placeholder("Loading…")
        elif state == "gap":
            self.btn_play.setIcon(make_icon("play-circle", "white", 20))
            self.video.set_placeholder("— NO RECORDING —")
        elif state == "ended":
            self.btn_play.setIcon(make_icon("play-circle", "white", 20))
            self.video.set_placeholder("— END OF RECORDING —")
        else:
            self.btn_play.setIcon(make_icon("play-circle", "white", 20))

    def _on_snapshot(self):
        img = getattr(self.video, "_image", None)
        if img is None:
            dlg_info(self, "No frame to save.")
            return
        try:
            snap_dir = Path(config.RECORDINGS_DIR) / "snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out = snap_dir / f"snap_{ts}.jpg"
            img.save(str(out), "JPG", 92)
            dlg_info(self, f"Saved: {out.name}", "Snapshot")
        except Exception as e:
            dlg_warning(self, f"Snapshot failed:\n{e}")

    def _on_video_zoom_changed(self, is_zoomed):
        pass

    # ============================================================
    # Cut button
    # ============================================================
    def _on_cut_clicked(self):
        if self._cut_in is None:
            self._cut_in = self.engine.get_position()
            self._cut_out = None
            self._update_range_lbl()
            self.btn_cut.setStyleSheet(f"""
                QPushButton {{
                    background: {theme.COLOR_ACCENT};
                    border: 1px solid {theme.COLOR_ACCENT};
                    border-radius: 6px;
                }}
            """)
            return

        self._cut_out = self.engine.get_position()
        if self._cut_out <= self._cut_in:
            dlg_info(self, "پایان باید بعد از شروع باشد.")
            self._cut_out = None
            return

        self._update_range_lbl()
        self._open_export_dialog()

        self._cut_in = None
        self._cut_out = None
        self._update_range_lbl()
        self.btn_cut.setStyleSheet(f"""
            QPushButton {{
                background: {theme.COLOR_BG_PANEL};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_BG_HOVER};
            }}
        """)

    def _update_range_lbl(self):
        if self._cut_in is None:
            self.range_lbl.setText("")
            return
        txt = f"IN {_fmt_hms(self._cut_in)}"
        if self._cut_out is not None:
            txt += f"  →  OUT {_fmt_hms(self._cut_out)}"
        self.range_lbl.setText(txt)

    # ============================================================
    # Export
    # ============================================================
    def _open_export_dialog(self):
        if self._current_cam_uid is None:
            dlg_info(self, "ابتدا یک دوربین انتخاب کنید.")
            return
        cam = self.cam_manager.get(self._current_cam_uid)
        day = self.calendar.selected_date()
        segments = self._scan_segments(self._current_cam_uid, day)
        if not segments:
            dlg_info(self, "برای این روز ضبطی وجود ندارد.")
            return

        if self._cut_in is not None and self._cut_out is not None:
            start_sec = int(self._cut_in)
            end_sec = int(self._cut_out)
        elif self._cut_in is not None:
            start_sec = int(self._cut_in)
            end_sec = min(86400, start_sec + 300)
        else:
            pos = int(self.engine.get_position())
            start_sec = max(0, pos - 30)
            end_sec = min(86400, pos + 300)

        dlg = ExportDialog(
            camera_name=cam.name if cam else "camera",
            day=day,
            start_sec=start_sec,
            end_sec=end_sec,
            segments=segments,
            parent=self,
        )
        dlg.exec()

    # ============================================================
    # Layout / Fullscreen / Close
    # ============================================================
    def _cycle_layout(self):
        dlg_info(self, "چیدمان چند-کاشی در فاز بعدی اضافه می‌شود.")

    def _close_active(self):
        self.engine.stop()
        self.video.set_placeholder("Closed")

    def _toggle_fullscreen(self):
        w = self.window()
        if w.isFullScreen():
            w.showNormal()
        else:
            w.showFullScreen()

    # ============================================================
    # Context panel
    # ============================================================
    def build_panel_content(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        t = QLabel("تشخیص حرکت / قطعات")
        t.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_SECONDARY};
            font-size: 10px; font-weight: 700; letter-spacing: 1.2px;
            padding-bottom: 4px;
        """)
        v.addWidget(t)

        self._panel_cam_lbl = QLabel("—")
        self._panel_cam_lbl.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_PRIMARY};
            font-size: 11px; font-weight: 700;
            background: {theme.COLOR_BG_CARD};
            padding: 6px 10px; border-radius: 4px;
        """)
        v.addWidget(self._panel_cam_lbl)

        self._panel_date_lbl = QLabel("—")
        self._panel_date_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 10px; padding: 0 4px;")
        v.addWidget(self._panel_date_lbl)

        self._panel_list = QListWidget()
        self._panel_list.setStyleSheet(f"""
            QListWidget {{
                background: {theme.COLOR_BG_DARK};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 5px; padding: 3px;
                font-size: 11px; font-family: Consolas, monospace;
            }}
            QListWidget::item {{ padding: 6px 8px; border-radius: 3px; }}
            QListWidget::item:hover {{ background: {theme.COLOR_BG_HOVER}; }}
            QListWidget::item:selected {{
                background: {theme.COLOR_ACCENT}; color: white;
            }}
        """)
        self._panel_list.itemClicked.connect(self._on_panel_segment_clicked)
        v.addWidget(self._panel_list, 1)

        self._panel_summary = QLabel("—")
        self._panel_summary.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_SECONDARY};
            font-size: 10px;
            background: {theme.COLOR_BG_CARD};
            padding: 6px 8px; border-radius: 4px;
        """)
        v.addWidget(self._panel_summary)
        return w

    def _update_panel_segments(self):
        if not hasattr(self, "_panel_list"):
            return

        uid = self._current_cam_uid
        if uid is None:
            self._panel_cam_lbl.setText("—")
            self._panel_date_lbl.setText("—")
            self._panel_list.clear()
            self._panel_summary.setText("—")
            return

        cam = self.cam_manager.get(uid)
        day = self.calendar.selected_date()

        self._panel_cam_lbl.setText(cam.name if cam else "—")
        self._panel_date_lbl.setText(day.strftime("%Y-%m-%d"))

        segments = self._scan_segments(uid, day)
        self._panel_list.clear()

        total_dur = 0
        for seg in segments:
            hh = int(seg["start"]) // 3600
            mm = (int(seg["start"]) % 3600) // 60
            ss = int(seg["start"]) % 60
            dur = int(seg["duration"])
            dm = dur // 60
            ds = dur % 60

            state = seg.get("state", "")
            if state == STATE_WRITING:
                text = f"🔴  {hh:02d}:{mm:02d}:{ss:02d}   (live)"
            else:
                text = f"🎬  {hh:02d}:{mm:02d}:{ss:02d}   ({dm}:{ds:02d})"

            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, seg["start"])
            self._panel_list.addItem(it)
            total_dur += dur

        if not segments:
            self._panel_summary.setText("بدون قطعه")
        else:
            th = total_dur // 3600
            tm = (total_dur % 3600) // 60
            self._panel_summary.setText(
                f"{len(segments)} قطعه  •  {th}:{tm:02d} مجموع"
            )

    def _on_panel_segment_clicked(self, item):
        try:
            start = item.data(Qt.UserRole)
            self.engine.seek(float(start))
        except Exception:
            pass

    # ============================================================
    # Lifecycle
    # ============================================================
    def on_show(self):
        try:
            self.cam_manager.reload()
        except Exception as e:
            print(f"[playback] reload cameras: {e}")

        prev_uid = self._current_cam_uid
        self._load_cameras()

        restored = False
        if prev_uid:
            for i in range(self.cam_list.count()):
                it = self.cam_list.item(i)
                if it and it.data(Qt.UserRole) == prev_uid:
                    self.cam_list.setCurrentRow(i)
                    restored = True
                    break

        if not restored and self.cam_list.count() > 0:
            self.cam_list.setCurrentRow(0)

        self._update_panel_segments()

    def on_hide(self):
        self.engine.pause()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self._toggle()
        elif event.key() == Qt.Key_Left:
            self._step(-1)
        elif event.key() == Qt.Key_Right:
            self._step(1)
        else:
            super().keyPressEvent(event)