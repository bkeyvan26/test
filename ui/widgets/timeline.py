# -*- coding: utf-8 -*-
"""K1 VMS — Timeline with deep zoom + readable labels"""
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRect, QPoint
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QPolygon

from ui import theme


def _fmt_hms(seconds):
    s = max(0, int(seconds))
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


COLOR_RECORDED     = "#e74c3c"
COLOR_NOT_RECORDED = "#000000"
COLOR_MOTION       = "#f1c40f"
COLOR_NO_MOTION    = "#0a1a2a"
COLOR_PLAYHEAD     = "#ff4d4d"


class TimelineWidget(QWidget):
    seek_requested = Signal(float)

    RULER_H = 24
    TRACK_GAP = 3
    REC_TRACK_H = 16
    MOTION_TRACK_H = 12

    # === Zoom limits ===
    MIN_SPAN_SEC = 60.0       # min zoom = 1 minute (per user request)
    MAX_SPAN_SEC = 86400.0

    # === Label spacing ===
    MIN_LABEL_GAP_PX = 58     # labels closer than this are skipped

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(72)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("background: transparent;")

        self._raw_segments = []
        self._segments = []
        self._motion = []
        self._alarms = []
        self._playhead = 0.0

        self._dragging = False
        self._panning = False
        self._pan_start_x = 0
        self._pan_start_zoom = (0.0, 86400.0)
        self._hover_x = -1
        self._zoom_start = 0.0
        self._zoom_end = 86400.0

    # ============================================================
    def set_segments(self, segments):
        self._raw_segments = sorted(segments or [],
                                     key=lambda s: float(s.get("start", 0)))
        self._merge()

    def set_motion_events(self, events):
        self._motion = sorted(events or [], key=lambda e: e.get("time", 0))
        self.update()

    def set_alarm_events(self, events):
        self._alarms = events or []
        self.update()

    def set_playhead(self, seconds):
        self._playhead = max(0.0, min(86400.0, float(seconds)))
        self.update()

    # ============================================================
    # Zoom
    # ============================================================
    def reset_zoom(self):
        self._zoom_start = 0.0
        self._zoom_end = 86400.0
        self.update()

    def zoom_in(self):
        span = self._zoom_end - self._zoom_start
        mid = (self._zoom_start + self._zoom_end) / 2
        new_span = max(self.MIN_SPAN_SEC, span / 2)
        self._zoom_start = max(0, mid - new_span / 2)
        self._zoom_end = min(86400, self._zoom_start + new_span)
        self.update()

    def zoom_out(self):
        span = self._zoom_end - self._zoom_start
        mid = (self._zoom_start + self._zoom_end) / 2
        new_span = min(self.MAX_SPAN_SEC, span * 2)
        self._zoom_start = max(0, mid - new_span / 2)
        self._zoom_end = min(86400, self._zoom_start + new_span)
        self.update()

    def zoom_at(self, cursor_x, factor):
        cursor_sec = self._x_to_sec(cursor_x)
        span = (self._zoom_end - self._zoom_start) * factor
        span = max(self.MIN_SPAN_SEC, min(self.MAX_SPAN_SEC, span))
        ratio = cursor_x / max(1, self.width())
        new_start = cursor_sec - ratio * span
        new_end = new_start + span
        if new_start < 0:
            new_start = 0
            new_end = span
        if new_end > 86400:
            new_end = 86400
            new_start = 86400 - span
        self._zoom_start = new_start
        self._zoom_end = new_end
        self.update()

    # ============================================================
    # Merge
    # ============================================================
    def _merge(self):
        if not self._raw_segments:
            self._segments = []
            return

        out = []
        for s in self._raw_segments:
            st = float(s.get("start", 0))
            dur = float(s.get("duration", 0))
            if dur <= 0:
                continue
            en = st + dur
            if out and st <= out[-1]["end"] + 0.5:
                out[-1]["end"] = max(out[-1]["end"], en)
                out[-1]["duration"] = out[-1]["end"] - out[-1]["start"]
            else:
                out.append({
                    "start": st,
                    "end": en,
                    "duration": dur,
                    "type": s.get("type", "continuous"),
                })
        self._segments = out

    # ============================================================
    # Coordinates
    # ============================================================
    def _sec_to_x(self, sec):
        w = max(1, self.width())
        r = (sec - self._zoom_start) / max(1.0, self._zoom_end - self._zoom_start)
        return int(r * w)

    def _x_to_sec(self, x):
        w = max(1, self.width())
        r = max(0.0, min(1.0, x / w))
        return self._zoom_start + r * (self._zoom_end - self._zoom_start)

    # ============================================================
    # Ruler step selection — deeper levels added
    # ============================================================
    def _ruler_steps(self):
        span = self._zoom_end - self._zoom_start
        if span >= 43200:  return 3600, 900    # ≥12h  : 1h    / 15m
        if span >= 21600:  return 1800, 300    # ≥6h   : 30m   / 5m
        if span >= 10800:  return 900, 180     # ≥3h   : 15m   / 3m
        if span >= 5400:   return 600, 60      # ≥90m  : 10m   / 1m
        if span >= 2700:   return 300, 30      # ≥45m  : 5m    / 30s
        if span >= 1800:   return 120, 15      # ≥30m  : 2m    / 15s
        if span >= 900:    return 60, 10       # ≥15m  : 1m    / 10s
        if span >= 300:    return 30, 5        # ≥5m   : 30s   / 5s
        if span >= 120:    return 10, 2        # ≥2m   : 10s   / 2s
        if span >= 60:     return 5, 1         # ≥1m   : 5s    / 1s
        if span >= 30:     return 2, 1         # ≥30s  : 2s    / 1s
        return 1, 1                            # <30s  : 1s    / 1s

    # ============================================================
    # Paint
    # ============================================================
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)

        w = self.width()
        h = self.height()

        p.fillRect(self.rect(), QColor(theme.COLOR_BG_PANEL))

        # ===== RULER =====
        p.fillRect(QRect(0, 0, w, self.RULER_H), QColor(theme.COLOR_BG_CARD))
        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        p.drawLine(0, self.RULER_H, w, self.RULER_H)

        major_step, minor_step = self._ruler_steps()

        # --- Minor ticks (short) ---
        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        t_min = int(self._zoom_start // minor_step) * minor_step
        while t_min <= self._zoom_end:
            x = self._sec_to_x(t_min)
            if 0 <= x <= w and (t_min % major_step) != 0:
                p.drawLine(x, self.RULER_H - 4, x, self.RULER_H)
            t_min += minor_step

        # --- Major ticks with labels (with adaptive skipping) ---
        p.setFont(QFont("Consolas", 7))
        last_label_x = -10000
        t_maj = int(self._zoom_start // major_step) * major_step
        while t_maj <= self._zoom_end:
            x = self._sec_to_x(t_maj)
            if -40 <= x <= w + 40:
                # Draw tick line always
                p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
                p.drawLine(x, 0, x, self.RULER_H)

                # Draw label only if far enough from the previous one
                if x - last_label_x >= self.MIN_LABEL_GAP_PX:
                    p.setPen(QColor(theme.COLOR_TEXT_SECONDARY))
                    hh = t_maj // 3600
                    mm = (t_maj % 3600) // 60
                    ss = t_maj % 60

                    if major_step < 60:
                        label = f"{hh:02d}:{mm:02d}:{ss:02d}"
                    else:
                        label = f"{hh:02d}:{mm:02d}"

                    p.drawText(QRect(x + 3, 0, 80, self.RULER_H),
                               Qt.AlignVCenter | Qt.AlignLeft, label)
                    last_label_x = x
            t_maj += major_step

        # ===== TRACK 1: RECORDING =====
        y1 = self.RULER_H + self.TRACK_GAP
        rec_rect = QRect(0, y1, w, self.REC_TRACK_H)
        p.fillRect(rec_rect, QColor(COLOR_NOT_RECORDED))

        p.setPen(Qt.NoPen)
        for seg in self._segments:
            x0 = self._sec_to_x(seg["start"])
            x1 = self._sec_to_x(seg["end"])
            if x1 < 0 or x0 > w:
                continue
            x0c = max(0, x0)
            x1c = min(w, x1)
            if x1c - x0c < 1:
                x1c = x0c + 1
            p.fillRect(QRect(x0c, y1, x1c - x0c, self.REC_TRACK_H),
                       QColor(COLOR_RECORDED))

        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        p.drawRect(rec_rect)

        # ===== TRACK 2: MOTION =====
        y2 = y1 + self.REC_TRACK_H + self.TRACK_GAP
        mot_rect = QRect(0, y2, w, self.MOTION_TRACK_H)
        p.fillRect(mot_rect, QColor(COLOR_NO_MOTION))

        p.setPen(Qt.NoPen)
        for ev in self._motion:
            t0 = float(ev.get("time", 0))
            td = float(ev.get("duration", 5.0)) or 5.0
            x0 = self._sec_to_x(t0)
            x1 = self._sec_to_x(t0 + td)
            if x1 < 0 or x0 > w:
                continue
            x0c = max(0, x0)
            x1c = min(w, x1)
            if x1c - x0c < 1:
                x1c = x0c + 1
            p.fillRect(QRect(x0c, y2, x1c - x0c, self.MOTION_TRACK_H),
                       QColor(COLOR_MOTION))

        for ev in self._alarms:
            x = self._sec_to_x(float(ev.get("time", 0)))
            if 0 <= x <= w:
                p.setBrush(QBrush(QColor("#ff0000")))
                p.setPen(Qt.NoPen)
                p.drawPolygon([
                    QPoint(x - 3, y2 + self.MOTION_TRACK_H - 1),
                    QPoint(x + 3, y2 + self.MOTION_TRACK_H - 1),
                    QPoint(x, y2 + 1),
                ])

        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        p.drawRect(mot_rect)

        # ===== HOVER =====
        if self._hover_x >= 0:
            p.setPen(QPen(QColor(theme.COLOR_TEXT_SECONDARY), 1, Qt.DotLine))
            p.drawLine(self._hover_x, 0, self._hover_x, h)
            hover_sec = self._x_to_sec(self._hover_x)
            label = _fmt_hms(hover_sec)
            p.setPen(QColor(theme.COLOR_TEXT_PRIMARY))
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            tx = self._hover_x + 4
            if tx + 60 > w:
                tx = self._hover_x - 64
            p.fillRect(QRect(tx - 2, 0, 56, self.RULER_H),
                       QColor(theme.COLOR_BG_DARK))
            p.drawText(QRect(tx, 0, 52, self.RULER_H),
                       Qt.AlignVCenter | Qt.AlignLeft, label)

        # ===== PLAYHEAD =====
        px = self._sec_to_x(self._playhead)
        if -5 <= px <= w + 5:
            p.setPen(QPen(QColor(COLOR_PLAYHEAD), 2))
            p.drawLine(px, 0, px, h)
            p.setBrush(QBrush(QColor(COLOR_PLAYHEAD)))
            p.setPen(Qt.NoPen)
            p.drawPolygon([
                QPoint(px - 4, 0), QPoint(px + 4, 0), QPoint(px, 6),
            ])

        p.end()

    # ============================================================
    # Mouse
    # ============================================================
    def mousePressEvent(self, event):
        pos = event.position()
        x = int(pos.x())
        y = int(pos.y())

        if event.button() == Qt.LeftButton:
            if y < self.RULER_H:
                # PAN mode
                self._panning = True
                self._pan_start_x = x
                self._pan_start_zoom = (self._zoom_start, self._zoom_end)
                self.setCursor(Qt.ClosedHandCursor)
            else:
                # SEEK mode
                self._dragging = True
                self.seek_requested.emit(self._x_to_sec(x))
        elif event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start_x = x
            self._pan_start_zoom = (self._zoom_start, self._zoom_end)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        pos = event.position()
        x = int(pos.x())
        y = int(pos.y())
        self._hover_x = x

        if self._panning:
            dx = x - self._pan_start_x
            w = max(1, self.width())
            span = self._pan_start_zoom[1] - self._pan_start_zoom[0]
            shift_sec = -dx / w * span
            new_start = self._pan_start_zoom[0] + shift_sec
            new_end = self._pan_start_zoom[1] + shift_sec
            if new_start < 0:
                new_start = 0
                new_end = span
            if new_end > 86400:
                new_end = 86400
                new_start = 86400 - span
            self._zoom_start = new_start
            self._zoom_end = new_end
            self.update()
        elif self._dragging:
            self.seek_requested.emit(self._x_to_sec(x))
            self.update()
        else:
            if y < self.RULER_H:
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.PointingHandCursor)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._dragging = False
            self._panning = False
            self.setCursor(Qt.PointingHandCursor)

    def leaveEvent(self, event):
        self._hover_x = -1
        self.setCursor(Qt.PointingHandCursor)
        self.update()

    def wheelEvent(self, event):
        cursor_x = int(event.position().x())
        factor = 1.25 if event.angleDelta().y() < 0 else 0.8
        self.zoom_at(cursor_x, factor)
