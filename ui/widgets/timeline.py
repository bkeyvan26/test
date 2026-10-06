# -*- coding: utf-8 -*-
"""K1 VMS — Timeline (3-layer professional VMS timeline)

Layers:
  1. RULER     : time labels + ticks (zoom aware)
  2. RECORDING : RED on BLACK background  ← واقعیت ضبط
  3. MOTION    : YELLOW on dark-blue bg   ← آماده برای Phase F
  4. ALARM     : RED triangles            ← آماده برای Phase F
  Playhead     : red line + triangle marker

Backward compatible with old API:
  set_segments(list of {start, duration, state, ...})
New API (preferred, from RecordingIndexer):
  set_day_index(DayIndex)
  set_motion_events([{start, end, type}, ...])
  set_alarm_events([{time, level, label}, ...])
"""
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRect, QPoint
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QPolygon

from ui import theme


def _fmt_hms(seconds):
    s = max(0, int(seconds))
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


def _fmt_hm(seconds):
    s = max(0, int(seconds))
    return f"{s//3600:02d}:{(s%3600)//60:02d}"


# ============================================================
# Colors — single source of truth
# ============================================================
COLOR_RECORDED          = "#e74c3c"   # قرمز روشن — ضبط‌شده
COLOR_NOT_RECORDED      = "#000000"   # مشکی — بدون ضبط / gap
COLOR_MOTION_FILL       = "#f1c40f"   # زرد — حرکت
COLOR_MOTION_BG         = "#0a1a2a"   # آبی تیره — پس‌زمینه motion
COLOR_ALARM_MARKER      = "#c0392b"   # قرمز تیره — marker آلارم
COLOR_PLAYHEAD          = "#ff4d4d"
VISUAL_JOIN_GAP_SEC     = 3.0
COLOR_ACTIVE_SEGMENT    = "#ffffff"   # border سفید روی segment فعال
COLOR_RULER_BG          = None        # = theme.COLOR_BG_CARD
COLOR_GRID              = None        # = theme.COLOR_BORDER


class TimelineWidget(QWidget):
    """Professional 3-layer VMS timeline."""

    seek_requested = Signal(float)

    # ---- Layout constants ----
    RULER_H         = 24
    GAP             = 3
    RECORDING_H     = 18
    MOTION_H        = 14
    ALARM_H         = 8
    BOTTOM_PAD      = 4

    # ---- Zoom limits ----
    MIN_SPAN_SEC    = 60.0          # حداقل 1 دقیقه
    MAX_SPAN_SEC    = 86400.0       # حداکثر 24 ساعت

    # ---- Label spacing ----
    MIN_LABEL_GAP_PX = 58

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(96)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("background: transparent;")

        # Data
        self._raw_segments = []      # list of {start, end, state, type, path}
        self._visual_runs = []  # cached merged runs; rebuild only when segment data changes       # cached recording coverage for fast paint
        self._motion_events = []     # list of {start, end, type}
        self._alarm_events = []      # list of {time, level, label}

        # Playback
        self._playhead = 0.0
        self._active_segment_path = None  # for highlight

        # Interaction
        self._dragging = False
        self._panning = False
        self._pan_start_x = 0
        self._pan_start_zoom = (0.0, 86400.0)
        self._hover_x = -1

        # Zoom window
        self._zoom_start = 0.0
        self._zoom_end = 86400.0

    # ============================================================
    # Public API
    # ============================================================
    def set_segments(self, segments):
        """Accept list of dicts. Backward-compatible with:
             - {start, duration, ...}
             - {start, end, ...}
        """
        out = []
        for s in segments or []:
            try:
                start = float(s.get("start", 0))
                if "end" in s and s["end"] is not None:
                    end = float(s["end"])
                else:
                    dur = float(s.get("duration") or 0)
                    end = start + dur
                if end <= start:
                    continue
                out.append({
                    "start": start,
                    "end": end,
                    "state": s.get("state", ""),
                    "type": s.get("type", "continuous"),
                    "path": s.get("path", ""),
                })
            except Exception:
                continue
        out.sort(key=lambda x: x["start"])
        self._raw_segments = out
        self._rebuild_visual_runs()
        self.update()

    def _rebuild_visual_runs(self):
        runs = []
        for seg in self._raw_segments:
            if not runs:
                runs.append([seg["start"], seg["end"]])
                continue
            prev = runs[-1]
            gap = float(seg["start"]) - float(prev[1])
            if gap <= VISUAL_JOIN_GAP_SEC:
                prev[1] = max(float(prev[1]), float(seg["end"]))
            else:
                runs.append([seg["start"], seg["end"]])
        self._visual_runs = runs

    def set_day_index(self, day_index):
        """Preferred: accept a RecordingIndexer.DayIndex object."""
        segs = []
        for s in getattr(day_index, "segments", []) or []:
            try:
                start = float(s.start_sec)
                end = float(s.end_sec) if s.end_sec is not None else None
                if end is None and s.duration:
                    end = start + float(s.duration)
                if end is None or end <= start:
                    continue
                segs.append({
                    "start": start,
                    "end": end,
                    "state": getattr(s, "state", ""),
                    "type": "continuous",
                    "path": getattr(s, "file_path", ""),
                })
            except Exception:
                continue
        self.set_segments(segs)

        # Also load gaps if present (they're implicit here; ignore for now)
        self.update()

    def set_motion_events(self, events):
        """Accept list of {start, end} or {time, duration}.
        Empty list is fine — Motion layer stays empty.
        """
        out = []
        for e in events or []:
            try:
                if "start" in e and "end" in e:
                    s, en = float(e["start"]), float(e["end"])
                else:
                    s = float(e.get("time", 0))
                    en = s + float(e.get("duration", 5.0))
                if en <= s:
                    continue
                out.append({
                    "start": s,
                    "end": en,
                    "type": e.get("type", "motion"),
                })
            except Exception:
                continue
        out.sort(key=lambda x: x["start"])
        self._motion_events = out
        self.update()

    def set_alarm_events(self, events):
        """Accept list of {time} or {start}."""
        out = []
        for e in events or []:
            try:
                t = float(e.get("time", e.get("start", 0)))
                out.append({
                    "time": t,
                    "level": e.get("level", "info"),
                    "label": e.get("label", ""),
                })
            except Exception:
                continue
        out.sort(key=lambda x: x["time"])
        self._alarm_events = out
        self.update()

    def set_playhead(self, seconds):
        self._playhead = max(0.0, min(86400.0, float(seconds)))
        self.update()

    def set_active_segment(self, path):
        self._active_segment_path = path or None
        self.update()

    # ---------- Zoom controls ----------
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
    # Ruler step selection
    # ============================================================
    def _ruler_steps(self):
        span = self._zoom_end - self._zoom_start
        if span >= 43200:  return 3600, 900      # ≥12h   : 1h   / 15m
        if span >= 21600:  return 1800, 300      # ≥6h    : 30m  / 5m
        if span >= 10800:  return 900, 180       # ≥3h    : 15m  / 3m
        if span >= 5400:   return 600, 60        # ≥90m   : 10m  / 1m
        if span >= 2700:   return 300, 30        # ≥45m   : 5m   / 30s
        if span >= 1800:   return 120, 15        # ≥30m   : 2m   / 15s
        if span >= 900:    return 60, 10         # ≥15m   : 1m   / 10s
        if span >= 300:    return 30, 5          # ≥5m    : 30s  / 5s
        if span >= 120:    return 10, 2          # ≥2m    : 10s  / 2s
        if span >= 60:     return 5, 1           # ≥1m    : 5s   / 1s
        if span >= 30:     return 2, 1           # ≥30s   : 2s   / 1s
        return 1, 1

    # ============================================================
    # Paint
    # ============================================================
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)

        w = self.width()
        h = self.height()

        # Background
        p.fillRect(self.rect(), QColor(theme.COLOR_BG_PANEL))

        # ===== 1. RULER =====
        y_ruler = 0
        p.fillRect(QRect(0, y_ruler, w, self.RULER_H),
                   QColor(theme.COLOR_BG_CARD))
        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        p.drawLine(0, self.RULER_H, w, self.RULER_H)

        major_step, minor_step = self._ruler_steps()

        # Minor ticks
        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        t_min = int(self._zoom_start // minor_step) * minor_step
        while t_min <= self._zoom_end:
            x = self._sec_to_x(t_min)
            if 0 <= x <= w and (t_min % major_step) != 0:
                p.drawLine(x, self.RULER_H - 4, x, self.RULER_H)
            t_min += minor_step

        # Major ticks + labels (adaptive skip)
        p.setFont(QFont("Consolas", 7))
        last_label_x = -10000
        t_maj = int(self._zoom_start // major_step) * major_step
        while t_maj <= self._zoom_end:
            x = self._sec_to_x(t_maj)
            if -40 <= x <= w + 40:
                p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
                p.drawLine(x, 0, x, self.RULER_H)
                if x - last_label_x >= self.MIN_LABEL_GAP_PX:
                    p.setPen(QColor(theme.COLOR_TEXT_SECONDARY))
                    if major_step < 60:
                        label = _fmt_hms(t_maj)
                    else:
                        label = _fmt_hm(t_maj)
                    p.drawText(QRect(x + 3, 0, 80, self.RULER_H),
                               Qt.AlignVCenter | Qt.AlignLeft, label)
                    last_label_x = x
            t_maj += major_step

        # ===== 2. RECORDING TRACK =====
        y_rec = self.RULER_H + self.GAP
        rec_rect = QRect(0, y_rec, w, self.RECORDING_H)
        p.fillRect(rec_rect, QColor(COLOR_NOT_RECORDED))

        p.setPen(Qt.NoPen)

        # Do not paint every TS file as a separate red island. MediaMTX may
        # rotate recording files frequently and a small boundary jitter does
        # not mean that the camera stopped recording. Collapse short gaps into
        # one continuous visual recording interval; real gaps remain black.
        for start, end in self._visual_runs:
            x0 = self._sec_to_x(start)
            x1 = self._sec_to_x(end)
            if x1 < 0 or x0 > w:
                continue
            x0c = max(0, x0)
            x1c = min(w, x1)
            if x1c <= x0c:
                x1c = x0c + 1
            p.fillRect(QRect(x0c, y_rec, x1c - x0c, self.RECORDING_H),
                       QColor(COLOR_RECORDED))

        # active segment highlight
        if self._active_segment_path:
            for seg in self._raw_segments:
                if seg.get("path") == self._active_segment_path:
                    x0 = self._sec_to_x(seg["start"])
                    x1 = self._sec_to_x(seg["end"])
                    if x1 >= 0 and x0 <= w:
                        p.setPen(QPen(QColor(COLOR_ACTIVE_SEGMENT), 2))
                        p.setBrush(Qt.NoBrush)
                        p.drawRect(QRect(max(0, x0), y_rec,
                                         min(w, x1) - max(0, x0),
                                         self.RECORDING_H))
                    break

        # Track border
        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rec_rect)

        # ===== 3. MOTION TRACK =====
        y_mot = y_rec + self.RECORDING_H + self.GAP
        mot_rect = QRect(0, y_mot, w, self.MOTION_H)
        p.fillRect(mot_rect, QColor(COLOR_MOTION_BG))

        p.setPen(Qt.NoPen)
        for ev in self._motion_events:
            x0 = self._sec_to_x(ev["start"])
            x1 = self._sec_to_x(ev["end"])
            if x1 < 0 or x0 > w:
                continue
            x0c = max(0, x0)
            x1c = min(w, x1)
            if x1c <= x0c:
                x1c = x0c + 1
            p.fillRect(QRect(x0c, y_mot, x1c - x0c, self.MOTION_H),
                       QColor(COLOR_MOTION_FILL))

        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(mot_rect)

        # ===== 4. ALARM TRACK =====
        y_alm = y_mot + self.MOTION_H + self.GAP
        alm_rect = QRect(0, y_alm, w, self.ALARM_H)
        p.fillRect(alm_rect, QColor("#0a0e14"))

        p.setBrush(QBrush(QColor(COLOR_ALARM_MARKER)))
        p.setPen(Qt.NoPen)
        for ev in self._alarm_events:
            x = self._sec_to_x(ev["time"])
            if 0 <= x <= w:
                p.drawPolygon([
                    QPoint(x - 3, y_alm + self.ALARM_H - 1),
                    QPoint(x + 3, y_alm + self.ALARM_H - 1),
                    QPoint(x, y_alm + 1),
                ])

        p.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(alm_rect)

        # ===== HOVER =====
        if self._hover_x >= 0:
            p.setPen(QPen(QColor(theme.COLOR_TEXT_SECONDARY), 1, Qt.DotLine))
            p.drawLine(self._hover_x, 0, self._hover_x, h)

            hover_sec = self._x_to_sec(self._hover_x)
            label = _fmt_hms(hover_sec)
            p.setPen(QColor(theme.COLOR_TEXT_PRIMARY))
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            tx = self._hover_x + 4
            if tx + 64 > w:
                tx = self._hover_x - 68
            p.fillRect(QRect(tx - 2, 0, 62, self.RULER_H),
                       QColor(theme.COLOR_BG_DARK))
            p.drawText(QRect(tx, 0, 58, self.RULER_H),
                       Qt.AlignVCenter | Qt.AlignLeft, label)

        # ===== PLAYHEAD =====
        px = self._sec_to_x(self._playhead)
        if -5 <= px <= w + 5:
            p.setPen(QPen(QColor(COLOR_PLAYHEAD), 2))
            p.drawLine(px, 0, px, h)

            p.setBrush(QBrush(QColor(COLOR_PLAYHEAD)))
            p.setPen(Qt.NoPen)
            p.drawPolygon([
                QPoint(px - 5, 0),
                QPoint(px + 5, 0),
                QPoint(px, 8),
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
            shift = -dx / w * span
            ns = self._pan_start_zoom[0] + shift
            ne = self._pan_start_zoom[1] + shift
            if ns < 0:
                ns = 0
                ne = span
            if ne > 86400:
                ne = 86400
                ns = 86400 - span
            self._zoom_start = ns
            self._zoom_end = ne
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