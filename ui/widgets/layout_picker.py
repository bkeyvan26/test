# -*- coding: utf-8 -*-
"""
K1 VMS — Professional layout picker (mini-preview buttons)
"""
from PySide6.QtWidgets import QWidget, QPushButton, QHBoxLayout, QFrame, QSizePolicy
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QColor, QPen
from ui import theme
from ui.widgets.live_grid import get_uniform_layouts, get_focus_layouts


class LayoutPreviewButton(QPushButton):
    """Mini-preview button — paints the layout grid."""
    def __init__(self, layout_key, rows, cols, cells, is_focus=False, parent=None):
        super().__init__(parent)
        self.layout_key = layout_key
        self._rows = rows
        self._cols = cols
        self._cells = cells
        self._is_focus = is_focus
        self._selected = False
        self.setFixedSize(38, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setToolTip(f"Layout: {layout_key}")

    def set_selected(self, on):
        self._selected = bool(on)
        self.setChecked(self._selected)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, False)

            # Background
            if self._selected:
                bg = QColor(theme.COLOR_ACCENT)
                border = QColor(theme.COLOR_ACCENT)
                fg = QColor("#ffffff")
            else:
                bg = QColor(theme.COLOR_BG_CARD)
                border = QColor(theme.COLOR_BORDER)
                fg = QColor(theme.COLOR_ACCENT) if self._is_focus else QColor(theme.COLOR_TEXT_SECONDARY)

            p.fillRect(self.rect(), bg)
            p.setPen(QPen(border, 1))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))

            # Draw cell rectangles
            pad = 5
            w = self.width() - pad * 2
            h = self.height() - pad * 2
            cw = w / self._cols
            ch = h / self._rows

            p.setPen(Qt.NoPen)
            p.setBrush(fg)

            for (r, c, rs, cs) in self._cells:
                x = pad + c * cw
                y = pad + r * ch
                # Inset each cell by 0.5px so they don't touch
                p.drawRect(
                    int(x + 0.5), int(y + 0.5),
                    int(cs * cw - 1), int(rs * ch - 1)
                )
        finally:
            p.end()


class LayoutPicker(QWidget):
    """Two-row strip of layout preview buttons."""
    layout_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = "2x2"
        self._buttons = {}
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        from PySide6.QtWidgets import QVBoxLayout
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        v.addWidget(self._make_row(get_uniform_layouts(), is_focus=False))
        v.addWidget(self._make_row(get_focus_layouts(), is_focus=True))

        self.set_current("2x2")

    def _make_row(self, layouts, is_focus):
        row = QFrame()
        row.setStyleSheet("background: transparent;")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)

        for L in layouts:
            btn = LayoutPreviewButton(
                L["key"], L["rows"], L["cols"], L["cells"], is_focus=is_focus
            )
            btn.clicked.connect(lambda checked=False, k=L["key"]: self._on_click(k))
            h.addWidget(btn)
            self._buttons[L["key"]] = btn

        h.addStretch(1)
        return row

    def _on_click(self, key):
        self.set_current(key)
        self.layout_selected.emit(key)

    def set_current(self, key):
        self._current = key
        for k, btn in self._buttons.items():
            btn.set_selected(k == key)

    def current_key(self):
        return self._current