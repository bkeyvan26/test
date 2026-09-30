# -*- coding: utf-8 -*-
"""K1 VMS — Playback page (Phase 6.7.4: crash-safe)"""
import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QWidget, QSlider, QMenu,
    QAbstractItemView
)
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QMimeData, QObject, Signal, QRunnable, QThreadPool
)
from PySide6.QtGui import QColor, QAction, QDrag

from ui import theme
from ui.icons import make_icon
from ui.pages import BasePage
from ui.dialogs import info as dlg_info, warning as dlg_warning
from ui.dialogs.export_dialog import ExportDialog
from ui.widgets.timeline import TimelineWidget
from ui.widgets.playback_grid import PlaybackGrid, MIME_PLAYBACK_CAM
from ui.widgets.persian_calendar import (
    PersianCalendar, gregorian_to_jalali, PERSIAN_MONTHS
)
from ui.activity_bus import ActivityBus
from ui.loading_texts import t as t_load
from core.camera_manager import CameraManager
from core.nvr_engine import _camera_path_name
from core.recording_indexer import RecordingIndexer, STATE_WRITING
from core import settings_manager
import config


def _fmt_hms(seconds):
    s = max(0, int(seconds))
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


class _ScanSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _ScanJob(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _ScanSignals()

    def run(self):
        try:
            self.signals.finished.emit(self.fn())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class PlaybackCameraList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None:
            return
        uid = item.data(Qt.UserRole) or ""
        if not uid:
            return
        md = QMimeData()
        md.setData(MIME_PLAYBACK_CAM, uid.encode("utf-8"))
        md.setText(uid)
        drag = QDrag(self)
        drag.setMimeData(md)
        try:
            drag.exec(Qt.CopyAction)
        except Exception as ex:
            print(f"[PlaybackCameraList.drag] {ex}")


class PlaybackPage(BasePage):
    PAGE_KEY = "playback"
    PAGE_TITLE = "Playback"
    PANEL_TITLE = "قطعات / تشخیص حرکت"

    def __init__(self, nvr_engine=None, parent=None):
        super().__init__(parent)
        self.cam_manager = CameraManager()
        self.nvr = nvr_engine
        self.indexer = RecordingIndexer.instance(nvr_engine)

        self._cut_in = None
        self._cut_out = None
        self._scan_cache = {}
        self._scan_pool = QThreadPool.globalInstance()
        self._scan_generation = 0

        self._build()
        self._load_cameras()

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

    def _build_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet(
            f"QFrame {{ background: {theme.COLOR_BG_HEADER}; "
            f"border-bottom: 1px solid {theme.COLOR_BORDER}; }}"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(8)

        title = QLabel("🎬 Playback")
        title.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"font-size: 14px; font-weight: 700;"
        )
        h.addWidget(title)
        h.addSpacing(12)

        lay_lbl = QLabel("چیدمان:")
        lay_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 10px;")
        h.addWidget(lay_lbl)

        self.layout_combo = QComboBox()
        self.layout_combo.setFixedWidth(90)
        for label, key in [("۱ کاشی", "1x1"), ("۲ کاشی", "1+1"),
                           ("۴ کاشی", "2x2"), ("۹ کاشی", "3x3")]:
            self.layout_combo.addItem(label, key)
        self.layout_combo.setCurrentIndex(2)
        self.layout_combo.currentIndexChanged.connect(
            self._on_layout_changed)
        self.layout_combo.setStyleSheet(f"""
            QComboBox {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 5px;
                padding: 3px 8px; font-size: 11px;
            }}
        """)
        h.addWidget(self.layout_combo)
        h.addSpacing(12)

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

        self.cam_list = PlaybackCameraList()
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
        self.cam_list.itemDoubleClicked.connect(self._on_cam_double_clicked)
        self.cam_list.itemClicked.connect(self._on_cam_clicked)
        v.addWidget(self.cam_list, 1)

        hint = QLabel(
            "• دابل کلیک: کاشی خالی بعدی\n"
            "• Drag روی کاشی: همان کاشی\n"
            "• دابل کلیک روی کاشی: تک‌صفحه\n"
            "• 💡 فقط کاشی فعال پخش می‌شود")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {theme.COLOR_TEXT_MUTED}; font-size: 10px; padding: 4px;")
        v.addWidget(hint)

        return panel

    def _build_center_panel(self):
        center = QFrame()
        center.setStyleSheet("background: #000000;")
        v = QVBoxLayout(center)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.grid = PlaybackGrid()
        self.grid.set_layout("2x2")
        self.grid.cell_clicked.connect(self._on_cell_clicked)
        self.grid.cell_double_clicked.connect(self._on_cell_double_clicked)
        self.grid.cell_context.connect(self._on_cell_context)
        self.grid.camera_dropped.connect(self._on_camera_dropped)
        self.grid.cell_state.connect(self._on_cell_state)
        self.grid.cell_position.connect(self._on_cell_position)
        self.grid.fullscreen_about_to_change.connect(
            self._on_fs_about_to_change)
        v.addWidget(self.grid, 1)

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

        self.btn_close = self._small_btn("x", "بستن کاشی فعال")
        self.btn_close.clicked.connect(self._close_active_cell)
        th.addWidget(self.btn_close)

        self.btn_snap = self._small_btn("camera", "عکس فوری")
        self.btn_snap.clicked.connect(self._on_snapshot)
        th.addWidget(self.btn_snap)

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
        self.btn_play.setToolTip("پخش / توقف (Space)")
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
            f"font-weight: 700; min-width: 24px;"
        )
        th.addWidget(self.speed_lbl)

        th.addSpacing(8)
        th.addWidget(self._vsep())
        th.addSpacing(8)

        self.btn_cut = self._small_btn("edit",
                                       "برش: کلیک ۱ = شروع، کلیک ۲ = پایان")
        self.btn_cut.clicked.connect(self._on_cut_clicked)
        th.addWidget(self.btn_cut)

        self.range_lbl = QLabel("")
        self.range_lbl.setStyleSheet(
            f"color: {theme.COLOR_ACCENT}; font-size: 11px; font-weight: 700;"
        )
        th.addWidget(self.range_lbl)

        th.addStretch(1)

        self.btn_export = self._small_btn("save", "خروجی")
        self.btn_export.clicked.connect(self._open_export_dialog)
        th.addWidget(self.btn_export)

        # ★ دو دکمه fullscreen جدا
        self.btn_fs = self._small_btn("maximize",
                                      "کاشی فعال — تمام‌صفحه")
        self.btn_fs.clicked.connect(self._toggle_active_cell_fullscreen)
        th.addWidget(self.btn_fs)

        self.btn_app_fs = self._small_btn("maximize-2",
                                          "کل برنامه — تمام‌صفحه (F11)")
        self.btn_app_fs.clicked.connect(self._toggle_app_fullscreen)
        th.addWidget(self.btn_app_fs)

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
            lh.setContentsMargins(0, 0, 0, 0)
            lh.setSpacing(3)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 11px;")
            lh.addWidget(dot)
            txt = QLabel(label)
            txt.setStyleSheet(
                f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 9px;"
            )
            lh.addWidget(txt)
            legend.addWidget(w)
        legend.addStretch(1)
        v.addLayout(legend)

        v.addSpacing(4)

        info_title = QLabel("اطلاعات کاشی فعال")
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
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(1)
        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 9px; font-weight: 700;"
        )
        val = QLabel("—")
        val.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 11px; font-weight: 600;"
        )
        val.setWordWrap(True)
        h.addWidget(lbl)
        h.addWidget(val)
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
            f"font-weight: 700; letter-spacing: 1px;"
        )
        return l

    # ============================================================
    def _load_cameras(self):
        self.cam_list.blockSignals(True)
        self.cam_list.clear()
        for c in self.cam_manager.all():
            item = QListWidgetItem(f"📷 {c.name or c.uid}")
            item.setData(Qt.UserRole, c.uid)
            self.cam_list.addItem(item)
        self.cam_list.blockSignals(False)

    # ============================================================
    def _scan_dates(self, cam_uid):
        if cam_uid in self._scan_cache:
            return self._scan_cache[cam_uid]["dates"]
        cam = self.cam_manager.get(cam_uid)
        if cam is None:
            return set()
        name = _camera_path_name(cam)
        try:
            dates = self.indexer.get_recording_dates(name)
        except Exception as e:
            print(f"[playback] dates scan error: {e}")
            dates = set()
        self._scan_cache.setdefault(cam_uid, {})["dates"] = dates
        self._scan_cache[cam_uid].setdefault("days", {})
        return dates

    def _scan_segments(self, cam_uid, day):
        cache = self._scan_cache.get(cam_uid, {})
        days = cache.get("days", {})
        if day in days:
            return days[day]

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
        self._scan_cache.setdefault(cam_uid, {})["days"] = days
        days[day] = out
        return out

    # ============================================================
    # ★ Single-active (defensive)
    # ============================================================
    def _pause_other_engines(self, keep_idx):
        for i, cell in enumerate(self.grid.get_cells()):
            if i == keep_idx:
                continue
            try:
                eng = cell.engine()
                if eng is not None and hasattr(eng, "pause"):
                    eng.pause()
            except RuntimeError:
                pass
            except Exception as ex:
                print(f"[pause_other {i}] {ex}")

    # ============================================================
    def _on_cam_clicked(self, item):
        """Single click = select/load camera; never auto-play."""
        uid = item.data(Qt.UserRole)
        if uid is None:
            return
        idx = self.grid.find_cell_by_cam(uid)
        if idx < 0:
            idx = self.grid.first_empty_idx()
            if idx < 0:
                idx = self.grid.get_active_idx()
            if idx < 0:
                return
            self._load_camera_to_cell(uid, idx)
            self.grid._active_idx = idx
            self.grid._update_active()
            return
        self._pause_other_engines(keep_idx=idx)
        self.grid._active_idx = idx
        self.grid._update_active()
        self._bind_active_cell()
        self._update_info_panel()
        self._update_panel_segments()

    def _on_cam_double_clicked(self, item):
        uid = item.data(Qt.UserRole)
        if uid is None:
            return
        idx = self.grid.find_cell_by_cam(uid)
        if idx >= 0:
            self._pause_other_engines(keep_idx=idx)
            self.grid._active_idx = idx
            self.grid._update_active()
            self._bind_active_cell()
            return
        target = self.grid.first_empty_idx()
        if target < 0:
            target = self.grid.get_active_idx()
        self._load_camera_to_cell(uid, target)

    def _on_camera_dropped(self, cell_idx, uid):
        self._load_camera_to_cell(uid, cell_idx)

    def _load_camera_to_cell(self, uid, cell_idx):
        cam = self.cam_manager.get(uid)
        if cam is None:
            return

        cell = self.grid.get_cell(cell_idx)
        if cell is None:
            return

        cell.set_loading(True, t_load("playback_scan",
                                       camera=cam.name))

        try:
            ActivityBus.instance().show(
                t_load("playback_scan", camera=cam.name or ""))
        except Exception:
            pass

        day = self.calendar.selected_date()

        self._scan_generation += 1
        generation = self._scan_generation

        def _scan():
            dates = self._scan_dates(uid)
            segments = self._scan_segments(uid, day)
            return dates, segments

        job = _ScanJob(_scan)

        def _done(result):
            if generation != self._scan_generation:
                return
            dates, segments = result
            self.calendar.set_recording_dates(dates)
            self.grid.assign_to_cell(
                cell_idx, uid, cam.name or uid, day, segments)
            self._pause_other_engines(keep_idx=cell_idx)
            self._bind_active_cell()
            self._update_info_panel()
            self._update_panel_segments()
            print(f"[playback] loaded {cam.name} in cell "
                  f"{cell_idx} ({len(segments)} segments)")
            cell.set_loading(False)
            ActivityBus.instance().hide()

        def _failed(message):
            if generation != self._scan_generation:
                return
            print(f"[playback.scan] {message}")
            cell.set_loading(False)
            ActivityBus.instance().hide()

        job.signals.finished.connect(_done)
        job.signals.failed.connect(_failed)
        self._scan_pool.start(job)

    # ============================================================
    # Cell interaction
    # ============================================================
    def _on_cell_clicked(self, idx):
        self._pause_other_engines(keep_idx=idx)
        self._bind_active_cell()
        self._update_info_panel()
        self._update_panel_segments()

    def _on_cell_double_clicked(self, idx):
        """فقط active_idx را sync کن. pause از _on_fs_about_to_change."""
        self.grid._active_idx = idx
        self.grid._update_active()
        self._bind_active_cell()

    def _on_fs_about_to_change(self, idx):
        if idx >= 0:
            self._pause_other_engines(keep_idx=idx)

    def _on_cell_context(self, idx, global_pos):
        cell = self.grid.get_cell(idx)
        if cell is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                padding: 4px;
            }}
            QMenu::item {{ padding: 6px 20px; border-radius: 3px; }}
            QMenu::item:selected {{ background: {theme.COLOR_ACCENT}; }}
        """)

        if cell.cam_uid():
            act_close = QAction("بستن کاشی", self)
            act_close.triggered.connect(lambda: self._close_cell(idx))
            menu.addAction(act_close)

            act_snap = QAction("عکس فوری", self)
            act_snap.triggered.connect(self._on_snapshot)
            menu.addAction(act_snap)

            if self.grid.is_fullscreen():
                act_exit = QAction("خروج از تمام‌صفحه", self)
                act_exit.triggered.connect(self.grid.exit_fullscreen)
                menu.addAction(act_exit)
            else:
                act_fs = QAction("تمام‌صفحه کاشی", self)
                act_fs.triggered.connect(
                    lambda: self._toggle_active_cell_fullscreen())
                menu.addAction(act_fs)

            menu.addSeparator()

            act_export = QAction("خروجی...", self)
            act_export.triggered.connect(self._open_export_dialog)
            menu.addAction(act_export)
        else:
            act_help = QAction("برای بارگذاری، دوربین را drag کن", self)
            act_help.setEnabled(False)
            menu.addAction(act_help)

        menu.exec(global_pos)

    def _close_cell(self, idx):
        self.grid.clear_cell(idx)
        if self.grid.get_active_idx() == idx:
            self._bind_active_cell()
            self._update_info_panel()
            self._update_panel_segments()

    def _close_active_cell(self):
        idx = self.grid.get_active_idx()
        self._close_cell(idx)

    # ============================================================
    def _bind_active_cell(self):
        cell = self.grid.get_active_cell()
        if cell is None:
            self.timeline.set_segments([])
            return
        self.timeline.set_segments(cell.segments())
        try:
            pos = cell.engine().get_position()
            self.timeline.set_playhead(pos)
            total = cell.engine().get_total_duration()
            txt = f"{_fmt_hms(pos)} / {_fmt_hms(total)}"
            self.time_lbl.setText(txt)
            self.header_time_lbl.setText(txt)
        except Exception:
            pass

    def _update_info_panel(self):
        cell = self.grid.get_active_cell()
        if cell is None or not cell.cam_uid():
            self.info_rows["camera"].setText("—")
            self.info_rows["date"].setText("—")
            self.info_rows["time"].setText("—")
            self.info_rows["count"].setText("—")
            return
        self.info_rows["camera"].setText(cell.cam_name())
        if cell.day():
            self.info_rows["date"].setText(cell.day().strftime("%Y-%m-%d"))
        self.info_rows["count"].setText(f"{len(cell.segments())} قطعه")

    def _on_layout_changed(self, idx):
        key = self.layout_combo.itemData(idx)
        if key:
            self.grid.set_layout(key)

    # ============================================================
    def _active_engine(self):
        cell = self.grid.get_active_cell()
        return cell.engine() if cell else None

    def _toggle(self):
        eng = self._active_engine()
        if eng is None or not eng.has_content():
            return
        self._pause_other_engines(keep_idx=self.grid.get_active_idx())
        try:
            if not eng.is_playing():
                from core.trace import new_trace
                cell = self.grid.get_active_cell()
                cam_name = cell.cam_name() if cell else ""
                t = new_trace("PLAY", camera=cam_name)
                t.mark("PLAY_CLICK")
                eng.set_active_trace(t)
        except Exception:
            pass
        eng.toggle()

    def _stop(self):
        eng = self._active_engine()
        if eng:
            eng.stop()

    def _step(self, direction):
        eng = self._active_engine()
        if eng:
            eng.pause()
            eng.step_frame(direction)

    def _prev_segment(self):
        eng = self._active_engine()
        if eng is None:
            return
        idx = eng._segment_idx
        if idx > 0:
            segs = eng.get_segments()
            eng.seek(segs[idx - 1]["start"])

    def _next_segment(self):
        eng = self._active_engine()
        if eng is None:
            return
        idx = eng._segment_idx
        segs = eng.get_segments()
        if idx < len(segs) - 1:
            eng.seek(segs[idx + 1]["start"])

    def _on_speed_slider(self, value):
        speed_map = {0: 0.5, 1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}
        s = speed_map.get(value, 1.0)
        self.speed_lbl.setText(f"{s:g}x")
        eng = self._active_engine()
        if eng:
            eng.set_speed(s)

    def _on_seek(self, seconds):
        eng = self._active_engine()
        if eng is None:
            return
        self._pause_other_engines(keep_idx=self.grid.get_active_idx())
        try:
            from core.trace import new_trace
            cell = self.grid.get_active_cell()
            cam_name = cell.cam_name() if cell else ""
            t = new_trace("SEEK", camera=cam_name)
            t.mark("USER_SEEK_CLICK", target=f"{float(seconds):.2f}")
            eng.set_active_trace(t)
        except Exception:
            pass
        eng.request_seek(seconds)

    def _on_cell_state(self, idx, state):
        if idx != self.grid.get_active_idx():
            return
        if state == "playing":
            self.btn_play.setIcon(make_icon("pause", "white", 20))
        elif state in ("ended", "gap", "idle"):
            self.btn_play.setIcon(make_icon("play-circle", "white", 20))

    def _on_cell_position(self, idx, seconds):
        if idx != self.grid.get_active_idx():
            return
        self.timeline.set_playhead(seconds)
        eng = self._active_engine()
        total = eng.get_total_duration() if eng else 0
        txt = f"{_fmt_hms(seconds)} / {_fmt_hms(total)}"
        self.time_lbl.setText(txt)
        self.header_time_lbl.setText(txt)
        self.info_rows["time"].setText(_fmt_hms(seconds))

    # ============================================================
    def _on_calendar_date_selected(self, date_obj):
        idx = self.grid.get_active_idx()
        cell = self.grid.get_cell(idx)
        if cell is None or not cell.cam_uid():
            self._update_date_label(date_obj)
            return
        uid = cell.cam_uid()
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        self._scan_generation += 1
        generation = self._scan_generation
        self.grid.get_cell(idx).set_loading(True, "در حال بارگذاری…")

        job = _ScanJob(lambda: self._scan_segments(uid, date_obj))

        def _done(segments):
            if generation != self._scan_generation:
                return
            self.grid.assign_to_cell(
                idx, uid, cam.name if cam else "", date_obj, segments)
            self._pause_other_engines(keep_idx=idx)
            self._bind_active_cell()
            self._update_info_panel()
            self._update_panel_segments()
            self._update_date_label(date_obj)
            print(f"[playback] reloaded cell {idx} for {date_obj}")
            self.grid.get_cell(idx).set_loading(False)

        def _failed(message):
            if generation != self._scan_generation:
                return
            print(f"[playback] reload error: {message}")
            self.grid.get_cell(idx).set_loading(False)

        job.signals.finished.connect(_done)
        job.signals.failed.connect(_failed)
        self._scan_pool.start(job)

    def _update_date_label(self, day):
        jy, jm, jd = gregorian_to_jalali(day.year, day.month, day.day)
        self.date_lbl.setText(
            f"{day.strftime('%Y-%m-%d')} — "
            f"{jd} {PERSIAN_MONTHS[jm-1]} {jy}"
        )

    def _shift_day(self, delta):
        d = self.calendar.selected_date() + datetime.timedelta(days=delta)
        self.calendar.set_selected_date(d)
        self._on_calendar_date_selected(d)

    def _go_today(self):
        d = datetime.date.today()
        self.calendar.set_selected_date(d)
        self._on_calendar_date_selected(d)

    # ============================================================
    def _on_snapshot(self):
        cell = self.grid.get_active_cell()
        if cell is None:
            return
        img = getattr(cell.video, "_image", None)
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

    def _on_cut_clicked(self):
        eng = self._active_engine()
        if eng is None:
            return
        if self._cut_in is None:
            self._cut_in = eng.get_position()
            self._cut_out = None
            self._update_range_lbl()
            return
        self._cut_out = eng.get_position()
        if self._cut_out <= self._cut_in:
            dlg_info(self, "پایان باید بعد از شروع باشد.")
            self._cut_out = None
            return
        self._update_range_lbl()
        self._open_export_dialog()
        self._cut_in = None
        self._cut_out = None
        self._update_range_lbl()

    def _update_range_lbl(self):
        if self._cut_in is None:
            self.range_lbl.setText("")
            return
        txt = f"IN {_fmt_hms(self._cut_in)}"
        if self._cut_out is not None:
            txt += f" → OUT {_fmt_hms(self._cut_out)}"
        self.range_lbl.setText(txt)

    def _open_export_dialog(self):
        cell = self.grid.get_active_cell()
        if cell is None or not cell.cam_uid():
            dlg_info(self, "ابتدا یک دوربین روی کاشی بارگذاری کنید.")
            return
        uid = cell.cam_uid()
        cam = self.cam_manager.get(uid)
        day = cell.day() or self.calendar.selected_date()
        segments = cell.segments()
        if not segments:
            dlg_info(self, "برای این روز ضبطی وجود ندارد.")
            return

        eng = self._active_engine()
        if self._cut_in is not None and self._cut_out is not None:
            start_sec = int(self._cut_in)
            end_sec = int(self._cut_out)
        elif self._cut_in is not None:
            start_sec = int(self._cut_in)
            end_sec = min(86400, start_sec + 300)
        else:
            pos = int(eng.get_position()) if eng else 0
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
    def _toggle_active_cell_fullscreen(self):
        if self.grid.is_fullscreen():
            self.grid.exit_fullscreen()
            return
        idx = self.grid.get_active_idx()
        cell = self.grid.get_cell(idx)
        if cell is None or not cell.cam_uid():
            for i, c in enumerate(self.grid.get_cells()):
                if c.cam_uid():
                    idx = i
                    break
            else:
                return
        self._pause_other_engines(keep_idx=idx)
        self.grid.toggle_fullscreen(idx)

    def _toggle_app_fullscreen(self):
        w = self.window()
        if w.isFullScreen():
            w.showNormal()
        else:
            w.showFullScreen()

    # ============================================================
    def build_panel_content(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        t = QLabel("قطعات کاشی فعال")
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
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 10px; padding: 0 4px;"
        )
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
        cell = self.grid.get_active_cell()
        if cell is None or not cell.cam_uid():
            self._panel_cam_lbl.setText("—")
            self._panel_date_lbl.setText("—")
            self._panel_list.clear()
            self._panel_summary.setText("—")
            return

        self._panel_cam_lbl.setText(cell.cam_name())
        if cell.day():
            self._panel_date_lbl.setText(cell.day().strftime("%Y-%m-%d"))
        segments = cell.segments()
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
                text = f"🔴 {hh:02d}:{mm:02d}:{ss:02d} (live)"
            else:
                text = f"🎬 {hh:02d}:{mm:02d}:{ss:02d} ({dm}:{ds:02d})"
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
                f"{len(segments)} قطعه • مجموع {th:02d}:{tm:02d}"
            )

    def _on_panel_segment_clicked(self, item):
        eng = self._active_engine()
        if eng is None:
            return
        try:
            start = item.data(Qt.UserRole)
            self._pause_other_engines(keep_idx=self.grid.get_active_idx())
            eng.seek(float(start))
        except Exception:
            pass

    # ============================================================
    def on_show(self):
        try:
            self.cam_manager.reload()
        except Exception:
            pass
        self._load_cameras()
        cell = self.grid.get_active_cell()
        if cell and cell.cam_uid():
            self._bind_active_cell()
            self._update_info_panel()

    def on_hide(self):
        for c in self.grid.get_cells():
            try:
                c.engine().pause()
            except Exception:
                pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self._toggle()
        elif event.key() == Qt.Key_Left:
            self._step(-1)
        elif event.key() == Qt.Key_Right:
            self._step(1)
        elif event.key() == Qt.Key_Escape:
            if self.grid.is_fullscreen():
                self.grid.exit_fullscreen()
            elif self.window().isFullScreen():
                self.window().showNormal()
        elif event.key() == Qt.Key_F11:
            self._toggle_app_fullscreen()
        else:
            super().keyPressEvent(event)