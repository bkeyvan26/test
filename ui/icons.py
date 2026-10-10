# -*- coding: utf-8 -*-
"""K1 VMS — SVG icons (Feather-style, professional)"""
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer


ICON_PATHS = {
    # ---------- Navigation ----------
    "home": [
        "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
        "M9 22V12h6v10",
    ],
    "video": [
        "M23 7l-7 5 7 5V7z",
        "M14 5H3a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2z",
    ],
    "grid": [
        "M3 3h7v7H3z",
        "M14 3h7v7h-7z",
        "M14 14h7v7h-7z",
        "M3 14h7v7H3z",
    ],
    "play-circle": [
        "M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z",
        "M10 8l6 4-6 4V8z",
    ],
    "pause": [
        "M6 4h4v16H6z",
        "M14 4h4v16h-4z",
    ],
    "bell": [
        "M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9",
        "M13.73 21a2 2 0 0 1-3.46 0",
    ],
    "cpu": [
        "M4 4h16v16H4z",
        "M9 9h6v6H9z",
        "M9 1v3", "M15 1v3", "M9 20v3", "M15 20v3",
        "M20 9h3", "M20 14h3", "M1 9h3", "M1 14h3",
    ],
    "search": [
        "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z",
        "M21 21l-4.35-4.35",
    ],
    "map": [
        "M1 6v16l7-4 8 4 7-4V2l-7 4-8-4-7 4z",
        "M8 2v16",
        "M16 6v16",
    ],
    "bar-chart": [
        "M18 20V10",
        "M12 20V4",
        "M6 20v-6",
    ],
    "sliders": [
        "M4 21v-7", "M4 10V3",
        "M12 21v-9", "M12 8V3",
        "M20 21v-5", "M20 12V3",
        "M1 14h6",
        "M9 8h6",
        "M17 16h6",
    ],
    "users": [
        "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2",
        "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
        "M23 21v-2a4 4 0 0 0-3-3.87",
        "M16 3.13a4 4 0 0 1 0 7.75",
    ],
    "log-out": [
        "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4",
        "M16 17l5-5-5-5",
        "M21 12H9",
    ],

    # ---------- Chevrons / Arrows ----------
    "chevron-left": [
        "M15 18l-6-6 6-6",
    ],
    "chevron-right": [
        "M9 18l6-6-6-6",
    ],
    "chevron-down": [
        "M6 9l6 6 6-6",
    ],
    "chevron-up": [
        "M18 15l-6-6-6 6",
    ],
    "arrow-left": [
        "M19 12H5",
        "M12 19l-7-7 7-7",
    ],
    "arrow-right": [
        "M5 12h14",
        "M12 5l7 7-7 7",
    ],
    "skip-back": [
        "M19 20L9 12l10-8v16z",
        "M5 19V5",
    ],
    "skip-forward": [
        "M5 4l10 8-10 8V4z",
        "M19 5v14",
    ],

    # ---------- Actions ----------
    "x": [
        "M18 6L6 18",
        "M6 6l12 12",
    ],
    "check": [
        "M20 6L9 17l-5-5",
    ],
    "plus": [
        "M12 5v14",
        "M5 12h14",
    ],
    "minus": [
        "M5 12h14",
    ],
    "edit": [
        "M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7",
        "M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z",
    ],
    "trash": [
        "M3 6h18",
        "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6",
        "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",
    ],
    "save": [
        "M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z",
        "M17 21v-8H7v8",
        "M7 3v5h8",
    ],
    "camera": [
        "M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z",
        "M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    ],
    "maximize": [
        "M8 3H5a2 2 0 0 0-2 2v3",
        "M21 8V5a2 2 0 0 0-2-2h-3",
        "M3 16v3a2 2 0 0 0 2 2h3",
        "M16 21h3a2 2 0 0 0 2-2v-3",
    ],
    "refresh": [
        "M23 4v6h-6",
        "M1 20v-6h6",
        "M3.51 9a9 9 0 0 1 14.85-3.36L23 10",
        "M1 14l4.64 4.36A9 9 0 0 0 20.49 15",
    ],
    "wifi": [
        "M5 12.55a11 11 0 0 1 14.08 0",
        "M1.42 9a16 16 0 0 1 21.16 0",
        "M8.53 16.11a6 6 0 0 1 6.95 0",
        "M12 20h.01",
    ],
    "alert-circle": [
        "M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z",
        "M12 8v4",
        "M12 16h.01",
    ],
    "info": [
        "M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z",
        "M12 16v-4",
        "M12 8h.01",
    ],
}


def _build_svg(paths, color, size):
    """Build an SVG string from a list of path 'd' attributes."""
    paths_svg = "".join(f'<path d="{p}"/>' for p in paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'{paths_svg}</svg>'
    )


def make_icon(name, color="#e6edf3", size=22):
    """Return a QIcon for the given icon name, color and size."""
    paths = ICON_PATHS.get(name)
    if not paths:
        # fallback: empty icon (invisible)
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        return QIcon(pix)

    svg = _build_svg(paths, color, size)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(p)
    p.end()
    return QIcon(pix)