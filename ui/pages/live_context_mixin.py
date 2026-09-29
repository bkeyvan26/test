# -*- coding: utf-8 -*-
"""Context menu mixin for LivePage."""
from PySide6.QtWidgets import (
    QMenu, QDialog, QVBoxLayout, QFormLayout,
    QDialogButtonBox, QLabel
)
from PySide6.QtCore import Qt

from ui import theme
from ui.icons import make_icon


class LiveContextMenuMixin:
    """Mix-in providing context menu + camera info dialog.
    Requires self to have: cell_to_uid, uid_to_cell, cam_manager,
    grid, _statuses, and methods: _assign_camera_to_cell,
    _on_cell_close, _enter_fullscreen_mode, _exit_fullscreen_mode,
    _take_snapshot, _copy_url.
    """

    def _menu_qss(self):
        return (
            f"QMenu {{ background: {theme.COLOR_BG_CARD}; "
            f"color: {theme.COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {theme.COLOR_BORDER}; "
            f"border-radius: 8px; padding: 6px; font-size: 12px; }} "
            f"QMenu::item {{ padding: 7px 28px 7px 10px; "
            f"border-radius: 5px; min-width: 190px; }} "
            f"QMenu::item:selected {{ background: {theme.COLOR_ACCENT}; "
            f"color: #ffffff; }} "
            f"QMenu::item:disabled {{ color: {theme.COLOR_TEXT_MUTED}; "
            f"padding: 5px 28px 5px 10px; }} "
            f"QMenu::separator {{ height: 1px; "
            f"background: {theme.COLOR_BORDER}; margin: 5px 10px; }} "
            f"QMenu::icon {{ padding-left: 6px; }} "
            f"QMenu::right-arrow {{ width: 12px; }}"
        )

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
                lambda checked=False, i=idx: self._enter_fullscreen_mode(i)
            )
        menu.addSeparator()

        act_reset_zoom = menu.addAction("🔍   بازنشانی زوم")
        act_reset_zoom.triggered.connect(
            lambda checked=False, i=idx: self._reset_cell_zoom(i)
        )
        menu.addSeparator()

        act_snap = menu.addAction(
            make_icon("camera", theme.COLOR_TEXT_PRIMARY, 14), "عکس فوری"
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