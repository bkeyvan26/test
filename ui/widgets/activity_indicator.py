# -*- coding: utf-8 -*-
"""K1 VMS — Global activity indicator (Phase 6.3)

یک ویجت کوچک که کنار Status Bar نشان می‌دهد برنامه در حال انجام کاری است.
- فقط زمانی که active است، QTimer روشن می‌شود
- فقط در همان حالت repaint می‌کند
- وقتی active نیست، مصرف CPU = صفر
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer, QRect, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from ui import theme


# رنگ‌ها (module-level — یک‌بار ساخته می‌شوند)
_COLOR_ARC      = QColor(22, 160, 133)
_COLOR_ARC_HI   = QColor(26, 188, 156)
_COLOR_TRACK    = QColor(60, 68, 82, 180)
_COLOR_TEXT     = QColor(150, 160, 175)
_COLOR_BG       = QColor(20, 24, 30, 220)

_SPINNER_SIZE = 14
_TICK_MS = 70
_ANGLE_STEP = 14


class ActivityIndicator(QWidget):
    """
    ویجت کوچک: [چرخنده] [متن وضعیت]
    - start(text) → نمایش + شروع چرخش
    - stop() → مخفی + توقف
    - set_text(text) → فقط متن را عوض می‌کند (بدون restart)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self.setVisible(False)

        # layout: spinner (خودمان می‌کشیم) + label
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(_SPINNER_SIZE + 8, 0, 8, 0)
        self._layout.setSpacing(6)

        self._label = QLabel("")
        self._label.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 10px; background: transparent;"
        )
        self._layout.addWidget(self._label)

        self._angle = 0
        self._active = False
        self._text = ""

        # Timer فقط در حالت active روشن است
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ============================================================
    # Public API
    # ============================================================
    def start(self, text: str = ""):
        """نمایش + شروع چرخش"""
        if text:
            self._text = text
            self._label.setText(text)
        if not self._active:
            self._active = True
            self.setVisible(True)
            self._timer.start()
            self.update()
        else:
            # already active → فقط متن را آپدیت کن
            if text and text != self._label.text():
                self._label.setText(text)

    def stop(self):
        """مخفی + توقف کامل"""
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self.setVisible(False)
        self._label.setText("")
        self._text = ""

    def set_text(self, text: str):
        """فقط متن را تغییر بده (بدون تغییر حالت)"""
        if not self._active:
            return
        if text != self._label.text():
            self._label.setText(text)

    def is_active(self) -> bool:
        return self._active

    # ============================================================
    # Internals
    # ============================================================
    def _tick(self):
        self._angle = (self._angle + _ANGLE_STEP) % 360
        # فقط ناحیه‌ی spinner را repaint کن (نه کل ویجت)
        self.update(0, 0, _SPINNER_SIZE + 8, self.height())

    def paintEvent(self, e):
        """رسم spinner سمت چپ ویجت"""
        if not self._active:
            return

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)

            # background گرد کوچک
            r = _SPINNER_SIZE
            cx = r // 2 + 2
            cy = self.height() // 2
            radius = r // 2

            # track (ring پس‌زمینه)
            p.setBrush(Qt.NoBrush)
            pen = QPen(_COLOR_TRACK, 2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawEllipse(QPoint(cx, cy), radius, radius)

            # arc در حال چرخش
            pen = QPen(_COLOR_ARC, 2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            # sweep = 100° (1600 در واحد 1/16)
            p.drawArc(cx - radius, cy - radius, radius * 2, radius * 2,
                      int(self._angle * 16), int(100 * 16))

            p.setRenderHint(QPainter.Antialiasing, False)
        except Exception:
            pass
        finally:
            p.end()