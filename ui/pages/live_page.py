# -*- coding: utf-8 -*-
"""
K1 VMS — Live View page (Phase D-lite + speed optimizations)

Features:
- Grid reader (low quality) + Single reader (high quality)
- MediaMTX readiness check before spawning ffmpeg (fast startup)
- Hover prewarm: single reader warms up when mouse hovers a tile
- Smooth transition: grid frame stays visible until single is ready
"""
import os
import json
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
from core.camera_manager import CameraManager
from core.live_reader import LiveReader
from core.nvr_engine import _camera_path_name
import config


STATE_FILE = config.DATA_DIR / "live_state.json"


# ============================================================
# Camera list
# ============================================================
class CameraListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragOnly)
        self.setSelectionMode(QListWidget.SingleSelection)

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None:
            return
        uid = item.data(Qt.UserRole) or ""
        if not uid:
            return
        md = QMimeData()
        payload = json.dumps({"uid": uid, "from_cell": -1})
        md.setData(MIME_CAMERA, payload.encode("utf-8"))
        md.setText(uid)
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)


# ============================================================
# Live Page
# ============================================================
class LivePage(BasePage):
    PAGE_KEY = "live"
    PAGE_TITLE = "Live"
    HAS_PANEL = False

    DEFAULT_LAYOUT = "2x2"
    PREWARM_DELAY_MS = 250

    def __init__(self, nvr_engine=None, parent=None):
        super().__init__(parent)
        self.cam_manager = CameraManager()
        self.nvr = nvr_engine

        # Readers
        self.grid_readers = {}      # uid -> LiveReader
        self.single_readers = {}    # uid -> LiveReader

        # Cell mapping
        self.cell_to_uid = {}
        self.uid_to_cell = {}
        self.cell_source = {}       # cell_idx -> "grid" | "single"
        self._statuses = {}

        # Fullscreen state
        self._fullscreen_uid = None
        self._pending_switch = None
        self._single_ready_uids = set()

        # Prewarm
        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.timeout.connect(self._do_prewarm)
        self._prewarm_idx = None

        self._shutting_down = False
        self._restoring = False
        self._initial_fill_done = False

        self._build()
        self._load_cameras()
        self._restore_state()

    # ============================================================
    # UI
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
    # MediaMTX readiness check
    # ============================================================
    def _mediamtx_path_ready(self, cam, profile_id) -> bool:
        """
        Check if MediaMTX has the path ready.
        Returns True if API fails (assume ok — let ffmpeg try).
        """
        if not self.nvr:
            return True
        try:
            rec_pid = cam.recording_profile_id
            mot_pid = cam.motion_profile_id
            name = _camera_path_name(cam)

            if profile_id == rec_pid:
                path_name = name
            elif profile_id == mot_pid:
                path_name = f"{name}_sub"
            else:
                return True  # unknown profile — let ffmpeg try

            from core.nvr_engine import MediaMTXApi
            api = MediaMTXApi()
            paths = api.list_paths()
            p = paths.get(path_name)
            if p is None:
                return False
            return bool(p.get("ready"))
        except Exception:
            return True

    # ============================================================
    # State persistence
    # ============================================================
    def _load_state_from_disk(self):
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"[live] load state: {e}")
        return None

    def _save_state_to_disk(self):
        if self._restoring:
            return
        try:
            layout = self.grid.get_layout_key()
            n = len(self.grid.get_cells())
            assignments = []
            for i in range(n):
                assignments.append(self.cell_to_uid.get(i, "") or "")
            data = {"layout": layout, "assignments": assignments}
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[live] save state: {e}")

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

        self._stop_all_single_readers()
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

        wanted_uids = set()
        for uid in old_cell_uids[:n]:
            if uid:
                wanted_uids.add(uid)
        if self._initial_fill_done:
            all_cams = self.cam_manager.all()
            for c in all_cams:
                if len(wanted_uids) >= n:
                    break
                if c.uid not in wanted_uids:
                    wanted_uids.add(c.uid)

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
            all_cams = self.cam_manager.all()
            for c in all_cams:
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
    # Assign
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
            other_cells = [i for i, u in self.cell_to_uid.items()
                           if u == old_uid and i != cell_idx]
            if not other_cells:
                self._stop_grid_reader(old_uid)
                self._stop_single_reader(old_uid)
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
    # Config helpers
    # ============================================================
    @staticmethod
    def _parse_size(s):
        try:
            s = str(s).lower().replace("×", "x")
            w, h = s.split("x")
            return int(w.strip()), int(h.strip())
        except Exception:
            return (0, 0)

    def _build_stream_url(self, cam, profile_id: str) -> str:
        profile = cam.get_profile_by_id(profile_id) if profile_id else None
        if profile is None:
            return ""
        if not self.nvr:
            return profile.url or ""
        name = _camera_path_name(cam)
        rec_pid = cam.recording_profile_id
        mot_pid = cam.motion_profile_id
        if profile_id == rec_pid:
            return f"rtsp://127.0.0.1:8554/{name}"
        if profile_id == mot_pid:
            return f"rtsp://127.0.0.1:8554/{name}_sub"
        return profile.url or ""

    def _compute_output_size_for(self, cam, role: str):
        if role == "grid":
            profile_id = cam.grid_profile_id
            override = getattr(cam, "grid_resolution_override", "") or ""
            default = (640, 360)
        else:
            profile_id = cam.live_profile_id
            override = getattr(cam, "live_resolution_override", "") or ""
            default = (1280, 720)

        profile = cam.get_profile_by_id(profile_id) if profile_id else None
        src_w = profile.width if profile else 0
        src_h = profile.height if profile else 0

        if not override:
            if src_w and src_h:
                if role == "single" and (src_w > 1280 or src_h > 720):
                    return (1280, 720)
                return (src_w, src_h)
            return default

        des_w, des_h = self._parse_size(override)
        if des_w < 16 or des_h < 16:
            if src_w and src_h:
                return (src_w, src_h)
            return default
        if src_w and src_h:
            if des_w > src_w or des_h > src_h:
                return (src_w, src_h)
        return (des_w, des_h)

    def _compute_fps_for(self, cam, role: str, n_cells: int = 1) -> int:
        if role == "grid":
            profile_id = cam.grid_profile_id
            override_fps = int(getattr(cam, "grid_display_fps", 0) or 0)
        else:
            profile_id = cam.live_profile_id
            override_fps = int(getattr(cam, "live_display_fps", 0) or 0)

        profile = cam.get_profile_by_id(profile_id) if profile_id else None
        src_fps = profile.fps if (profile and profile.fps) else 0

        if override_fps > 0:
            if src_fps > 0:
                return max(1, min(override_fps, src_fps))
            return max(1, override_fps)

        want = src_fps if src_fps > 0 else (15 if role == "single" else 2)

        if role == "grid":
            if n_cells > 36:
                want = min(want, 1)
            elif n_cells > 16:
                want = min(want, 2)
            elif n_cells > 9:
                want = min(want, 4)
            elif n_cells > 4:
                want = min(want, 6)

        return max(1, want)

    # ============================================================
    # Grid reader
    # ============================================================
    def _start_grid_reader(self, uid):
        if uid in self.grid_readers:
            return
        # ★ Verify cell still exists (retry-safe)
        if uid not in self.uid_to_cell:
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return

        # ★ Check MediaMTX readiness first
        if not self._mediamtx_path_ready(cam, cam.grid_profile_id):
            idx = self.uid_to_cell.get(uid)
            if idx is not None:
                self.grid.set_status(idx, "waiting for source…")
            # Retry in 700ms without spawning ffmpeg
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

        print(f"[live-grid] {cam.name or uid}: url={url}  fps={fps}  "
              f"size={out_w}x{out_h}")

        reader = LiveReader(uid, url, target_fps=fps,
                            out_w=out_w, out_h=out_h)
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
    # Single reader
    # ============================================================
    def _start_single_reader(self, uid):
        if uid in self.single_readers:
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return

        # MediaMTX readiness check
        if not self._mediamtx_path_ready(cam, cam.live_profile_id):
            # Retry in 500ms
            QTimer.singleShot(500, lambda u=uid: self._start_single_reader(u))
            return

        url = self._build_stream_url(cam, cam.live_profile_id)
        if not url:
            return

        out_w, out_h = self._compute_output_size_for(cam, "single")
        fps = self._compute_fps_for(cam, "single")

        print(f"[live-single] {cam.name or uid}: url={url}  fps={fps}  "
              f"size={out_w}x{out_h}")

        reader = LiveReader(uid, url, target_fps=fps,
                            out_w=out_w, out_h=out_h)
        reader.frame_ready.connect(
            lambda u, p, r=reader: self._on_single_frame(u, p, r)
        )
        reader.status.connect(self._on_status)
        reader.start()
        self.single_readers[uid] = reader

    def _stop_single_reader(self, uid):
        r = self.single_readers.pop(uid, None)
        if r is None:
            return
        self._single_ready_uids.discard(uid)
        try:
            r.stop()
            if not r.wait(2000):
                print(f"[live] single reader {uid} did not stop")
        except Exception as e:
            print(f"[live] stop single reader {uid}: {e}")

    def _stop_all_single_readers(self):
        for uid in list(self.single_readers.keys()):
            self._stop_single_reader(uid)

    # ============================================================
    # Frame handlers
    # ============================================================
    def _on_grid_frame(self, uid, payload, reader):
        if self.grid_readers.get(uid) is not reader:
            return
        cell_idx = self.uid_to_cell.get(uid)
        if cell_idx is None:
            return
        if self.cell_source.get(cell_idx, "grid") == "grid":
            self.grid.set_frame(cell_idx, payload)

    def _on_single_frame(self, uid, payload, reader):
        if self.single_readers.get(uid) is not reader:
            return
        cell_idx = self.uid_to_cell.get(uid)
        if cell_idx is None:
            return

        # Mark as first-frame-ready (for prewarm / instant switch)
        if uid not in self._single_ready_uids:
            self._single_ready_uids.add(uid)

        if self._pending_switch == uid:
            self._pending_switch = None
            self.cell_source[cell_idx] = "single"
            self.grid.set_frame(cell_idx, payload)
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
    # Hover prewarm
    # ============================================================
    def _on_cell_hover_entered(self, idx):
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return
        cam = self.cam_manager.get(uid)
        if cam is None:
            return
        # Same profile → no need to prewarm
        if cam.live_profile_id == cam.grid_profile_id:
            return
        # Already running
        if uid in self.single_readers:
            return
        # Start a timer — if user stays hovering, prewarm
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
            self._enter_fullscreen_mode(idx)

    def _enter_fullscreen_mode(self, idx):
        uid = self.cell_to_uid.get(idx)
        if not uid:
            return

        self._fullscreen_uid = uid
        self.grid.toggle_fullscreen_cell(idx)

        cam = self.cam_manager.get(uid)
        if cam is None:
            return

        # Same profile → instant
        if cam.live_profile_id == cam.grid_profile_id:
            self.cell_source[idx] = "single"
            print(f"[live] fullscreen same-profile — instant")
            return

        # Already prewarmed with frames ready → instant switch
        if uid in self.single_readers and uid in self._single_ready_uids:
            self.cell_source[idx] = "single"
            print(f"[live] fullscreen prewarmed — instant switch for {uid}")
            return

        # Otherwise: pending switch, wait for first frame
        self._pending_switch = uid
        self._start_single_reader(uid)

    def _exit_fullscreen_mode(self):
        uid = self._fullscreen_uid
        if uid:
            cell_idx = self.uid_to_cell.get(uid)
            if cell_idx is not None:
                self.cell_source[cell_idx] = "grid"
            self._stop_single_reader(uid)
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
                self._stop_single_reader(uid)
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
    # Context menu
    # ============================================================
    def _on_cell_context(self, idx, global_pos):
        uid = self.cell_to_uid.get(idx)
        cam = self.cam_manager.get(uid) if uid else None
        cells = self.grid.get_cells()
        cell = cells[idx] if 0 <= idx < len(cells) else None

        menu = QMenu(self)
        menu.setStyleSheet(self._menu_qss())
        menu.setToolTipsVisible(True)

        if cam is None:
            self._build_empty_menu(menu, idx)
        else:
            self._build_camera_menu(menu, idx, cam, cell)

        menu.exec(global_pos)

    def _build_empty_menu(self, menu, idx):
        hdr = menu.addAction(f"▢   کاشی {idx + 1} — خالی")
        hdr.setEnabled(False)
        menu.addSeparator()

        connect_menu = menu.addMenu(
            make_icon("camera", theme.COLOR_TEXT_PRIMARY, 14),
            "اتصال دوربین"
        )
        connect_menu.setStyleSheet(self._menu_qss())

        cams = self.cam_manager.all()
        available = [c for c in cams if c.uid not in self.uid_to_cell]

        if not cams:
            a = connect_menu.addAction("(دوربینی تعریف نشده)")
            a.setEnabled(False)
        elif not available:
            a = connect_menu.addAction("(همه دوربین‌ها متصل هستند)")
            a.setEnabled(False)
        else:
            for c in available:
                act = connect_menu.addAction(f"📷   {c.name or c.uid}")
                act.triggered.connect(
                    lambda checked=False, cc=c.uid, ii=idx:
                        self._assign_camera_to_cell(ii, cc, start=True)
                )

        menu.addSeparator()
        act_clear = menu.addAction("🗑   پاک کردن کاشی")
        act_clear.setEnabled(False)

    def _build_camera_menu(self, menu, idx, cam, cell):
        cam_name = cam.name or cam.uid
        hdr = menu.addAction(f"📷   {cam_name}")
        f = hdr.font()
        f.setBold(True)
        hdr.setFont(f)
        hdr.setEnabled(False)

        specs = self._camera_specs(cam)
        if specs:
            s = menu.addAction(specs)
            s.setEnabled(False)

        menu.addSeparator()

        is_fs = (self.grid.get_fullscreen_idx() == idx)
        act_fs = menu.addAction(
            make_icon("maximize", theme.COLOR_TEXT_PRIMARY, 14),
            "خروج از تمام‌صفحه" if is_fs else "تمام‌صفحه"
        )
        act_fs.setShortcut("F11")
        if is_fs:
            act_fs.triggered.connect(self._exit_fullscreen_mode)
        else:
            act_fs.triggered.connect(
                lambda checked=False, i=idx:
                    self._enter_fullscreen_mode(i)
            )

        menu.addSeparator()
        act_reset_zoom = menu.addAction("🔍   بازنشانی زوم")
        act_reset_zoom.triggered.connect(
            lambda checked=False, i=idx: self._reset_cell_zoom(i)
        )
        menu.addSeparator()

        act_snap = menu.addAction(
            make_icon("camera", theme.COLOR_TEXT_PRIMARY, 14),
            "عکس فوری"
        )
        act_snap.setShortcut("Ctrl+S")
        act_snap.setEnabled(cell is not None and cell._frame is not None)
        act_snap.triggered.connect(
            lambda checked=False, i=idx: self._take_snapshot(i)
        )

        menu.addSeparator()

        act_url = menu.addAction("📋   کپی آدرس RTSP")
        act_url.triggered.connect(
            lambda checked=False, c=cam: self._copy_url(c)
        )
        act_info = menu.addAction("ℹ   اطلاعات دوربین…")
        act_info.triggered.connect(
            lambda checked=False, c=cam: self._show_cam_info(c)
        )
        menu.addSeparator()

        act_disc = menu.addAction("⏏   قطع دوربین")
        act_disc.triggered.connect(
            lambda checked=False, i=idx: self._on_cell_close(i)
        )

    def _reset_cell_zoom(self, idx):
        cells = self.grid.get_cells()
        if 0 <= idx < len(cells):
            cells[idx].reset_zoom()

    def _camera_specs(self, cam):
        parts = []
        try:
            live_p = cam.get_live_profile()
            if live_p:
                if live_p.resolution_or():
                    parts.append(live_p.resolution_or())
                if live_p.codec:
                    parts.append(live_p.codec.upper())
                if live_p.fps:
                    parts.append(f"{live_p.fps}fps")
            ip = getattr(cam, "ip", None) or getattr(cam, "host", None)
            if ip:
                parts.append(str(ip))
        except Exception:
            pass
        return "   ·   ".join(parts) if parts else ""

    def _menu_qss(self):
        return (
            f"QMenu {{ "
            f"background: {theme.COLOR_BG_CARD}; "
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {theme.COLOR_BORDER}; "
            f"border-radius: 8px; padding: 6px; font-size: 12px; }} "
            f"QMenu::item {{ "
            f"padding: 7px 28px 7px 10px; border-radius: 5px; "
            f"min-width: 190px; }} "
            f"QMenu::item:selected {{ "
            f"background: {theme.COLOR_ACCENT}; color: #ffffff; }} "
            f"QMenu::item:disabled {{ "
            f"color: {theme.COLOR_TEXT_MUTED}; "
            f"padding: 5px 28px 5px 10px; }} "
            f"QMenu::separator {{ "
            f"height: 1px; background: {theme.COLOR_BORDER}; "
            f"margin: 5px 10px; }} "
            f"QMenu::icon {{ padding-left: 6px; }} "
            f"QMenu::right-arrow {{ width: 12px; }}"
        )

    # ============================================================
    # Context actions
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

    def _show_cam_info(self, cam):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"ℹ   {cam.name or cam.uid}")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(
            f"QDialog {{ background: {theme.COLOR_BG_APP}; }} "
            f"QLabel {{ color: {theme.COLOR_TEXT_PRIMARY}; font-size: 12px; }}"
        )
        v = QVBoxLayout(dlg)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)

        title = QLabel(f"📷   {cam.name or cam.uid}")
        title.setStyleSheet(
            f"color: {theme.COLOR_ACCENT}; "
            f"font-size: 15px; font-weight: 700; padding-bottom: 6px;"
        )
        v.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        def _row(label, value):
            if value in (None, "", 0):
                value = "—"
            lbl = QLabel(str(label))
            lbl.setStyleSheet(
                f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px; "
                f"font-weight: 600;"
            )
            val = QLabel(str(value))
            val.setStyleSheet(
                f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 12px;"
            )
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(lbl, val)

        _row("UID", cam.uid)
        _row("IP", getattr(cam, "ip", None))
        _row("Status", self._statuses.get(cam.uid, "—"))

        v.addLayout(form)
        v.addSpacing(8)

        try:
            profiles = getattr(cam, "stream_profiles", []) or []
            if profiles:
                prof_lbl = QLabel("پروفایل‌های استریم:")
                prof_lbl.setStyleSheet(
                    f"color: {theme.COLOR_TEXT_SECONDARY}; "
                    f"font-size: 10px; font-weight: 600;"
                )
                v.addWidget(prof_lbl)

                lines = []
                for p in profiles:
                    marks = []
                    if p.id == cam.recording_profile_id: marks.append("R")
                    if p.id == cam.motion_profile_id:    marks.append("M")
                    if p.id == cam.live_profile_id:      marks.append("L")
                    if p.id == cam.grid_profile_id:      marks.append("G")
                    mark_str = "/".join(marks) if marks else " "
                    lines.append(f"[{mark_str:5s}]  {p.name}  •  {p.summary()}")

                prof_box = QLabel("\n".join(lines))
                prof_box.setWordWrap(True)
                prof_box.setTextInteractionFlags(Qt.TextSelectableByMouse)
                prof_box.setStyleSheet(
                    f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 10px; "
                    f"font-family: Consolas, monospace; "
                    f"background: {theme.COLOR_BG_CARD}; "
                    f"padding: 8px; border-radius: 4px; "
                    f"border: 1px solid {theme.COLOR_BORDER};"
                )
                v.addWidget(prof_box)
        except Exception:
            pass

        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(dlg.accept)
        bb.setStyleSheet(
            f"QPushButton {{ background: {theme.COLOR_ACCENT}; "
            f"color: white; padding: 6px 18px; "
            f"border: none; border-radius: 5px; font-weight: 600; }} "
            f"QPushButton:hover {{ background: {theme.COLOR_ACCENT_HOVER}; }}"
        )
        v.addWidget(bb)
        dlg.exec()

    # ============================================================
    # Bulk
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
        self._stop_all_single_readers()
        self.cell_to_uid.clear()
        self.uid_to_cell.clear()
        self.cell_source.clear()
        self._statuses.clear()
        self._refresh_status_list()
        self._save_state_to_disk()

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
        pass

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        self._save_state_to_disk()
        self._stop_all_grid_readers()
        self._stop_all_single_readers()

    def _fill_empty_cells(self, save=True):
        cells = self.grid.get_cells()
        n_cells = len(cells)

        all_uids = {c.uid for c in self.cam_manager.all()}
        for cell_idx in list(self.cell_to_uid.keys()):
            uid = self.cell_to_uid[cell_idx]
            if uid not in all_uids:
                self._stop_grid_reader(uid)
                self._stop_single_reader(uid)
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