# -*- coding: utf-8 -*-
"""K1 VMS — Video display with rectangle + wheel zoom"""
import numpy as np
from PySide6.QtWidgets import QLabel, QSizePolicy
from PySide6.QtCore import Qt, QSize, QRect, QPoint, Signal
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QPen, QBrush

from ui import theme


class VideoDisplay(QLabel):
    """Displays RGB frames + rectangle zoom + wheel zoom."""
    zoom_changed = Signal(bool)   # True when zoomed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 180)
        self.setStyleSheet("background: #000000;")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._image = None
        self._placeholder = "No recording selected"

        # Zoom state
        self._zoom_enabled = False        # user clicked zoom button
        self._zoomed_region = None        # (x, y, w, h) in image coords
        self._dragging = False
        self._drag_start = QPoint()
        self._drag_end = QPoint()

    # ============================================================
    # Public
    # ============================================================
    def set_placeholder(self, text):
        self._placeholder = text
        self._image = None
        self._zoomed_region = None
        self.update()

    def set_frame(self, rgb_array):
        if rgb_array is None:
            return
        try:
            arr = np.ascontiguousarray(rgb_array)
            h, w = arr.shape[:2]
            if h == 0 or w == 0:
                return
            img = QImage(arr.data, w, h, w * 3, QImage.Format_RGB888).copy()
            self._image = img
            self.update()
        except Exception as e:
            print(f"[VideoDisplay] {e}")

    def zoom_in_mode(self):
        """Enter rectangle-select zoom mode."""
        self._zoom_enabled = True
        self.setCursor(Qt.CrossCursor)
        self.update()

    def zoom_out(self):
        """Exit zoom entirely."""
        self._zoom_enabled = False
        self._zoomed_region = None
        self.setCursor(Qt.ArrowCursor)
        self.zoom_changed.emit(False)
        self.update()

    def is_zoomed(self):
        return self._zoomed_region is not None

    # ============================================================
    # Painting
    # ============================================================
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor("#000"))

        if self._image is None:
            p.setPen(QColor(theme.COLOR_TEXT_MUTED))
            p.setFont(QFont("Segoe UI", 12))
            p.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
            p.end()
            return

        iw = self._image.width()
        ih = self._image.height()

        # Source rect (crop if zoomed)
        if self._zoomed_region is not None:
            sx, sy, sw, sh = self._zoomed_region
            source = QRect(sx, sy, sw, sh)
            src_w, src_h = sw, sh
        else:
            source = QRect(0, 0, iw, ih)
            src_w, src_h = iw, ih

        # Target rect (letterbox on widget)
        cw = max(1, self.width())
        ch = max(1, self.height())
        scale = min(cw / src_w, ch / src_h)
        nw = max(1, int(src_w * scale))
        nh = max(1, int(src_h * scale))
        ox = (cw - nw) // 2
        oy = (ch - nh) // 2

        p.drawImage(QRect(ox, oy, nw, nh), self._image, source)

        # Rectangle drag preview
        if self._dragging:
            x0 = min(self._drag_start.x(), self._drag_end.x())
            y0 = min(self._drag_start.y(), self._drag_end.y())
            w = abs(self._drag_end.x() - self._drag_start.x())
            h = abs(self._drag_end.y() - self._drag_start.y())
            p.setPen(QPen(QColor("#16a085"), 2, Qt.DashLine))
            p.setBrush(QBrush(QColor(22, 160, 133, 40)))
            p.drawRect(QRect(x0, y0, w, h))

        # Zoom mode hint
        if self._zoom_enabled and not self._dragging:
            p.setPen(QColor("#16a085"))
            p.setFont(QFont("Segoe UI", 10, QFont.Bold))
            p.drawText(QRect(10, 10, 320, 20),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       "🔍  Drag a rectangle or scroll wheel to zoom  •  Esc to cancel")

        # Zoomed indicator
        if self._zoomed_region is not None:
            p.setPen(QColor("#16a085"))
            p.setFont(QFont("Segoe UI", 9, QFont.Bold))
            p.drawText(QRect(10, self.height() - 30, 260, 20),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       "🔍  ZOOMED  •  Double-click to reset")

        p.end()

    # ============================================================
    # Mouse interaction
    # ============================================================
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._zoom_enabled:
            self._dragging = True
            self._drag_start = event.position().toPoint()
            self._drag_end = self._drag_start
            self.update()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._drag_end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            x0 = min(self._drag_start.x(), self._drag_end.x())
            y0 = min(self._drag_start.y(), self._drag_end.y())
            w = abs(self._drag_end.x() - self._drag_start.x())
            h = abs(self._drag_end.y() - self._drag_start.y())

            if w < 20 or h < 20:
                self.update()
                return

            if self._image is not None:
                iw = self._image.width()
                ih = self._image.height()

                # Current source rect
                if self._zoomed_region is not None:
                    sx, sy, sw, sh = self._zoomed_region
                else:
                    sx, sy, sw, sh = 0, 0, iw, ih

                # Target rect on widget
                cw = max(1, self.width())
                ch = max(1, self.height())
                scale = min(cw / sw, ch / sh)
                nw = int(sw * scale)
                nh = int(sh * scale)
                ox = (cw - nw) // 2
                oy = (ch - nh) // 2

                # widget pixels → image pixels
                ix0 = sx + int((x0 - ox) / scale)
                iy0 = sy + int((y0 - oy) / scale)
                ix1 = sx + int((x0 + w - ox) / scale)
                iy1 = sy + int((y0 + h - oy) / scale)

                # clamp
                ix0 = max(sx, min(sx + sw - 1, ix0))
                iy0 = max(sy, min(sy + sh - 1, iy0))
                ix1 = max(ix0 + 10, min(sx + sw, ix1))
                iy1 = max(iy0 + 10, min(sy + sh, iy1))

                self._zoomed_region = (ix0, iy0, ix1 - ix0, iy1 - iy0)
                self._zoom_enabled = False
                self.setCursor(Qt.ArrowCursor)
                self.zoom_changed.emit(True)

            self.update()

    def mouseDoubleClickEvent(self, event):
        # Double-click = reset zoom
        if self._zoomed_region is not None:
            self.zoom_out()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self._zoom_enabled:
                self._zoom_enabled = False
                self.setCursor(Qt.ArrowCursor)
                self.update()
            elif self._zoomed_region is not None:
                self.zoom_out()
        else:
            super().keyPressEvent(event)

    # ============================================================
    # Wheel zoom (in/out around cursor)
    # ============================================================
    def wheelEvent(self, event):
        """Mouse wheel = zoom in/out around cursor."""
        if self._image is None:
            return

        factor = 1.25 if event.angleDelta().y() < 0 else 0.8

        iw = self._image.width()
        ih = self._image.height()

        # Current source rect
        if self._zoomed_region is not None:
            sx, sy, sw, sh = self._zoomed_region
        else:
            sx, sy, sw, sh = 0, 0, iw, ih

        # Cursor position
        pos = event.position()
        cw = max(1, self.width())
        ch = max(1, self.height())

        # Current scale
        scale = min(cw / sw, ch / sh)
        disp_w = int(sw * scale)
        disp_h = int(sh * scale)
        ox = (cw - disp_w) // 2
        oy = (ch - disp_h) // 2

        # Cursor in image coordinates (relative to current source)
        rel_x = (pos.x() - ox) / max(0.0001, scale)
        rel_y = (pos.y() - oy) / max(0.0001, scale)
        cx = sx + rel_x
        cy = sy + rel_y

        # New source size
        new_sw = int(sw * factor)
        new_sh = int(sh * factor)
        new_sw = max(40, min(iw, new_sw))
        new_sh = max(30, min(ih, new_sh))

        # New top-left keeping cursor relative position
        rel_frac_x = rel_x / max(1.0, sw)
        rel_frac_y = rel_y / max(1.0, sh)
        new_sx = int(cx - new_sw * rel_frac_x)
        new_sy = int(cy - new_sh * rel_frac_y)

        # Clamp
        new_sx = max(0, min(iw - new_sw, new_sx))
        new_sy = max(0, min(ih - new_sh, new_sy))

        # If we're at (nearly) full image → clear zoom
        if new_sw >= iw - 2 and new_sh >= ih - 2:
            self._zoomed_region = None
            self.zoom_changed.emit(False)
        else:
            self._zoomed_region = (new_sx, new_sy, new_sw, new_sh)
            self.zoom_changed.emit(True)

        self.update()