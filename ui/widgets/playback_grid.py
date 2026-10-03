# -*- coding: utf-8 -*-
"""K1 VMS — Playback Grid (Phase 6.7.4: crash-safe fullscreen)"""
from PySide6.QtWidgets import QWidget, QGridLayout, QMenu
from PySide6.QtCore import Qt, Signal, QTimer, QPoint
from PySide6.QtGui import QAction

from ui import theme
from ui.widgets.playback_cell import PlaybackCell


MIME_PLAYBACK_CAM = "application/x-k1vms-playback-camera"


class PlaybackGrid(QWidget):
    cell_clicked = Signal(int)
    cell_double_clicked = Signal(int)
    cell_close = Signal(int)
    cell_context = Signal(int, QPoint)
    cell_state = Signal(int, str)
    cell_position = Signal(int, float)
    cell_frame_ready = Signal(int)
    camera_dropped = Signal(int, str)
    fullscreen_about_to_change = Signal(int)

    LAYOUTS = {
        "1x1": [(0, 0, 1, 1)],
        "2x2": [(0, 0, 1, 1), (0, 1, 1, 1),
                (1, 0, 1, 1), (1, 1, 1, 1)],
        "1+1": [(0, 0, 1, 1), (0, 1, 1, 1)],
        "3x3": [(r, c, 1, 1) for r in range(3) for c in range(3)],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #000;")
        self.setAcceptDrops(True)
        self._grid = QGridLayout(self)
        self._grid.setSpacing(3)
        self._grid.setContentsMargins(3, 3, 3, 3)

        self._cells = []
        self._active_idx = 0
        self._fullscreen_idx = None
        self._layout_key = "2x2"
        self._dbl_busy = False  # ★ guard

        self._spin_timer = QTimer(self)
        self._spin_timer.timeout.connect(self._tick_spinners)
        self._spin_timer.start(60)

        self.set_layout("2x2")

    def _tick_spinners(self):
        for c in self._cells:
            try:
                c.tick_spinner()
            except RuntimeError:
                pass
            except Exception:
                pass

    # ============================================================
    def get_layout_key(self):
        return self._layout_key

    def get_cells(self):
        return self._cells

    def get_active_idx(self):
        return self._active_idx

    def get_active_cell(self):
        if 0 <= self._active_idx < len(self._cells):
            return self._cells[self._active_idx]
        return None

    def get_cell(self, idx):
        if 0 <= idx < len(self._cells):
            return self._cells[idx]
        return None

    def find_cell_by_cam(self, cam_uid):
        for i, c in enumerate(self._cells):
            try:
                if c.cam_uid() == cam_uid:
                    return i
            except RuntimeError:
                continue
        return -1

    def first_empty_idx(self):
        for i, c in enumerate(self._cells):
            try:
                if not c.cam_uid():
                    return i
            except RuntimeError:
                continue
        return -1

    def is_fullscreen(self):
        return self._fullscreen_idx is not None

    def get_fullscreen_idx(self):
        return self._fullscreen_idx

    # ============================================================
    def set_layout(self, key):
        if key not in self.LAYOUTS:
            key = "2x2"
        self._layout_key = key
        self._fullscreen_idx = None
        self._rebuild()
        self._arrange()

    def _rebuild(self):
        saved = []
        for c in self._cells:
            try:
                saved.append({
                    "uid": c.cam_uid(),
                    "name": c.cam_name(),
                    "day": c.day(),
                    "segments": c.segments(),
                })
            except RuntimeError:
                saved.append({"uid": None, "name": "",
                              "day": None, "segments": []})

        # ★ متوقف‌سازی paint قبل از حذف
        self.setUpdatesEnabled(False)
        try:
            while self._grid.count():
                self._grid.takeAt(0)
            for c in self._cells:
                try:
                    c.mark_dead()  # ★ غیرفعال‌سازی قبل از deleteLater
                except Exception:
                    pass
                try:
                    c.setParent(None)
                    c.deleteLater()
                except Exception:
                    pass
            self._cells = []
        finally:
            self.setUpdatesEnabled(True)

        n = len(self.LAYOUTS[self._layout_key])
        for i in range(n):
            cell = PlaybackCell(i)
            cell.clicked.connect(self._on_click)
            cell.double_clicked.connect(self._on_double_click)
            cell.context_requested.connect(self.cell_context.emit)
            cell.state_changed.connect(self.cell_state.emit)
            cell.position_changed.connect(self.cell_position.emit)
            cell.frame_ready.connect(self.cell_frame_ready.emit)
            cell.close_requested.connect(self.cell_close.emit)
            self._cells.append(cell)

        for i, s in enumerate(saved[:n]):
            if s["uid"]:
                try:
                    self._cells[i].assign(s["uid"], s["name"],
                                           s["day"], s["segments"])
                except Exception:
                    pass
        self._update_active()

    def _arrange(self):
        # ★ مهم: غیرفعال‌سازی paint حین rearrange
        self.setUpdatesEnabled(False)
        try:
            while self._grid.count():
                self._grid.takeAt(0)

            for r in range(4):
                self._grid.setRowStretch(r, 0)
            for c in range(4):
                self._grid.setColumnStretch(c, 0)

            if self._fullscreen_idx is not None:
                idx = self._fullscreen_idx
                if 0 <= idx < len(self._cells):
                    self._cells[idx].show()
                    self._grid.addWidget(self._cells[idx], 0, 0, 1, 1)
                for i, c in enumerate(self._cells):
                    if i != idx:
                        c.hide()
                self._grid.setRowStretch(0, 1)
                self._grid.setColumnStretch(0, 1)
            else:
                spec = self.LAYOUTS[self._layout_key]
                for i, (r, c, rs, cs) in enumerate(spec):
                    if i < len(self._cells):
                        self._cells[i].show()
                        self._grid.addWidget(self._cells[i],
                                              r, c, rs, cs)
                rows = max(r + rs for (r, c, rs, cs) in spec)
                cols = max(c + cs for (r, c, rs, cs) in spec)
                for r in range(rows):
                    self._grid.setRowStretch(r, 1)
                for c in range(cols):
                    self._grid.setColumnStretch(c, 1)

            self._grid.invalidate()
        finally:
            self.setUpdatesEnabled(True)
            self.update()

    def _update_active(self):
        for i, c in enumerate(self._cells):
            try:
                c.set_active(i == self._active_idx)
            except RuntimeError:
                pass
            except Exception:
                pass

    # ============================================================
    def _on_click(self, idx):
        if not (0 <= idx < len(self._cells)):
            return
        self._active_idx = idx
        self._update_active()
        try:
            self.cell_clicked.emit(idx)
        except RuntimeError:
            pass

    def _on_double_click(self, idx):
        """★ کل عملیات را defer کن تا re-entrancy رخ ندهد."""
        if self._dbl_busy:
            return
        if not (0 <= idx < len(self._cells)):
            return
        self._dbl_busy = True
        # خارج از event handler فعلی
        QTimer.singleShot(0, lambda: self._do_double_click(idx))

    def _do_double_click(self, idx):
        try:
            if not (0 <= idx < len(self._cells)):
                return
            # 1) ابتدا emit
            try:
                self.fullscreen_about_to_change.emit(idx)
            except RuntimeError:
                pass
            # 2) toggle fullscreen (با updates disabled)
            if self._fullscreen_idx == idx:
                self._fullscreen_idx = None
            else:
                self._fullscreen_idx = idx
            self._arrange()
            # 3) signal
            try:
                self.cell_double_clicked.emit(idx)
            except RuntimeError:
                pass
        except Exception as e:
            print(f"[PlaybackGrid._do_double_click] {e}")
        finally:
            QTimer.singleShot(150, self._clear_dbl_busy)

    def _clear_dbl_busy(self):
        self._dbl_busy = False

    # ============================================================
    def toggle_fullscreen(self, idx):
        if not (0 <= idx < len(self._cells)):
            return
        if self._fullscreen_idx == idx:
            self._fullscreen_idx = None
        else:
            self._fullscreen_idx = idx
        self._arrange()

    def exit_fullscreen(self):
        if self._fullscreen_idx is not None:
            self._fullscreen_idx = None
            self._arrange()

    # ============================================================
    def assign_to_first_empty(self, cam_uid, cam_name, day, segments):
        idx = self.first_empty_idx()
        if idx < 0:
            idx = self._active_idx
        self.assign_to_cell(idx, cam_uid, cam_name, day, segments)
        return idx

    def assign_to_cell(self, idx, cam_uid, cam_name, day, segments):
        if not (0 <= idx < len(self._cells)):
            return
        old = self.find_cell_by_cam(cam_uid)
        if old >= 0 and old != idx:
            try:
                self._cells[old].clear()
            except Exception:
                pass
        try:
            self._cells[idx].assign(cam_uid, cam_name, day, segments)
        except Exception as e:
            print(f"[PlaybackGrid.assign_to_cell] {e}")
            return
        self._active_idx = idx
        self._update_active()

    def clear_cell(self, idx):
        if 0 <= idx < len(self._cells):
            try:
                self._cells[idx].clear()
            except Exception:
                pass

    def clear_all(self):
        for c in self._cells:
            try:
                c.clear()
            except Exception:
                pass
        self._active_idx = 0
        self._update_active()

    # ============================================================
    # Drag & Drop
    # ============================================================
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_PLAYBACK_CAM):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_PLAYBACK_CAM):
            e.acceptProposedAction()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(MIME_PLAYBACK_CAM):
            return
        pos = e.position().toPoint()
        target_idx = -1
        for i, cell in enumerate(self._cells):
            try:
                if not cell.isVisible():
                    continue
                cell_pos = cell.mapTo(self, QPoint(0, 0))
                cx = cell_pos.x()
                cy = cell_pos.y()
                cw = cell.width()
                ch = cell.height()
                if (cx <= pos.x() < cx + cw and
                        cy <= pos.y() < cy + ch):
                    target_idx = i
                    break
            except RuntimeError:
                continue

        if target_idx < 0:
            target_idx = self.first_empty_idx()
            if target_idx < 0:
                target_idx = self._active_idx

        try:
            uid = bytes(e.mimeData().data(MIME_PLAYBACK_CAM)).decode("utf-8")
            uid = uid.strip()
            if uid:
                self.camera_dropped.emit(target_idx, uid)
                e.acceptProposedAction()
        except Exception as ex:
            print(f"[PlaybackGrid.drop] {ex}")