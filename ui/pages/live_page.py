# -*- coding: utf-8 -*-
"""K1 VMS — Live View page (Phase 8.2: fast clear + reconnect + drop fix)"""
import os
import json
import re
import time
import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QListWidget, QListWidgetItem, QMenu, QApplication
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QAction

from ui import theme
from ui.icons import make_icon
from ui.pages import BasePage
from ui.dialogs import info as dlg_info, warning as dlg_warning, question as dlg_question
from ui.widgets.live_grid import LiveGrid, MIME_CAMERA
from ui.widgets.layout_picker import LayoutPicker
from ui.widgets.camera_list_widget import (
    CameraTreeWidget, NVR_PREFIX, GROUP_PREFIX,
)
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
from core.nvr_manager import NVRManager
from core.group_manager import GroupManager
from core.live_reader import LiveReader
import config


STATE_FILE = config.DATA_DIR / "live_state.json"
ACTIVE_FILE = config.DATA_DIR / "live_active.json"
MAX_GRID_READERS = int(getattr(config, "LIVE_GRID_MAX_READERS", 16) or 0)
GRID_OFFLINE_GRACE_SEC = 12.0


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

    def __init__(self, nvr_engine=None, parent=None):
        super().__init__(parent)
        self.cam_manager = CameraManager()
        self.nvr_manager = NVRManager.instance()
        self.group_manager = GroupManager.instance()
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
        self._drop_epoch = 0
        self._prewarm_in_flight = {}
        self._switch_wait_started_at = {}

        self._active_nvr_uid = None
        self._active_group_uid = None

        self._single_last_used = {}
        self._single_idle_timer = QTimer(self)
        self._single_idle_timer.timeout.connect(self._check_single_idle)
        self._single_idle_timer.start(self.IDLE_CHECK_INTERVAL_MS)

        self._gc_timer = QTimer(self)
        self._gc_timer.timeout.connect(self._gc_dying_readers)
        self._gc_timer.start(3000)

        self._budget_timer = QTimer(self)
        self._budget_timer.timeout.connect(self._enforce_grid_budget)
        self._budget_timer.start(2000)

        self._grid_started_at = {}
        self._grid_frame_seen = {}
        self._offline_timer = QTimer(self)
        self._offline_timer.timeout.connect(self._check_grid_offline)
        self._offline_timer.start(3000)

        self._shutting_down = False
        self._clearing = False
        self._restoring = False
        self._initial_fill_done = False
        self._page_suspended = False

        self._mediamtx_retry = {}
        self._max_mediamtx_retry = 5

        self._activity_timer = QTimer(self)
        self._activity_timer.timeout.connect(self._check_activity)
        self._activity_timer.start(400)
        self._last_activity_text = ""

        self._focus_paused = False
        self._focus_paused_uids = set()
        self._focus_frames_cache = {}
        self._focus_delay_timer = None

        self._load_active_state()
        self._build()
        self._load_cameras()
        self._restore_state()

    # ============================================================
    # Active state persistence
    # ============================================================
    def _load_active_state(self):
        try:
            if not ACTIVE_FILE.exists():
                return
            data = json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
            self._active_nvr_uid = data.get("nvr_uid") or None
            self._active_group_uid = data.get("group_uid") or None
            print(f"[live] loaded active: nvr={self._active_nvr_uid} "
                  f"group={self._active_group_uid}")
        except Exception as e:
            print(f"[live] active state load failed: {e}")

    def _save_active_state(self):
        try:
            ACTIVE_FILE.write_text(json.dumps({
                "nvr_uid": self._active_nvr_uid or "",
                "group_uid": self._active_group_uid or "",
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # ============================================================
    # Reader lifecycle
    # ============================================================
    def _retire_reader(self, reader):
        if reader is None:
            return
        for sig_name in ("frame_ready", "status", "finished"):
            try:
                sig = getattr(reader, sig_name, None)
                if sig is None:
                    continue
                try:
                    if sig.receivers() > 0:
                        sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
                except Exception:
                    pass
            except Exception:
                pass
        try:
            reader.stop()
        except Exception:
            pass
        try:
            self._dying_readers.append(reader)
        except Exception:
            pass
        try:
            QTimer.singleShot(200, lambda r=reader: self._final_delete(r))
        except Exception:
            pass

    def _final_delete(self, reader):
        try:
            if reader is None:
                return
            for sig_name in ("frame_ready", "status", "finished"):
                try:
                    sig = getattr(reader, sig_name, None)
                    if sig is None:
                        continue
                    try:
                        if sig.receivers() > 0:
                            sig.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                    except Exception:
                        pass
                except Exception:
                    pass
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
            except Exception:
                pass
        self._dying_readers = alive

    # ============================================================
    def _enforce_grid_budget(self):
        if self._shutting_down or self._clearing:
            return
        if MAX_GRID_READERS <= 0:
            return
        try:
            n_cells = len(self.grid.get_cells())
            effective_max = max(MAX_GRID_READERS, n_cells)

            active = list(self.grid_readers.keys())
            if len(active) <= effective_max:
                return

            cell_uids = set(u for u in self.cell_to_uid.values() if u)
            not_in_cells = [u for u in active if u not in cell_uids]
            to_stop = list(not_in_cells)

            in_cells = [u for u in active if u in cell_uids]
            if len(in_cells) > effective_max:
                try:
                    active_idx = self.grid.get_active_index()
                except Exception:
                    active_idx = 0

                def _dist(uid):
                    idx = self.uid_to_cell.get(uid, 0)
                    return abs(idx - active_idx)

                sorted_in = sorted(in_cells, key=_dist)
                extra = len(in_cells) - effective_max
                to_stop.extend(sorted_in[-extra:])

            stopped = 0
            for uid in to_stop:
                self._stop_grid_reader(uid)
                stopped += 1
            if stopped:
                print(f"[live] grid budget: stopped {stopped} readers "
                      f"(kept {len(self.grid_readers)})")
        except Exception as e:
            print(f"[live] budget error: {e}")

    # ============================================================
    def _check_grid_offline(self):
        if self._shutting_down or self._clearing:
            return
        now = time.monotonic()
        to_kill = []
        for uid, started_at in list(self._grid_started_at.items()):
            if uid not in self.grid_readers:
                self._grid_started_at.pop(uid, None)
                self._grid_frame_seen.pop(uid, None)
                continue
            if self._grid_frame_seen.get(uid):
                continue
            if now - started_at > GRID_OFFLINE_GRACE_SEC:
                to_kill.append(uid)
        for uid in to_kill:
            print(f"[live] grid offline (no frame {GRID_OFFLINE_GRACE_SEC}s): "
                  f"{uid}")
            self._stop_grid_reader(uid)
            self._mark_camera_offline(uid)

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
        # ★ فقط دکمه Clear (Connect All و Reset Zoom حذف شدند)
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
        self.grid.cell_reconnect.connect(self._on_cell_reconnect)
        v.addWidget(self.grid, 1)
        return center

    def _build_right_panel(self):
        panel = QFrame()
        panel.setFixedWidth(260)
        panel.setStyleSheet(
            f"QFrame {{ background: {theme.COLOR_BG_PANEL}; "
            f"border-left: 1px solid {theme.COLOR_BORDER}; }}")
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        t1 = QLabel("CAMERAS & NVR")
        t1.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 10px; font-weight: 700; letter-spacing: 1.2px;")
        head.addWidget(t1)
        head.addStretch(1)
        self.btn_groups = QPushButton("📁")
        self.btn_groups.setFixedSize(28, 24)
        self.btn_groups.setToolTip("مدیریت گروه‌ها")
        self.btn_groups.setCursor(Qt.PointingHandCursor)
        self.btn_groups.setStyleSheet(
            f"QPushButton {{ background: {theme.COLOR_BG_CARD}; "
            f"border: 1px solid {theme.COLOR_BORDER}; "
            f"border-radius: 4px; color: {theme.COLOR_TEXT_PRIMARY}; }} "
            f"QPushButton:hover {{ background: {theme.COLOR_BG_HOVER}; }}")
        self.btn_groups.clicked.connect(self._on_manage_groups)
        head.addWidget(self.btn_groups)
        v.addLayout(head)

        self.cam_list = CameraTreeWidget()
        self.cam_list.setStyleSheet(
            f"QTreeWidget {{ background: {theme.COLOR_BG_DARK}; "
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {theme.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 3px; font-size: 12px; "
            f"outline: none; }} "
            f"QTreeWidget::item {{ padding: 6px 4px; border-radius: 4px; }} "
            f"QTreeWidget::item:hover {{ background: {theme.COLOR_BG_HOVER}; }} "
            f"QTreeWidget::item:selected {{ "
            f"background: {theme.COLOR_ACCENT}; color: white; }}")
        self.cam_list.itemDoubleClicked.connect(self._on_cam_double_clicked)
        self.cam_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cam_list.customContextMenuRequested.connect(
            self._on_tree_context_menu)
        v.addWidget(self.cam_list, 1)

        hint = QLabel(
            "•  Drag NVR/گروه روی کاشی: همه پخش شوند\n"
            "•  راست‌کلیک روی NVR/گروه: پخش/توقف\n"
            "•  روی کاشی آفلاین: دکمه «اتصال مجدد»")
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
        self.status_list.setFixedHeight(120)
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
            return get_live_transport()
        except Exception:
            return get_live_transport()

    def _check_activity(self):
        if self._shutting_down or self._clearing:
            return
        active = False
        text = ""
        if self._pending_switch:
            uid = self._pending_switch
            cam = self.cam_manager.get(uid)
            name = cam.name if cam else "دوربین"
            active = True
            text = t_load("live_switch_single", camera=name)
        elif self._prewarm_in_flight:
            n = len(self._prewarm_in_flight)
            active = True
            text = t_load("live_prewarm_n", count=n)
        else:
            loading = 0
            first_name = ""
            for uid, txt in self._statuses.items():
                if txt and txt not in ("online", "connected", "متصل",
                                        "stopped", "آفلاین"):
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
        if save:
            self._save_state_to_disk()

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

    # ============================================================
    # start grid reader — fast offline detection
    # ============================================================
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

        if ready_state is None:
            cnt = self._mediamtx_retry.get(uid, 0) + 1
            self._mediamtx_retry[uid] = cnt
            if cnt >= 2:
                self._mark_camera_offline(uid)
                self._mediamtx_retry.pop(uid, None)
                return
            e = self._clear_epoch
            QTimer.singleShot(
                800, lambda u=uid: self._start_grid_reader(u, _epoch=e))
            return

        if ready_state is not True:
            cnt = self._mediamtx_retry.get(uid, 0) + 1
            self._mediamtx_retry[uid] = cnt
            if cnt > self._max_mediamtx_retry:
                self._mark_camera_offline(uid)
                self._mediamtx_retry.pop(uid, None)
                return
            e = self._clear_epoch
            QTimer.singleShot(
                400, lambda u=uid: self._start_grid_reader(u, _epoch=e))
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
        self._grid_started_at[uid] = time.monotonic()
        self._grid_frame_seen[uid] = False

    def _mark_camera_offline(self, uid):
        cell_idx = self.uid_to_cell.get(uid)
        cam = self.cam_manager.get(uid)
        name = cam.name if cam else uid
        print(f"[live] camera offline: {name}")
        if cell_idx is not None:
            self.grid.set_status(cell_idx, "آفلاین")
        self._statuses[uid] = "آفلاین"
        self._refresh_status_list()

    def _deferred_start(self, idx, uid, drop_epoch):
        if drop_epoch != self._drop_epoch:
            return
        if self._clearing or self._shutting_down:
            return
        self._start_grid_reader(uid, _epoch=self._clear_epoch)

    def _stop_grid_reader(self, uid):
        r = self.grid_readers.pop(uid, None)
        self._grid_started_at.pop(uid, None)
        self._grid_frame_seen.pop(uid, None)
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
            QTimer.singleShot(400,
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
        self._switch_wait_started_at.pop(uid, None)
        if r is None:
            return
        self._retire_reader(r)

    def _on_single_reader_finished(self, uid, reader):
        if self.single_readers.get(uid) is not reader:
            return
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
                if uid in self._prewarm_in_flight:
                    self._prewarm_in_flight.pop(uid, None)
                self._force_stop_single_reader(uid)
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
            self._force_stop_single_reader(uid)

    def _on_grid_frame(self, uid, payload, reader):
        if self._shutting_down or self._clearing:
            return
        if self.grid_readers.get(uid) is not reader:
            return
        self._grid_frame_seen[uid] = True
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
        cell_idx = self.uid_to_cell.get(uid)
        if cell_idx is None:
            return
        if self._pending_switch == uid:
            self._pending_switch = None
            self.cell_source[cell_idx] = "single"
            self.grid.set_frame(cell_idx, payload)
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
    def on_hide(self):
        if self._shutting_down or self._page_suspended:
            return
        self._page_suspended = True
        self._cancel_focus_pause()
        self._fullscreen_uid = None
        self._pending_switch = None
        self._prewarm_in_flight.clear()
        self._switch_wait_started_at.clear()
        for uid, r in list(self.grid_readers.items()):
            if r is not None:
                try:
                    r.set_output_enabled(False)
                except Exception:
                    pass
        for uid, r in list(self.single_readers.items()):
            if r is not None:
                try:
                    r.set_output_enabled(False)
                except Exception:
                    pass
        print(f"[live] page hidden — nvr={self._active_nvr_uid} "
              f"grp={self._active_group_uid}")

    def on_show(self):
        self._load_cameras()
        if self._shutting_down or self._clearing:
            return
        was_suspended = self._page_suspended
        self._page_suspended = False
        for uid, r in list(self.grid_readers.items()):
            if r is not None:
                try:
                    r.set_output_enabled(True)
                except Exception:
                    pass
        for uid, r in list(self.single_readers.items()):
            if r is not None:
                try:
                    r.set_output_enabled(True)
                except Exception:
                    pass
        started = 0
        for uid in list(self.cell_to_uid.values()):
            if uid and uid not in self.grid_readers:
                self._start_grid_reader(uid, _epoch=self._clear_epoch)
                started += 1
        if was_suspended:
            print(f"[live] page shown — resumed "
                  f"{len(self.grid_readers)} grid, started {started} new, "
                  f"nvr={self._active_nvr_uid} grp={self._active_group_uid}")

    def _on_cell_hover_entered(self, idx):
        return

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
        to_pause = [u for u in list(self.grid_readers.keys()) if u != focused]
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
        self._focus_paused = True
        self._focus_paused_uids = set(to_pause)
        for uid in to_pause:
            r = self.grid_readers.get(uid)
            if r is not None:
                try:
                    r.set_output_enabled(False)
                except Exception:
                    pass

    def _reset_focus_state(self):
        self._cancel_focus_pause()
        self._focus_paused = False
        self._focus_paused_uids.clear()
        self._focus_frames_cache.clear()

    def _enter_fullscreen_mode(self, idx):
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return
        self._fullscreen_uid = uid
        self.grid.toggle_fullscreen_cell(idx)
        self._schedule_focus_pause()
        cam = self.cam_manager.get(uid)
        if cam is None:
            self.cell_source[idx] = "grid"
            return
        if cam.live_profile_id == cam.grid_profile_id:
            self._pending_switch = None
            try:
                self.status_lbl.setText(
                    f"⚠ {cam.name or uid}: Single=Grid")
                QTimer.singleShot(4000,
                                  lambda: self.status_lbl.setText("آماده"))
            except Exception:
                pass
            return
        r = self.single_readers.get(uid)
        showed_single = False
        if r is not None and r.is_ready():
            latest = r.get_latest_frame()
            if latest is not None:
                self.grid.set_frame(idx, (r.get_latest_frame_seq(), latest))
                showed_single = True
        if not showed_single:
            gr = self.grid_readers.get(uid)
            if gr is not None:
                g = gr.get_latest_frame()
                if g is not None:
                    self.grid.set_frame(idx, (gr.get_latest_frame_seq(), g))
        if uid in self._single_ready_uids and showed_single:
            self.cell_source[idx] = "single"
            self._pending_switch = None
            self._single_last_used[uid] = time.monotonic()
            return
        self.cell_source[idx] = "single"
        if uid not in self.single_readers:
            if len(self.single_readers) >= self.MAX_PREWARM_SINGLE_READERS:
                for old_uid in list(self.single_readers.keys()):
                    if old_uid != uid and old_uid != self._fullscreen_uid:
                        self._force_stop_single_reader(old_uid)
                        break
            self._start_single_reader(uid, _epoch=self._clear_epoch)
        self._pending_switch = uid
        self._switch_wait_started_at[uid] = time.monotonic()
        self._single_last_used[uid] = time.monotonic()

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
        self._focus_paused = False
        self._focus_paused_uids.clear()
        self._focus_frames_cache.clear()
        for uid, r in list(self.grid_readers.items()):
            if r is not None:
                try:
                    r.set_output_enabled(True)
                except Exception:
                    pass
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

    # ============================================================
    # ★ Reconnect offline camera
    # ============================================================
    def _on_cell_reconnect(self, idx):
        """دکمه اتصال مجدد روی کاشی آفلاین را پردازش می‌کند."""
        uid = self.cell_to_uid.get(idx)
        if not uid:
            print(f"[live] reconnect: no uid at idx={idx}")
            return
        cam = self.cam_manager.get(uid)
        name = cam.name if cam else uid
        print(f"[live] RECONNECT requested: idx={idx} uid={uid} name={name}")

        # پاک کردن state آفلاین
        self._mediamtx_retry.pop(uid, None)
        self._statuses.pop(uid, None)
        self.grid.set_status(idx, "connecting…")

        # ریست offline tracker
        self._grid_started_at.pop(uid, None)
        self._grid_frame_seen.pop(uid, None)

        # اگر reader قدیمی وجود دارد، حذف شود
        self._stop_grid_reader(uid)

        # شروع مجدد
        self._start_grid_reader(uid, _epoch=self._clear_epoch)

        try:
            self.status_lbl.setText(f"⏳ اتصال مجدد: {name}")
            QTimer.singleShot(3000, lambda: self.status_lbl.setText("آماده"))
        except Exception:
            pass

    # ============================================================
    # NVR / Group drop
    # ============================================================
    def _on_cell_drop_camera(self, idx, uid):
        if self._clearing:
            return
        if not uid:
            return
        print(f"[live] CELL DROP: idx={idx} uid={uid!r}")
        if uid.startswith(NVR_PREFIX):
            self._handle_nvr_drop(uid[len(NVR_PREFIX):])
            return
        if uid.startswith(GROUP_PREFIX):
            self._handle_group_drop(uid[len(GROUP_PREFIX):])
            return
        self._assign_camera_to_cell(idx, uid, start=True)

    def _handle_nvr_drop(self, nvr_uid):
        if self._active_nvr_uid == nvr_uid:
            print(f"[live] NVR TOGGLE → STOP (nvr_uid={nvr_uid})")
            self._active_nvr_uid = None
            self._save_active_state()
            self._on_clear_lightweight()
            try:
                self.status_lbl.setText("✓ NVR متوقف شد")
                QTimer.singleShot(2000,
                                  lambda: self.status_lbl.setText("آماده"))
            except Exception:
                pass
            return

        cams = [c for c in self.cam_manager.all() if c.nvr_uid == nvr_uid]
        if not cams:
            dlg_warning(self, "این NVR هیچ کانال اضافه‌شده‌ای ندارد.")
            return

        print(f"[live] NVR PLAY — nvr_uid={nvr_uid} cams={len(cams)}")

        def _ch_num(c):
            m = re.search(r"دوربین\s+(\d+)", c.name or "")
            if m:
                return int(m.group(1))
            m = re.search(r"(\d+)", c.name or "")
            return int(m.group(1)) if m else 999
        cams.sort(key=_ch_num)

        self._active_nvr_uid = nvr_uid
        self._active_group_uid = None
        self._save_active_state()

        layout = self._pick_layout_for_count(len(cams))
        self._on_clear_lightweight()
        drop_epoch = self._drop_epoch

        self._apply_layout(layout, save=False)
        n_cells = len(self.grid.get_cells())
        assign_cams = cams[:n_cells]

        for i, cam in enumerate(assign_cams):
            self.cell_to_uid[i] = cam.uid
            self.uid_to_cell[cam.uid] = i
            self.cell_source[i] = "grid"
            self.grid.set_uid(i, cam.uid)
            self.grid.set_title(i, cam.name or cam.uid)
            self.grid.set_status(i, "connecting…")

        # ★ stagger 40ms — سریع‌تر
        for i, cam in enumerate(assign_cams):
            QTimer.singleShot(
                i * 40,
                lambda idx=i, u=cam.uid, e=drop_epoch:
                    self._deferred_start(idx, u, e))

        QTimer.singleShot(len(assign_cams) * 40 + 200,
                          self._save_state_to_disk)
        try:
            self.status_lbl.setText(
                f"✓ NVR — {len(assign_cams)} کانال")
            QTimer.singleShot(4000, lambda: self.status_lbl.setText("آماده"))
        except Exception:
            pass

    def _handle_group_drop(self, group_uid):
        if self._active_group_uid == group_uid:
            print(f"[live] GROUP TOGGLE → STOP (group_uid={group_uid})")
            self._active_group_uid = None
            self._save_active_state()
            self._on_clear_lightweight()
            try:
                self.status_lbl.setText("✓ گروه متوقف شد")
                QTimer.singleShot(2000,
                                  lambda: self.status_lbl.setText("آماده"))
            except Exception:
                pass
            return

        grp = self.group_manager.get(group_uid)
        if not grp:
            dlg_warning(self, "گروه یافت نشد.")
            return
        cam_uids = list(grp.camera_uids or [])
        cams = [self.cam_manager.get(u) for u in cam_uids]
        cams = [c for c in cams if c is not None]
        if not cams:
            dlg_warning(self, "این گروه هیچ دوربین معتبری ندارد.")
            return

        print(f"[live] GROUP PLAY — {grp.name} cams={len(cams)}")

        self._active_group_uid = group_uid
        self._active_nvr_uid = None
        self._save_active_state()

        layout = self._pick_layout_for_count(len(cams))
        self._on_clear_lightweight()
        drop_epoch = self._drop_epoch

        self._apply_layout(layout, save=False)
        n_cells = len(self.grid.get_cells())
        assign_cams = cams[:n_cells]

        for i, cam in enumerate(assign_cams):
            self.cell_to_uid[i] = cam.uid
            self.uid_to_cell[cam.uid] = i
            self.cell_source[i] = "grid"
            self.grid.set_uid(i, cam.uid)
            self.grid.set_title(i, cam.name or cam.uid)
            self.grid.set_status(i, "connecting…")

        for i, cam in enumerate(assign_cams):
            QTimer.singleShot(
                i * 40,
                lambda idx=i, u=cam.uid, e=drop_epoch:
                    self._deferred_start(idx, u, e))

        QTimer.singleShot(len(assign_cams) * 40 + 200,
                          self._save_state_to_disk)
        try:
            self.status_lbl.setText(
                f"✓ گروه «{grp.name}» — {len(assign_cams)} دوربین")
            QTimer.singleShot(4000, lambda: self.status_lbl.setText("آماده"))
        except Exception:
            pass

    def _on_clear_lightweight(self):
        print("[live] CLEAR (lightweight) requested")
        self._clear_epoch += 1
        self._drop_epoch += 1
        self._reset_focus_state()
        self._prewarm_in_flight.clear()
        self._switch_wait_started_at.clear()

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
        self._grid_started_at.clear()
        self._grid_frame_seen.clear()

        for i in range(len(self.grid.get_cells())):
            self.grid.clear_cell(i)
            self.grid.set_uid(i, "")
            self.grid.set_title(i, "")
            self.grid.set_status(i, "")

        self._refresh_status_list()

        # ★ retire غیرمسدود — QTimer.singleShot(0)
        for r in (grid_list + single_list):
            QTimer.singleShot(0, lambda rr=r: self._retire_reader(rr))

        print(f"[live] CLEAR (lightweight) done — "
              f"{len(grid_list) + len(single_list)} readers scheduled")

    def _pick_layout_for_count(self, count):
        try:
            from ui.widgets.live_grid import get_layout
        except Exception:
            return "2x2"
        candidates = ["1x1", "1+1", "2x2", "2x3", "3x3",
                      "3x4", "4x4", "5x5", "6x6"]
        for key in candidates:
            try:
                L = get_layout(key)
                if not L:
                    continue
                cells = L.get("cells") or []
                if len(cells) >= count:
                    return key
            except Exception:
                continue
        return "2x2"

    def _on_cell_swap(self, src_idx, dst_idx):
        cells = self.grid.get_cells()
        if not (0 <= src_idx < len(cells) and 0 <= dst_idx < len(cells)):
            return
        if src_idx == dst_idx:
            return
        ca, cb = cells[src_idx], cells[dst_idx]
        sa, sb = ca.get_state(), cb.get_state()
        ca.set_state(sb)
        cb.set_state(sa)
        uid_a, uid_b = sa.get("uid"), sb.get("uid")
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
            dlg_warning(self, f"خطا:\n{e}")

    def _copy_url(self, cam):
        try:
            url = self._build_stream_url(cam, cam.live_profile_id)
            QApplication.clipboard().setText(url)
            dlg_info(self, f"URL کپی شد:\n{redact_url(url)}", "کپی")
        except Exception as e:
            dlg_warning(self, f"خطا:\n{e}")

    # ============================================================
    def _on_tree_context_menu(self, pos):
        item = self.cam_list.itemAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_style())

        if item is not None:
            data = item.data(0, Qt.UserRole)
        else:
            data = None

        if data:
            kind, uid = data
            if kind == "camera":
                a1 = QAction("▶ افزودن به کاشی فعال", self)
                a1.triggered.connect(
                    lambda: self._on_cam_double_clicked(item, 0))
                menu.addAction(a1)
                a2 = QAction("➕ افزودن به گروه…", self)
                a2.triggered.connect(
                    lambda: self._add_cam_to_group_dialog(uid))
                menu.addAction(a2)
                menu.addSeparator()
                a3 = QAction("📸 Snapshot", self)
                idx = self.uid_to_cell.get(uid)
                if idx is not None:
                    a3.triggered.connect(
                        lambda: self._take_snapshot(idx))
                    menu.addAction(a3)
                a4 = QAction("🔗 کپی URL", self)
                cam = self.cam_manager.get(uid)
                if cam:
                    a4.triggered.connect(
                        lambda: self._copy_url(cam))
                    menu.addAction(a4)
                menu.addSeparator()
                a5 = QAction("⚙ ویرایش دوربین", self)
                a5.triggered.connect(lambda: self._edit_camera_dialog(uid))
                menu.addAction(a5)
            elif kind == "nvr":
                is_active = (self._active_nvr_uid == uid)
                print(f"[live] menu NVR uid={uid} "
                      f"active={self._active_nvr_uid} match={is_active}")
                if is_active:
                    a1 = QAction("⏹ توقف همه‌ی کانال‌ها", self)
                else:
                    a1 = QAction("▶ پخش همه‌ی کانال‌ها", self)
                a1.triggered.connect(lambda: self._handle_nvr_drop(uid))
                menu.addAction(a1)
                menu.addSeparator()
                a2 = QAction("⚙ ویرایش NVR", self)
                a2.triggered.connect(lambda: self._edit_nvr_dialog(uid))
                menu.addAction(a2)
            elif kind == "group":
                is_active = (self._active_group_uid == uid)
                print(f"[live] menu GROUP uid={uid} "
                      f"active={self._active_group_uid} match={is_active}")
                if is_active:
                    a1 = QAction("⏹ توقف دوربین‌های گروه", self)
                else:
                    a1 = QAction("▶ پخش دوربین‌های گروه", self)
                a1.triggered.connect(lambda: self._handle_group_drop(uid))
                menu.addAction(a1)
                menu.addSeparator()
                a2 = QAction("⚙ ویرایش گروه", self)
                a2.triggered.connect(lambda: self._edit_group_dialog(uid))
                menu.addAction(a2)
                a3 = QAction("🗑 حذف گروه", self)
                a3.triggered.connect(lambda: self._delete_group(uid))
                menu.addAction(a3)
        else:
            a1 = QAction("➕ افزودن گروه جدید", self)
            a1.triggered.connect(self._on_manage_groups)
            menu.addAction(a1)
            a2 = QAction("🔄 بازخوانی درخت", self)
            a2.triggered.connect(self._load_cameras)
            menu.addAction(a2)

        menu.exec(self.cam_list.viewport().mapToGlobal(pos))

    def _menu_style(self):
        return (f"QMenu {{ background: {theme.COLOR_BG_CARD}; "
                f"color: {theme.COLOR_TEXT_PRIMARY}; "
                f"border: 1px solid {theme.COLOR_BORDER}; "
                f"border-radius: 6px; padding: 4px; }} "
                f"QMenu::item {{ padding: 6px 20px; border-radius: 4px; }} "
                f"QMenu::item:selected {{ background: {theme.COLOR_ACCENT}; "
                f"color: white; }}")

    def _edit_camera_dialog(self, uid):
        try:
            from ui.camera_dialog import CameraDialog
            cam = self.cam_manager.get(uid)
            if cam is None:
                return
            dlg = CameraDialog(cam, self)
            if dlg.exec() == CameraDialog.Accepted:
                updated = dlg.get_camera()
                self.cam_manager.update(uid, updated)
                self._load_cameras()
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    def _edit_nvr_dialog(self, nvr_uid):
        try:
            from ui.dialogs.nvr_dialog import NVRDialog
            nvr = self.nvr_manager.get(nvr_uid)
            if nvr is None:
                return
            dlg = NVRDialog(nvr, self)
            if dlg.exec() == NVRDialog.Accepted:
                updated = dlg.get_nvr()
                self.nvr_manager.update(nvr_uid, updated)
                self._load_cameras()
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    def _edit_group_dialog(self, group_uid):
        try:
            from ui.dialogs.group_dialog import GroupDialog
            grp = self.group_manager.get(group_uid)
            if grp is None:
                return
            dlg = GroupDialog(grp, self)
            if dlg.exec() == GroupDialog.Accepted:
                updated = dlg.get_group()
                self.group_manager.update(group_uid, updated)
                self._load_cameras()
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    def _add_cam_to_group_dialog(self, cam_uid):
        groups = self.group_manager.all()
        if not groups:
            r = dlg_question(self, "هیچ گروهی وجود ندارد. گروه جدید بسازیم؟",
                             "گروه")
            if r:
                self._open_new_group()
            return
        try:
            menu = QMenu(self)
            menu.setStyleSheet(self._menu_style())
            for g in groups:
                a = QAction(f"➕ {g.name}", self)
                a.triggered.connect(
                    lambda checked=False, gg=g: self._append_cam_to_group(
                        gg.uid, cam_uid))
                menu.addAction(a)
            menu.addSeparator()
            a_new = QAction("➕ گروه جدید…", self)
            a_new.triggered.connect(self._open_new_group)
            menu.addAction(a_new)
            menu.exec(QApplication.instance().activeWindow().cursor().pos())
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    def _append_cam_to_group(self, group_uid, cam_uid):
        grp = self.group_manager.get(group_uid)
        if not grp:
            return
        if cam_uid not in grp.camera_uids:
            grp.camera_uids.append(cam_uid)
            self.group_manager.update(group_uid, grp)
            self._load_cameras()

    def _delete_group(self, group_uid):
        grp = self.group_manager.get(group_uid)
        if not grp:
            return
        if not dlg_question(self, f'حذف گروه "{grp.name}"؟', "حذف گروه"):
            return
        self.group_manager.delete(group_uid)
        self._load_cameras()

    def _on_manage_groups(self):
        try:
            from ui.dialogs.group_manager_dialog import GroupManagerDialog
            dlg = GroupManagerDialog(self)
            dlg.exec()
            self._load_cameras()
        except Exception as e:
            print(f"[live] manage groups error: {e}")
            dlg_warning(self, f"خطا: {e}")

    def _open_new_group(self):
        try:
            from ui.dialogs.group_dialog import GroupDialog
            dlg = GroupDialog(None, self)
            if dlg.exec() == GroupDialog.Accepted:
                self.group_manager.add(dlg.get_group())
                self._load_cameras()
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    def _on_cam_double_clicked(self, item, column=0):
        if self._clearing:
            return
        try:
            data = item.data(0, Qt.UserRole)
        except TypeError:
            data = item.data(Qt.UserRole)
        if not data:
            return
        kind, uid = data
        if kind in ("nvr", "group"):
            item.setExpanded(not item.isExpanded())
            return
        idx = self.grid.get_active_index()
        self._assign_camera_to_cell(idx, uid, start=True)

    def _on_clear(self):
        if self._clearing:
            return
        print("[live] CLEAR requested")
        self._clearing = True
        self._clear_epoch += 1
        self._drop_epoch += 1
        self._reset_focus_state()
        self._prewarm_in_flight.clear()
        self._switch_wait_started_at.clear()
        self._active_nvr_uid = None
        self._active_group_uid = None
        self._save_active_state()

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
        self._grid_started_at.clear()
        self._grid_frame_seen.clear()

        for i in range(len(self.grid.get_cells())):
            self.grid.clear_cell(i)
            self.grid.set_uid(i, "")
            self.grid.set_title(i, "")
            self.grid.set_status(i, "")

        self._refresh_status_list()
        try:
            self._save_state_to_disk()
        except Exception:
            pass

        # ★ retire غیرمسدود
        all_readers = grid_list + single_list
        for r in all_readers:
            QTimer.singleShot(0, lambda rr=r: self._retire_reader(rr))

        self._clearing = False
        try:
            ActivityBus.instance().hide()
        except Exception:
            pass
        print(f"[live] CLEAR done — instant ({len(all_readers)} readers)")

    def _refresh_status_list(self):
        self.status_list.clear()
        for uid, txt in self._statuses.items():
            cam = self.cam_manager.get(uid)
            name = cam.name if cam else uid
            self.status_list.addItem(f"{name}: {txt}")

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        self._clear_epoch += 1
        self._drop_epoch += 1
        import time as _time
        _shutdown_t0 = _time.monotonic()
        print("[SHUTDOWN] BEGIN")
        self._reset_focus_state()
        try:
            self._activity_timer.stop()
        except Exception:
            pass
        try:
            self._budget_timer.stop()
        except Exception:
            pass
        try:
            self._offline_timer.stop()
        except Exception:
            pass
        try:
            ActivityBus.instance().hide()
        except Exception:
            pass
        for t in (self._single_idle_timer, self._gc_timer):
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
        print(f"[SHUTDOWN] readers={len(all_readers)}")

        # ★ فاز ۱: stop همه
        for r in all_readers:
            try:
                r.stop()
            except Exception:
                pass

        # ★ فاز ۲: wait کوتاه (۵۰۰ms هر کدام، ولی همزمان)
        for r in all_readers:
            try:
                if not r.wait(500):
                    try:
                        r.terminate()
                        r.wait(150)
                    except Exception:
                        pass
            except Exception:
                pass

        alive_count = 0
        for r in all_readers:
            try:
                if r.isRunning():
                    alive_count += 1
            except Exception:
                pass
        self.cell_to_uid.clear()
        self.uid_to_cell.clear()
        self.cell_source.clear()
        self._statuses.clear()
        print(f"[SHUTDOWN] COMPLETE alive={alive_count} "
              f"elapsed={(_time.monotonic()-_shutdown_t0):.1f}s")

    def _load_cameras(self):
        try:
            self.cam_manager.reload()
        except Exception as e:
            print(f"[live] cam reload error: {e}")

        self.cam_list.blockSignals(True)
        self.cam_list.clear()
        all_cams = self.cam_manager.all()

        for nvr in self.nvr_manager.all():
            nvr_cams = [c for c in all_cams if c.nvr_uid == nvr.uid]
            if not nvr_cams:
                continue

            def _ch_num(c):
                m = re.search(r"دوربین\s+(\d+)", c.name or "")
                if m:
                    return int(m.group(1))
                m = re.search(r"(\d+)", c.name or "")
                return int(m.group(1)) if m else 999
            nvr_cams.sort(key=_ch_num)
            self.cam_list.add_nvr(nvr, nvr_cams)

        for grp in self.group_manager.all():
            grp_cams = []
            for u in grp.camera_uids:
                c = self.cam_manager.get(u)
                if c is not None:
                    grp_cams.append(c)
            self.cam_list.add_custom_group(
                grp.name, grp.uid, grp_cams, grp.color)

        standalone = [c for c in all_cams if not c.nvr_uid]
        if standalone:
            root = self.cam_list.add_standalone_root(len(standalone))
            for c in standalone:
                self.cam_list.add_standalone_under(root, c)

        self.cam_list.blockSignals(False)

    def _restore_state(self):
        self._restoring = True
        try:
            state = self._load_state_from_disk()
            if not state:
                self._apply_layout(self.DEFAULT_LAYOUT, save=False)
                self._initial_fill_done = True
                print("[live] restored: default layout")
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
                    cam = self.cam_manager.get(uid)
                    if cam is None:
                        continue
                    self.cell_to_uid[i] = uid
                    self.uid_to_cell[uid] = i
                    self.cell_source[i] = "grid"
                    self.grid.set_uid(i, uid)
                    self.grid.set_title(i, cam.name or cam.uid)
                    self.grid.set_status(i, "متوقف")
            self._initial_fill_done = True
            print(f"[live] restored: {len(valid_uids & set(target))} "
                  f"placeholders")
        finally:
            self._restoring = False