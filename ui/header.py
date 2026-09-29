# -*- coding: utf-8 -*-
"""K1 VMS — Global Header"""
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton
)
from PySide6.QtCore import Qt, Signal
from ui import theme
from ui.icons import make_icon


# ============================================================
# Status Pill
# ============================================================
class StatusPill(QFrame):
    """Status pill with clean, visible styling."""

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
        # Icon
        self.icon_lbl.setPixmap(
            make_icon(self._icon_name, self._color, 14).pixmap(14, 14)
        )
        # Pill frame
        self.setStyleSheet(f"""
            QFrame#StatusPill {{
                background: #1e242e;
                border: 1px solid #2e3846;
                border-radius: 13px;
            }}
        """)
        # Text
        self.text_lbl.setStyleSheet(
            f"color: {self._color}; background: transparent; "
            f"font-size: 11px; font-weight: 600; border: none;"
        )

    def set_text(self, text, color=None):
        self.text_lbl.setText(text)
        if color:
            self._color = color
        self._apply()


# ============================================================
# Global Header
# ============================================================
class GlobalHeader(QFrame):
    search_requested = Signal()
    user_menu_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GlobalHeader")
        self.setFixedHeight(theme.HEADER_HEIGHT)
        self.setStyleSheet(f"""
            QFrame#GlobalHeader {{
                background: {theme.COLOR_BG_HEADER};
                border-bottom: 1px solid {theme.COLOR_BORDER};
            }}
        """)

        h = QHBoxLayout(self)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(10)

        # Brand
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

        # Pills
        self.online_pill = StatusPill("video", "0 / 0", theme.COLOR_STATUS_OFFLINE)
        h.addWidget(self.online_pill)

        self.alarm_pill = StatusPill("bell", "0 Alarms", theme.COLOR_STATUS_OFFLINE)
        h.addWidget(self.alarm_pill)

        self.record_pill = StatusPill("play-circle", "0 Rec", theme.COLOR_STATUS_OFFLINE)
        h.addWidget(self.record_pill)

        self.ai_pill = StatusPill("cpu", "AI Disabled", theme.COLOR_TEXT_SECONDARY)
        h.addWidget(self.ai_pill)

        h.addStretch(1)

        # Search
        self.search_btn = QPushButton("  Search…")
        self.search_btn.setIcon(make_icon("search", theme.COLOR_TEXT_SECONDARY, 14))
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.setFixedWidth(220)
        self.search_btn.setFixedHeight(30)
        self.search_btn.setToolTip("Ctrl+K")
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

        # User
        self.user_btn = QPushButton("  User")
        self.user_btn.setIcon(make_icon("users", theme.COLOR_TEXT_PRIMARY, 14))
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
            QPushButton:hover {{
                background: #252d3a;
            }}
        """)
        self.user_btn.clicked.connect(self.user_menu_requested.emit)
        h.addWidget(self.user_btn)

    # ------------------------------------------------------------
    # Public setters
    # ------------------------------------------------------------
    def set_online(self, online, total):
        if total == 0:
            self.online_pill.set_text("No cameras", theme.COLOR_TEXT_SECONDARY)
        elif online == total:
            self.online_pill.set_text(f"{online} / {total} Online", theme.COLOR_STATUS_ONLINE)
        elif online == 0:
            self.online_pill.set_text(f"0 / {total} Offline", theme.COLOR_STATUS_ERROR)
        else:
            self.online_pill.set_text(f"{online} / {total} Online", theme.COLOR_STATUS_WARNING)

    def set_alarms(self, count):
        if count == 0:
            self.alarm_pill.set_text("No Alarms", theme.COLOR_TEXT_SECONDARY)
        else:
            self.alarm_pill.set_text(f"{count} Alarms", theme.COLOR_STATUS_ERROR)

    def set_recording(self, active, total=None):
        """Show recording count.

        - set_recording(n)             → legacy: "n REC"
        - set_recording(active, total) → new:    "active / total REC"
        """
        if total is None:
            if active == 0:
                self.record_pill.set_text("Not Recording", theme.COLOR_TEXT_SECONDARY)
            else:
                self.record_pill.set_text(f"{active} REC", theme.COLOR_STATUS_ONLINE)
            return

        if total == 0:
            self.record_pill.set_text("No cameras", theme.COLOR_TEXT_SECONDARY)
        elif active == 0:
            self.record_pill.set_text(f"0 / {total} REC", theme.COLOR_STATUS_ERROR)
        elif active == total:
            self.record_pill.set_text(f"{active} / {total} REC", theme.COLOR_STATUS_ONLINE)
        else:
            self.record_pill.set_text(f"{active} / {total} REC", theme.COLOR_STATUS_WARNING)

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