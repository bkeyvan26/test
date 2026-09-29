# -*- coding: utf-8 -*-
"""K1 VMS — Page base classes"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt
from ui import theme


class BasePage(QWidget):
    PAGE_KEY = "page"
    PAGE_TITLE = "Page"
    PANEL_TITLE = "Panel"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {theme.COLOR_BG_APP};")

    def build_panel_content(self):
        """Override to return a widget for the context panel."""
        return None

    def on_show(self): pass
    def on_hide(self): pass


class PlaceholderPage(BasePage):
    """Simple placeholder for upcoming features."""
    def __init__(self, key, title, icon_name, subtitle="Coming soon", parent=None):
        super().__init__(parent)
        self.PAGE_KEY = key
        self.PAGE_TITLE = title
        self.PANEL_TITLE = title

        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(16)

        from ui.icons import make_icon
        ic = QLabel()
        ic.setPixmap(make_icon(icon_name, theme.COLOR_TEXT_MUTED, 56).pixmap(56, 56))
        ic.setAlignment(Qt.AlignCenter)
        v.addWidget(ic)

        t = QLabel(title)
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet(
            f"color: {theme.COLOR_TEXT_PRIMARY}; font-size: 22px; font-weight: 700;"
        )
        v.addWidget(t)

        s = QLabel(subtitle)
        s.setAlignment(Qt.AlignCenter)
        s.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 12px;"
        )
        v.addWidget(s)