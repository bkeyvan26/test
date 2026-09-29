# -*- coding: utf-8 -*-
"""K1 VMS — Command Bar (Ctrl+K)"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from ui import theme
from ui.icons import make_icon


class CommandBar(QDialog):
    command_selected = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setFixedSize(560, 400)
        self.setStyleSheet(f"""
            QDialog {{
                background: {theme.COLOR_BG_PANEL};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 10px;
            }}
        """)
        
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        
        # Input area
        wrap = QFrame()
        wrap.setFixedHeight(50)
        wrap.setStyleSheet(f"""
            background: {theme.COLOR_BG_CARD};
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            border-bottom: 1px solid {theme.COLOR_BORDER};
        """)
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(14, 8, 14, 8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command or search…")
        self.input.setStyleSheet(f"""
            QLineEdit {{
                background: transparent; color: {theme.COLOR_TEXT_PRIMARY};
                border: none; font-size: 14px; padding: 4px;
            }}
        """)
        self.input.textChanged.connect(self._filter)
        self.input.returnPressed.connect(self._exec_current)
        wl.addWidget(self.input)
        v.addWidget(wrap)
        
        self.list = QListWidget()
        self.list.setStyleSheet(f"""
            QListWidget {{
                background: {theme.COLOR_BG_PANEL};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: none; padding: 6px; font-size: 13px;
            }}
            QListWidget::item {{ padding: 10px 14px; border-radius: 6px; }}
            QListWidget::item:hover {{ background: {theme.COLOR_BG_HOVER}; }}
            QListWidget::item:selected {{
                background: {theme.COLOR_ACCENT}; color: white;
            }}
        """)
        self.list.itemActivated.connect(self._exec_item)
        v.addWidget(self.list, 1)
        
        self._all = [
            ("Dashboard",         "dashboard", "home"),
            ("Live View",         "live",      "video"),
            ("Cameras",           "cameras",   "grid"),
            ("Playback",          "playback",  "play-circle"),
            ("Alarms",            "alarms",    "bell"),
            ("AI Analytics",      "ai",        "cpu"),
            ("Search",            "search",    "search"),
            ("Map",               "map",       "map"),
            ("Reports",           "reports",   "bar-chart"),
            ("Settings",          "settings",  "sliders"),
            ("Users",             "users",     "users"),
            ("Exit Application",  "exit",      "log-out"),
        ]
        self._populate(self._all)
    
    def _populate(self, items):
        self.list.clear()
        for label, key, icon in items:
            it = QListWidgetItem(f"  {label}")
            it.setIcon(make_icon(icon, theme.COLOR_TEXT_PRIMARY, 16))
            it.setData(Qt.UserRole, key)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
    
    def _filter(self, text):
        text = text.strip().lower()
        if not text:
            self._populate(self._all); return
        self._populate([c for c in self._all if text in c[0].lower()])
    
    def _exec_current(self):
        it = self.list.currentItem()
        if it: self._exec_item(it)
    
    def _exec_item(self, item):
        key = item.data(Qt.UserRole)
        if key:
            self.command_selected.emit(key)
            self.accept()
    
    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key_Escape:
            self.reject(); return
        if e.key() == Qt.Key_Down:
            self.list.setCurrentRow(min(self.list.currentRow() + 1, self.list.count() - 1)); return
        if e.key() == Qt.Key_Up:
            self.list.setCurrentRow(max(self.list.currentRow() - 1, 0)); return
        super().keyPressEvent(e)