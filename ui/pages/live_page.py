# -*- coding: utf-8 -*-
"""
K1 VMS — Live View page (Phase 4: persistent readers + idle lifecycle)

KEY CHANGES vs previous:
- Layout change does NOT stop single readers (only marks idle)
- Double-click reuses READY single reader instantly
- Single readers auto-released after 10 min idle
- Max 6 concurrent single readers (LRU eviction)
- Grid readers always persistent
- Idle check timer every 60s
"""
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
    mediamtx_path_ready,
)
from ui.pages.live_context_mixin import LiveContextMenuMixin
from ui.pages.live_persist_mixin import LivePersistenceMixin

from core.camera_manager import CameraManager
from core.live_reader import LiveReader
from core.nvr_engine import _camera_path_name
import config


STATE_FILE = config.DATA_DIR / "live_state.json"


class LivePage(BasePage, LiveContextMenuMixin, LivePersistenceMixin):
    PAGE_KEY = "live"
    PAGE_TITLE = "Live"
    HAS_PANEL = False

    DEFAULT_LAYOUT = "2x2"
    PREWARM_DELAY_MS = 100              # ← چک سریع‌تر برای prewarm
    SINGLE_IDLE_TIMEOUT_SEC = 600       # ← 10 دقیقه
    IDLE_CHECK_INTERVAL_MS = 60_000     # ← هر 60s چک
    MAX_PREWARM_SINGLE_READERS = 6      # ← سقف prewarm همزمان
    STATE_FILE = STATE_FILE

    def __init__(self, nvr_engine=None, parent=None):
        super().__init__(parent)
        self.cam_manager = CameraManager()
        self.nvr = nvr_engine

        # ---- Readers ----
        self.grid_readers = {}      # uid -> LiveReader (persistent)
        self.single_readers = {}    # uid -> LiveReader (persistent with idle)

        # ---- Cell mapping ----
        self.cell_to_uid = {}
        self.uid_to_cell = {}
        self.cell_source = {}       # cell_idx -> "grid" | "single"
        self._statuses = {}

        # ---- Fullscreen / switch ----
        self._fullscreen_uid = None
        self._pending_switch = None
        self._single_ready_uids = set()
        self._single_ready_walltime = {}

        # ---- Prewarm ----
        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.timeout.connect(self._do_prewarm)
        self._prewarm_idx = None

        # ---- Single reader idle lifecycle ----
        self._single_last_used = {}         # uid -> monotonic timestamp
        self._single_idle_timer = QTimer(self)
        self._single_idle_timer.timeout.connect(self._check_single_idle)
        self._single_idle_timer.start(self.IDLE_CHECK_INTERVAL_MS)

        # ---- Lifecycle ----
        self._shutting_down = False
        self._restoring = False
        self._initial_fill_done = False

        # ---- Trace ----
        self._current_gs_trace = None
        self._prewarm_trace = None
        self._prewarm_trace_uid = None
        self._prewarm_start_wall = None
        self._grid_traces = {}

        self._build()
        self._load_cameras()
        self._restore_state()

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
            f"border-bottom: 1px solid {theme.COLOR_BORDER}; }}"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)
        title = QLabel("📹  Live View")
        title.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"font-size: 14px; font-weight: 700;"
        )
        left.addWidget(title)
        subtitle = QLabel("Layout:")
        subtitle.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 10px;"
        )
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
            f"border-left: 1px solid {theme.COLOR_BORDER}; }}"
        )
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        t1 = QLabel("CAMERAS")
        t1.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 10px; font-weight: 700; letter-spacing: 1.2px;"
        )
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
            f"background: {theme.COLOR_ACCENT}; color: white; }}"
        )
        self.cam_list.itemDoubleClicked.connect(self._on_cam_double_clicked)
        v.addWidget(self.cam_list, 1)

        hint = QLabel(
            "•  دو کلیک: تمام‌صفحه (کیفیت تکی)\n"
            "•  اسکرول روی تصویر: زوم\n"
            "•  Drag دوربین/کاشی"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {theme.COLOR_TEXT_MUTED}; font-size: 10px; padding: 4px;"
        )
        v.addWidget(hint)

        t2 = QLabel("وضعیت")
        t2.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 10px; font-weight: 700; letter-spacing: 1px; "
            f"padding-top: 8px;"
        )
        v.addWidget(t2)

        self.status_list = QListWidget()
        self.status_list.setFixedHeight(140)
        self.status_list.setStyleSheet(
            f"QListWidget {{ background: {theme.COLOR_BG_DARK}; "
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {theme.COLOR_BORDER}; "
            f"border-radius: 5px; padding: 3px; "
            f"font-size: 11px; font-family: Consolas, monospace; }} "
            f"QListWidget::item {{ padding: 5px 8px; border-radius: 3px; }}"
        )
        v.addWidget(self.status_list)

        return panel

    def _build_statusbar(self):
        bar = QFrame()
        bar.setFixedHeight(24)
        bar.setStyleSheet(
            f"background: {theme.COLOR_BG_SHELL}; "
            f"border-top: 1px solid {theme.COLOR_BORDER};"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 12, 0)
        self.status_lbl = QLabel("آماده")
        self.status_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;"
        )
        h.addWidget(self.status_lbl)
        h.addStretch(1)
        self.cells_lbl = QLabel("")
        self.cells_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;"
        )
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
            f"QPushButton:hover {{ background: {theme.COLOR_BG_HOVER}; }}"
        )
        return b

    # ============================================================
    # Helper wrappers
    # ============================================================
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
            uid,
            self.single_readers,
            self._single_ready_uids,
            self._prewarm_idx,
            self._prewarm_timer.isActive(),
            self._prewarm_trace_uid,
        )

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

        # ---- DO NOT stop single readers on layout change ----
        # Just mark them as recently used; they'll be cleaned up by idle timer
        # if the user doesn't come back to them within SINGLE_IDLE_TIMEOUT_SEC.
        for uid in list(self.single_readers.keys()):
            self._single_last_used[uid] = time.monotonic()
        self._fullscreen_uid = None
        self._pending_switch = None

        self.grid.set_layout(key)
        n = len(self.grid.get_cells())
        self.cells_lbl.setText(f"کاشی‌ها: {n}")

        for i in range(n):
            self.grid.set_title(i, "")
            self.grid.set_status(i, "")
            self.grid.clear_cell(i)
            self.grid.set_uid(i, "")

        # Which UIDs are still needed for grid?
        wanted_uids = set()
        for uid in old_cell_uids[:n]:
            if uid:
                wanted_uids.add(uid)
        if self._initial_fill_done:
            for c in self.cam_manager.all():
                if len(wanted_uids) >= n:
                    break
                if c.uid not in wanted_uids:
                    wanted_uids.add(c.uid)

        # Stop grid readers that are no longer needed in current layout
        for uid in list(self.grid_readers.keys()):
            if uid not in wanted_uids:
                self._stop_grid_reader(uid)

        self.cell_to_uid.clear()
        self.uid_to_cell.clear()
        self.cell_source.clear()

        target_uids = []
        for uid in old_cell_uids:
            if uid and uid not in target_uids:
                target_uids.append(uid)
            if len(target_uids) >= n:
                break

        if self._initial_fill_done and len(target_uids) < n:
            for c in self.cam_manager.all():
                if c.uid not in target_uids:
                    target_uids.append(c.uid)
                    if len(target_uids) >= n:
                        break

        for i, uid in enumerate(target_uids[:n]):
            if uid:
                self._assign_camera_to_cell(i, uid, start=True, save=False)

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

    # ============================================================
    # Grid reader (persistent)
    # ============================================================
    def _start_grid_reader(self, uid):
        if uid in self.grid_readers:
            return
        if uid not in self.uid_to_cell:
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return

        if not self._mediamtx_path_ready(cam, cam.grid_profile_id):
            idx = self.uid_to_cell.get(uid)
            if idx is not None:
                self.grid.set_status(idx, "waiting for source…")
            QTimer.singleShot(700, lambda u=uid: self._start_grid_reader(u))
            return

        url = self._build_stream_url(cam, cam.grid_profile_id)
        if not url:
            idx = self.uid_to_cell.get(uid)
            if idx is not None:
                self.grid.set_status(idx, "no url")
            return

        n = max(1, len(self.grid.get_cells()))
        out_w, out_h = self._compute_output_size_for(cam, "grid")
        fps = self._compute_fps_for(cam, "grid", n_cells=n)

        t = None
        try:
            from core.trace import new_trace
            t = new_trace("LIVE_GRID", camera=cam.name or cam.uid)
            t.mark("GRID_READER_START", profile=cam.grid_profile_id,
                   fps=fps, size=f"{out_w}x{out_h}")
            self._grid_traces[uid] = t
        except Exception:
            t = None

        print(f"[live-grid] {cam.name or uid}: url={url}  fps={fps}  "
              f"size={out_w}x{out_h}")

        reader = LiveReader(uid, url, target_fps=fps,
                            out_w=out_w, out_h=out_h, role="grid")
        reader.trace = t
        reader.trace_label = "LIVE_GRID"
        reader.frame_ready.connect(
            lambda u, p, r=reader: self._on_grid_frame(u, p, r)
        )
        reader.status.connect(self._on_status)
        reader.start()
        self.grid_readers[uid] = reader

    def _stop_grid_reader(self, uid):
        r = self.grid_readers.pop(uid, None)
        if r is None:
            return
        try:
            r.stop()
            if not r.wait(3000):
                print(f"[live] grid reader {uid} did not stop")
        except Exception as e:
            print(f"[live] stop grid reader {uid}: {e}")

    def _stop_all_grid_readers(self):
        for uid in list(self.grid_readers.keys()):
            self._stop_grid_reader(uid)

    # ============================================================
    # Single reader (persistent with idle release)
    # ============================================================
    def _start_single_reader(self, uid):
        # Mark as recently used
        self._single_last_used[uid] = time.monotonic()

        active_trace = self._current_gs_trace or self._prewarm_trace

        def _m(event, **ctx):
            try:
                if active_trace is not None:
                    active_trace.mark(event, **ctx)
            except Exception:
                pass

        _m("SINGLE_READER_START_BEGIN", uid=uid)

        if uid in self.single_readers:
            _m("SINGLE_READER_ALREADY_EXISTS")
            return

        # Enforce MAX_PREWARM limit
        if len(self.single_readers) >= self.MAX_PREWARM_SINGLE_READERS:
            self._evict_oldest_single_reader()

        cam = self.cam_manager.get(uid)
        if cam is None:
            _m("SINGLE_READER_ABORT", reason="cam_not_found")
            return

        _m("MEDIAMTX_STREAM_CHECK_BEGIN",
           profile=cam.live_profile_id,
           path_hint=_camera_path_name(cam))

        mt_state = self._query_mediamtx_state(cam, cam.live_profile_id)
        _m("MEDIAMTX_STREAM_CHECK_END",
           queried=mt_state["queried"], path=mt_state["path_name"],
           exists=mt_state["exists"], ready=mt_state["ready"],
           source_present=mt_state["source_present"],
           readers=mt_state["readers"],
           bytes_received=mt_state["bytes_received"],
           error=mt_state["error"])

        ready_legacy = self._mediamtx_path_ready(cam, cam.live_profile_id)
        if not ready_legacy:
            _m("MEDIAMTX_NOT_READY_SCHEDULING_RETRY")
            QTimer.singleShot(500, lambda u=uid: self._start_single_reader(u))
            return

        url = self._build_stream_url(cam, cam.live_profile_id)
        if not url:
            _m("SINGLE_READER_ABORT", reason="no_url")
            return

        out_w, out_h = self._compute_output_size_for(cam, "single")
        fps = self._compute_fps_for(cam, "single")

        _m("SINGLE_READER_CREATE",
           profile=cam.live_profile_id, fps=fps,
           size=f"{out_w}x{out_h}", url=url)

        print(f"[live-single] {cam.name or uid}: url={url}  fps={fps}  "
              f"size={out_w}x{out_h}")

        reader = LiveReader(uid, url, target_fps=fps,
                            out_w=out_w, out_h=out_h, role="single")
        try:
            if self._current_gs_trace is not None:
                reader.trace = self._current_gs_trace
                reader.trace_label = "GRID_SINGLE"
            elif self._prewarm_trace is not None:
                reader.trace = self._prewarm_trace
                reader.trace_label = "PREWARM"
            else:
                reader.trace = None
        except Exception:
            pass
        reader.frame_ready.connect(
            lambda u, p, r=reader: self._on_single_frame(u, p, r)
        )
        reader.status.connect(self._on_status)
        _m("SINGLE_READER_INIT_BEGIN")
        reader.start()
        _m("SINGLE_READER_INIT_END")
        self.single_readers[uid] = reader

    def _release_single_reader(self, uid):
        """Mark single reader as idle. Does NOT stop it.
        Idle timer will clean up after timeout."""
        if uid not in self.single_readers:
            return
        self._single_last_used[uid] = time.monotonic()

    def _force_stop_single_reader(self, uid):
        """Actually stop and remove the reader immediately."""
        r = self.single_readers.pop(uid, None)
        if r is None:
            return
        self._single_ready_uids.discard(uid)
        self._single_ready_walltime.pop(uid, None)
        self._single_last_used.pop(uid, None)
        try:
            r.stop()
            if not r.wait(2000):
                print(f"[live] single reader {uid} did not stop")
        except Exception as e:
            print(f"[live] stop single reader {uid}: {e}")

    def _force_stop_all_single_readers(self):
        for uid in list(self.single_readers.keys()):
            self._force_stop_single_reader(uid)

    def _check_single_idle(self):
        """Timer callback: stop single readers idle > timeout."""
        if self._shutting_down:
            return
        now = time.monotonic()
        to_stop = []
        for uid in list(self.single_readers.keys()):
            # Never stop the one currently in fullscreen
            if self._fullscreen_uid == uid:
                continue
            # Never stop the one that's the target of a pending switch
            if self._pending_switch == uid:
                continue
            last = self._single_last_used.get(uid, 0)
            if now - last > self.SINGLE_IDLE_TIMEOUT_SEC:
                to_stop.append(uid)
        for uid in to_stop:
            print(f"[live] idle timeout ({self.SINGLE_IDLE_TIMEOUT_SEC}s): "
                  f"stopping single reader {uid}")
            self._force_stop_single_reader(uid)

    def _evict_oldest_single_reader(self):
        """When hitting MAX_PREWARM limit, kill oldest idle one."""
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
        oldest = candidates[0][0]
        print(f"[live] evicting oldest prewarm: {oldest}")
        self._force_stop_single_reader(oldest)

    # ============================================================
    # Frame handlers
    # ============================================================
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
        # Mark as recently used
        self._single_last_used[uid] = time.monotonic()
        cell_idx = self.uid_to_cell.get(uid)
        if cell_idx is None:
            return

        became_ready = False
        if uid not in self._single_ready_uids:
            self._single_ready_uids.add(uid)
            self._single_ready_walltime[uid] = datetime.datetime.now()
            became_ready = True

            # Prewarm trace: mark first frame + complete
            try:
                if self._prewarm_trace is not None and self._prewarm_trace_uid == uid:
                    self._prewarm_trace.mark(
                        "PREWARM_STREAM_READY",
                        seq=payload[0] if isinstance(payload, tuple) else None
                    )
                    self._prewarm_trace.mark("PREWARM_READER_READY")
                    self._prewarm_trace.mark("PREWARM_FIRST_FRAME")
                    self._prewarm_trace.mark("PREWARM_COMPLETE")
                    try:
                        if self._prewarm_start_wall is not None:
                            done_ms = (time.monotonic() - self._prewarm_start_wall) * 1000.0
                            self._prewarm_trace.mark("PREWARM_TOTAL_MS",
                                                     ms=f"{done_ms:.0f}")
                    except Exception:
                        pass
                    self._prewarm_trace.end("PREWARM_DONE")
                    self._prewarm_trace = None
                    self._prewarm_trace_uid = None
            except Exception:
                pass

            # GRID_SINGLE trace: mark first frame
            try:
                if self._current_gs_trace:
                    self._current_gs_trace.mark(
                        "SINGLE_READER_FIRST_FRAME", became_ready=True)
            except Exception:
                pass

        if self._pending_switch == uid:
            self._pending_switch = None
            self.cell_source[cell_idx] = "single"
            self.grid.set_frame(cell_idx, payload)
            try:
                if self._current_gs_trace:
                    self._current_gs_trace.mark("SINGLE_FRAME_RENDERED")
                    self._current_gs_trace.end("SINGLE_VIEW_USABLE")
                    self._current_gs_trace = None
            except Exception:
                pass
            print(f"[live] switched to single quality for {uid}")
            return

        if self.cell_source.get(cell_idx) == "single":
            self.grid.set_frame(cell_idx, payload)

    def _on_status(self, uid, text):
        self._statuses[uid] = text
        idx = self.uid_to_cell.get(uid)
        if idx is not None:
            self.grid.set_status(idx, text)
        self._refresh_status_list()

    # ============================================================
    # Prewarm (hover)
    # ============================================================
    def _on_cell_hover_entered(self, idx):
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        if cam.live_profile_id == cam.grid_profile_id:
            return
        if uid in self.single_readers:
            return
        self._prewarm_idx = idx
        self._prewarm_timer.start(self.PREWARM_DELAY_MS)

    def _on_cell_hover_left(self, idx):
        if self._prewarm_idx == idx:
            self._prewarm_timer.stop()
            self._prewarm_idx = None

    def _do_prewarm(self):
        idx = self._prewarm_idx
        self._prewarm_idx = None
        if idx is None:
            return
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return
        if uid in self.single_readers:
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        if cam.live_profile_id == cam.grid_profile_id:
            return

        try:
            from core.trace import new_trace
            t = new_trace("PREWARM", camera=cam.name or cam.uid)
            t.mark("PREWARM_BEGIN", uid=uid)
            self._prewarm_trace = t
            self._prewarm_trace_uid = uid
        except Exception:
            self._prewarm_trace = None
            self._prewarm_trace_uid = None

        try:
            self._prewarm_start_wall = time.monotonic()
            if self._prewarm_trace is not None:
                self._prewarm_trace.mark(
                    "PREWARM_START_WALLTIME",
                    wall=f"{self._prewarm_start_wall:.3f}"
                )
        except Exception:
            pass

        print(f"[live] prewarming single for {cam.name or uid}")
        self._start_single_reader(uid)

    # ============================================================
    # Cell interactions
    # ============================================================
    def _on_cell_clicked(self, idx):
        pass

    def _on_cell_double_clicked(self, idx):
        if self.grid.is_fullscreen():
            self._exit_fullscreen_mode()
        else:
            uid = self.cell_to_uid.get(idx)
            cam = self.cam_manager.get(uid) if uid else None
            cam_name = cam.name if cam else ""

            try:
                from core.trace import new_trace
                t = new_trace("GRID_SINGLE", camera=cam_name)
                t.mark("DOUBLE_CLICK", idx=idx)
                self._current_gs_trace = t
            except Exception:
                pass

            try:
                now = time.monotonic()
                if self._prewarm_start_wall is not None:
                    elapsed_ms = (now - self._prewarm_start_wall) * 1000.0
                    if self._current_gs_trace is not None:
                        self._current_gs_trace.mark(
                            "PREWARM_ELAPSED_AT_CLICK",
                            elapsed_ms=f"{elapsed_ms:.0f}")
                else:
                    if self._current_gs_trace is not None:
                        self._current_gs_trace.mark(
                            "PREWARM_ELAPSED_AT_CLICK",
                            elapsed_ms="no_prewarm")
            except Exception:
                pass

            self._enter_fullscreen_mode(idx)

    def _enter_fullscreen_mode(self, idx):
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return

        def _m(event, **ctx):
            try:
                if self._current_gs_trace:
                    self._current_gs_trace.mark(event, **ctx)
            except Exception:
                pass

        self._fullscreen_uid = uid
        self.grid.toggle_fullscreen_cell(idx)
        _m("FULLSCREEN_ARRANGED")

        _m("PREWARM_CHECK_BEGIN")
        state = self._collect_prewarm_state(uid)
        _m("PREWARM_CHECK_RESULT",
           reader_exists=state["reader_exists"],
           reader_alive=state["reader_alive"],
           reader_ready=state["reader_ready"],
           proc_exists=state["proc_exists"],
           proc_alive=state["proc_alive"],
           proc_pid=state["proc_pid"],
           prewarm_idx=state["prewarm_idx"],
           prewarm_timer_active=state["prewarm_timer_active"],
           prewarm_trace_uid=state["prewarm_trace_uid"],
           single_ready_count=state["single_ready_count"])

        cam = self.cam_manager.get(uid)
        if cam is None:
            _m("ABORT_CAM_NOT_FOUND")
            return

        if cam.live_profile_id == cam.grid_profile_id:
            self.cell_source[idx] = "single"
            _m("SAME_PROFILE_INSTANT")
            try:
                if self._current_gs_trace:
                    self._current_gs_trace.end("SINGLE_VIEW_USABLE")
                    self._current_gs_trace = None
            except Exception:
                pass
            print(f"[live] fullscreen same-profile — instant")
            return

        if uid in self.single_readers and uid in self._single_ready_uids:
            self.cell_source[idx] = "single"
            _m("PREWARMED_INSTANT")
            try:
                if self._current_gs_trace:
                    self._current_gs_trace.end("SINGLE_VIEW_USABLE")
                    self._current_gs_trace = None
            except Exception:
                pass
            print(f"[live] fullscreen prewarmed — instant switch for {uid}")
            return

        self._pending_switch = uid
        _m("START_SINGLE_READER")
        self._start_single_reader(uid)

    def _exit_fullscreen_mode(self):
        t = None
        try:
            from core.trace import new_trace
            uid = self._fullscreen_uid
            cam = self.cam_manager.get(uid) if uid else None
            cam_name = cam.name if cam else ""
            t = new_trace("GRID_SINGLE_EXIT", camera=cam_name)
            t.mark("EXIT_REQUESTED")
        except Exception:
            t = None
        uid = self._fullscreen_uid
        if uid:
            cell_idx = self.uid_to_cell.get(uid)
            if cell_idx is not None:
                self.cell_source[cell_idx] = "grid"
            self._release_single_reader(uid)
        self._fullscreen_uid = None
        self._pending_switch = None
        self.grid.exit_fullscreen()
        try:
            if t is not None:
                t.end("EXIT_DONE")
        except Exception:
            pass

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

    # ============================================================
    # Snapshot / copy (used by context menu)
    # ============================================================
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
                for ch in cam_name
            )[:40].strip() or "snap"
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
            dlg_info(self, f"URL کپی شد:\n{url}", "کپی")
        except Exception as e:
            dlg_warning(self, f"خطا:\n{e}")

    # ============================================================
    # Bulk actions
    # ============================================================
    def _on_cam_double_clicked(self, item):
        uid = item.data(Qt.UserRole)
        if not uid:
            return
        idx = self.grid.get_active_index()
        self._assign_camera_to_cell(idx, uid, start=True)

    def _connect_all(self):
        cams = self.cam_manager.all()
        cells = self.grid.get_cells()
        for i, c in enumerate(cams):
            if i >= len(cells):
                break
            self._assign_camera_to_cell(i, c.uid, start=True, save=False)
        self._save_state_to_disk()

    def _on_clear(self):
        if self.grid.is_fullscreen():
            self._exit_fullscreen_mode()
        for idx in list(self.cell_to_uid.keys()):
            uid = self.cell_to_uid.pop(idx, None)
            if uid:
                self.uid_to_cell.pop(uid, None)
            self.cell_source.pop(idx, None)
            self.grid.clear_cell(idx)
            self.grid.set_uid(idx, "")
            self.grid.set_title(idx, "")
            self.grid.set_status(idx, "")
        self._stop_all_grid_readers()
        self._force_stop_all_single_readers()
        self.cell_to_uid.clear()
        self.uid_to_cell.clear()
        self.cell_source.clear()
        self._statuses.clear()
        self._refresh_status_list()
        self._save_state_to_disk()
        print("[live] cleared all tiles")

    # ============================================================
    # Status
    # ============================================================
    def _refresh_status_list(self):
        self.status_list.clear()
        for uid, txt in self._statuses.items():
            cam = self.cam_manager.get(uid)
            name = cam.name if cam else uid
            self.status_list.addItem(f"{name}: {txt}")

    # ============================================================
    # Lifecycle
    # ============================================================
    def on_show(self):
        try:
            self.cam_manager.reload()
        except Exception:
            pass
        self._load_cameras()
        if not self._initial_fill_done:
            try:
                self._fill_empty_cells()
            except Exception as e:
                print(f"[live] fill empty: {e}")
            self._initial_fill_done = True

    def on_hide(self):
        # Readers remain persistent across page changes.
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
        self._save_state_to_disk()
        self._stop_all_grid_readers()
        self._force_stop_all_single_readers()

    # ============================================================
    # Cameras list
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
    # Restore state
    # ============================================================
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
        cells = self.grid.get_cells()
        n_cells = len(cells)
        all_uids = {c.uid for c in self.cam_manager.all()}
        for cell_idx in list(self.cell_to_uid.keys()):
            uid = self.cell_to_uid[cell_idx]
            if uid not in all_uids:
                self._stop_grid_reader(uid)
                self._release_single_reader(uid)
                self.uid_to_cell.pop(uid, None)
                self.cell_to_uid.pop(cell_idx, None)
                self._statuses.pop(uid, None)
                self.cell_source.pop(cell_idx, None)
                self.grid.clear_cell(cell_idx)
                self.grid.set_uid(cell_idx, "")
                self.grid.set_title(cell_idx, "")
                self.grid.set_status(cell_idx, "")
        used_cells = set(self.cell_to_uid.keys())
        empty_cells = [i for i in range(n_cells) if i not in used_cells]
        if not empty_cells:
            return
        used_uids = set(self.uid_to_cell.keys())
        new_uids = [u for u in all_uids if u not in used_uids]
        if not new_uids:
            return
        for cell_idx, uid in zip(empty_cells, new_uids):
            self._assign_camera_to_cell(cell_idx, uid, start=True, save=False)
        if save:
            self._save_state_to_disk()