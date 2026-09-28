# -*- coding: utf-8 -*-
"""K1 VMS — Collapsible Context Panel (collapses to a thin visible strip)"""
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget
)
from PySide6.QtCore import Qt, Signal
from ui import theme
from ui.icons import make_icon


COLLAPSED_WIDTH = 28


class ContextPanel(QFrame):
    collapsed_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ContextPanel")
        self.setFixedWidth(theme.PANEL_WIDTH)
        self.setStyleSheet(f"""
            QFrame#ContextPanel {{
                background: {theme.COLOR_BG_PANEL};
                border-right: 1px solid {theme.COLOR_BORDER};
            }}
        """)

        self._collapsed = False
        self._contents = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ===== Header (visible when expanded) =====
        self.header = QFrame()
        self.header.setFixedHeight(40)
        self.header.setStyleSheet(f"""
            background: {theme.COLOR_BG_CARD};
            border-bottom: 1px solid {theme.COLOR_BORDER};
        """)
        hh = QHBoxLayout(self.header)
        hh.setContentsMargins(12, 0, 6, 0)

        self.title_lbl = QLabel("PANEL")
        self.title_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; background: transparent; "
            f"font-size: 10px; font-weight: 700; letter-spacing: 1.5px;"
        )
        hh.addWidget(self.title_lbl)
        hh.addStretch(1)

        self.collapse_btn = QPushButton()
        self.collapse_btn.setIcon(make_icon("chevron-left", theme.COLOR_TEXT_SECONDARY, 14))
        self.collapse_btn.setFixedSize(26, 26)
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.setToolTip("جمع کردن پنل")
        self.collapse_btn.clicked.connect(self.toggle_collapsed)
        self.collapse_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; border-radius: 4px;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_BG_HOVER};
            }}
        """)
        hh.addWidget(self.collapse_btn)
        v.addWidget(self.header)

        # ===== Content (visible when expanded) =====
        self.content_area = QWidget()
        self._content_layout = QVBoxLayout(self.content_area)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        v.addWidget(self.content_area, 1)

        # ===== Collapsed strip (visible when collapsed) =====
        self.collapsed_strip = QWidget()
        cs = QVBoxLayout(self.collapsed_strip)
        cs.setContentsMargins(2, 12, 2, 12)
        cs.setSpacing(0)

        self.expand_btn = QPushButton()
        self.expand_btn.setIcon(make_icon("chevron-right", theme.COLOR_TEXT_PRIMARY, 14))
        self.expand_btn.setFixedSize(24, 40)
        self.expand_btn.setCursor(Qt.PointingHandCursor)
        self.expand_btn.setToolTip("باز کردن پنل")
        self.expand_btn.clicked.connect(self.toggle_collapsed)
        self.expand_btn.setStyleSheet(f"""
            QPushButton {{
                background: {theme.COLOR_BG_CARD};
                border: 1px solid #2e3846;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_ACCENT};
                border-color: {theme.COLOR_ACCENT};
            }}
        """)
        cs.addWidget(self.expand_btn, 0, Qt.AlignTop | Qt.AlignHCenter)
        cs.addStretch(1)
        v.addWidget(self.collapsed_strip)

        self.collapsed_strip.hide()

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed):
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        if collapsed:
            self.setFixedWidth(COLLAPSED_WIDTH)
            self.header.hide()
            self.content_area.hide()
            self.collapsed_strip.show()
        else:
            self.setFixedWidth(theme.PANEL_WIDTH)
            self.header.show()
            self.content_area.show()
            self.collapsed_strip.hide()
        self.collapsed_changed.emit(collapsed)

    # ---- Content registration (unchanged API) ----
    def register_content(self, key, widget, title):
        widget.setParent(self.content_area)
        widget.hide()
        self._contents[key] = (widget, title)
        self._content_layout.addWidget(widget)

    def show_content(self, key):
        if key not in self._contents:
            return
        for w, _ in self._contents.values():
            w.hide()
        w, title = self._contents[key]
        w.show()
        self.title_lbl.setText(title.upper())