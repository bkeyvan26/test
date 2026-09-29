# -*- coding: utf-8 -*-
"""K1 VMS — Icon Rail"""
from PySide6.QtWidgets import QFrame, QVBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Qt, Signal, QSize
from ui import theme
from ui.icons import make_icon


class IconRailButton(QPushButton):
    def __init__(self, icon_name, tooltip, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.setToolTip(tooltip)
        self.setFixedSize(theme.RAIL_WIDTH - 12, 44)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setIconSize(QSize(20, 20))
        self._active = False
        self._refresh()
    
    def set_active(self, active):
        self._active = active
        self.setChecked(active)
        self._refresh()
    
    def _refresh(self):
        color = theme.COLOR_TEXT_PRIMARY if self._active else theme.COLOR_TEXT_SECONDARY
        self.setIcon(make_icon(self.icon_name, color, 20))
        bg = theme.COLOR_BG_ACTIVE if self._active else "transparent"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                border: none;
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_BG_HOVER};
            }}
        """)


class IconRail(QFrame):
    page_changed = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("IconRail")
        self.setFixedWidth(theme.RAIL_WIDTH)
        self.setStyleSheet(f"""
            QFrame#IconRail {{
                background: {theme.COLOR_BG_RAIL};
                border-right: 1px solid {theme.COLOR_BORDER};
            }}
        """)
        
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 12, 6, 12)
        v.setSpacing(3)
        
        # Logo
        logo = QLabel("K1")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(theme.RAIL_WIDTH - 12, 36)
        logo.setStyleSheet(f"""
            color: {theme.COLOR_ACCENT};
            font-size: 16px;
            font-weight: 900;
            letter-spacing: 1px;
            background: {theme.COLOR_BG_CARD};
            border-radius: 6px;
        """)
        v.addWidget(logo)
        v.addSpacing(6)
        
        self.buttons = {}
        nav = [
            ("dashboard", "home",        "Dashboard  (D)"),
            ("live",      "video",       "Live View  (L)"),
            ("cameras",   "grid",        "Cameras  (C)"),
            ("playback",  "play-circle", "Playback  (P)"),
            ("alarms",    "bell",        "Alarms  (A)"),
            ("ai",        "cpu",         "AI Analytics  (I)"),
            ("search",    "search",      "Search  (S)"),
            ("map",       "map",         "Map  (M)"),
            ("reports",   "bar-chart",   "Reports  (R)"),
        ]
        for key, icon, tip in nav:
            b = IconRailButton(icon, tip)
            b.clicked.connect(lambda _=False, k=key: self._on_click(k))
            v.addWidget(b, 0, Qt.AlignHCenter)
            self.buttons[key] = b
        
        v.addStretch(1)
        
        for key, icon, tip in [
            ("settings", "sliders",  "Settings"),
            ("users",    "users",    "Users"),
            ("exit",     "log-out",  "Exit"),
        ]:
            b = IconRailButton(icon, tip)
            b.clicked.connect(lambda _=False, k=key: self._on_click(k))
            v.addWidget(b, 0, Qt.AlignHCenter)
            self.buttons[key] = b
        
        self.set_active_page("dashboard")
    
    def _on_click(self, key):
        if key == "exit":
            self.page_changed.emit(key)
            return
        self.set_active_page(key)
        self.page_changed.emit(key)
    
    def set_active_page(self, key):
        for k, b in self.buttons.items():
            b.set_active(k == key)