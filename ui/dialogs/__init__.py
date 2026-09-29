# -*- coding: utf-8 -*-
"""K1 VMS — Dialogs package"""
from PySide6.QtWidgets import QMessageBox

from ui import theme


# ============================================================
# Styled message helpers (dark theme)
# ============================================================
def _apply_style(box):
    box.setStyleSheet(f"""
        QMessageBox {{
            background-color: {theme.COLOR_BG_PANEL};
        }}
        QMessageBox QLabel {{
            color: {theme.COLOR_TEXT_PRIMARY};
            font-size: 13px;
            background: transparent;
            padding: 4px;
        }}
        QMessageBox QPushButton {{
            background: {theme.COLOR_BG_CARD};
            color: {theme.COLOR_TEXT_PRIMARY};
            border: 1px solid {theme.COLOR_BORDER};
            border-radius: 6px;
            padding: 6px 20px;
            font-size: 12px;
            font-weight: 600;
            min-width: 80px;
            min-height: 26px;
        }}
        QMessageBox QPushButton:hover {{
            background: {theme.COLOR_ACCENT};
            border-color: {theme.COLOR_ACCENT};
            color: white;
        }}
    """)


def info(parent, text, title="اطلاع"):
    """Show an information message box."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(title)
    box.setText(text)
    _apply_style(box)
    return box.exec()


def warning(parent, text, title="هشدار"):
    """Show a warning message box."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    _apply_style(box)
    return box.exec()


def error(parent, text, title="خطا"):
    """Show a critical error message box."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText(text)
    _apply_style(box)
    return box.exec()


def question(parent, text, title="تأیید"):
    """Show a Yes/No question. Returns True if user clicked Yes."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    _apply_style(box)
    return box.exec() == QMessageBox.Yes


# Export the dialog class for convenience
from ui.dialogs.export_dialog import ExportDialog  # noqa: E402