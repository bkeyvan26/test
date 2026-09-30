# -*- coding: utf-8 -*-
"""
K1 VMS — Live grid with zoom + hover signals + modern loading spinner
(Phase 6.5: bad-frame detection)
"""
import json
import math
import numpy as np
from PySide6.QtWidgets import QWidget, QGridLayout, QFrame, QApplication
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QMimeData, QTimer
from PySide6.QtGui import (
    QImage, QPainter, QColor, QFont, QPen, QDrag, QPixmap,
    QConicalGradient
)
from ui import theme


MIME_CAMERA = "application/x-k1vms-camera"

# ============================================================
# ★ Loading spinner resources (module-level, cheap)
# ============================================================
_SP_OVERLAY      = QColor(8, 8, 12, 170)
_SP_TRACK        = QColor(48, 54, 66, 220)
_SP_ARC          = QColor(22, 160, 133)
_SP_ARC_HI       = QColor(26, 188, 156)
_SP_DOT          = QColor(180, 190, 200, 80)
_SP_TEXT         = QColor(190, 200, 215)
_SP_TEXT_SHADOW  = QColor(0, 0, 0, 180)
_SP_FONT         = QFont("Sans", 8)


def _uniform(n):
    return [(r, c, 1, 1) for r in range(n) for c in range(n)]


LAYOUTS = [
    {"key": "1x1", "name": "1 × 1", "rows": 1, "cols": 1, "cells": _uniform(1)},
    {"key": "2x2", "name": "2 × 2", "rows": 2, "cols": 2, "cells": _uniform(2)},
    {"key": "3x3", "name": "3 × 3", "rows": 3, "cols": 3, "cells": _uniform(3)},
    {"key": "4x4", "name": "4 × 4", "rows": 4, "cols": 4, "cells": _uniform(4)},
    {"key": "5x5", "name": "5 × 5", "rows": 5, "cols": 5, "cells": _uniform(5)},
    {"key": "6x6", "name": "6 × 6", "rows": 6, "cols": 6, "cells": _uniform(6)},
    {"key": "7x7", "name": "7 × 7", "rows": 7, "cols": 7, "cells": _uniform(7)},
    {"key": "8x8", "name": "8 × 8", "rows": 8, "cols": 8, "cells": _uniform(8)},
    {"key": "1+3", "name": "1 + 3 (Hero)", "rows": 3, "cols": 3,
     "cells": [(0, 0, 2, 2), (0, 2, 1, 1), (1, 2, 1, 1), (2, 0, 1, 3)]},
    {"key": "1+4", "name": "1 + 4 (Focus)", "rows": 2, "cols": 4,
     "cells": [(0, 0, 2, 2), (0, 2, 1, 1), (0, 3, 1, 1),
               (1, 2, 1, 1), (1, 3, 1, 1)]},
    {"key": "1+5", "name": "1 + 5 (Focus)", "rows": 3, "cols": 3,
     "cells": [(0, 0, 2, 2), (0, 2, 1, 1), (1, 2, 1, 1),
               (2, 0, 1, 1), (2, 1, 1, 1), (2, 2, 1, 1)]},
    {"key": "1+8", "name": "1 + 8 (Tall)", "rows": 4, "cols": 3,
     "cells": [(0, 0, 4, 1),
               (0, 1, 1, 1), (0, 2, 1, 1),
               (1, 1, 1, 1), (1, 2, 1, 1),
               (2, 1, 1, 1), (2, 2, 1, 1),
               (3, 1, 1, 1), (3, 2, 1, 1)]},
    {"key": "1+9", "name": "1 + 9 (Tall)", "rows": 3, "cols": 4,
     "cells": [(0, 0, 3, 1),
               (0, 1, 1, 1), (0, 2, 1, 1), (0, 3, 1, 1),
               (1, 1, 1, 1), (1, 2, 1, 1), (1, 3, 1, 1),
               (2, 1, 1, 1), (2, 2, 1, 1), (2, 3, 1, 1)]},
    {"key": "4+1", "name": "4 + 1 (Stack)", "rows": 3, "cols": 2,
     "cells": [(0, 0, 1, 1), (0, 1, 1, 1),
               (1, 0, 1, 1), (1, 1, 1, 1),
               (2, 0, 1, 2)]},
    {"key": "2+4", "name": "2 + 4 (Split)", "rows": 2, "cols": 4,
     "cells": [(0, 0, 1, 2), (0, 2, 1, 2),
               (1, 0, 1, 1), (1, 1, 1, 1),
               (1, 2, 1, 1), (1, 3, 1, 1)]},
    {"key": "3+3", "name": "3 + 3 (Grid)", "rows": 2, "cols": 3,
     "cells": [(0, 0, 1, 1), (0, 1, 1, 1), (0, 2, 1, 1),
               (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1)]},
    {"key": "cinema", "name": "Cinema", "rows": 2, "cols": 5,
     "cells": [(0, 0, 1, 5),
               (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1),
               (1, 3, 1, 1), (1, 4, 1, 1)]},
    {"key": "4+2", "name": "8 + 2 (Wide)", "rows": 3, "cols": 4,
     "cells": [(0, 0, 1, 1), (0, 1, 1, 1), (0, 2, 1, 1), (0, 3, 1, 1),
               (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1), (1, 3, 1, 1),
               (2, 0, 1, 2), (2, 2, 1, 2)]},
    {"key": "pyramid", "name": "Pyramid", "rows": 3, "cols": 3,
     "cells": [(0, 0, 1, 3),
               (1, 0, 1, 1), (1, 2, 1, 1),
               (2, 0, 1, 1), (2, 1, 1, 1), (2, 2, 1, 1)]},
    {"key": "6+1", "name": "6 + 1 (Stack)", "rows": 3, "cols": 3,
     "cells": [(0, 0, 1, 1), (0, 1, 1, 1), (0, 2, 1, 1),
               (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1),
               (2, 0, 1, 3)]},
]


def get_layout(key):
    for L in LAYOUTS:
        if L["key"] == key:
            return L
    return LAYOUTS[1]


def get_uniform_layouts():
    return [L for L in LAYOUTS if "×" in L["name"]]


def get_focus_layouts():
    return [L for L in LAYOUTS if "×" not in L["name"]]


# ============================================================
# LiveCell
# ============================================================
class LiveCell(QFrame):
    clicked = Signal(int)
    double_clicked = Signal(int)
    close_requested = Signal(int)
    context_requested = Signal(int, object)
    drop_camera = Signal(int, str)
    swap_cells = Signal(int, int)
    hover_entered = Signal(int)
    hover_left = Signal(int)

    MIN_ZOOM = 1.0
    MAX_ZOOM = 6.0
    ZOOM_STEP = 1.25

    def __init__(self, idx, parent=None):
        super().__init__(parent)
        self.idx = idx
        self._uid = ""
        self._frame = None
        self._cached_qimage = None
        self._cached_seq = -1
        self._title = ""
        self._status = ""
        self._show_close = False
        self._recording = False
        self._motion = False
        self._border_color = "#232830"
        self._drag_start = None

        self._zoom = 1.0
        self._zoom_anchor = None

        self._spinner_angle = 0

        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "LiveCell { background: #0a0a0a; "
            "border: 2px solid #232830; border-radius: 4px; }"
        )
        self.setMinimumSize(20, 20)
        self.setMouseTracking(True)

    # ---------- loading state ----------
    def is_loading(self) -> bool:
        return self._frame is None and bool(self._uid)

    def set_spinner_angle(self, angle: int):
        self._spinner_angle = angle
        if self.is_loading():
            self.update()

    # ---------- zoom ----------
    def zoom_factor(self):
        return self._zoom

    def reset_zoom(self):
        self._zoom = 1.0
        self._zoom_anchor = None
        self.update()

    def zoom_at(self, factor, local_x, local_y, view_w, view_h, img_w, img_h):
        new_zoom = self._zoom * factor
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        if abs(new_zoom - self._zoom) < 0.001:
            return

        s = min(view_w / img_w, view_h / img_h) if img_w and img_h else 1.0
        dw, dh = int(img_w * s), int(img_h * s)
        ox, oy = (view_w - dw) // 2, (view_h - dh) // 2

        px = (local_x - ox) / max(1, dw)
        py = (local_y - oy) / max(1, dh)
        px = max(0.0, min(1.0, px))
        py = max(0.0, min(1.0, py))

        ax = px * img_w
        ay = py * img_h

        self._zoom = new_zoom
        self._zoom_anchor = (ax, ay)
        self.update()

    # ---------- state ----------
    def set_uid(self, uid):
        self._uid = uid or ""
        self.update()

    def get_uid(self):
        return self._uid

    def get_state(self):
        return {
            "uid": self._uid,
            "title": self._title,
            "status": self._status,
            "frame": self._frame,
            "seq": self._cached_seq,
            "recording": self._recording,
            "motion": self._motion,
        }

    def set_state(self, state):
        self._uid = state.get("uid", "")
        self._title = state.get("title", "")
        self._status = state.get("status", "")
        self._frame = state.get("frame")
        self._cached_seq = state.get("seq", -1)
        self._cached_qimage = None
        self._recording = state.get("recording", False)
        self._motion = state.get("motion", False)
        self.update()

    # ★ Phase 6.5: bad-frame detection
    def set_frame(self, payload):
        if isinstance(payload, tuple) and len(payload) == 2:
            seq, frame = payload
        else:
            seq, frame = -1, payload
        if seq != -1 and seq == self._cached_seq:
            return
        self._cached_seq = seq

        # ★ تشخیص فریم خراب (نصف سبز)
        try:
            if frame is not None and hasattr(frame, "shape"):
                h = frame.shape[0]
                if h > 20:
                    # ناحیه پایین (۷۰٪ به بعد)
                    bottom = frame[int(h * 0.7):, :, :]
                    mean_rgb = bottom.reshape(-1, 3).mean(axis=0)
                    r, g, b = float(mean_rgb[0]), float(mean_rgb[1]), float(mean_rgb[2])
                    # اگر G خیلی بیشتر از R و B بود → فریم خراب
                    if g > 100 and (g - r) > 60 and (g - b) > 60:
                        # نگه‌داشتن فریم قبلی
                        return
        except Exception:
            pass

        self._frame = frame
        self._cached_qimage = None
        self.update()

    def clear(self):
        self._frame = None
        self._cached_qimage = None
        self._cached_seq = -1
        self._uid = ""
        self._zoom = 1.0
        self._zoom_anchor = None
        self.update()

    def set_title(self, t):
        self._title = t or ""
        self.update()

    def set_status(self, s):
        self._status = s or ""
        self.update()

    def set_show_close(self, on):
        self._show_close = bool(on)
        self.update()

    def set_recording(self, on):
        self._recording = bool(on)
        self.update()

    def set_motion(self, on):
        self._motion = bool(on)
        self.update()

    def set_border_color(self, color):
        self._border_color = color
        self.setStyleSheet(
            f"LiveCell {{ background: #0a0a0a; "
            f"border: 2px solid {color}; border-radius: 4px; }}"
        )
        self.update()

    def _close_rect(self):
        r = 10
        return QRect(self.width() - r * 2 - 6, 6, r * 2, r * 2)

    # ---------- hover ----------
    def enterEvent(self, e):
        self.hover_entered.emit(self.idx)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover_left.emit(self.idx)
        super().leaveEvent(e)

    # ---------- drag out ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_start = e.position().toPoint()

            if self._show_close and self._frame is not None:
                if self._close_rect().contains(e.position().toPoint()):
                    self.close_requested.emit(self.idx)
                    self._drag_start = None
                    return

            self.clicked.emit(self.idx)

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.LeftButton):
            return
        if self._drag_start is None:
            return
        if not self._uid:
            return

        if (e.position().toPoint() - self._drag_start).manhattanLength() < \
                QApplication.startDragDistance():
            return

        md = QMimeData()
        payload = json.dumps({"uid": self._uid, "from_cell": self.idx})
        md.setData(MIME_CAMERA, payload.encode("utf-8"))
        md.setText(self._uid)

        drag = QDrag(self)
        drag.setMimeData(md)

        if self._frame is not None and self._cached_qimage is not None:
            try:
                thumb = QPixmap.fromImage(self._cached_qimage).scaled(
                    160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                drag.setPixmap(thumb)
                drag.setHotSpot(QPoint(thumb.width() // 2, thumb.height() // 2))
            except Exception:
                pass

        self._drag_start = None
        drag.exec(Qt.MoveAction)

    def mouseReleaseEvent(self, e):
        self._drag_start = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._zoom > 1.0:
                self.reset_zoom()
                return
            self.double_clicked.emit(self.idx)

    def contextMenuEvent(self, e):
        self.context_requested.emit(self.idx, self.mapToGlobal(e.pos()))

    # ---------- wheel = zoom ----------
    def wheelEvent(self, e):
        if self._frame is None:
            e.ignore()
            return

        delta = e.angleDelta().y()
        if delta == 0:
            e.ignore()
            return

        factor = self.ZOOM_STEP if delta > 0 else 1.0 / self.ZOOM_STEP

        pos = e.position()
        vh, vw = self._frame.shape[:2]
        self.zoom_at(factor, pos.x(), pos.y(),
                     self.width(), self.height(), vw, vh)
        e.accept()

    # ---------- drag in ----------
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME_CAMERA) or md.hasText():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME_CAMERA) or md.hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME_CAMERA):
            try:
                raw = bytes(md.data(MIME_CAMERA)).decode("utf-8")
                data = json.loads(raw)
                uid = (data.get("uid") or "").strip()
                src = int(data.get("from_cell", -1))
                if src >= 0 and src != self.idx:
                    self.swap_cells.emit(src, self.idx)
                elif uid:
                    self.drop_camera.emit(self.idx, uid)
            except Exception as ex:
                print(f"[LiveCell.drop] {ex}")
        elif md.hasText():
            uid = md.text().strip()
            if uid:
                self.drop_camera.emit(self.idx, uid)
        e.acceptProposedAction()

    # ============================================================
    # Modern Loading Spinner
    # ============================================================
    def _draw_loading(self, p: QPainter):
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _SP_OVERLAY)

        cx, cy = w // 2, h // 2
        r = max(12, min(30, min(w, h) // 6))

        p.setBrush(Qt.NoBrush)
        pen = QPen(_SP_TRACK, 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawEllipse(QPoint(cx, cy), r, r)

        p.setRenderHint(QPainter.Antialiasing, True)

        pen = QPen(_SP_ARC, 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(cx - r, cy - r, r * 2, r * 2,
                  int(self._spinner_angle * 16),
                  int(100 * 16))

        tip_angle = math.radians(self._spinner_angle + 100)
        tip_x = cx + r * math.cos(tip_angle)
        tip_y = cy + r * math.sin(tip_angle)
        p.setPen(Qt.NoPen)
        p.setBrush(_SP_ARC_HI)
        p.drawEllipse(QPoint(int(tip_x), int(tip_y)), 2, 2)

        status = (self._status or "").strip()
        if status and h > 80:
            text_y = cy + r + 10
            p.setPen(_SP_TEXT_SHADOW)
            p.setFont(_SP_FONT)
            shadow_rect = QRect(1, text_y + 1, w, 18)
            p.drawText(shadow_rect, Qt.AlignHCenter | Qt.AlignTop,
                       status[:32])
            p.setPen(_SP_TEXT)
            text_rect = QRect(0, text_y, w, 18)
            p.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop,
                       status[:32])

        p.setRenderHint(QPainter.Antialiasing, False)

    # ---------- paint ----------
    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor("#0a0a0a"))

            if self._frame is None:
                if not self._uid:
                    p.setPen(QColor(theme.COLOR_TEXT_MUTED))
                    p.setFont(QFont("Sans", 9))
                    txt = self._title or ""
                    if txt:
                        p.drawText(self.rect(), Qt.AlignCenter, txt)
                    else:
                        p.setPen(QPen(QColor(60, 60, 60), 1, Qt.DashLine))
                        p.drawRect(self.rect().adjusted(6, 6, -7, -7))
                        p.setPen(QColor("#4a5560"))
                        p.drawText(self.rect(), Qt.AlignCenter, "Drop camera")
                    return

                self._draw_loading(p)
                return

            vh, vw = self._frame.shape[:2]
            ww = max(1, self.width())
            wh = max(1, self.height())

            if self._cached_qimage is None:
                rgb = np.ascontiguousarray(self._frame)
                self._cached_qimage = QImage(
                    rgb.data, vw, vh, vw * 3, QImage.Format_RGB888
                ).copy()

            if self._zoom > 1.0 and self._zoom_anchor is not None:
                src_w = vw / self._zoom
                src_h = vh / self._zoom
                cx, cy = self._zoom_anchor
                sx = int(cx - src_w / 2)
                sy = int(cy - src_h / 2)
                sx = max(0, min(vw - int(src_w), sx))
                sy = max(0, min(vh - int(src_h), sy))
                src_rect = QRect(sx, sy, int(src_w), int(src_h))
                p.drawImage(self.rect(), self._cached_qimage, src_rect)
            else:
                s = min(ww / vw, wh / vh)
                dw, dh = int(vw * s), int(vh * s)
                ox, oy = (ww - dw) // 2, (wh - dh) // 2
                p.drawImage(QRect(ox, oy, dw, dh), self._cached_qimage)

            bh = 18
            p.fillRect(QRect(0, self.height() - bh, self.width(), bh),
                       QColor(0, 0, 0, 200))
            p.setPen(QColor("#ffffff"))
            p.setFont(QFont("Sans", 8, QFont.Bold))
            txt = self._title or "—"
            if self._status and self._status not in ("online", "connected", "متصل"):
                txt += f" ({self._status})"
            p.drawText(QRect(6, self.height() - bh, self.width() - 12, bh),
                       Qt.AlignVCenter | Qt.AlignLeft, txt)

            if self._zoom > 1.0:
                badge = f"🔍 {self._zoom:.1f}x"
                p.setFont(QFont("Sans", 8, QFont.Bold))
                tw = p.fontMetrics().horizontalAdvance(badge) + 12
                badge_rect = QRect(self.width() - tw - 6, 6, tw, 18)
                p.fillRect(badge_rect, QColor(22, 160, 133, 230))
                p.setPen(QColor("#ffffff"))
                p.drawText(badge_rect, Qt.AlignCenter, badge)

            if self._recording:
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(230, 60, 60))
                p.drawEllipse(8, 8, 10, 10)
                p.setRenderHint(QPainter.Antialiasing, False)

            if self._motion:
                p.setPen(QPen(QColor(241, 196, 15), 3))
                p.drawRect(self.rect().adjusted(2, 2, -3, -3))

            if self._show_close:
                r = self._close_rect()
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(180, 40, 40, 230))
                p.drawEllipse(r)
                p.setPen(QPen(QColor(255, 255, 255), 2))
                m = 5
                p.drawLine(r.left() + m, r.top() + m,
                           r.right() - m, r.bottom() - m)
                p.drawLine(r.right() - m, r.top() + m,
                           r.left() + m, r.bottom() - m)
                p.setRenderHint(QPainter.Antialiasing, False)

        except Exception as ex:
            print(f"[LiveCell.paintEvent] {ex}")
        finally:
            p.end()


# ============================================================
# LiveGrid
# ============================================================
class LiveGrid(QWidget):
    cell_clicked = Signal(int)
    cell_double_clicked = Signal(int)
    cell_close = Signal(int)
    cell_context = Signal(int, object)
    cell_drop_camera = Signal(int, str)
    cell_swap = Signal(int, int)
    cell_hover_entered = Signal(int)
    cell_hover_left = Signal(int)

    MAX_GRID = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #000;")
        self._grid = QGridLayout(self)
        self._grid.setSpacing(3)
        self._grid.setContentsMargins(3, 3, 3, 3)

        self._cells = []
        self._layout_key = "2x2"
        self._active_idx = 0
        self._fullscreen_idx = None

        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.timeout.connect(self._tick_spinner)
        self._spinner_timer.start(60)

        self.set_layout("2x2")

    def _tick_spinner(self):
        self._spinner_angle = (self._spinner_angle + 12) % 360
        for cell in self._cells:
            try:
                if cell.is_loading():
                    cell.set_spinner_angle(self._spinner_angle)
            except Exception:
                pass

    def get_layout_key(self):
        return self._layout_key

    def get_cells(self):
        return self._cells

    def get_active_index(self):
        return self._active_idx

    def is_fullscreen(self):
        return self._fullscreen_idx is not None

    def get_fullscreen_idx(self):
        return self._fullscreen_idx

    def reset_all_zooms(self):
        for c in self._cells:
            c.reset_zoom()

    def toggle_fullscreen_cell(self, idx):
        if self._fullscreen_idx == idx:
            self._fullscreen_idx = None
        else:
            self._fullscreen_idx = idx
        self._arrange()

    def exit_fullscreen(self):
        if self._fullscreen_idx is not None:
            self._fullscreen_idx = None
            self._arrange()

    def set_layout(self, key):
        self._layout_key = get_layout(key)["key"]
        self._fullscreen_idx = None
        self._create_cells()
        self._arrange()

    def _create_cells(self):
        while self._grid.count():
            self._grid.takeAt(0)
        for c in self._cells:
            c.setParent(None)
            c.deleteLater()
        self._cells = []

        L = get_layout(self._layout_key)
        for i in range(len(L["cells"])):
            cell = LiveCell(i)
            cell.clicked.connect(self._on_click)
            cell.double_clicked.connect(self.cell_double_clicked.emit)
            cell.close_requested.connect(self.cell_close.emit)
            cell.context_requested.connect(self.cell_context.emit)
            cell.drop_camera.connect(self.cell_drop_camera.emit)
            cell.swap_cells.connect(self.cell_swap.emit)
            cell.hover_entered.connect(self.cell_hover_entered.emit)
            cell.hover_left.connect(self.cell_hover_left.emit)
            self._cells.append(cell)

    def _arrange(self):
        while self._grid.count():
            self._grid.takeAt(0)

        for r in range(self.MAX_GRID):
            self._grid.setRowStretch(r, 0)
        for c in range(self.MAX_GRID):
            self._grid.setColumnStretch(c, 0)

        if self._fullscreen_idx is not None:
            idx = self._fullscreen_idx
            if 0 <= idx < len(self._cells):
                target = self._cells[idx]
                target.show()
                target.setMinimumSize(20, 20)
                self._grid.addWidget(target, 0, 0, 1, 1)
            for i, c in enumerate(self._cells):
                if i != idx:
                    c.hide()
            self._grid.setRowStretch(0, 1)
            self._grid.setColumnStretch(0, 1)
        else:
            L = get_layout(self._layout_key)
            for i, (r, c, rs, cs) in enumerate(L["cells"]):
                if i < len(self._cells):
                    cell = self._cells[i]
                    cell.show()
                    self._grid.addWidget(cell, r, c, rs, cs)
            for i in range(len(L["cells"]), len(self._cells)):
                self._cells[i].hide()
            for r in range(L["rows"]):
                self._grid.setRowStretch(r, 1)
            for c in range(L["cols"]):
                self._grid.setColumnStretch(c, 1)

        self._grid.invalidate()
        self._update_active()
        self.update()

    def _update_active(self):
        for i, c in enumerate(self._cells):
            try:
                color = "#16a085" if i == self._active_idx else "#232830"
                c.set_border_color(color)
            except Exception:
                pass

    def _on_click(self, idx):
        self._active_idx = idx
        self._update_active()
        self.cell_clicked.emit(idx)

    def set_frame(self, idx, payload):
        if 0 <= idx < len(self._cells):
            self._cells[idx].set_frame(payload)

    def clear_cell(self, idx):
        if 0 <= idx < len(self._cells):
            self._cells[idx].clear()

    def set_title(self, idx, title):
        if 0 <= idx < len(self._cells):
            self._cells[idx].set_title(title)

    def set_uid(self, idx, uid):
        if 0 <= idx < len(self._cells):
            self._cells[idx].set_uid(uid)

    def set_status(self, idx, status):
        if 0 <= idx < len(self._cells):
            self._cells[idx].set_status(status)

    def set_recording(self, idx, on):
        if 0 <= idx < len(self._cells):
            self._cells[idx].set_recording(on)

    def set_motion(self, idx, on):
        if 0 <= idx < len(self._cells):
            self._cells[idx].set_motion(on)

    def set_show_close(self, on):
        for c in self._cells:
            c.set_show_close(on)