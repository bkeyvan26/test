# -*- coding: utf-8 -*-
"""K1 VMS — Dashboard page"""
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout, QWidget
)
from PySide6.QtCore import Qt
from ui import theme
from ui.pages import BasePage


class StatCard(QFrame):
    def __init__(self, title, value="—", parent=None):
        super().__init__(parent)
        self.setFixedHeight(80)
        self.setStyleSheet(f"""
            QFrame {{
                background: {theme.COLOR_BG_CARD};
                border-radius: 8px;
                border: 1px solid {theme.COLOR_BORDER};
            }}
        """)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(4)
        t = QLabel(title.upper())
        t.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        v.addWidget(t)
        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 22px; font-weight: 800;"
        )
        v.addWidget(self.value_lbl)
    
    def set_value(self, value, color=None):
        self.value_lbl.setText(value)
        self.value_lbl.setStyleSheet(
            f"color: {color or theme.COLOR_TEXT_PRIMARY}; "
            f"font-size: 22px; font-weight: 800;"
        )


class DashboardPage(BasePage):
    PAGE_KEY = "dashboard"
    PAGE_TITLE = "Dashboard"
    PANEL_TITLE = "Overview"
    
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(16)
        
        head = QLabel("System Overview")
        head.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 20px; font-weight: 700;"
        )
        v.addWidget(head)
        
        grid = QGridLayout(); grid.setSpacing(12)
        self.card_online  = StatCard("Cameras Online", "—")
        self.card_alarms  = StatCard("Active Alarms",  "0")
        self.card_record  = StatCard("Recording",      "—")
        self.card_ai      = StatCard("AI Engine",      "Disabled")
        grid.addWidget(self.card_online, 0, 0)
        grid.addWidget(self.card_alarms, 0, 1)
        grid.addWidget(self.card_record, 0, 2)
        grid.addWidget(self.card_ai,     0, 3)
        v.addLayout(grid)
        
        bottom = QHBoxLayout(); bottom.setSpacing(12)
        
        events_card = QFrame()
        events_card.setStyleSheet(f"""
            QFrame {{
                background: {theme.COLOR_BG_CARD};
                border-radius: 8px;
                border: 1px solid {theme.COLOR_BORDER};
            }}
        """)
        ev = QVBoxLayout(events_card)
        ev.setContentsMargins(16, 12, 16, 12)
        et = QLabel("RECENT EVENTS")
        et.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; "
            f"font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        ev.addWidget(et)
        empty = QLabel("No recent events")
        empty.setStyleSheet(f"color: {theme.COLOR_TEXT_MUTED}; font-size: 12px;")
        ev.addWidget(empty)
        ev.addStretch(1)
        bottom.addWidget(events_card, 1)
        
        health_card = QFrame()
        health_card.setStyleSheet(events_card.styleSheet())
        hv = QVBoxLayout(health_card)
        hv.setContentsMargins(16, 12, 16, 12)
        ht = QLabel("SYSTEM HEALTH")
        ht.setStyleSheet(et.styleSheet())
        hv.addWidget(ht)
        self.health_rows = {}
        for key, label in [("cpu", "CPU"), ("ram", "RAM"),
                            ("storage", "Storage"), ("network", "Network")]:
            row = QHBoxLayout()
            k = QLabel(label)
            k.setStyleSheet(f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 12px;")
            k.setFixedWidth(80)
            row.addWidget(k)
            vl = QLabel("—")
            vl.setStyleSheet(f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
            row.addWidget(vl, 1)
            hv.addLayout(row)
            self.health_rows[key] = vl
        bottom.addWidget(health_card, 1)
        v.addLayout(bottom)
        v.addStretch(1)
    
    def update_stats(self, online, total, alarms, recording, ai_status):
        self.card_online.set_value(f"{online} / {total}",
            theme.COLOR_STATUS_ONLINE if online else theme.COLOR_STATUS_ERROR)
        self.card_alarms.set_value(str(alarms),
            theme.COLOR_STATUS_ERROR if alarms else theme.COLOR_TEXT_PRIMARY)
        self.card_record.set_value(str(recording),
            theme.COLOR_ACCENT if recording else theme.COLOR_TEXT_PRIMARY)
        self.card_ai.set_value(ai_status.capitalize(),
            theme.COLOR_STATUS_ONLINE if ai_status == "ready" else theme.COLOR_TEXT_SECONDARY)