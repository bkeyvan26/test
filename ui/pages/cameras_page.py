# -*- coding: utf-8 -*-
"""K1 VMS — Cameras page (smart NVR reload)"""
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QListWidgetItem,
    QLabel
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from ui import theme
from ui.icons import make_icon
from ui.pages import BasePage
from ui.camera_dialog import CameraDialog
from ui.dialogs import info as dlg_info, warning as dlg_warning, question as dlg_question
from core.camera_manager import CameraManager
from core import settings_manager


STATUS_ICONS = {
    "recording":     ("🟢", "REC",       "#2ecc71"),
    "online":        ("🔵", "ONLINE",    "#3498db"),
    "reconnecting":  ("🟡", "RECONNECT", "#f39c12"),
    "error":         ("🔴", "ERROR",     "#e74c3c"),
    "starting":      ("🟠", "STARTING",  "#f39c12"),
    "offline":       ("⚫", "OFFLINE",   "#5a6674"),
    "unknown":       ("⚪", "—",         "#5a6674"),
}


class CamerasPage(BasePage):
    PAGE_KEY = "cameras"
    PAGE_TITLE = "Cameras"
    PANEL_TITLE = "Cameras"
    cameras_changed = Signal()

    def __init__(self, nvr_engine=None, parent=None):
        super().__init__(parent)
        self.cam_manager = CameraManager()
        self.nvr = nvr_engine
        self._statuses = {}
        self._last_summary = None

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        head = QHBoxLayout()

        title = QLabel("Camera Management")
        title.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"font-size: 20px; font-weight: 700;"
        )
        head.addWidget(title)
        head.addStretch(1)

        add_btn = QPushButton("  Add Camera")
        add_btn.setIcon(make_icon("plus", "#ffffff", 14))
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {theme.COLOR_ACCENT};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 700;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_ACCENT_HOVER};
            }}
        """)
        add_btn.clicked.connect(self._add_camera)
        head.addWidget(add_btn)

        apply_btn = QPushButton("  Apply to NVR")
        apply_btn.setIcon(make_icon("refresh", theme.COLOR_TEXT_PRIMARY, 14))
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(self._btn_style())
        apply_btn.clicked.connect(self._manual_apply)
        head.addWidget(apply_btn)

        v.addLayout(head)

        hint = QLabel(
            "💡 تغییرات مرتبط با NVR (IP، پروفایل، ضبط) به صورت خودکار "
            "به MediaMTX اعمال می‌شود. تغییرات فقط لایو نیازی به reload ندارند."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_SECONDARY};
            font-size: 11px;
            background: {theme.COLOR_BG_CARD};
            padding: 8px 12px;
            border-radius: 6px;
            border-left: 3px solid {theme.COLOR_ACCENT};
        """)
        v.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 8px;
                padding: 6px;
                font-size: 13px;
            }}
            QListWidget::item {{
                padding: 12px 14px;
                border-radius: 6px;
            }}
            QListWidget::item:hover {{
                background: {theme.COLOR_BG_HOVER};
            }}
            QListWidget::item:selected {{
                background: {theme.COLOR_ACCENT};
                color: white;
            }}
        """)
        self.list_widget.itemDoubleClicked.connect(lambda _: self._edit_camera())
        v.addWidget(self.list_widget, 1)

        act = QHBoxLayout()
        act.setSpacing(8)

        edit_btn = QPushButton("  Edit")
        edit_btn.setIcon(make_icon("edit", theme.COLOR_TEXT_PRIMARY, 14))
        edit_btn.setStyleSheet(self._btn_style())
        edit_btn.clicked.connect(self._edit_camera)
        act.addWidget(edit_btn)

        del_btn = QPushButton("  Delete")
        del_btn.setIcon(make_icon("trash", "white", 14))
        del_btn.setStyleSheet(self._btn_style(danger=True))
        del_btn.clicked.connect(self._delete_camera)
        act.addWidget(del_btn)

        act.addStretch(1)

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;"
        )
        act.addWidget(self.count_lbl)

        v.addLayout(act)

        self._refresh_list()

        if self.nvr:
            try:
                self.nvr.register_listener(self._on_nvr_status)
            except Exception:
                pass

    # ============================================================
    # Styles
    # ============================================================
    def _btn_style(self, danger=False):
        base = theme.COLOR_STATUS_ERROR if danger else theme.COLOR_BG_CARD
        text = "white" if danger else theme.COLOR_TEXT_PRIMARY
        return f"""
            QPushButton {{
                background: {base};
                color: {text};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_BG_HOVER};
            }}
        """

    # ============================================================
    # NVR status
    # ============================================================
    def _on_nvr_status(self, statuses):
        new_summary = {
            uid: info.get("status", "unknown")
            for uid, info in statuses.items()
        }
        if new_summary == self._last_summary:
            return
        self._last_summary = new_summary
        self._statuses = statuses
        self._refresh_list()

    def _auto_apply_to_nvr(self):
        if not self.nvr:
            return
        import config as _c
        if not _c.MEDIAMTX_EXE.exists():
            return
        try:
            ok = self.nvr.reload(cameras=self.cam_manager.all(),
                                 settings=settings_manager.load_settings())
            if ok:
                print("[NVR] Auto-reload OK")
            else:
                print(f"[NVR] Auto-reload FAILED: {self.nvr.get_last_error()}")
        except Exception as e:
            print(f"[NVR] Auto-reload exception: {e}")

    def _manual_apply(self):
        if not self.nvr:
            dlg_info(self, "NVR Engine not available.")
            return
        import config as _c
        if not _c.MEDIAMTX_EXE.exists():
            dlg_warning(self,
                f"MediaMTX not found at:\n{_c.MEDIAMTX_EXE}")
            return
        ok = self.nvr.reload(cameras=self.cam_manager.all(),
                             settings=settings_manager.load_settings())
        if ok:
            dlg_info(self, "MediaMTX reloaded successfully.", "NVR")
        else:
            dlg_warning(self, f"Reload failed:\n{self.nvr.get_last_error()}", "NVR")

    # ============================================================
    # List
    # ============================================================
    def _refresh_list(self):
        cur_uid = None
        it = self.list_widget.currentItem()
        if it:
            cur_uid = it.data(Qt.UserRole)

        self.list_widget.clear()
        cameras = self.cam_manager.all()
        for c in cameras:
            st = self._statuses.get(c.uid, {}).get("status", "unknown")
            if not c.enabled:
                st = "offline"
            icon, label, color = STATUS_ICONS.get(st, STATUS_ICONS["unknown"])

            name = c.name or "(unnamed)"
            ip = c.ip or "—"
            text = f"  {icon}  {name}   —   {ip}   [{label}]"

            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, c.uid)
            item.setForeground(QColor(color))
            self.list_widget.addItem(item)

            if cur_uid and c.uid == cur_uid:
                self.list_widget.setCurrentItem(item)

        self.count_lbl.setText(f"{len(cameras)} camera(s)")

    # ============================================================
    # Actions
    # ============================================================
    def _selected_uid(self):
        it = self.list_widget.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _add_camera(self):
        dlg = CameraDialog(None, self)
        if dlg.exec() == CameraDialog.Accepted:
            cam = dlg.get_camera()
            self.cam_manager.add(cam)
            settings_manager.append_audit("camera.add", "-",
                {"uid": cam.uid, "name": cam.name})
            self._refresh_list()
            self.cameras_changed.emit()
            self._auto_apply_to_nvr()

    def _edit_camera(self):
        uid = self._selected_uid()
        if not uid:
            dlg_info(self, "Select a camera first.")
            return
        old_cam = self.cam_manager.get(uid)
        if old_cam is None:
            return

        # Snapshot MediaMTX-relevant fields
        old_state = self._mediatmx_state(old_cam)

        dlg = CameraDialog(old_cam, self)
        if dlg.exec() == CameraDialog.Accepted:
            updated = dlg.get_camera()
            self.cam_manager.update(uid, updated)
            settings_manager.append_audit("camera.edit", "-", {"uid": uid})
            self._refresh_list()
            self.cameras_changed.emit()

            # ★ Only reload MediaMTX if relevant fields changed
            new_state = self._mediatmx_state(updated)
            if old_state != new_state:
                print("[NVR] MediaMTX-relevant fields changed → reload")
                self._auto_apply_to_nvr()
            else:
                print("[NVR] Only live settings changed → skip reload")

    @staticmethod
    def _mediatmx_state(cam):
        """Fields that affect MediaMTX config."""
        try:
            profiles = getattr(cam, "stream_profiles", []) or []
            prof_sig = tuple(
                (p.id, p.url, p.rtsp_path) for p in profiles
            )
        except Exception:
            prof_sig = ()

        return {
            "enabled": cam.enabled,
            "ip": cam.ip,
            "port": cam.port,
            "user": cam.user,
            "password": cam.password,
            "rtsp_path_main": cam.rtsp_path_main,
            "rtsp_path_sub": cam.rtsp_path_sub,
            "recording_profile_id": getattr(cam, "recording_profile_id", ""),
            "motion_profile_id": getattr(cam, "motion_profile_id", ""),
            "record_enabled_continuous": cam.record_enabled_continuous,
            "record_enabled_motion": cam.record_enabled_motion,
            "record_segment_minutes": cam.record_segment_minutes,
            "record_path_override": cam.record_path_override,
            "profiles": prof_sig,
        }

    def _delete_camera(self):
        uid = self._selected_uid()
        if not uid:
            return
        c = self.cam_manager.get(uid)
        if c is None:
            return
        if not dlg_question(self, f'Delete "{c.name}"?', "Delete Camera"):
            return
        self.cam_manager.delete(uid)
        settings_manager.append_audit("camera.delete", "-", {"uid": uid})
        self._refresh_list()
        self.cameras_changed.emit()
        self._auto_apply_to_nvr()

    # ============================================================
    # Lifecycle
    # ============================================================
    def on_show(self):
        self._refresh_list()