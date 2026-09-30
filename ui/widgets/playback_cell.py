# -*- coding: utf-8 -*-
"""K1 VMS — Playback Cell (Phase 6.7.5: zoom + crash-safe)"""
import math
from PySide6.QtWidgets import QFrame, QVBoxLayout
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QTimer
from PySide6.QtGui import QColor, QPainter, QFont, QPen

from ui import theme
from core.playback_engine import PlaybackEngine
from ui.widgets.video_display import VideoDisplay


class PlaybackCell(QFrame):
    clicked = Signal(int)
    double_clicked = Signal(int)
    close_requested = Signal(int)
    context_requested = Signal(int, QPoint)
    state_changed = Signal(int, str)
    position_changed = Signal(int, float)
    frame_ready = Signal(int)

    def __init__(self, idx: int, parent=None):
        super().__init__(parent)
        self.idx = idx
        self._cam_uid = None
        self._cam_name = ""
        self._day = None
        self._segments = []
        self._active = False
        self._show_close = False
        self._hovering = False
        self._show_loading = False
        self._loading_text = ""
        self._spinner_angle = 0
        self._alive = True

        self._engine = PlaybackEngine(self)
        self._engine.frame_ready.connect(self._on_frame)
        self._engine.position_changed.connect(self._on_position)
        self._engine.state_changed.connect(self._on_state)

        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "PlaybackCell { background: #0a0a0a; "
            "border: 2px solid #232830; border-radius: 4px; }"
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(0)

        self.video = VideoDisplay()
        self.video.set_placeholder("")
        # ★ مهم: video داخلی events را نمی‌گیرد
        try:
            self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        except Exception:
            pass
        v.addWidget(self.video, 1)

    # ============================================================
    def mark_dead(self):
        self._alive = False
        try:
            self._engine.stop()
        except Exception:
            pass

    def engine(self):
        return self._engine

    def cam_uid(self):
        return self._cam_uid

    def cam_name(self):
        return self._cam_name

    def day(self):
        return self._day

    def segments(self):
        return self._segments

    def set_active(self, active: bool):
        self._active = bool(active)
        self._refresh_border()

    def _refresh_border(self):
        if self._active:
            color = theme.COLOR_ACCENT
        elif self._hovering:
            color = "#3a4a5a"
        else:
            color = "#232830"
        self.setStyleSheet(
            f"PlaybackCell {{ background: #0a0a0a; "
            f"border: 2px solid {color}; border-radius: 4px; }}"
        )
        if self._alive:
            try:
                self.update()
            except RuntimeError:
                pass

    def set_show_close(self, on: bool):
        self._show_close = bool(on)
        if self._alive:
            try:
                self.update()
            except RuntimeError:
                pass

    # ============================================================
    def assign(self, cam_uid: str, cam_name: str, day, segments: list):
        self._cam_uid = cam_uid
        self._cam_name = cam_name
        self._day = day
        self._segments = segments or []
        self._show_loading = False
        try:
            self._engine.stop()
            self._engine.set_segments(self._segments)
            if self._segments:
                first = self._segments[0]["start"]
                self._engine.seek(first)
                self._engine.pause()
            else:
                self.video.set_placeholder(
                    f"بدون ضبط\n{cam_name}\n{day}"
                )
        except Exception as e:
            print(f"[PlaybackCell.assign] {e}")

    def clear(self):
        try:
            self._engine.stop()
            self._engine.set_segments([])
        except Exception:
            pass
        self._cam_uid = None
        self._cam_name = ""
        self._day = None
        self._segments = []
        self._show_loading = False
        try:
            self.video.set_placeholder("")
        except Exception:
            pass

    def set_loading(self, on: bool, text: str = ""):
        self._show_loading = bool(on)
        self._loading_text = text
        if self._alive:
            try:
                self.update()
            except RuntimeError:
                pass

    def tick_spinner(self):
        if not self._alive:
            return
        self._spinner_angle = (self._spinner_angle + 14) % 360
        if self._show_loading:
            try:
                self.update()
            except RuntimeError:
                pass

    # ============================================================
    # Engine handlers
    # ============================================================
    def _on_frame(self, rgb):
        if not self._alive:
            return
        try:
            if rgb is None:
                self.video.set_placeholder("— NO RECORDING —")
                return
            self.video.set_frame(rgb)
            self.frame_ready.emit(self.idx)
        except RuntimeError:
            pass
        except Exception as e:
            print(f"[PlaybackCell._on_frame] {e}")

    def _on_position(self, seconds):
        if not self._alive:
            return
        try:
            self.position_changed.emit(self.idx, seconds)
        except RuntimeError:
            pass

    def _on_state(self, state):
        if not self._alive:
            return
        try:
            if state == "playing":
                self._show_loading = False
            self.state_changed.emit(self.idx, state)
            self.update()
        except RuntimeError:
            pass

    # ============================================================
    # Close rect
    # ============================================================
    def _close_rect(self) -> QRect:
        r = 10
        return QRect(self.width() - r * 2 - 6, 6, r * 2, r * 2)

    # ============================================================
    # ★ Mouse — events به cell می‌رسند (چون video شفاف است)
    # ============================================================
    def mousePressEvent(self, e):
        if not self._alive:
            return
        if e.button() == Qt.LeftButton:
            if self._show_close and self._cam_uid:
                if self._close_rect().contains(e.position().toPoint()):
                    e.accept()
                    QTimer.singleShot(0, lambda: self._emit_close())
                    return
            e.accept()
            QTimer.singleShot(0, lambda: self._emit_click())
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if not self._alive:
            return
        if e.button() == Qt.LeftButton:
            e.accept()
            QTimer.singleShot(0, lambda: self._emit_double_click())
            return
        super().mouseDoubleClickEvent(e)

    def _emit_click(self):
        if not self._alive:
            return
        try:
            self.clicked.emit(self.idx)
        except RuntimeError:
            pass

    def _emit_double_click(self):
        if not self._alive:
            return
        try:
            self.double_clicked.emit(self.idx)
        except RuntimeError:
            pass

    def _emit_close(self):
        if not self._alive:
            return
        try:
            self.close_requested.emit(self.idx)
        except RuntimeError:
            pass

    def contextMenuEvent(self, e):
        if not self._alive:
            return
        try:
            self.context_requested.emit(self.idx, self.mapToGlobal(e.pos()))
        except RuntimeError:
            pass

    def enterEvent(self, e):
        if not self._alive:
            return
        self._hovering = True
        self.set_show_close(True)
        self._refresh_border()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self._alive:
            return
        self._hovering = False
        self.set_show_close(False)
        self._refresh_border()
        super().leaveEvent(e)

    # ============================================================
    # ★ Zoom — روی cell، به video forward کن
    # ============================================================
    def wheelEvent(self, e):
        if not self._alive or self._cam_uid is None:
            e.ignore()
            return
        # به video forward کن (چون video transparent است)
        try:
            self.video.wheelEvent(e)
        except Exception:
            e.ignore()

    # ============================================================
    # Paint overlay
    # ============================================================
    def paintEvent(self, e):
        try:
            super().paintEvent(e)
        except Exception:
            pass
        if not self._alive:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)

            if self._cam_uid:
                bh = 20
                p.fillRect(2, self.height() - bh - 2,
                           self.width() - 4, bh,
                           QColor(0, 0, 0, 200))
                p.setPen(QColor("#ecf0f1"))
                p.setFont(QFont("Sans", 8, QFont.Bold))
                label = self._cam_name or "—"
                if self._day:
                    label += f"  •  {self._day.strftime('%Y-%m-%d')}"
                p.drawText(6, self.height() - bh,
                           self.width() - 12, bh,
                           Qt.AlignVCenter | Qt.AlignLeft, label)

            if self._show_close and self._cam_uid:
                r = self._close_rect()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(180, 40, 40, 230))
                p.drawEllipse(r)
                p.setPen(QPen(QColor(255, 255, 255), 2))
                m = 5
                p.drawLine(r.left() + m, r.top() + m,
                           r.right() - m, r.bottom() - m)
                p.drawLine(r.right() - m, r.top() + m,
                           r.left() + m, r.bottom() - m)

            if self._show_loading:
                cx, cy = self.width() // 2, self.height() // 2
                rad = 20
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(52, 60, 76, 220), 3))
                p.drawEllipse(QPoint(cx, cy), rad, rad)
                p.setPen(QPen(QColor(22, 160, 133), 3))
                p.drawArc(cx - rad, cy - rad, rad * 2, rad * 2,
                          int(self._spinner_angle * 16), int(110 * 16))
                if self._loading_text:
                    p.setPen(QColor("#e6ebf2"))
                    p.setFont(QFont("Sans", 9, QFont.Bold))
                    p.drawText(0, cy + rad + 8, self.width(), 20,
                               Qt.AlignHCenter | Qt.AlignTop,
                               self._loading_text[:40])

            if self._active and self._cam_uid:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(22, 160, 133, 240))
                p.drawEllipse(8, 8, 12, 12)

        except RuntimeError:
            pass
        except Exception:
            pass
        finally:
            try:
                p.end()
            except Exception:
                pass