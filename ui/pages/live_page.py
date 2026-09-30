# -*- coding: utf-8 -*-
"""K1 VMS — Live View page (Phase 6.7.6: safe signal management)"""
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
from ui.activity_bus import ActivityBus
from ui.loading_texts import t as t_load

from ui.pages.live_helpers import (
    parse_size, redact_url, build_stream_url,
    compute_output_size_for, compute_fps_for,
    query_mediamtx_state, collect_prewarm_state,
    mediamtx_path_ready, get_live_transport,
    resolve_single_profile_id,
)
from ui.pages.live_context_mixin import LiveContextMenuMixin
from ui.pages.live_persist_mixin import LivePersistenceMixin

from core.camera_manager import CameraManager
from core.live_reader import LiveReader
from core.nvr_engine import _camera_path_name
import config


STATE_FILE = config.DATA_DIR / "live_state.json"

PREWARM_COALESCE_MS = getattr(config, "LIVE_PREWARM_COALESCE_MS", 600)
PREWARM_STAGGER_MS = getattr(config, "LIVE_PREWARM_STAGGER_MS", 200)
PREWARM_STUCK_SEC = getattr(config, "LIVE_PREWARM_STUCK_SEC", 30)
PREWARM_CONCURRENCY = getattr(config, "LIVE_PREWARM_CONCURRENCY", 3)
PREWARM_TOP_N = getattr(config, "LIVE_PREWARM_TOP_N", 8)


class LivePage(BasePage, LiveContextMenuMixin, LivePersistenceMixin):
    PAGE_KEY = "live"
    PAGE_TITLE = "Live"
    HAS_PANEL = False

    DEFAULT_LAYOUT = "2x2"
    SINGLE_IDLE_TIMEOUT_SEC = getattr(config, "LIVE_SINGLE_IDLE_SEC", 600)
    IDLE_CHECK_INTERVAL_MS = 5000
    MAX_PREWARM_SINGLE_READERS = getattr(
        config, "LIVE_MAX_SINGLE_READERS", 8)
    STATE_FILE = STATE_FILE

    FOCUS_DELAY_MS = 300
    FOCUS_RESUME_STAGGER_MS = 20

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

        self._clear_epoch = 0

        self._prewarm_queue = []
        self._prewarm_in_flight = {}
        self._prewarm_started_at = {}
        self._switch_wait_started_at = {}

        self._prewarm_coalesce_timer = QTimer(self)
        self._prewarm_coalesce_timer.setSingleShot(True)
        self._prewarm_coalesce_timer.timeout.connect(
            self._prewarm_visible_cameras)

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
        self._page_suspended = False

        self._mediamtx_retry = {}
        self._max_mediamtx_retry = 8

        self._activity_timer = QTimer(self)
        self._activity_timer.timeout.connect(self._check_activity)
        self._activity_timer.start(400)
        self._last_activity_text = ""

        self._focus_paused = False
        self._focus_paused_uids = set()
        self._focus_frames_cache = {}
        self._focus_delay_timer = None

        self._focus_resume_started = None
        self._focus_resume_total = 0
        self._focus_resume_done = 0

        self._build()
        self._load_cameras()
        self._restore_state()

    # ============================================================
    def _retire_reader(self, reader):
        if reader is None:
            return
        try:
            reader.stop()
            reader.deleteLater()
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
        self.grid.cell_hover_entered.connect(self._on_cell_hover_entered)
        self.grid.cell_double_clicked.connect(self._on_cell_double_clicked)
        self.grid.cell_close.connect(self._on_cell_close)
        self.grid.cell_context.connect(self._on_cell_context)
        self.grid.cell_drop_camera.connect(self._on_cell_drop_camera)
        self.grid.cell_swap.connect(self._on_cell_swap)
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
            "•  Drag دوربین/کاشی\n"
            "•  💡 در حالت تکی، CPU برای دوربین فعال آزاد می‌شود")
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

    def _build_stream_url(self, cam, profile_id):
        return build_stream_url(cam, profile_id, self.nvr)

    def _compute_output_size_for(self, cam, role):
        return compute_output_size_for(cam, role)

    def _compute_fps_for(self, cam, role, n_cells=1):
        return compute_fps_for(cam, role, n_cells)

    def _mediamtx_path_ready(self, cam, profile_id):
        return mediamtx_path_ready(self.nvr, cam, profile_id)

    def _pick_transport(self, role, cam=None):
        try:
            if cam is not None:
                if role == "single":
                    cam_pref = getattr(cam, "live_transport", "auto")
                else:
                    cam_pref = getattr(cam, "grid_transport", "auto")
                if cam_pref == "tcp":
                    return "tcp"
                if cam_pref == "udp":
                    return "udp"

            if role == "single":
                mode = getattr(config, "LIVE_SINGLE_FORCE_TCP", "auto")
            else:
                mode = getattr(config, "LIVE_GRID_FORCE_TCP", "auto")

            if mode is True or mode == "tcp":
                return "tcp"
            if mode is False or mode == "udp":
                return "udp"

            if role == "single":
                return "tcp"

            if cam is not None:
                try:
                    pid = cam.grid_profile_id
                    p = cam.get_profile_by_id(pid) if pid else None
                    bitrate = int(
                        getattr(p, "bitrate_kbps", 0) or 0
                    ) if p else 0
                    threshold = int(getattr(
                        config, "LIVE_GRID_TCP_BITRATE_THRESHOLD", 2000))
                    if bitrate >= threshold:
                        return "tcp"
                except Exception:
                    pass

            return get_live_transport()
        except Exception:
            return get_live_transport()

    def _check_activity(self):
        if self._shutting_down or self._clearing:
            return

        active = False
        text = ""

        if getattr(self, "_focus_resume_total", 0) > 0:
            done = getattr(self, "_focus_resume_done", 0)
            total = self._focus_resume_total
            active = True
            text = f"بازگشت به شبکه… ({done}/{total})"
        elif self._pending_switch:
            uid = self._pending_switch
            cam = self.cam_manager.get(uid)
            name = cam.name if cam else "دوربین"
            active = True
            text = t_load("live_switch_single", camera=name)
        elif self._prewarm_in_flight:
            n = len(self._prewarm_in_flight)
            active = True
            if n == 1:
                uid = next(iter(self._prewarm_in_flight.keys()))
                cam = self.cam_manager.get(uid)
                name = cam.name if cam else "دوربین"
                text = t_load("live_prewarm", camera=name)
            else:
                text = t_load("live_prewarm_n", count=n)
        else:
            loading = 0
            first_name = ""
            for uid, txt in self._statuses.items():
                if txt and txt not in (
                    "online", "connected", "متصل", "stopped"
                ):
                    loading += 1
                    if not first_name:
                        cam = self.cam_manager.get(uid)
                        first_name = cam.name if cam else uid
            if loading > 0:
                active = True
                if loading == 1:
                    text = t_load("live_connect", camera=first_name)
                else:
                    text = t_load("live_loading_n", count=loading)

        if self._clearing:
            active = True
            text = t_load("live_clear")

        try:
            bus = ActivityBus.instance()
            if active:
                if text != self._last_activity_text:
                    bus.show(text)
                    self._last_activity_text = text
            else:
                if self._last_activity_text:
                    bus.hide()
                    self._last_activity_text = ""
        except Exception:
            pass

    def _on_layout_selected(self, key):
        if self.grid.is_fullscreen():
            self._exit_fullscreen_mode()
        self._apply_layout(key)

    def _apply_layout(self, key, save=True):
        old_cell_uids = []
        if self.cell_to_uid:
            for i in range(max(self.cell_to_uid.keys()) + 1):
                old_cell_uids.append(self.cell_to_uid.get(i))

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
            if not uid or uid not in self.grid_readers:
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

        self._schedule_prewarm_visible()

        if save:
            self._save_state_to_disk()

    def _schedule_prewarm_visible(self):
        if PREWARM_TOP_N > 0:
            self._prewarm_coalesce_timer.start(PREWARM_COALESCE_MS)

    def _prewarm_visible_cameras(self):
        if self._shutting_down or self._clearing:
            return

        visible = []
        for idx in range(len(self.grid.get_cells())):
            uid = self.cell_to_uid.get(idx)
            if not uid or uid in visible:
                continue
            visible.append(uid)

        if self._fullscreen_uid and self._fullscreen_uid in visible:
            visible.remove(self._fullscreen_uid)
            visible.insert(0, self._fullscreen_uid)

        if PREWARM_TOP_N <= 0:
            return
        visible = visible[:PREWARM_TOP_N]

        queue = []
        for uid in visible:
            if uid in self._single_ready_uids:
                continue
            if uid in self.single_readers:
                continue
            if uid in self._prewarm_in_flight:
                continue
            cam = self.cam_manager.get(uid)
            if cam is None:
                continue
            if cam.live_profile_id == cam.grid_profile_id:
                continue
            queue.append(uid)

        self._prewarm_queue = queue
        if queue:
            print(f"[prewarm] visible-cameras: {len(queue)} queued "
                  f"(topN={PREWARM_TOP_N} concurrency={PREWARM_CONCURRENCY})")
        self._drain_prewarm_queue()

    def _drain_prewarm_queue(self, _epoch=None):
        if _epoch is not None and _epoch != self._clear_epoch:
            return
        if self._shutting_down or self._clearing:
            self._prewarm_queue = []
            return

        while len(self._prewarm_in_flight) < PREWARM_CONCURRENCY:
            if not self._prewarm_queue:
                return

            if len(self.single_readers) >= self.MAX_PREWARM_SINGLE_READERS:
                if not self._evict_lru_single_reader():
                    print("[prewarm] cache full; keeping existing readers and dropping remainder")
                    self._prewarm_queue = []
                    return

            uid = self._prewarm_queue.pop(0)

            if uid in self.single_readers:
                continue
            if uid in self._prewarm_in_flight:
                continue
            cam = self.cam_manager.get(uid)
            if cam is None:
                continue
            if cam.live_profile_id == cam.grid_profile_id:
                continue

            now = time.monotonic()
            self._prewarm_started_at[uid] = now
            self._prewarm_in_flight[uid] = now
            print(f"[PREWARM] BEGIN uid={uid} "
                  f"in_flight={len(self._prewarm_in_flight)}")
            self._start_single_reader(uid, _epoch=self._clear_epoch)

    def _on_prewarm_reader_ready(self, uid):
        if uid not in self._prewarm_in_flight:
            pf = self._prewarm_started_at.pop(uid, None)
            if pf is not None:
                elapsed = (time.monotonic() - pf) * 1000
                print(f"[PREWARM] READY uid={uid} "
                      f"prewarm_elapsed_ms={elapsed:.0f}")
            e = self._clear_epoch
            QTimer.singleShot(PREWARM_STAGGER_MS,
                              lambda: self._drain_prewarm_queue(e))
            return
        t0 = self._prewarm_in_flight.pop(uid, None)
        elapsed = (time.monotonic() - t0) * 1000 if t0 else 0
        print(f"[PREWARM] READY uid={uid} "
              f"prewarm_elapsed_ms={elapsed:.0f}")
        self._prewarm_started_at.pop(uid, None)
        e = self._clear_epoch
        QTimer.singleShot(PREWARM_STAGGER_MS,
                          lambda: self._drain_prewarm_queue(e))

    def _evict_lru_single_reader(self):
        if not self.single_readers:
            return False
        visible_uids = set(self.cell_to_uid.values())
        candidates = [
            (uid, t) for uid, t in self._single_last_used.items()
            if uid in self.single_readers
            and uid not in visible_uids
            and uid != self._fullscreen_uid
            and uid != self._pending_switch
            and uid not in self._prewarm_in_flight
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda x: x[1])
        oldest = candidates[0][0]
        print(f"[prewarm] LRU evict uid={oldest}")
        self._force_stop_single_reader(oldest)
        return True

    def _assign_camera_to_cell(self, cell_idx, uid, start=True, save=True):
        if self._clearing or self._shutting_down:
            return
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
            self._start_grid_reader(uid, _epoch=self._clear_epoch)
        if save:
            self._save_state_to_disk()

    def _start_grid_reader(self, uid, _epoch=None):
        if _epoch is not None and _epoch != self._clear_epoch:
            return
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
                return
            e = self._clear_epoch
            QTimer.singleShot(700,
                              lambda u=uid: self._start_grid_reader(u, _epoch=e))
            return
        self._mediamtx_retry.pop(uid, None)
        url = self._build_stream_url(cam, cam.grid_profile_id)
        if not url:
            return
        n = max(1, len(self.grid.get_cells()))
        out_w, out_h = self._compute_output_size_for(cam, "grid")
        fps = self._compute_fps_for(cam, "grid", n_cells=n)
        transport = self._pick_transport("grid", cam=cam)
        print(f"[live-grid] {cam.name or uid}: fps={fps} "
              f"size={out_w}x{out_h} transport={transport}")
        reader = LiveReader(uid, url, target_fps=fps,
                            out_w=out_w, out_h=out_h, role="grid",
                            transport=transport)
        reader.frame_ready.connect(
            lambda u, p, r=reader: self._on_grid_frame(u, p, r))
        reader.status.connect(self._on_status)
        reader.start()
        self.grid_readers[uid] = reader

    def _stop_grid_reader(self, uid):
        r = self.grid_readers.pop(uid, None)
        if r is None:
            return
        self._retire_reader(r)

    def _start_single_reader(self, uid, _epoch=None):
        if _epoch is not None and _epoch != self._clear_epoch:
            return
        if self._clearing or self._shutting_down:
            return
        if uid in self.single_readers:
            return
        self._single_last_used[uid] = time.monotonic()

        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        ready_state = self._mediamtx_path_ready(cam, cam.live_profile_id)
        if ready_state is None or not ready_state:
            cnt = self._mediamtx_retry.get(uid, 0) + 1
            self._mediamtx_retry[uid] = cnt
            if cnt > self._max_mediamtx_retry:
                self._mediamtx_retry.pop(uid, None)
                return
            e = self._clear_epoch
            QTimer.singleShot(500,
                              lambda u=uid: self._start_single_reader(u, _epoch=e))
            return
        self._mediamtx_retry.pop(uid, None)

        pid = resolve_single_profile_id(cam)
        url = self._build_stream_url(cam, pid)
        if not url:
            return

        p = cam.get_profile_by_id(pid)
        if p and p.width and p.height:
            out_w, out_h = p.width, p.height
            if out_w > 1280 or out_h > 720:
                out_w, out_h = 1280, 720
        else:
            out_w, out_h = self._compute_output_size_for(cam, "single")

        fps = self._compute_fps_for(cam, "single")
        transport = self._pick_transport("single", cam=cam)
        print(f"[live-single] {cam.name or uid}: fps={fps} "
              f"size={out_w}x{out_h} transport={transport}")

        reader = LiveReader(uid, url, target_fps=fps,
                            out_w=out_w, out_h=out_h, role="single",
                            transport=transport)
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
        self._single_ready_uids.discard(uid)
        self._single_ready_walltime.pop(uid, None)
        self._single_last_used.pop(uid, None)
        self._mediamtx_retry.pop(uid, None)
        self._prewarm_in_flight.pop(uid, None)
        self._prewarm_started_at.pop(uid, None)
        self._switch_wait_started_at.pop(uid, None)
        if r is None:
            return
        self._retire_reader(r)

    def _on_single_reader_finished(self, uid, reader):
        if self.single_readers.get(uid) is not reader:
            return
        if self._pending_switch == uid:
            self._pending_switch = None
        if uid in self._prewarm_in_flight:
            self._prewarm_in_flight.pop(uid, None)
            self._prewarm_started_at.pop(uid, None)
            e = self._clear_epoch
            QTimer.singleShot(PREWARM_STAGGER_MS,
                              lambda: self._drain_prewarm_queue(e))
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
                if uid in self._prewarm_in_flight:
                    self._prewarm_in_flight.pop(uid, None)
                    self._prewarm_started_at.pop(uid, None)
                self._force_stop_single_reader(uid)

        for uid in list(self._prewarm_in_flight.keys()):
            t0 = self._prewarm_in_flight[uid]
            if now - t0 > PREWARM_STUCK_SEC:
                print(f"[PREWARM] STUCK uid={uid} "
                      f"elapsed={now - t0:.1f}s")
                self._prewarm_in_flight.pop(uid, None)
                self._prewarm_started_at.pop(uid, None)
                self._force_stop_single_reader(uid)
                print(f"[PREWARM] RECOVER uid={uid}")
        e = self._clear_epoch
        QTimer.singleShot(200,
                          lambda: self._drain_prewarm_queue(e))

        to_stop = []
        for uid in list(self.single_readers.keys()):
            if self._fullscreen_uid == uid:
                continue
            if self._pending_switch == uid:
                continue
            if uid in self._prewarm_in_flight:
                continue
            last = self._single_last_used.get(uid, 0)
            if now - last > self.SINGLE_IDLE_TIMEOUT_SEC:
                to_stop.append(uid)
        for uid in to_stop:
            print(f"[prewarm] idle timeout uid={uid}")
            self._force_stop_single_reader(uid)

    def _on_grid_frame(self, uid, payload, reader):
        if self._shutting_down or self._clearing:
            return
        if self.grid_readers.get(uid) is not reader:
            return
        cell_idx = self.uid_to_cell.get(uid)
        if cell_idx is None:
            return
        if self.cell_source.get(cell_idx, "grid") == "grid":
            self.grid.set_frame(cell_idx, payload)

    def _on_single_frame(self, uid, payload, reader):
        if self._shutting_down or self._clearing:
            return
        if self.single_readers.get(uid) is not reader:
            return
        self._single_last_used[uid] = time.monotonic()

        first_ready = uid not in self._single_ready_uids
        if first_ready:
            self._single_ready_uids.add(uid)
            self._single_ready_walltime[uid] = datetime.datetime.now()
            self._on_prewarm_reader_ready(uid)

        cell_idx = self.uid_to_cell.get(uid)
        if cell_idx is None:
            return

        if self._pending_switch == uid:
            self._pending_switch = None
            self.cell_source[cell_idx] = "single"
            self.grid.set_frame(cell_idx, payload)
            t0 = self._switch_wait_started_at.pop(uid, None)
            wait_ms = (time.monotonic() - t0) * 1000 if t0 else 0
            print(f"[SWITCH] FIRST_MAIN_AFTER_CLICK uid={uid} "
                  f"switch_wait_ms={wait_ms:.0f}")
            return

        if self.cell_source.get(cell_idx) == "single":
            self.grid.set_frame(cell_idx, payload)

    def _on_status(self, uid, text):
        if self._shutting_down or self._clearing:
            return
        self._statuses[uid] = text
        idx = self.uid_to_cell.get(uid)
        if idx is not None:
            self.grid.set_status(idx, text)
        self._refresh_status_list()

    # ============================================================
    # Page lifecycle. Live decoders must not compete with Playback.
    def on_hide(self):
        if self._shutting_down or self._page_suspended:
            return
        self._page_suspended = True
        self._cancel_focus_pause()
        self._fullscreen_uid = None
        self._pending_switch = None
        self._clear_epoch += 1
        self._prewarm_queue = []
        self._prewarm_in_flight.clear()
        self._prewarm_started_at.clear()
        self._switch_wait_started_at.clear()

        for uid in list(self.single_readers.keys()):
            self._force_stop_single_reader(uid)
        for uid in list(self.grid_readers.keys()):
            self._stop_grid_reader(uid)

        self._single_ready_uids.clear()
        self._single_ready_walltime.clear()
        self._reset_focus_state()
        print("[live] page hidden: all Live decoders suspended")

    def on_show(self):
        if self._shutting_down:
            return
        if self._page_suspended:
            self._page_suspended = False
            for uid in list(self.cell_to_uid.values()):
                if uid:
                    self._start_grid_reader(uid, _epoch=self._clear_epoch)
            self._schedule_prewarm_visible()
            print("[live] page shown: grid restore scheduled")
        else:
            self._schedule_prewarm_visible()

    def _on_cell_hover_entered(self, idx):
        """Prewarm the exact camera the operator is about to inspect."""
        uid = self.cell_to_uid.get(idx)
        if not uid or self._page_suspended or self._fullscreen_uid is not None:
            return
        cam = self.cam_manager.get(uid)
        if cam is None or cam.live_profile_id == cam.grid_profile_id:
            return
        if uid in self.single_readers or uid in self._prewarm_in_flight:
            return

        # Demand-driven prewarm: only prepare the camera under the cursor.
        self._prewarm_queue = [u for u in self._prewarm_queue if u == uid]
        if uid not in self._prewarm_queue:
            self._prewarm_queue.insert(0, uid)
        self._drain_prewarm_queue(self._clear_epoch)

    def _on_cell_clicked(self, idx):
        return

    def _on_cell_double_clicked(self, idx):
        if self._clearing:
            return
        if self.grid.is_fullscreen():
            self._exit_fullscreen_mode()
        else:
            self._enter_fullscreen_mode(idx)

    def _schedule_focus_pause(self):
        if not getattr(config, "LIVE_FOCUS_OPTIMIZE", True):
            return
        if self._focus_delay_timer is not None:
            try:
                self._focus_delay_timer.stop()
            except Exception:
                pass
            self._focus_delay_timer = None
        self._focus_delay_timer = QTimer(self)
        self._focus_delay_timer.setSingleShot(True)
        self._focus_delay_timer.timeout.connect(self._activate_focus_pause)
        self._focus_delay_timer.start(self.FOCUS_DELAY_MS)

    def _cancel_focus_pause(self):
        if self._focus_delay_timer is not None:
            try:
                self._focus_delay_timer.stop()
            except Exception:
                pass
            self._focus_delay_timer = None

    def _activate_focus_pause(self):
        self._focus_delay_timer = None
        if self._focus_paused:
            return
        if self._fullscreen_uid is None:
            return

        focused = self._fullscreen_uid
        self._focus_frames_cache = {}
        to_pause = []
        for uid in list(self.grid_readers.keys()):
            if uid == focused:
                continue
            to_pause.append(uid)

        if not to_pause:
            return

        for uid in to_pause:
            r = self.grid_readers.get(uid)
            if r is None:
                continue
            try:
                f = r.get_latest_frame()
                if f is not None:
                    seq = r.get_latest_frame_seq()
                    try:
                        fcopy = f.copy()
                    except Exception:
                        fcopy = f
                    self._focus_frames_cache[uid] = (seq, fcopy)
            except Exception:
                pass

        # Do NOT stop/reconnect RTSP readers. Mute Qt delivery only.
        # The decoder continues consuming the stream and keeps the latest
        # frame, so returning to grid is effectively instantaneous.
        self._focus_paused = True
        self._focus_paused_uids = set(to_pause)
        for uid in to_pause:
            r = self.grid_readers.get(uid)
            if r is not None:
                try:
                    r.set_output_enabled(False)
                except Exception:
                    pass

        print(f"[focus] paused {len(to_pause)} grid readers "
              f"(cached {len(self._focus_frames_cache)} frames, "
              f"kept focused={focused})")

    def _resume_grid_after_focus(self):
        if not self._focus_paused:
            return
        self._focus_paused = False
        uids = list(self._focus_paused_uids)
        self._focus_paused_uids.clear()
        if not uids:
            return
        if self._fullscreen_uid is not None:
            self._focus_paused = True
            self._focus_paused_uids = set(uids)
            return

        restored = 0
        for uid in uids:
            cell_idx = self.uid_to_cell.get(uid)
            r = self.grid_readers.get(uid)
            if r is not None:
                try:
                    r.set_output_enabled(True)
                except Exception:
                    pass
            if cell_idx is None:
                continue

            # Prefer the freshest decoder frame. The cached frame is only
            # a fallback for the tiny hand-off window.
            restored_frame = None
            if r is not None:
                try:
                    latest = r.get_latest_frame()
                    if latest is not None:
                        restored_frame = (r.get_latest_frame_seq(), latest)
                except Exception:
                    pass
            if restored_frame is None:
                restored_frame = self._focus_frames_cache.get(uid)
            if restored_frame is not None:
                try:
                    self.grid.set_frame(cell_idx, restored_frame)
                    restored += 1
                except Exception:
                    pass

        # No reconnect, no stagger, no FFmpeg/RTSP startup.
        # Readers have remained alive throughout Focus.
        print(f"[focus] resumed {len(uids)} grid readers "
              f"(restored {restored} cached/latest frames; no reconnect)")

        self._focus_resume_started = time.monotonic()
        self._focus_resume_total = len(uids)
        self._focus_resume_done = len(uids)
        self._focus_resume_total = 0
        self._focus_resume_done = 0
        self.status_lbl.setText("آماده")
        self._focus_frames_cache = {}

    def _start_grid_reader_with_progress(self, uid, epoch):
        try:
            self._start_grid_reader(uid, _epoch=epoch)
        finally:
            self._focus_resume_done += 1
            self._update_focus_progress()

    def _update_focus_progress(self):
        try:
            total = getattr(self, "_focus_resume_total", 0)
            done = getattr(self, "_focus_resume_done", 0)
            if total <= 0:
                return
            if done >= total:
                self._focus_resume_total = 0
                self._focus_resume_done = 0
                self.status_lbl.setText("آماده")
                return
            self.status_lbl.setText(
                f"🔄 بازگشت به شبکه… ({done}/{total})")
        except Exception:
            pass

    def _reset_focus_state(self):
        self._cancel_focus_pause()
        self._focus_paused = False
        self._focus_paused_uids.clear()
        self._focus_frames_cache.clear()
        self._focus_resume_total = 0
        self._focus_resume_done = 0
        self._focus_resume_started = None

    def _enter_fullscreen_mode(self, idx):
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return

        t0 = time.monotonic()
        print(f"[SWITCH] DOUBLE_CLICK uid={uid} idx={idx}")

        self._fullscreen_uid = uid
        self.grid.toggle_fullscreen_cell(idx)

        cam = self.cam_manager.get(uid)
        if cam is None:
            self.cell_source[idx] = "grid"
            return

        # Fullscreen is immediate; the grid reader remains the visual bridge.
        if cam.live_profile_id == cam.grid_profile_id:
            self._pending_switch = None
            print(f"[SWITCH] SAME_PROFILE uid={uid}")
            try:
                self.status_lbl.setText(
                    f"⚠ {cam.name or uid}: برای Single، "
                    f"پروفایل Live را متفاوت از Grid بگذار")
                QTimer.singleShot(
                    4000,
                    lambda: self.status_lbl.setText("آماده"))
            except Exception:
                pass
            elapsed = (time.monotonic() - t0) * 1000
            print(f"[SWITCH] END elapsed_ms={elapsed:.1f}")
            return

        r = self.single_readers.get(uid)
        showed_single = False
        if r is not None:
            print(f"[SWITCH] READER_EXISTING uid={uid}")
            if r.is_ready():
                latest = r.get_latest_frame()
                if latest is not None:
                    self.grid.set_frame(idx,
                                        (r.get_latest_frame_seq(), latest))
                    showed_single = True
                    print(f"[SWITCH] READY uid={uid}")

        if not showed_single:
            gr = self.grid_readers.get(uid)
            if gr is not None:
                g = gr.get_latest_frame()
                if g is not None:
                    self.grid.set_frame(idx, (gr.get_latest_frame_seq(), g))
                    print(f"[SWITCH] GRID_BRIDGE uid={uid}")

        if uid in self._single_ready_uids and showed_single:
            self.cell_source[idx] = "single"
            self._pending_switch = None
            self._single_last_used[uid] = time.monotonic()
            elapsed = (time.monotonic() - t0) * 1000
            print(f"[SWITCH] END elapsed_ms={elapsed:.1f}")
            return

        if uid in self._prewarm_queue:
            self._prewarm_queue.remove(uid)
            self._prewarm_queue.insert(0, uid)
            print(f"[PREWARM] PROMOTE uid={uid}")

        self.cell_source[idx] = "single"

        if uid not in self.single_readers:
            now = time.monotonic()
            self._prewarm_started_at.setdefault(uid, now)
            self._prewarm_in_flight[uid] = now
            print(f"[SWITCH] cold-start uid={uid}")
            self._start_single_reader(uid, _epoch=self._clear_epoch)

        self._pending_switch = uid
        self._switch_wait_started_at[uid] = time.monotonic()
        self._single_last_used[uid] = time.monotonic()
        elapsed = (time.monotonic() - t0) * 1000
        print(f"[SWITCH] END elapsed_ms={elapsed:.1f} (pending)")

    def _exit_fullscreen_mode(self):
        uid = self._fullscreen_uid
        if uid:
            cell_idx = self.uid_to_cell.get(uid)
            if cell_idx is not None:
                self.cell_source[cell_idx] = "grid"
            if uid in self.single_readers:
                self._single_last_used[uid] = time.monotonic()
        self._fullscreen_uid = None
        self._pending_switch = None
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
        self._schedule_prewarm_visible()

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

    def _on_cam_double_clicked(self, item):
        if self._clearing:
            return
        uid = item.data(Qt.UserRole)
        if not uid:
            return
        idx = self.grid.get_active_index()
        self._assign_camera_to_cell(idx, uid, start=True)
        self._schedule_prewarm_visible()

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
        self._schedule_prewarm_visible()

    def _on_clear(self):
        if self._clearing:
            return
        self._clearing = True
        self._clear_epoch += 1

        self._reset_focus_state()

        try:
            ActivityBus.instance().show(t_load("live_clear"))
        except Exception:
            pass

        try:
            self._prewarm_queue = []
            self._prewarm_in_flight.clear()
            self._prewarm_started_at.clear()
            self._switch_wait_started_at.clear()
            try:
                self._prewarm_coalesce_timer.stop()
            except Exception:
                pass

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
            self._fullscreen_uid = None
            self._pending_switch = None

            n = len(self.grid.get_cells())
            for i in range(n):
                self.grid.clear_cell(i)
                self.grid.set_uid(i, "")
                self.grid.set_title(i, "")
                self.grid.set_status(i, "")

            self._refresh_status_list()
            self._save_state_to_disk()

            # ★ بدون disconnect — Qt خودش هنگام stop() پاک می‌کند
            all_readers = grid_list + single_list
            for idx, r in enumerate(all_readers):
                QTimer.singleShot(idx * 25,
                                  lambda rr=r: self._retire_reader(rr))

            print(f"[live] cleared all tiles "
                  f"({len(grid_list)} grid + {len(single_list)} single)")
        except Exception as e:
            print(f"[live.clear] error: {e}")
        finally:
            QTimer.singleShot(700, self._finish_clearing)

    def _finish_clearing(self):
        self._clearing = False
        try:
            ActivityBus.instance().hide()
        except Exception:
            pass

    def _refresh_status_list(self):
        self.status_list.clear()
        for uid, txt in self._statuses.items():
            cam = self.cam_manager.get(uid)
            name = cam.name if cam else uid
            self.status_list.addItem(f"{name}: {txt}")

    def on_show(self):
        try:
            self.cam_manager.reload()
        except Exception:
            pass
        self._load_cameras()
        for idx in range(len(self.grid.get_cells())):
            uid = self.cell_to_uid.get(idx)
            if not uid:
                continue
            r = self.grid_readers.get(uid)
            if r is None or not r.is_alive():
                if r is not None:
                    self._stop_grid_reader(uid)
                if uid in self.uid_to_cell:
                    self._start_grid_reader(uid, _epoch=self._clear_epoch)
        self._schedule_prewarm_visible()

    def on_hide(self):
        pass

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        self._clear_epoch += 1
        import time as _time
        _shutdown_t0 = _time.monotonic()
        SHUTDOWN_TIMEOUT_SEC = 4.0
        print("[SHUTDOWN] BEGIN")

        self._reset_focus_state()

        self._prewarm_queue = []
        try:
            self._activity_timer.stop()
        except Exception:
            pass
        try:
            ActivityBus.instance().hide()
        except Exception:
            pass

        for t in (self._single_idle_timer,
                  self._prewarm_coalesce_timer,
                  self._gc_timer):
            try:
                t.stop()
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

        print(f"[SHUTDOWN] readers={len(all_readers)} "
              f"(grid={len(grid_list)} single={len(single_list)} "
              f"dying={len(dying_list)})")

        # ★ بدون disconnect — Qt خودش هنگام stop() پاک می‌کند

        # Phase 1: stop() با timeout کلی
        for r in all_readers:
            if _time.monotonic() - _shutdown_t0 > SHUTDOWN_TIMEOUT_SEC:
                print("[SHUTDOWN] global timeout during STOP")
                break
            try:
                print(f"[SHUTDOWN] STOP uid={r.uid} role={r.role}")
                r.stop()
            except Exception as e:
                print(f"[SHUTDOWN] STOP error: {e}")

        # Phase 2: wait() با timeout کوچکتر
        for r in all_readers:
            if _time.monotonic() - _shutdown_t0 > SHUTDOWN_TIMEOUT_SEC:
                break
            try:
                if not r.wait(800):
                    print(f"[SHUTDOWN] THREAD_TIMEOUT uid={r.uid}")
                    try:
                        r.terminate()
                        r.wait(200)
                    except Exception:
                        pass
                else:
                    print(f"[SHUTDOWN] THREAD_STOPPED uid={r.uid}")
            except Exception:
                pass

        alive_count = 0
        for r in all_readers:
            try:
                if r.isRunning():
                    alive_count += 1
                    print(f"[SHUTDOWN] ORPHAN_THREAD uid={r.uid}")
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
        self._prewarm_in_flight.clear()
        self._prewarm_started_at.clear()
        self._switch_wait_started_at.clear()

        print(f"[SHUTDOWN] COMPLETE readers_alive={alive_count} "
              f"elapsed={(_time.monotonic()-_shutdown_t0):.1f}s")

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
                    self._assign_camera_to_cell(i, uid,
                                                start=True, save=False)
            self._initial_fill_done = True
            self._schedule_prewarm_visible()
        finally:
            self._restoring = False
            self._save_state_to_disk()