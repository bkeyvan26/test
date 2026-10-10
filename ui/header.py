# -*- coding: utf-8 -*-
"""K1 VMS — Global Header (anti-flicker stats)"""
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton
)
from PySide6.QtCore import Qt, Signal, QTimer
from ui import theme
from ui.icons import make_icon
from ui.activity_bus import ActivityBus


class StatusPill(QFrame):
    def __init__(self, icon_name, text, color=None, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setFixedHeight(26)
        self._icon_name = icon_name
        self._color = color or theme.COLOR_TEXT_SECONDARY

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 12, 0)
        h.setSpacing(6)

        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(14, 14)
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")
        h.addWidget(self.icon_lbl)

        self.text_lbl = QLabel(text)
        self.text_lbl.setStyleSheet("background: transparent; border: none;")
        h.addWidget(self.text_lbl)
        self._apply()

    def _apply(self):
        self.icon_lbl.setPixmap(
            make_icon(self._icon_name, self._color, 14).pixmap(14, 14)
        )
        self.setStyleSheet(f"""
            QFrame#StatusPill {{
                background: #1e242e;
                border: 1px solid #2e3846;
                border-radius: 13px;
            }}
        """)
        self.text_lbl.setStyleSheet(
            f"color: {self._color}; background: transparent; "
            f"font-size: 11px; font-weight: 600; border: none;"
        )

    def set_text(self, text, color=None):
        self.text_lbl.setText(text)
        if color:
            self._color = color
        self._apply()


class HeaderActivity(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HeaderActivity")
        self.setFixedHeight(26)
        self.setVisible(False)

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 12, 0)
        h.setSpacing(6)

        self.dot = QLabel("●")
        self.dot.setFixedSize(14, 14)
        self.dot.setAlignment(Qt.AlignCenter)
        self.dot.setStyleSheet(
            "color: #1abc9c; background: transparent; border: none; "
            "font-size: 11px;"
        )
        h.addWidget(self.dot)

        self.text_lbl = QLabel("")
        self.text_lbl.setStyleSheet(
            "color: #ecf0f1; background: transparent; "
            "font-size: 11px; font-weight: 600; border: none;"
        )
        h.addWidget(self.text_lbl)

        self.setStyleSheet("""
            QFrame#HeaderActivity {
                background: #1e242e;
                border: 1px solid #16a085;
                border-radius: 13px;
            }
        """)

        self._on = True
        self._blink = QTimer(self)
        self._blink.timeout.connect(self._tick)
        self._blink.setInterval(500)

        try:
            ActivityBus.instance().changed.connect(self._on_activity)
        except Exception:
            pass

    def _tick(self):
        self._on = not self._on
        color = "#1abc9c" if self._on else "#0e6b58"
        self.dot.setStyleSheet(
            f"color: {color}; background: transparent; border: none; "
            f"font-size: 11px;"
        )

    def _on_activity(self, active: bool, text: str):
        if active:
            self.text_lbl.setText(text or "در حال انجام…")
            self.show()
            self._blink.start()
        else:
            self._blink.stop()
            self.hide()


class GlobalHeader(QFrame):
    search_requested = Signal()
    user_menu_requested = Signal()

    def __init__(self, parent=None, nvr_engine=None):
        super().__init__(parent)
        self.setObjectName("GlobalHeader")
        self.setFixedHeight(theme.HEADER_HEIGHT)
        self._nvr_engine = nvr_engine

        # ★ cache ضد flicker
        self._cached_online = 0
        self._cached_recording = 0
        self._cached_total = 0
        self._empty_strikes = 0

        self.setStyleSheet(f"""
            QFrame#GlobalHeader {{
                background: {theme.COLOR_BG_HEADER};
                border-bottom: 1px solid {theme.COLOR_BORDER};
            }}
        """)

        h = QHBoxLayout(self)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(10)

        brand = QLabel("K1 VMS")
        brand.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; background: transparent; "
            f"font-size: 15px; font-weight: 800; letter-spacing: 1px;"
        )
        h.addWidget(brand)

        sep = QFrame()
        sep.setFixedSize(1, 22)
        sep.setStyleSheet("background: #2e3846; border: none;")
        h.addWidget(sep)

        self.online_pill = StatusPill(
            "video", "0 / 0", theme.COLOR_STATUS_OFFLINE)
        h.addWidget(self.online_pill)

        self.alarm_pill = StatusPill(
            "bell", "0 Alarms", theme.COLOR_STATUS_OFFLINE)
        h.addWidget(self.alarm_pill)

        self.record_pill = StatusPill(
            "play-circle", "0 Rec", theme.COLOR_STATUS_OFFLINE)
        h.addWidget(self.record_pill)

        self.ai_pill = StatusPill(
            "cpu", "AI Disabled", theme.COLOR_TEXT_SECONDARY)
        h.addWidget(self.ai_pill)

        self.activity = HeaderActivity(self)
        h.addWidget(self.activity)

        h.addStretch(1)

        self.search_btn = QPushButton("  Search…")
        self.search_btn.setIcon(
            make_icon("search", theme.COLOR_TEXT_SECONDARY, 14))
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.setFixedWidth(220)
        self.search_btn.setFixedHeight(30)
        self.search_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1e242e;
                color: {theme.COLOR_TEXT_SECONDARY};
                border: 1px solid #2e3846;
                border-radius: 6px;
                padding: 4px 12px;
                text-align: left;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: #252d3a;
                color: {theme.COLOR_TEXT_PRIMARY};
            }}
        """)
        self.search_btn.clicked.connect(self.search_requested.emit)
        h.addWidget(self.search_btn)

        self.user_btn = QPushButton("  User")
        self.user_btn.setIcon(
            make_icon("users", theme.COLOR_TEXT_PRIMARY, 14))
        self.user_btn.setCursor(Qt.PointingHandCursor)
        self.user_btn.setFixedHeight(30)
        self.user_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1e242e;
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid #2e3846;
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #252d3a; }}
        """)
        self.user_btn.clicked.connect(self.user_menu_requested.emit)
        h.addWidget(self.user_btn)

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self.refresh_stats)
        self._stats_timer.start(3000)
        QTimer.singleShot(1500, self.refresh_stats)

    def refresh_stats(self):
        """آمار پایدار — cache ضد flicker."""
        try:
            from core.camera_manager import CameraManager
            cam_manager = CameraManager()
            all_cams = cam_manager.all()
            total = len(all_cams)

            if total == 0:
                self._cached_online = 0
                self._cached_recording = 0
                self._cached_total = 0
                self.set_online(0, 0)
                self.set_recording(0, 0)
                return

            self._cached_total = total

            if self._nvr_engine is None:
                self._cached_online = total
                self.set_online(total, total)
                self.set_recording(self._cached_recording, total)
                return

            try:
                statuses = self._nvr_engine.get_all_statuses() or {}
            except Exception:
                statuses = {}

            # ★ اگر statuses خیلی کم است، احتمالاً reload → cache قبلی
            if len(statuses) < max(3, int(total * 0.3)):
                self._empty_strikes += 1
                if self._empty_strikes <= 10 and self._cached_online > 0:
                    self.set_online(self._cached_online, self._cached_total)
                    self.set_recording(self._cached_recording,
                                        self._cached_total)
                    return
                # ۱۰ بار پشت‌سرهم → واقعاً آفلاین
                self._empty_strikes = 0
                self._cached_online = 0
                self._cached_recording = 0
                self.set_online(0, total)
                self.set_recording(0, total)
                return

            self._empty_strikes = 0

            online = 0
            rec = 0
            for uid, st in statuses.items():
                if st in ("online", "recording", "starting"):
                    online += 1
                if st == "recording":
                    rec += 1

            if online == 0 and self._cached_online > 0:
                self._empty_strikes += 1
                if self._empty_strikes <= 10:
                    self.set_online(self._cached_online, self._cached_total)
                    self.set_recording(self._cached_recording,
                                        self._cached_total)
                    return

            self._cached_online = online
            self._cached_recording = rec
            self.set_online(online, total)
            self.set_recording(rec, total)
        except Exception as e:
            print(f"[header] refresh_stats error: {e}")

    def set_online(self, online, total):
        if total == 0:
            self.online_pill.set_text("No cameras", theme.COLOR_TEXT_SECONDARY)
        elif online == total:
            self.online_pill.set_text(
                f"{online} / {total} Online", theme.COLOR_STATUS_ONLINE)
        elif online == 0:
            self.online_pill.set_text(
                f"0 / {total} Offline", theme.COLOR_STATUS_ERROR)
        else:
            self.online_pill.set_text(
                f"{online} / {total} Online", theme.COLOR_STATUS_WARNING)

    def set_alarms(self, count):
        if count == 0:
            self.alarm_pill.set_text("No Alarms", theme.COLOR_TEXT_SECONDARY)
        else:
            self.alarm_pill.set_text(
                f"{count} Alarms", theme.COLOR_STATUS_ERROR)

    def set_recording(self, active, total=None):
        if total is None:
            if active == 0:
                self.record_pill.set_text("Not Recording",
                                           theme.COLOR_TEXT_SECONDARY)
            else:
                self.record_pill.set_text(
                    f"{active} REC", theme.COLOR_STATUS_ONLINE)
            return
        if total == 0:
            self.record_pill.set_text("No cameras", theme.COLOR_TEXT_SECONDARY)
        elif active == 0:
            self.record_pill.set_text(
                f"0 / {total} REC", theme.COLOR_STATUS_ERROR)
        elif active == total:
            self.record_pill.set_text(
                f"{active} / {total} REC", theme.COLOR_STATUS_ONLINE)
        else:
            self.record_pill.set_text(
                f"{active} / {total} REC", theme.COLOR_STATUS_WARNING)

    def set_ai_status(self, status):
        mapping = {
            "disabled":   ("AI Disabled", theme.COLOR_TEXT_SECONDARY),
            "ready":      ("AI Ready", theme.COLOR_STATUS_ONLINE),
            "processing": ("AI Processing", theme.COLOR_ACCENT),
            "error":      ("AI Error", theme.COLOR_STATUS_ERROR),
        }
        text, color = mapping.get(status, ("AI", theme.COLOR_TEXT_SECONDARY))
        self.ai_pill.set_text(text, color)

    def set_username(self, name):
        self.user_btn.setText(f"  {name}")