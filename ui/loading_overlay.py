# -*- coding: utf-8 -*-
"""K1 VMS — Global Loading Overlay (Phase 6.6)

Widget شفاف روی کل پنجره. با ActivityBus وصل می‌شود.
- Fade in/out نرم
- اسپینر مدرن
- وسط صفحه، روی همه چیز
- کلیک‌ها را نمی‌گیرد (WA_TransparentForMouseEvents)
"""
import math
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, QRect, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QBrush

from ui.activity_bus import ActivityBus


class GlobalLoadingOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        # ★ مهم: کلیک‌ها از overlay عبور کنند
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setVisible(False)

        self._text = ""
        self._angle = 0
        self._opacity = 0.0
        self._target_opacity = 0.0

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(40)
        self._spin_timer.timeout.connect(self._tick)

        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._fade_step)

        try:
            ActivityBus.instance().changed.connect(self._on_activity)
        except Exception:
            pass

    # ------------------------------------------------------------
    def _on_activity(self, active: bool, text: str):
        self._text = (text or "").strip()
        if active:
            self._target_opacity = 1.0
            if not self._spin_timer.isActive():
                self._spin_timer.start()
            if not self._fade_timer.isActive():
                self._fade_timer.start()
            # Resize to fill parent
            try:
                p = self.parentWidget()
                if p is not None:
                    self.setGeometry(0, 0, p.width(), p.height())
            except Exception:
                pass
            self.show()
            self.raise_()
        else:
            self._target_opacity = 0.0
            if not self._fade_timer.isActive():
                self._fade_timer.start()

    def _tick(self):
        self._angle = (self._angle + 14) % 360
        if self._opacity > 0.01:
            self.update()

    def _fade_step(self):
        step = 0.14
        if self._opacity < self._target_opacity:
            self._opacity = min(self._target_opacity,
                                 self._opacity + step)
        elif self._opacity > self._target_opacity:
            self._opacity = max(self._target_opacity,
                                 self._opacity - step)
        else:
            self._fade_timer.stop()
            if self._target_opacity <= 0.01:
                self._spin_timer.stop()
                self.hide()
        self.update()

    # ------------------------------------------------------------
    def paintEvent(self, e):
        if self._opacity <= 0.01:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            op = self._opacity

            # Backdrop
            p.fillRect(self.rect(),
                       QColor(6, 8, 12, int(150 * op)))

            cx = self.width() // 2
            cy = self.height() // 2

            # Card
            card_w = 300
            card_h = 130
            card_x = cx - card_w // 2
            card_y = cy - card_h // 2
            card_rect = QRect(card_x, card_y, card_w, card_h)

            p.setBrush(QBrush(QColor(18, 22, 30, int(240 * op))))
            p.setPen(QPen(QColor(48, 58, 74, int(230 * op)), 1))
            p.drawRoundedRect(card_rect, 16, 16)

            # Spinner
            r = 22
            scy = cy - 14

            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(52, 60, 76, int(220 * op)), 3))
            p.drawEllipse(QPoint(cx, scy), r, r)

            p.setPen(QPen(QColor(22, 160, 133, int(255 * op)), 3))
            p.drawArc(cx - r, scy - r, r * 2, r * 2,
                      int(self._angle * 16), int(110 * 16))

            # Arc tip dot
            tip = math.radians(self._angle + 110)
            tx = cx + r * math.cos(tip)
            ty = scy + r * math.sin(tip)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(26, 188, 156, int(255 * op)))
            p.drawEllipse(QPoint(int(tx), int(ty)), 2, 2)

            # Text
            text = self._text or "…"
            p.setPen(QColor(230, 235, 242, int(255 * op)))
            p.setFont(QFont("Sans", 10, QFont.Bold))
            tr = QRect(card_x + 12, cy + 24, card_w - 24, 40)
            p.drawText(tr, Qt.AlignHCenter | Qt.AlignTop, text[:60])
        finally:
            p.end()

    # ------------------------------------------------------------
    def refresh_geometry(self):
        try:
            p = self.parentWidget()
            if p is not None:
                self.setGeometry(0, 0, p.width(), p.height())
        except Exception:
            pass