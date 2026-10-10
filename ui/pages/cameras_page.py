# -*- coding: utf-8 -*-
"""K1 VMS — Cameras page (NVR + Groups + camera tree)"""
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget, QTreeWidgetItem,
    QLabel, QMenu
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QAction

from ui import theme
from ui.icons import make_icon
from ui.pages import BasePage
from ui.camera_dialog import CameraDialog
from ui.dialogs import info as dlg_info, warning as dlg_warning, question as dlg_question
from core.camera_manager import CameraManager
from core.nvr_manager import NVRManager
from core.group_manager import GroupManager
from core import settings_manager
import config


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
        self.nvr_manager = NVRManager.instance()
        self.group_manager = GroupManager.instance()
        self.nvr = nvr_engine
        self._statuses = {}
        self._last_summary = None
        self._tree_items = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        # ---------- Header ----------
        head = QHBoxLayout()

        title = QLabel("Camera & NVR Management")
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

        add_nvr_btn = QPushButton("  Add NVR")
        add_nvr_btn.setIcon(make_icon("plus", "#ffffff", 14))
        add_nvr_btn.setCursor(Qt.PointingHandCursor)
        add_nvr_btn.setStyleSheet(f"""
            QPushButton {{
                background: #8e44ad;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 700;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: #9b59b6;
            }}
        """)
        add_nvr_btn.clicked.connect(self._add_nvr)
        head.addWidget(add_nvr_btn)

        groups_btn = QPushButton("  Groups")
        groups_btn.setIcon(make_icon("folder", "#ffffff", 14))
        groups_btn.setCursor(Qt.PointingHandCursor)
        groups_btn.setStyleSheet(f"""
            QPushButton {{
                background: #e67e22;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 700;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: #f39c12;
            }}
        """)
        groups_btn.clicked.connect(self._manage_groups)
        head.addWidget(groups_btn)

        apply_btn = QPushButton("  Apply to NVR")
        apply_btn.setIcon(make_icon("refresh", theme.COLOR_TEXT_PRIMARY, 14))
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(self._btn_style())
        apply_btn.clicked.connect(self._manual_apply)
        head.addWidget(apply_btn)

        v.addLayout(head)

        # ---------- Hint ----------
        hint = QLabel(
            "💡 ساختار درختی: NVRها و گروه‌ها با کانال‌هایشان. "
            "راست‌کلیک برای منوی عملیات.\n"
            "🔌 برای افزودن NVR، روی «Add NVR» بزنید و از تشخیص خودکار استفاده کنید."
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

        # ---------- Tree ----------
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setIndentation(18)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 8px; padding: 6px; font-size: 13px;
                outline: none;
            }}
            QTreeWidget::item {{
                padding: 10px 8px; border-radius: 5px;
            }}
            QTreeWidget::item:hover {{
                background: {theme.COLOR_BG_HOVER};
            }}
            QTreeWidget::item:selected {{
                background: {theme.COLOR_ACCENT}; color: white;
            }}
        """)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        v.addWidget(self.tree, 1)

        # ---------- Actions ----------
        act = QHBoxLayout()
        act.setSpacing(8)

        self.edit_btn = QPushButton("  Edit")
        self.edit_btn.setIcon(make_icon("edit", theme.COLOR_TEXT_PRIMARY, 14))
        self.edit_btn.setStyleSheet(self._btn_style())
        self.edit_btn.clicked.connect(self._edit_selected)
        act.addWidget(self.edit_btn)

        self.del_btn = QPushButton("  Delete")
        self.del_btn.setIcon(make_icon("trash", "white", 14))
        self.del_btn.setStyleSheet(self._btn_style(danger=True))
        self.del_btn.clicked.connect(self._delete_selected)
        act.addWidget(self.del_btn)

        act.addStretch(1)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;"
        )
        act.addWidget(self.count_lbl)
        v.addLayout(act)

        self._refresh_tree()

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

    def _menu_style(self):
        return (f"QMenu {{ background: {theme.COLOR_BG_CARD}; "
                f"color: {theme.COLOR_TEXT_PRIMARY}; "
                f"border: 1px solid {theme.COLOR_BORDER}; "
                f"border-radius: 6px; padding: 4px; }} "
                f"QMenu::item {{ padding: 6px 20px; border-radius: 4px; }} "
                f"QMenu::item:selected {{ background: {theme.COLOR_ACCENT}; "
                f"color: white; }}")

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
        self._refresh_tree()

    def _auto_apply_to_nvr(self):
        if not self.nvr:
            return
        if not config.MEDIAMTX_EXE.exists():
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
        if not config.MEDIAMTX_EXE.exists():
            dlg_warning(self, f"MediaMTX not found at:\n{config.MEDIAMTX_EXE}")
            return
        ok = self.nvr.reload(cameras=self.cam_manager.all(),
                             settings=settings_manager.load_settings())
        if ok:
            dlg_info(self, "MediaMTX reloaded successfully.", "NVR")
        else:
            dlg_warning(self, f"Reload failed:\n{self.nvr.get_last_error()}", "NVR")

    # ============================================================
    # Tree building
    # ============================================================
    def _refresh_tree(self):
        self.tree.clear()
        self._tree_items = {}

        cameras = self.cam_manager.all()

        # 1) NVR groups
        nvrs = self.nvr_manager.all()
        for nvr in nvrs:
            nvr_cams = [c for c in cameras if c.nvr_uid == nvr.uid]
            if not nvr_cams:
                continue

            nvr_item = QTreeWidgetItem(self.tree)
            nvr_item.setText(0, f"📹  {nvr.name}   ({len(nvr_cams)} ch)   —   {nvr.ip}")
            nvr_item.setData(0, Qt.UserRole, ("nvr", nvr.uid))
            nvr_item.setForeground(0, QColor("#c39bd3"))
            f = nvr_item.font(0)
            f.setBold(True)
            nvr_item.setFont(0, f)
            nvr_item.setExpanded(True)

            def _ch_num(c):
                import re
                m = re.search(r"دوربین\s+(\d+)", c.name or "")
                if m:
                    return int(m.group(1))
                m = re.search(r"(\d+)", c.name or "")
                return int(m.group(1)) if m else 999
            nvr_cams.sort(key=_ch_num)

            for cam in nvr_cams:
                child = self._make_camera_item(cam, parent=nvr_item)
                self._tree_items[cam.uid] = child

        # 2) Custom groups
        for grp in self.group_manager.all():
            grp_cams = []
            for u in grp.camera_uids:
                c = self.cam_manager.get(u)
                if c is not None:
                    grp_cams.append(c)
            grp_item = QTreeWidgetItem(self.tree)
            grp_item.setText(0, f"📁  {grp.name}   ({len(grp_cams)})")
            grp_item.setData(0, Qt.UserRole, ("group", grp.uid))
            grp_item.setForeground(0, QColor(grp.color or "#f39c12"))
            f = grp_item.font(0)
            f.setBold(True)
            grp_item.setFont(0, f)
            grp_item.setExpanded(True)

            for cam in grp_cams:
                child = self._make_camera_item(cam, parent=grp_item,
                                               group_color=grp.color)
                # چند گروه می‌توانند یک دوربین داشته باشند؛ فقط اولین بار
                if cam.uid not in self._tree_items:
                    self._tree_items[cam.uid] = child

        # 3) Standalone cameras
        standalone = [c for c in cameras if not c.nvr_uid]
        if standalone:
            standalone_root = QTreeWidgetItem(self.tree)
            standalone_root.setText(0, f"📷  Standalone Cameras   ({len(standalone)})")
            standalone_root.setForeground(0, QColor("#85c1e9"))
            f = standalone_root.font(0)
            f.setBold(True)
            standalone_root.setFont(0, f)
            standalone_root.setExpanded(True)

            for cam in standalone:
                child = self._make_camera_item(cam, parent=standalone_root)
                self._tree_items[cam.uid] = child

        # Count
        nvr_count = len(nvrs)
        grp_count = self.group_manager.count()
        self.count_lbl.setText(
            f"{len(cameras)} camera(s)  •  {nvr_count} NVR(s)  •  {grp_count} group(s)"
        )

    def _make_camera_item(self, cam, parent, group_color=None):
        st = self._statuses.get(cam.uid, {}).get("status", "unknown")
        if not cam.enabled:
            st = "offline"
        icon, label, color = STATUS_ICONS.get(st, STATUS_ICONS["unknown"])

        main_url = cam.get_recording_url() or ""
        has_sub = bool(cam.get_motion_url()
                       and cam.get_motion_url() != main_url)
        stream_tag = "  [M+S]" if has_sub else "  [M]"

        text = f"  {icon}  {cam.name}   —   {label}{stream_tag}"
        item = QTreeWidgetItem(parent)
        item.setText(0, text)
        item.setData(0, Qt.UserRole, ("camera", cam.uid))
        if group_color:
            item.setForeground(0, QColor(group_color).lighter(140))
        else:
            item.setForeground(0, QColor(color))
        return item

    # ============================================================
    # Selection
    # ============================================================
    def _selected_node(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    def _on_tree_double_click(self, item, col):
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        kind, uid = data
        if kind == "camera":
            self._edit_camera(uid)
        else:
            item.setExpanded(not item.isExpanded())

    # ============================================================
    # Context menu
    # ============================================================
    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_style())

        if item is not None:
            data = item.data(0, Qt.UserRole)
        else:
            data = None

        if data:
            kind, uid = data
            if kind == "camera":
                cam = self.cam_manager.get(uid)
                a1 = QAction("✏ ویرایش", self)
                a1.triggered.connect(lambda: self._edit_camera(uid))
                menu.addAction(a1)
                menu.addSeparator()
                a2 = QAction("➕ افزودن به گروه…", self)
                a2.triggered.connect(lambda: self._add_camera_to_group(uid))
                menu.addAction(a2)
                groups_of_cam = self.group_manager.groups_containing(uid)
                if groups_of_cam:
                    sub = menu.addMenu("➖ حذف از گروه")
                    sub.setStyleSheet(self._menu_style())
                    for g in groups_of_cam:
                        a_rm = QAction(g.name, self)
                        a_rm.triggered.connect(
                            lambda checked=False, gg=g:
                            self._remove_camera_from_group(gg.uid, uid))
                        sub.addAction(a_rm)
                menu.addSeparator()
                a3 = QAction("🗑 حذف دوربین", self)
                a3.triggered.connect(lambda: self._delete_camera_by_uid(uid))
                menu.addAction(a3)

            elif kind == "nvr":
                a1 = QAction("⚙ ویرایش NVR", self)
                a1.triggered.connect(lambda: self._edit_nvr(uid))
                menu.addAction(a1)
                a2 = QAction("🔄 کشف مجدد کانال‌ها", self)
                a2.triggered.connect(lambda: self._rediscover_nvr(uid))
                menu.addAction(a2)
                menu.addSeparator()
                a3 = QAction("🗑 حذف NVR و کانال‌ها", self)
                a3.triggered.connect(lambda: self._delete_nvr(uid))
                menu.addAction(a3)

            elif kind == "group":
                a1 = QAction("✏ ویرایش گروه", self)
                a1.triggered.connect(lambda: self._edit_group(uid))
                menu.addAction(a1)
                menu.addSeparator()
                a2 = QAction("🗑 حذف گروه", self)
                a2.triggered.connect(lambda: self._delete_group(uid))
                menu.addAction(a2)

        else:
            a1 = QAction("➕ افزودن دوربین", self)
            a1.triggered.connect(self._add_camera)
            menu.addAction(a1)
            a2 = QAction("➕ افزودن NVR", self)
            a2.triggered.connect(self._add_nvr)
            menu.addAction(a2)
            menu.addSeparator()
            a3 = QAction("➕ گروه جدید", self)
            a3.triggered.connect(lambda: self._open_new_group())
            menu.addAction(a3)
            a4 = QAction("🔄 بازخوانی", self)
            a4.triggered.connect(self._refresh_tree)
            menu.addAction(a4)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ============================================================
    # Actions
    # ============================================================
    def _add_camera(self):
        dlg = CameraDialog(None, self)
        if dlg.exec() == CameraDialog.Accepted:
            cam = dlg.get_camera()
            self.cam_manager.add(cam)
            settings_manager.append_audit("camera.add", "-",
                {"uid": cam.uid, "name": cam.name})
            self._refresh_tree()
            self.cameras_changed.emit()
            self._auto_apply_to_nvr()

    def _add_nvr(self):
        from ui.dialogs.nvr_dialog import NVRDialog

        dlg = NVRDialog(None, self)
        if dlg.exec() != NVRDialog.Accepted:
            return

        new_nvr = dlg.get_nvr()
        if not new_nvr.name or not new_nvr.ip:
            return

        self.nvr_manager.add(new_nvr)
        settings_manager.append_audit("nvr.add", "-",
            {"uid": new_nvr.uid, "name": new_nvr.name, "ip": new_nvr.ip})

        added = self.nvr_manager.add_channels_as_cameras(
            new_nvr, self.cam_manager, only_enabled=True
        )

        self._refresh_tree()
        self.cameras_changed.emit()

        if added > 0:
            dlg_info(
                self,
                f"NVR «{new_nvr.name}» اضافه شد.\n"
                f"{added} کانال به عنوان دوربین اضافه شد (با ضبط دائم).",
                "افزودن NVR"
            )
            self._auto_apply_to_nvr()
        else:
            dlg_info(
                self,
                f"NVR «{new_nvr.name}» اضافه شد ولی هیچ کانال جدیدی اضافه نشد.",
                "افزودن NVR"
            )

    def _edit_selected(self):
        data = self._selected_node()
        if not data:
            dlg_info(self, "یک مورد انتخاب کنید.")
            return
        kind, uid = data
        if kind == "camera":
            self._edit_camera(uid)
        elif kind == "nvr":
            self._edit_nvr(uid)
        elif kind == "group":
            self._edit_group(uid)

    def _edit_camera(self, uid: str):
        old_cam = self.cam_manager.get(uid)
        if old_cam is None:
            return
        old_state = self._mediatmx_state(old_cam)
        dlg = CameraDialog(old_cam, self)
        if dlg.exec() == CameraDialog.Accepted:
            updated = dlg.get_camera()
            self.cam_manager.update(uid, updated)
            settings_manager.append_audit("camera.edit", "-", {"uid": uid})
            self._refresh_tree()
            self.cameras_changed.emit()
            if old_state != self._mediatmx_state(updated):
                self._auto_apply_to_nvr()

    def _edit_nvr(self, uid: str):
        from ui.dialogs.nvr_dialog import NVRDialog
        nvr = self.nvr_manager.get(uid)
        if nvr is None:
            return
        dlg = NVRDialog(nvr, self)
        if dlg.exec() == NVRDialog.Accepted:
            updated = dlg.get_nvr()
            self.nvr_manager.update(uid, updated)
            self._refresh_tree()

    def _rediscover_nvr(self, uid: str):
        """کشف مجدد کانال‌های NVR (افزودن کانال‌های جدید)."""
        from ui.dialogs.nvr_dialog import NVRDialog
        nvr = self.nvr_manager.get(uid)
        if nvr is None:
            return
        dlg = NVRDialog(nvr, self)
        if dlg.exec() == NVRDialog.Accepted:
            updated = dlg.get_nvr()
            self.nvr_manager.update(uid, updated)
            added = self.nvr_manager.add_channels_as_cameras(
                updated, self.cam_manager, only_enabled=True
            )
            self._refresh_tree()
            self.cameras_changed.emit()
            if added > 0:
                dlg_info(self, f"{added} کانال جدید اضافه شد.")
                self._auto_apply_to_nvr()
            else:
                dlg_info(self, "هیچ کانال جدیدی نبود.")

    @staticmethod
    def _mediatmx_state(cam):
        try:
            profiles = getattr(cam, "stream_profiles", []) or []
            prof_sig = tuple((p.id, p.url, p.rtsp_path) for p in profiles)
        except Exception:
            prof_sig = ()
        return {
            "enabled": cam.enabled, "ip": cam.ip, "port": cam.port,
            "user": cam.user, "password": cam.password,
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

    def _delete_selected(self):
        data = self._selected_node()
        if not data:
            return
        kind, uid = data

        if kind == "camera":
            self._delete_camera_by_uid(uid)
        elif kind == "nvr":
            self._delete_nvr(uid)
        elif kind == "group":
            self._delete_group(uid)

    def _delete_camera_by_uid(self, uid: str):
        c = self.cam_manager.get(uid)
        if c is None:
            return
        if not dlg_question(self, f'حذف دوربین "{c.name}"؟', "حذف دوربین"):
            return
        self.cam_manager.delete(uid)
        # حذف از گروه‌ها
        try:
            self.group_manager.remove_camera(uid)
        except Exception:
            pass
        settings_manager.append_audit("camera.delete", "-", {"uid": uid})
        self._refresh_tree()
        self.cameras_changed.emit()
        self._auto_apply_to_nvr()

    def _delete_nvr(self, uid: str):
        nvr = self.nvr_manager.get(uid)
        if nvr is None:
            return
        if not dlg_question(
            self,
            f'حذف NVR "{nvr.name}" و همه‌ی کانال‌هایش؟',
            "حذف NVR"
        ):
            return
        # حذف کانال‌ها
        for cam in list(self.cam_manager.all()):
            if cam.nvr_uid == uid:
                self.cam_manager.delete(cam.uid)
                try:
                    self.group_manager.remove_camera(cam.uid)
                except Exception:
                    pass
        self.nvr_manager.delete(uid)
        settings_manager.append_audit("nvr.delete", "-", {"uid": uid})
        self._refresh_tree()
        self.cameras_changed.emit()
        self._auto_apply_to_nvr()

    # ============================================================
    # Groups
    # ============================================================
    def _manage_groups(self):
        try:
            from ui.dialogs.group_dialog import GroupDialog
            gm = self.group_manager

            menu = QMenu(self)
            menu.setStyleSheet(self._menu_style())
            a_new = QAction("➕ گروه جدید", self)
            menu.addAction(a_new)
            if gm.count() > 0:
                menu.addSeparator()
                for g in gm.all():
                    sub = menu.addMenu(f"📁 {g.name}  ({len(g.camera_uids)})")
                    sub.setStyleSheet(self._menu_style())
                    a_edit = QAction("✏ ویرایش", self)
                    a_edit.triggered.connect(
                        lambda checked=False, gg=g: self._edit_group(gg.uid))
                    sub.addAction(a_edit)
                    a_del = QAction("🗑 حذف", self)
                    a_del.triggered.connect(
                        lambda checked=False, gg=g: self._delete_group(gg.uid))
                    sub.addAction(a_del)

            pos = self.mapToGlobal(self.rect().topRight())
            chosen = menu.exec(pos)
            if chosen is a_new:
                self._open_new_group()
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    def _open_new_group(self):
        try:
            from ui.dialogs.group_dialog import GroupDialog
            dlg = GroupDialog(None, self)
            if dlg.exec() == GroupDialog.Accepted:
                self.group_manager.add(dlg.get_group())
                self._refresh_tree()
                self.cameras_changed.emit()
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    def _edit_group(self, uid: str):
        try:
            from ui.dialogs.group_dialog import GroupDialog
            grp = self.group_manager.get(uid)
            if grp is None:
                return
            dlg = GroupDialog(grp, self)
            if dlg.exec() == GroupDialog.Accepted:
                self.group_manager.update(uid, dlg.get_group())
                self._refresh_tree()
                self.cameras_changed.emit()
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    def _delete_group(self, uid: str):
        grp = self.group_manager.get(uid)
        if grp is None:
            return
        if not dlg_question(self, f'حذف گروه "{grp.name}"؟', "حذف گروه"):
            return
        self.group_manager.delete(uid)
        self._refresh_tree()
        self.cameras_changed.emit()

    def _add_camera_to_group(self, cam_uid: str):
        gm = self.group_manager
        groups = gm.all()
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_style())

        if groups:
            for g in groups:
                if cam_uid in g.camera_uids:
                    continue  # قبلاً عضو است
                a = QAction(f"➕ {g.name}", self)
                a.triggered.connect(
                    lambda checked=False, gg=g:
                    self._append_camera_to_group(gg.uid, cam_uid))
                menu.addAction(a)
            menu.addSeparator()

        a_new = QAction("➕ گروه جدید…", self)
        a_new.triggered.connect(
            lambda: self._new_group_with_camera(cam_uid))
        menu.addAction(a_new)

        pos = self.tree.viewport().mapToGlobal(
            self.tree.viewport().rect().center())
        menu.exec(pos)

    def _append_camera_to_group(self, group_uid: str, cam_uid: str):
        grp = self.group_manager.get(group_uid)
        if grp is None:
            return
        if cam_uid not in grp.camera_uids:
            grp.camera_uids.append(cam_uid)
            self.group_manager.update(group_uid, grp)
            self._refresh_tree()

    def _remove_camera_from_group(self, group_uid: str, cam_uid: str):
        grp = self.group_manager.get(group_uid)
        if grp is None:
            return
        grp.camera_uids = [u for u in grp.camera_uids if u != cam_uid]
        self.group_manager.update(group_uid, grp)
        self._refresh_tree()

    def _new_group_with_camera(self, cam_uid: str):
        try:
            from ui.dialogs.group_dialog import GroupDialog
            dlg = GroupDialog(None, self)
            if dlg.exec() == GroupDialog.Accepted:
                grp = dlg.get_group()
                if cam_uid not in grp.camera_uids:
                    grp.camera_uids.append(cam_uid)
                self.group_manager.add(grp)
                self._refresh_tree()
                self.cameras_changed.emit()
        except Exception as e:
            dlg_warning(self, f"خطا: {e}")

    # ============================================================
    # Lifecycle
    # ============================================================
    def on_show(self):
        self._refresh_tree()