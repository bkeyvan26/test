# -*- coding: utf-8 -*-
"""K1 VMS — Live View page (Phase 1.2: crash-free retire)"""
import os
import json
import time
import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QListWidget, QListWidgetItem, QMenu, QApplication,
    QDialog, QFormLayout, QDialogButtonBox
)
from PySide6.QtCore import Qt, QMimeData, QTimer
from PySide6.QtGui import QDrag, QImage

from ui import theme
from ui.icons import make_icon
from ui.pages import BasePage
from ui.dialogs import info as dlg_info, warning as dlg_warning
from ui.widgets.live_grid import LiveGrid, MIME_CAMERA
from ui.widgets.layout_picker import LayoutPicker
from ui.widgets.camera_list_widget import CameraListWidget

from ui.pages.live_helpers import (
    parse_size, redact_url, build_stream_url,
    compute_output_size_for, compute_fps_for,
    query_mediamtx_state, collect_prewarm_state,
    mediamtx_path_ready, get_live_transport,
)
from ui.pages.live_context_mixin import LiveContextMenuMixin
from ui.pages.live_persist_mixin import LivePersistenceMixin

from core.camera_manager import CameraManager
from core.live_reader import LiveReader
from core.nvr_engine import _camera_path_name
import config


STATE_FILE = config.DATA_DIR / "live_state.json"
STUCK_READER_SEC = 6.0
EXIT_GRACE_SEC = 1.5   # بعد از exit fullscreen، prewarm را نادیده بگیر


class LivePage(BasePage, LiveContextMenuMixin, LivePersistenceMixin):
    PAGE_KEY = "live"
    PAGE_TITLE = "Live"
    HAS_PANEL = False

    DEFAULT_LAYOUT = "2x2"
    PREWARM_DELAY_MS = 200
    SINGLE_IDLE_TIMEOUT_SEC = 30
    IDLE_CHECK_INTERVAL_MS = 15_000
    MAX_PREWARM_SINGLE_READERS = 6
    STATE_FILE = STATE_FILE

    def __init__(self, nvr_engine=None, parent=None):
        super().__init__(parent)
        self.cam_manager = CameraManager()
        self.nvr = nvr_engine

        self.grid_readers = {}
        self.single_readers = {}
        self._dying_readers = []

        self.cell_to_uid = {}
        self.uid_to_cell = {}
        self.cell_source = {}
        self._statuses = {}

        self._fullscreen_uid = None
        self._pending_switch = None
        self._single_ready_uids = set()
        self._single_ready_walltime = {}

        # ★ Phase 1.2: برای skip hover بعد از exit
        self._last_exit_uid = None
        self._last_exit_time = 0.0

        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.timeout.connect(self._do_prewarm)
        self._prewarm_idx = None

        self._single_last_used = {}
        self._single_idle_timer = QTimer(self)
        self._single_idle_timer.timeout.connect(self._check_single_idle)
        self._single_idle_timer.start(self.IDLE_CHECK_INTERVAL_MS)

        self._gc_timer = QTimer(self)
        self._gc_timer.timeout.connect(self._gc_dying_readers)
        self._gc_timer.start(3000)

        self._shutting_down = False
        self._clearing = False
        self._restoring = False
        self._initial_fill_done = False

        self._current_gs_trace = None
        self._prewarm_trace = None
        self._prewarm_trace_uid = None
        self._prewarm_start_wall = None
        self._grid_traces = {}

        self._mediamtx_retry = {}
        self._max_mediamtx_retry = 8

        self._build()
        self._load_cameras()
        self._restore_state()

    # ============================================================
    # Reader retirement (Phase 1.2: crash-safe)
    # ============================================================
    def _retire_reader(self, reader):
        """★ بدون deleteLater، با wait کوتاه. threadهای باقی‌مانده در _dying_readers می‌مانند."""
        if reader is None:
            return
        try:
            reader.stop()
        except Exception:
            pass
        # تلاش کوتاه برای exit
        try:
            reader.wait(80)
        except Exception:
            pass
        # اگر هنوز زنده است، در لیست نگه‌دار
        try:
            if reader.isRunning():
                self._dying_readers.append(reader)
        except RuntimeError:
            pass
        except Exception:
            pass

    def _gc_dying_readers(self):
        if not self._dying_readers:
            return
        alive = []
        for r in self._dying_readers:
            try:
                if r.isRunning():
                    alive.append(r)
            except RuntimeError:
                pass
            except Exception:
                pass
        self._dying_readers = alive

    # ============================================================
    # UI Build
    # ============================================================
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_center(), 1)
        body.addWidget(self._build_right_panel())
        root.addLayout(body, 1)
        root.addWidget(self._build_statusbar())

    def _build_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(76)
        bar.setStyleSheet(
            f"QFrame {{ background: {theme.COLOR_BG_HEADER}; "
            f"border-bottom: 1px solid {theme.COLOR_BORDER}; }}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(2)
        title = QLabel("📹  Live View")
        title.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"font-size: 14px; font-weight: 700;")
        left.addWidget(title)
        subtitle = QLabel("Layout:")
        subtitle.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 10px;")
        left.addWidget(subtitle)
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setFixedWidth(100)
        h.addWidget(left_w)
        self.layout_picker = LayoutPicker()
        self.layout_picker.layout_selected.connect(self._on_layout_selected)
        h.addWidget(self.layout_picker, 1)
        right = QVBoxLayout()
        right.setSpacing(4)
        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(4)
        self.btn_reset_zoom = self._tool_btn("search", "بازنشانی زوم")
        self.btn_reset_zoom.clicked.connect(self._reset_all_zooms)
        btn_row1.addWidget(self.btn_reset_zoom)
        self.btn_connect_all = self._tool_btn("play-circle", "اتصال همه")
        self.btn_connect_all.clicked.connect(self._connect_all)
        btn_row1.addWidget(self.btn_connect_all)
        self.btn_disconnect_all = self._tool_btn("x", "قطع همه")
        self.btn_disconnect_all.clicked.connect(self._on_clear)
        btn_row1.addWidget(self.btn_disconnect_all)
        self.btn_clear = self._tool_btn("trash", "پاک کردن همه کاشی‌ها")
        self.btn_clear.clicked.connect(self._on_clear)
        btn_row1.addWidget(self.btn_clear)
        btn_row1.addStretch(1)
        right.addLayout(btn_row1)
        h.addLayout(right)
        return bar

    def _build_center(self):
        center = QFrame()
        center.setStyleSheet("background: #000;")
        v = QVBoxLayout(center)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.grid = LiveGrid()
        self.grid.cell_clicked.connect(self._on_cell_clicked)
        self.grid.cell_double_clicked.connect(self._on_cell_double_clicked)
        self.grid.cell_close.connect(self._on_cell_close)
        self.grid.cell_context.connect(self._on_cell_context)
        self.grid.cell_drop_camera.connect(self._on_cell_drop_camera)
        self.grid.cell_swap.connect(self._on_cell_swap)
        self.grid.cell_hover_entered.connect(self._on_cell_hover_entered)
        self.grid.cell_hover_left.connect(self._on_cell_hover_left)
        v.addWidget(self.grid, 1)
        return center

    def _build_right_panel(self):
        panel = QFrame()
        panel.setFixedWidth(240)
        panel.setStyleSheet(
            f"QFrame {{ background: {theme.COLOR_BG_PANEL}; "
            f"border-left: 1px solid {theme.COLOR_BORDER}; }}")
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        t1 = QLabel("CAMERAS")
        t1.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 10px; font-weight: 700; letter-spacing: 1.2px;")
        v.addWidget(t1)
        self.cam_list = CameraListWidget()
        self.cam_list.setStyleSheet(
            f"QListWidget {{ background: {theme.COLOR_BG_DARK}; "
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {theme.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 3px; font-size: 12px; }} "
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 4px; }} "
            f"QListWidget::item:hover {{ background: {theme.COLOR_BG_HOVER}; }} "
            f"QListWidget::item:selected {{ "
            f"background: {theme.COLOR_ACCENT}; color: white; }}")
        self.cam_list.itemDoubleClicked.connect(self._on_cam_double_clicked)
        v.addWidget(self.cam_list, 1)
        hint = QLabel(
            "•  دو کلیک: تمام‌صفحه (کیفیت تکی)\n"
            "•  اسکرول روی تصویر: زوم\n"
            "•  Drag دوربین/کاشی")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {theme.COLOR_TEXT_MUTED}; font-size: 10px; padding: 4px;")
        v.addWidget(hint)
        t2 = QLabel("وضعیت")
        t2.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 10px; font-weight: 700; letter-spacing: 1px; "
            f"padding-top: 8px;")
        v.addWidget(t2)
        self.status_list = QListWidget()
        self.status_list.setFixedHeight(140)
        self.status_list.setStyleSheet(
            f"QListWidget {{ background: {theme.COLOR_BG_DARK}; "
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {theme.COLOR_BORDER}; "
            f"border-radius: 5px; padding: 3px; "
            f"font-size: 11px; font-family: Consolas, monospace; }} "
            f"QListWidget::item {{ padding: 5px 8px; border-radius: 3px; }}")
        v.addWidget(self.status_list)
        return panel

    def _build_statusbar(self):
        bar = QFrame()
        bar.setFixedHeight(24)
        bar.setStyleSheet(
            f"background: {theme.COLOR_BG_SHELL}; "
            f"border-top: 1px solid {theme.COLOR_BORDER};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 12, 0)
        self.status_lbl = QLabel("آماده")
        self.status_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;")
        h.addWidget(self.status_lbl)
        h.addStretch(1)
        self.cells_lbl = QLabel("")
        self.cells_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;")
        h.addWidget(self.cells_lbl)
        return bar

    def _tool_btn(self, icon, tip):
        b = QPushButton()
        b.setIcon(make_icon(icon, theme.COLOR_TEXT_PRIMARY, 14))
        b.setFixedSize(32, 30)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton {{ background: {theme.COLOR_BG_CARD}; "
            f"border: 1px solid {theme.COLOR_BORDER}; "
            f"border-radius: 6px; }} "
            f"QPushButton:hover {{ background: {theme.COLOR_BG_HOVER}; }}")
        return b

    # ---- wrappers ----
    def _build_stream_url(self, cam, profile_id):
        return build_stream_url(cam, profile_id, self.nvr)

    def _compute_output_size_for(self, cam, role):
        return compute_output_size_for(cam, role)

    def _compute_fps_for(self, cam, role, n_cells=1):
        return compute_fps_for(cam, role, n_cells)

    def _mediamtx_path_ready(self, cam, profile_id):
        return mediamtx_path_ready(self.nvr, cam, profile_id)

    def _query_mediamtx_state(self, cam, profile_id):
        return query_mediamtx_state(self.nvr, cam, profile_id)

    def _collect_prewarm_state(self, uid):
        return collect_prewarm_state(
            uid, self.single_readers, self._single_ready_uids,
            self._prewarm_idx, self._prewarm_timer.isActive(),
            self._prewarm_trace_uid)

    # ============================================================
    # Layout
    # ============================================================
    def _on_layout_selected(self, key):
        if self.grid.is_fullscreen():
            self._exit_fullscreen_mode()
        self._apply_layout(key)

    def _apply_layout(self, key, save=True):
        old_cell_uids = []
        if self.cell_to_uid:
            for i in range(max(self.cell_to_uid.keys()) + 1):
                old_cell_uids.append(self.cell_to_uid.get(i))

        for uid in list(self.single_readers.keys()):
            self._single_last_used[uid] = time.monotonic()
        self._fullscreen_uid = None
        self._pending_switch = None

        old_statuses = dict(self._statuses)

        self.grid.set_layout(key)
        n = len(self.grid.get_cells())
        self.cells_lbl.setText(f"کاشی‌ها: {n}")

        for i in range(n):
            self.grid.set_title(i, "")
            self.grid.set_status(i, "")
            self.grid.clear_cell(i)
            self.grid.set_uid(i, "")

        kept_uids = set()
        for i in range(min(n, len(old_cell_uids))):
            uid = old_cell_uids[i]
            if uid:
                kept_uids.add(uid)

        for uid in list(self.grid_readers.keys()):
            if uid not in kept_uids:
                self._stop_grid_reader(uid)

        self.cell_to_uid.clear()
        self.uid_to_cell.clear()
        self.cell_source.clear()

        for i in range(min(n, len(old_cell_uids))):
            uid = old_cell_uids[i]
            if not uid:
                continue
            if uid not in self.grid_readers:
                continue
            cam = self.cam_manager.get(uid)
            if cam is None:
                continue

            self.cell_to_uid[i] = uid
            self.uid_to_cell[uid] = i
            self.cell_source[i] = "grid"
            self.grid.set_uid(i, uid)
            self.grid.set_title(i, cam.name or cam.uid)
            if uid in old_statuses:
                self.grid.set_status(i, old_statuses[uid])

            r = self.grid_readers.get(uid)
            if r is not None:
                f = r.get_latest_frame()
                if f is not None:
                    self.grid.set_frame(i, (r.get_latest_frame_seq(), f))

        if save:
            self._save_state_to_disk()

    # ============================================================
    # Assign camera to cell
    # ============================================================
    def _assign_camera_to_cell(self, cell_idx, uid, start=True, save=True):
        cells = self.grid.get_cells()
        if cell_idx < 0 or cell_idx >= len(cells):
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        old_uid = self.cell_to_uid.get(cell_idx)
        if old_uid and old_uid != uid:
            other = [i for i, u in self.cell_to_uid.items()
                     if u == old_uid and i != cell_idx]
            if not other:
                self._stop_grid_reader(old_uid)
                self._release_single_reader(old_uid)
                self.uid_to_cell.pop(old_uid, None)
                self._statuses.pop(old_uid, None)
        self.cell_to_uid[cell_idx] = uid
        self.uid_to_cell[uid] = cell_idx
        self.cell_source[cell_idx] = "grid"
        self.grid.set_uid(cell_idx, uid)
        self.grid.set_title(cell_idx, cam.name or cam.uid)
        cached = self._statuses.get(uid, "")
        if cached:
            self.grid.set_status(cell_idx, cached)
        else:
            self.grid.set_status(cell_idx, "connecting…")
        if start:
            self._start_grid_reader(uid)
        if save:
            self._save_state_to_disk()

    # ---- Grid reader ----
    def _start_grid_reader(self, uid):
        if self._clearing or self._shutting_down:
            return
        if uid in self.grid_readers:
            return
        if uid not in self.uid_to_cell:
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        ready_state = self._mediamtx_path_ready(cam, cam.grid_profile_id)
        if ready_state is not True:
            cnt = self._mediamtx_retry.get(uid, 0) + 1
            self._mediamtx_retry[uid] = cnt
            if cnt > self._max_mediamtx_retry:
                self._mediamtx_retry.pop(uid, None)
                idx = self.uid_to_cell.get(uid)
                if idx is not None:
                    self.grid.set_status(idx, "source unavailable")
                return
            idx = self.uid_to_cell.get(uid)
            if idx is not None:
                self.grid.set_status(idx, "waiting for source…")
            QTimer.singleShot(700, lambda u=uid: self._start_grid_reader(u))
            return
        self._mediamtx_retry.pop(uid, None)
        url = self._build_stream_url(cam, cam.grid_profile_id)
        if not url:
            idx = self.uid_to_cell.get(uid)
            if idx is not None:
                self.grid.set_status(idx, "no url")
            return
        n = max(1, len(self.grid.get_cells()))
        out_w, out_h = self._compute_output_size_for(cam, "grid")
        fps = self._compute_fps_for(cam, "grid", n_cells=n)
        transport = get_live_transport()
        try:
            from core.trace import new_trace
            t = new_trace("LIVE_GRID", camera=cam.name or cam.uid)
            t.mark("GRID_READER_START", profile=cam.grid_profile_id,
                   fps=fps, size=f"{out_w}x{out_h}", transport=transport)
            self._grid_traces[uid] = t
        except Exception:
            t = None
        print(f"[live-grid] {cam.name or uid}: "
              f"fps={fps}  size={out_w}x{out_h}")
        reader = LiveReader(uid, url, target_fps=fps,
                            out_w=out_w, out_h=out_h, role="grid",
                            transport=transport)
        reader.trace = t
        reader.trace_label = "LIVE_GRID"
        reader.frame_ready.connect(
            lambda u, p, r=reader: self._on_grid_frame(u, p, r))
        reader.status.connect(self._on_status)
        reader.start()
        self.grid_readers[uid] = reader

    def _stop_grid_reader(self, uid):
        r = self.grid_readers.pop(uid, None)
        self._grid_traces.pop(uid, None)
        if r is None:
            return
        self._retire_reader(r)

    def _stop_all_grid_readers(self):
        for uid in list(self.grid_readers.keys()):
            self._stop_grid_reader(uid)

    # ---- Single reader ----
    def _start_single_reader(self, uid):
        if self._clearing or self._shutting_down:
            return
        self._single_last_used[uid] = time.monotonic()

        if uid in self.single_readers:
            r = self.single_readers[uid]
            if r.is_ready():
                return
            attempt = getattr(r, "_attempt", 0)
            elapsed = r.created_elapsed() if hasattr(r, "created_elapsed") else 0
            if r.is_alive() and attempt > 1:
                self._force_stop_single_reader(uid)
            elif r.is_alive() and elapsed > STUCK_READER_SEC:
                self._force_stop_single_reader(uid)
            elif r.is_alive():
                return
            else:
                self._force_stop_single_reader(uid)

        if len(self.single_readers) >= self.MAX_PREWARM_SINGLE_READERS:
            self._evict_oldest_single_reader()

        cam = self.cam_manager.get(uid)
        if cam is None:
            return

        ready_state = self._mediamtx_path_ready(cam, cam.live_profile_id)
        if ready_state is None or not ready_state:
            cnt = self._mediamtx_retry.get(uid, 0) + 1
            self._mediamtx_retry[uid] = cnt
            if cnt > self._max_mediamtx_retry:
                self._mediamtx_retry.pop(uid, None)
                if self._pending_switch == uid:
                    self._pending_switch = None
                return
            QTimer.singleShot(500, lambda u=uid: self._start_single_reader(u))
            return
        self._mediamtx_retry.pop(uid, None)

        url = self._build_stream_url(cam, cam.live_profile_id)
        if not url:
            return
        out_w, out_h = self._compute_output_size_for(cam, "single")
        fps = self._compute_fps_for(cam, "single")
        transport = get_live_transport()
        print(f"[live-single] {cam.name or uid}: "
              f"fps={fps}  size={out_w}x{out_h}  transport={transport}")

        reader = LiveReader(uid, url, target_fps=fps,
                            out_w=out_w, out_h=out_h, role="single",
                            transport=transport)
        try:
            if self._current_gs_trace is not None:
                reader.trace = self._current_gs_trace
                reader.trace_label = "GRID_SINGLE"
            elif self._prewarm_trace is not None:
                reader.trace = self._prewarm_trace
                reader.trace_label = "PREWARM"
        except Exception:
            pass
        reader.frame_ready.connect(
            lambda u, p, r=reader: self._on_single_frame(u, p, r))
        reader.status.connect(self._on_status)
        reader.finished.connect(
            lambda u=uid, r=reader: self._on_single_reader_finished(u, r))
        reader.start()
        self.single_readers[uid] = reader

    def _release_single_reader(self, uid):
        if uid not in self.single_readers:
            return
        self._single_last_used[uid] = time.monotonic()

    def _force_stop_single_reader(self, uid):
        r = self.single_readers.pop(uid, None)
        if r is None:
            return
        self._single_ready_uids.discard(uid)
        self._single_ready_walltime.pop(uid, None)
        self._single_last_used.pop(uid, None)
        self._mediamtx_retry.pop(uid, None)
        self._retire_reader(r)

    def _force_stop_all_single_readers(self):
        for uid in list(self.single_readers.keys()):
            self._force_stop_single_reader(uid)

    def _on_single_reader_finished(self, uid, reader):
        if self.single_readers.get(uid) is not reader:
            return
        print(f"[live] single reader thread finished: {uid}")
        if self._pending_switch == uid:
            self._pending_switch = None
        self._single_ready_uids.discard(uid)
        self._single_ready_walltime.pop(uid, None)

    def _check_single_idle(self):
        if self._shutting_down or self._clearing:
            return
        now = time.monotonic()
        for uid in list(self.single_readers.keys()):
            r = self.single_readers[uid]
            try:
                alive = r.is_alive()
            except Exception:
                alive = False
            if not alive:
                if self._pending_switch == uid:
                    self._pending_switch = None
                self._force_stop_single_reader(uid)
        to_stop = []
        for uid in list(self.single_readers.keys()):
            if self._fullscreen_uid == uid:
                continue
            if self._pending_switch == uid:
                continue
            last = self._single_last_used.get(uid, 0)
            if now - last > self.SINGLE_IDLE_TIMEOUT_SEC:
                to_stop.append(uid)
        for uid in to_stop:
            print(f"[live] idle timeout → killing single reader {uid}")
            self._force_stop_single_reader(uid)

    def _evict_oldest_single_reader(self):
        if not self.single_readers:
            return
        candidates = [
            (uid, t) for uid, t in self._single_last_used.items()
            if uid in self.single_readers
            and uid != self._fullscreen_uid
            and uid != self._pending_switch
        ]
        if not candidates:
            return
        candidates.sort(key=lambda x: x[1])
        self._force_stop_single_reader(candidates[0][0])

    # ---- Frame handlers ----
    def _on_grid_frame(self, uid, payload, reader):
        if self.grid_readers.get(uid) is not reader:
            return
        try:
            t = self._grid_traces.pop(uid, None)
            if t is not None:
                t.mark("FIRST_GRID_FRAME_DELIVERED")
                t.end("GRID_USABLE")
        except Exception:
            pass
        cell_idx = self.uid_to_cell.get(uid)
        if cell_idx is None:
            return
        if self.cell_source.get(cell_idx, "grid") == "grid":
            self.grid.set_frame(cell_idx, payload)

    def _on_single_frame(self, uid, payload, reader):
        if self.single_readers.get(uid) is not reader:
            return
        self._single_last_used[uid] = time.monotonic()
        cell_idx = self.uid_to_cell.get(uid)
        if cell_idx is None:
            return
        if uid not in self._single_ready_uids:
            self._single_ready_uids.add(uid)
            self._single_ready_walltime[uid] = datetime.datetime.now()
            try:
                if self._prewarm_trace is not None and self._prewarm_trace_uid == uid:
                    self._prewarm_trace.mark("PREWARM_COMPLETE")
                    self._prewarm_trace.end("PREWARM_DONE")
                    self._prewarm_trace = None
                    self._prewarm_trace_uid = None
            except Exception:
                pass
        if self._pending_switch == uid:
            self._pending_switch = None
            self.cell_source[cell_idx] = "single"
            self.grid.set_frame(cell_idx, payload)
            print(f"[live] upgraded to single quality for {uid}")
            return
        if self.cell_source.get(cell_idx) == "single":
            self.grid.set_frame(cell_idx, payload)

    def _on_status(self, uid, text):
        self._statuses[uid] = text
        idx = self.uid_to_cell.get(uid)
        if idx is not None:
            self.grid.set_status(idx, text)
        self._refresh_status_list()

    # ---- Prewarm ----
    def _on_cell_hover_entered(self, idx):
        if self._shutting_down or self._clearing:
            return
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return
        # ★ Phase 1.2: بعد از exit fullscreen، 1.5s صبر کن
        if (uid == self._last_exit_uid and
                (time.monotonic() - self._last_exit_time) < EXIT_GRACE_SEC):
            return
        r = self.single_readers.get(uid)
        if r is not None and r.is_alive():
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        if cam.live_profile_id == cam.grid_profile_id:
            return
        self._prewarm_idx = idx
        self._prewarm_timer.start(self.PREWARM_DELAY_MS)

    def _on_cell_hover_left(self, idx):
        if self._prewarm_idx == idx:
            self._prewarm_timer.stop()
            self._prewarm_idx = None

    def _do_prewarm(self):
        if self._shutting_down or self._clearing:
            return
        idx = self._prewarm_idx
        self._prewarm_idx = None
        if idx is None:
            return
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return
        r = self.single_readers.get(uid)
        if r is not None and r.is_alive():
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        if cam.live_profile_id == cam.grid_profile_id:
            return
        print(f"[live] hover-prewarm for {cam.name or uid}")
        self._start_single_reader(uid)

    def _prewarm_initial_cells(self):
        return

    # ---- Cell interactions ----
    def _on_cell_clicked(self, idx):
        if self._shutting_down or self._clearing:
            return
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return
        r = self.single_readers.get(uid)
        if r is not None and r.is_alive():
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        if cam.live_profile_id == cam.grid_profile_id:
            return
        if self._prewarm_idx == idx and self._prewarm_timer.isActive():
            return
        print(f"[live] single-click prewarm for {cam.name or uid}")
        self._start_single_reader(uid)

    def _on_cell_double_clicked(self, idx):
        if self._clearing:
            return
        if self.grid.is_fullscreen():
            self._exit_fullscreen_mode()
        else:
            self._enter_fullscreen_mode(idx)

    def _enter_fullscreen_mode(self, idx):
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return
        self._fullscreen_uid = uid
        self.grid.toggle_fullscreen_cell(idx)

        showed = False
        r = self.single_readers.get(uid)
        if r is not None:
            latest = r.get_latest_frame()
            if latest is not None:
                self.grid.set_frame(idx, (r.get_latest_frame_seq(), latest))
                showed = True
        if not showed:
            gr = self.grid_readers.get(uid)
            if gr is not None:
                g = gr.get_latest_frame()
                if g is not None:
                    self.grid.set_frame(idx, (gr.get_latest_frame_seq(), g))
                    showed = True
                    print(f"[live] fullscreen: instant show (grid frame) for {uid}")

        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        if cam.live_profile_id == cam.grid_profile_id:
            self.cell_source[idx] = "single"
            return
        if uid in self.single_readers and uid in self._single_ready_uids:
            self.cell_source[idx] = "single"
            return
        self._pending_switch = uid
        self._start_single_reader(uid)

    def _exit_fullscreen_mode(self):
        uid = self._fullscreen_uid
        if uid:
            cell_idx = self.uid_to_cell.get(uid)
            if cell_idx is not None:
                self.cell_source[cell_idx] = "grid"
            r = self.single_readers.get(uid)
            if r is not None:
                print(f"[live] exit fullscreen → killing single reader {uid}")
                self._force_stop_single_reader(uid)
        self._fullscreen_uid = None
        self._pending_switch = None
        self._last_exit_uid = uid
        self._last_exit_time = time.monotonic()
        self.grid.exit_fullscreen()

    def _on_cell_close(self, idx):
        uid = self.cell_to_uid.pop(idx, None)
        if uid:
            other = [i for i, u in self.cell_to_uid.items() if u == uid]
            if not other:
                self.uid_to_cell.pop(uid, None)
                self._statuses.pop(uid, None)
                self._stop_grid_reader(uid)
                self._release_single_reader(uid)
        self.cell_source.pop(idx, None)
        self.grid.clear_cell(idx)
        self.grid.set_uid(idx, "")
        self.grid.set_title(idx, "")
        self.grid.set_status(idx, "")
        self._save_state_to_disk()

    def _on_cell_drop_camera(self, idx, uid):
        if self._clearing:
            return
        self._assign_camera_to_cell(idx, uid, start=True)

    def _on_cell_swap(self, src_idx, dst_idx):
        cells = self.grid.get_cells()
        if not (0 <= src_idx < len(cells) and 0 <= dst_idx < len(cells)):
            return
        if src_idx == dst_idx:
            return
        ca = cells[src_idx]
        cb = cells[dst_idx]
        sa = ca.get_state()
        sb = cb.get_state()
        ca.set_state(sb)
        cb.set_state(sa)
        uid_a = sa.get("uid")
        uid_b = sb.get("uid")
        if uid_a:
            self.uid_to_cell[uid_a] = dst_idx
        if uid_b:
            self.uid_to_cell[uid_b] = src_idx
        cs_a = self.cell_source.get(src_idx, "grid")
        cs_b = self.cell_source.get(dst_idx, "grid")
        self.cell_source[src_idx] = cs_b
        self.cell_source[dst_idx] = cs_a
        self.cell_to_uid.clear()
        for uid, cidx in self.uid_to_cell.items():
            self.cell_to_uid[cidx] = uid
        self._refresh_status_list()
        self._save_state_to_disk()

    def _reset_all_zooms(self):
        self.grid.reset_all_zooms()

    # ---- Snapshot / copy ----
    def _take_snapshot(self, idx):
        cells = self.grid.get_cells()
        if not (0 <= idx < len(cells)):
            return
        cell = cells[idx]
        frame = cell._frame
        if frame is None:
            dlg_info(self, "فریمی برای ذخیره وجود ندارد.")
            return
        try:
            import numpy as np
            from PySide6.QtGui import QImage
            snap_dir = Path(config.RECORDINGS_DIR) / "snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            uid = self.cell_to_uid.get(idx) or "cell"
            cam = self.cam_manager.get(uid)
            cam_name = cam.name if cam else uid
            safe_name = "".join(
                ch if ch.isalnum() or ch in "-_ " else "_"
                for ch in cam_name)[:40].strip() or "snap"
            out = snap_dir / f"live_{safe_name}_{ts}.jpg"
            vh, vw = frame.shape[:2]
            rgb = np.ascontiguousarray(frame)
            img = QImage(rgb.data, vw, vh, vw * 3, QImage.Format_RGB888)
            img.save(str(out), "JPG", 92)
            dlg_info(self, f"ذخیره شد:\n{out.name}", "Snapshot")
        except Exception as e:
            dlg_warning(self, f"خطا در ذخیره عکس:\n{e}")

    def _copy_url(self, cam):
        try:
            url = self._build_stream_url(cam, cam.live_profile_id)
            QApplication.clipboard().setText(url)
            dlg_info(self, f"URL کپی شد:\n{redact_url(url)}", "کپی")
        except Exception as e:
            dlg_warning(self, f"خطا:\n{e}")

    # ---- Bulk ----
    def _on_cam_double_clicked(self, item):
        if self._clearing:
            return
        uid = item.data(Qt.UserRole)
        if not uid:
            return
        idx = self.grid.get_active_index()
        self._assign_camera_to_cell(idx, uid, start=True)

    def _connect_all(self):
        if self._clearing:
            return
        cams = self.cam_manager.all()
        cells = self.grid.get_cells()
        for i, c in enumerate(cams):
            if i >= len(cells):
                break
            self._assign_camera_to_cell(i, c.uid, start=True, save=False)
        self._save_state_to_disk()

    def _on_clear(self):
        if self._clearing:
            return
        self._clearing = True
        try:
            try:
                self._prewarm_timer.stop()
            except Exception:
                pass
            self._prewarm_idx = None

            if self.grid.is_fullscreen():
                self._fullscreen_uid = None
                self._pending_switch = None
                self.grid.exit_fullscreen()

            grid_list = list(self.grid_readers.values())
            single_list = list(self.single_readers.values())
            self.grid_readers.clear()
            self.single_readers.clear()
            self.cell_to_uid.clear()
            self.uid_to_cell.clear()
            self.cell_source.clear()
            self._statuses.clear()
            self._mediamtx_retry.clear()
            self._single_ready_uids.clear()
            self._single_ready_walltime.clear()
            self._single_last_used.clear()
            self._grid_traces.clear()
            self._fullscreen_uid = None
            self._pending_switch = None

            n = len(self.grid.get_cells())
            for i in range(n):
                self.grid.clear_cell(i)
                self.grid.set_uid(i, "")
                self.grid.set_title(i, "")
                self.grid.set_status(i, "")

            # ★ Phase 1.2: توقف با wait کوتاه
            all_readers = grid_list + single_list
            for r in all_readers:
                try:
                    r.stop()
                except Exception:
                    pass
            for r in all_readers:
                try:
                    if not r.wait(150):
                        self._dying_readers.append(r)
                except Exception:
                    pass

            self._refresh_status_list()
            self._save_state_to_disk()
            print(f"[live] cleared all tiles "
                  f"({len(grid_list)} grid + {len(single_list)} single)")
        finally:
            self._clearing = False

    def _refresh_status_list(self):
        self.status_list.clear()
        for uid, txt in self._statuses.items():
            cam = self.cam_manager.get(uid)
            name = cam.name if cam else uid
            self.status_list.addItem(f"{name}: {txt}")

    # ---- Lifecycle ----
    def on_show(self):
        try:
            self.cam_manager.reload()
        except Exception:
            pass
        self._load_cameras()

    def on_hide(self):
        pass

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        try:
            self._single_idle_timer.stop()
        except Exception:
            pass
        try:
            self._prewarm_timer.stop()
        except Exception:
            pass
        try:
            self._gc_timer.stop()
        except Exception:
            pass

        try:
            self._save_state_to_disk()
        except Exception:
            pass

        grid_list = list(self.grid_readers.values())
        single_list = list(self.single_readers.values())
        dying_list = list(self._dying_readers)
        self.grid_readers.clear()
        self.single_readers.clear()
        self._dying_readers.clear()

        all_readers = grid_list + single_list + dying_list

        for r in all_readers:
            try:
                r.stop()
            except Exception:
                pass

        # ★ Phase 1.2: wait بدون processEvents
        deadline_total = 4000
        per_reader = max(120, deadline_total // max(1, len(all_readers)))
        for r in all_readers:
            try:
                r.wait(per_reader)
            except Exception:
                pass

        self.cell_to_uid.clear()
        self.uid_to_cell.clear()
        self.cell_source.clear()
        self._statuses.clear()
        self._single_ready_uids.clear()
        self._single_ready_walltime.clear()
        self._single_last_used.clear()
        self._mediamtx_retry.clear()
        self._grid_traces.clear()

    def _load_cameras(self):
        self.cam_list.blockSignals(True)
        self.cam_list.clear()
        for c in self.cam_manager.all():
            item = QListWidgetItem(f"📷  {c.name or c.uid}")
            item.setData(Qt.UserRole, c.uid)
            self.cam_list.addItem(item)
        self.cam_list.blockSignals(False)

    def _restore_state(self):
        self._restoring = True
        try:
            state = self._load_state_from_disk()
            if not state:
                self._apply_layout(self.DEFAULT_LAYOUT, save=False)
                self._initial_fill_done = True
                return
            layout = state.get("layout") or self.DEFAULT_LAYOUT
            assignments = state.get("assignments") or []
            from ui.widgets.live_grid import get_layout
            L = get_layout(layout)
            if L["key"] != layout:
                layout = self.DEFAULT_LAYOUT
                assignments = []
            self._apply_layout(layout, save=False)
            n = len(self.grid.get_cells())
            valid_uids = {c.uid for c in self.cam_manager.all()}
            target = list(assignments)[:n]
            while len(target) < n:
                target.append("")
            for i, uid in enumerate(target):
                if uid and uid in valid_uids:
                    self._assign_camera_to_cell(i, uid, start=True, save=False)
            self._initial_fill_done = True
        finally:
            self._restoring = False
            self._save_state_to_disk()

    def _fill_empty_cells(self, save=True):
        return