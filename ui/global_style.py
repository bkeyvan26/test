# -*- coding: utf-8 -*-
"""K1 VMS — Global QSS applied at QApplication level."""
from ui import theme


def _c(name: str, default: str) -> str:
    """Safe color lookup — returns default if missing."""
    return getattr(theme, name, default)


def get_global_qss() -> str:
    bg_panel   = _c("COLOR_BG_PANEL",   "#131820")
    bg_card    = _c("COLOR_BG_CARD",    "#1a2028")
    bg_dark    = _c("COLOR_BG_DARK",    "#0a0d11")
    bg_hover   = _c("COLOR_BG_HOVER",   "#1f2630")
    border     = _c("COLOR_BORDER",     "#232a35")
    text_p     = _c("COLOR_TEXT_PRIMARY", "#e6edf3")
    text_s     = _c("COLOR_TEXT_SECONDARY", "#8b96a5")
    accent     = _c("COLOR_ACCENT",     "#16a085")
    accent_p   = _c("COLOR_ACCENT_PRESSED", "#0e8c73")
    font_fam   = _c("FONT_FAMILY",      "Segoe UI")

    return f"""
        /* ===== Tooltips ===== */
        QToolTip {{
            background-color: #1e242e;
            color: {text_p};
            border: 1px solid #2e3846;
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 11px;
            font-family: "{font_fam}", sans-serif;
        }}

        /* ===== Message Boxes ===== */
        QMessageBox {{
            background-color: {bg_panel};
        }}
        QMessageBox QLabel {{
            color: {text_p};
            font-size: 13px;
            background: transparent;
            padding: 4px;
        }}
        QMessageBox QPushButton {{
            background: {bg_card};
            color: {text_p};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 6px 20px;
            font-size: 12px;
            font-weight: 600;
            min-width: 80px;
            min-height: 26px;
        }}
        QMessageBox QPushButton:hover {{
            background: {accent};
            border-color: {accent};
            color: white;
        }}
        QMessageBox QPushButton:pressed {{
            background: {accent_p};
        }}

        /* ===== Generic dialogs ===== */
        QDialog {{
            background-color: {bg_dark};
        }}
        QDialog QLabel {{
            color: {text_p};
        }}
        QDialog QPushButton {{
            color: {text_p};
        }}

        /* ===== Menus ===== */
        QMenu {{
            background-color: {bg_card};
            color: {text_p};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 6px 20px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background: {accent};
            color: white;
        }}

        /* ===== Calendar popup ===== */
        QCalendarWidget QWidget {{
            background-color: {bg_panel};
            color: {text_p};
        }}
        QCalendarWidget QToolButton {{
            background: transparent;
            color: {text_p};
        }}
        QCalendarWidget QToolButton:hover {{
            background: {bg_hover};
        }}
        QCalendarWidget QAbstractItemView {{
            background: {bg_panel};
            color: {text_p};
            selection-background-color: {accent};
            selection-color: white;
            outline: none;
        }}

        /* ===== Progress dialog ===== */
        QProgressDialog {{
            background-color: {bg_panel};
        }}
        QProgressDialog QLabel {{
            color: {text_p};
        }}
        QProgressBar {{
            background: {bg_card};
            border: 1px solid {border};
            border-radius: 4px;
            text-align: center;
            color: {text_p};
        }}
        QProgressBar::chunk {{
            background: {accent};
            border-radius: 3px;
        }}
    """