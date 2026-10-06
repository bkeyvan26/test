# -*- coding: utf-8 -*-
"""K1 VMS — Video display with rectangle + wheel zoom (Phase 6.7.6)"""
import numpy as np
from PySide6.QtWidgets import QLabel, QSizePolicy
from PySide6.QtCore import Qt, QSize, QRect, QPoint, Signal
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QPen, QBrush

from ui import theme


class VideoDisplay(QLabel):
    """Displays RGB frames + rectangle zoom + wheel zoom."""
    zoom_changed = Signal(bool)

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
        self._zoom_enabled = False
        self._zoomed_region = None
        self._dragging = False
        self._drag_start = QPoint()
        self._drag_end = QPoint()

        # Wheel zoom state
        self._wheel_zoom = 1.0
        self._wheel_center = (0.5, 0.5)
        self._is_fullscreen = False

    # ============================================================
    # Public
    # ============================================================
    def set_placeholder(self, text):
        self._placeholder = text
        self._image = None
        self._zoomed_region = None
        self._wheel_zoom = 1.0
        self.update()

    def set_frame(self, rgb_array):
        """دریافت فریم RGB از engine."""
        if rgb_array is None:
            return
        try:
            arr = np.ascontiguousarray(rgb_array)
            h, w = arr.shape[:2]
            if h == 0 or w == 0:
                return
            # ★ Zero-Copy: QImage به buffer numpy اشاره می‌کند
            # ولی چون arr در Main Thread کپی شده (از reader)، ایمن است
            img = QImage(arr.data, w, h, w * 3,
                         QImage.Format_RGB888).copy()
            self._image = img
            if not self._is_fullscreen:
                self.update()
        except Exception as e:
            print(f"[VideoDisplay.set_frame] {e}")

    def zoom_in_mode(self):
        self._zoom_enabled = True
        self.setCursor(Qt.CrossCursor)
        self.update()

    def zoom_out(self):
        self._zoom_enabled = False
        self._zoomed_region = None
        self._wheel_zoom = 1.0
        self._wheel_center = (0.5, 0.5)
        self.setCursor(Qt.ArrowCursor)
        self.zoom_changed.emit(False)
        self.update()

    def reset_zoom(self):
        self.zoom_out()

    def is_zoomed(self):
        return (self._zoomed_region is not None) or (self._wheel_zoom > 1.01)

    def set_fullscreen_mode(self, on: bool):
        self._is_fullscreen = bool(on)

    # ============================================================
    # Painting
    # ============================================================
    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.fillRect(self.rect(), QColor("#000"))

            if self._image is None:
                p.setPen(QColor(theme.COLOR_TEXT_MUTED))
                p.setFont(QFont("Segoe UI", 12))
                p.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
                return

            iw = self._image.width()
            ih = self._image.height()

            if self._zoomed_region is not None:
                sx, sy, sw, sh = self._zoomed_region
            else:
                sx, sy, sw, sh = 0, 0, iw, ih

            if self._wheel_zoom > 1.01:
                cx_ratio, cy_ratio = self._wheel_center
                cx_img = sx + sw * cx_ratio
                cy_img = sy + sh * cy_ratio
                new_sw = int(sw / self._wheel_zoom)
                new_sh = int(sh / self._wheel_zoom)
                new_sw = max(20, min(iw, new_sw))
                new_sh = max(20, min(ih, new_sh))
                new_sx = int(cx_img - new_sw * cx_ratio)
                new_sy = int(cy_img - new_sh * cy_ratio)
                new_sx = max(0, min(iw - new_sw, new_sx))
                new_sy = max(0, min(ih - new_sh, new_sy))
                sx, sy, sw, sh = new_sx, new_sy, new_sw, new_sh

            source = QRect(sx, sy, sw, sh)

            cw = max(1, self.width())
            ch = max(1, self.height())
            scale = min(cw / sw, ch / sh)
            nw = max(1, int(sw * scale))
            nh = max(1, int(sh * scale))
            ox = (cw - nw) // 2
            oy = (ch - nh) // 2

            p.drawImage(QRect(ox, oy, nw, nh), self._image, source)

            if self._dragging:
                x0 = min(self._drag_start.x(), self._drag_end.x())
                y0 = min(self._drag_start.y(), self._drag_end.y())
                w = abs(self._drag_end.x() - self._drag_start.x())
                h = abs(self._drag_end.y() - self._drag_start.y())
                p.setPen(QPen(QColor("#16a085"), 2, Qt.DashLine))
                p.setBrush(QBrush(QColor(22, 160, 133, 40)))
                p.drawRect(QRect(x0, y0, w, h))

            if self._zoom_enabled and not self._dragging:
                p.setPen(QColor("#16a085"))
                p.setFont(QFont("Segoe UI", 10, QFont.Bold))
                p.drawText(QRect(10, 10, 500, 20),
                           Qt.AlignLeft | Qt.AlignVCenter,
                           "🔍 مستطیل بکش یا اسکرول کن  •  Esc = لغو")

            if self.is_zoomed():
                badge = f"🔍 x{self._wheel_zoom:.1f}" if self._wheel_zoom > 1.01 else "🔍"
                p.setPen(QColor("#16a085"))
                p.setFont(QFont("Segoe UI", 9, QFont.Bold))
                p.drawText(QRect(10, self.height() - 30, 300, 20),
                           Qt.AlignLeft | Qt.AlignVCenter,
                           f"{badge}  •  دابل‌کلیک = بازنشانی")
        finally:
            p.end()

    # ============================================================
    # Mouse
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

                if self._zoomed_region is not None:
                    sx, sy, sw, sh = self._zoomed_region
                else:
                    sx, sy, sw, sh = 0, 0, iw, ih

                cw = max(1, self.width())
                ch = max(1, self.height())
                scale = min(cw / sw, ch / sh)
                nw = int(sw * scale)
                nh = int(sh * scale)
                ox = (cw - nw) // 2
                oy = (ch - nh) // 2

                ix0 = sx + int((x0 - ox) / scale)
                iy0 = sy + int((y0 - oy) / scale)
                ix1 = sx + int((x0 + w - ox) / scale)
                iy1 = sy + int((y0 + h - oy) / scale)

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
        if self.is_zoomed():
            self.zoom_out()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self._zoom_enabled:
                self._zoom_enabled = False
                self.setCursor(Qt.ArrowCursor)
                self.update()
            elif self.is_zoomed():
                self.zoom_out()
        else:
            super().keyPressEvent(event)

    # ============================================================
    # ★ Wheel Zoom (in/out around cursor)
    # ============================================================
    def wheelEvent(self, event):
        if self._image is None:
            event.ignore()
            return

        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return

        iw = self._image.width()
        ih = self._image.height()

        if self._zoomed_region is not None:
            sx, sy, sw, sh = self._zoomed_region
        else:
            sx, sy, sw, sh = 0, 0, iw, ih

        if self._wheel_zoom > 1.01:
            cx_ratio, cy_ratio = self._wheel_center
            cx_img = sx + sw * cx_ratio
            cy_img = sy + sh * cy_ratio
            new_sw = int(sw / self._wheel_zoom)
            new_sh = int(sh / self._wheel_zoom)
            new_sw = max(20, min(iw, new_sw))
            new_sh = max(20, min(ih, new_sh))
            new_sx = int(cx_img - new_sw * cx_ratio)
            new_sy = int(cy_img - new_sh * cy_ratio)
            new_sx = max(0, min(iw - new_sw, new_sx))
            new_sy = max(0, min(ih - new_sh, new_sy))
            sx, sy, sw, sh = new_sx, new_sy, new_sw, new_sh

        pos = event.position()
        cw = max(1, self.width())
        ch = max(1, self.height())
        scale = min(cw / sw, ch / sh)
        nw = int(sw * scale)
        nh = int(sh * scale)
        ox = (cw - nw) // 2
        oy = (ch - nh) // 2

        rel_x = (pos.x() - ox) / max(0.0001, scale)
        rel_y = (pos.y() - oy) / max(0.0001, scale)
        rel_x = max(0, min(sw, rel_x))
        rel_y = max(0, min(sh, rel_y))

        self._wheel_center = (rel_x / max(1.0, sw),
                              rel_y / max(1.0, sh))

        factor = 1.15 if delta > 0 else 1.0 / 1.15
        self._wheel_zoom = max(1.0, min(8.0, self._wheel_zoom * factor))

        if self._wheel_zoom <= 1.01:
            self._wheel_zoom = 1.0

        self.zoom_changed.emit(self.is_zoomed())
        self.update()
        event.accept()