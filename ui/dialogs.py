# -*- coding: utf-8 -*-
"""K1 VMS — Styled dialogs (dark theme compatible)"""
from PySide6.QtWidgets import QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt
from ui import theme
from ui.icons import make_icon


def styled_message(parent, title, text, icon="info", buttons=None):
    """Show a message box with the dark theme."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    
    # Icon
    if icon == "info":
        box.setIcon(QMessageBox.Information)
    elif icon == "warning":
        box.setIcon(QMessageBox.Warning)
    elif icon == "error":
        box.setIcon(QMessageBox.Critical)
    elif icon == "question":
        box.setIcon(QMessageBox.Question)
    
    # Styling
    box.setStyleSheet(f"""
        QMessageBox {{
            background-color: {theme.COLOR_BG_PANEL};
            color: {theme.COLOR_TEXT_PRIMARY};
        }}
        QMessageBox QLabel {{
            color: {theme.COLOR_TEXT_PRIMARY};
            font-size: 13px;
            background: transparent;
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
        }}
        QMessageBox QPushButton:hover {{
            background: {theme.COLOR_ACCENT};
            border-color: {theme.COLOR_ACCENT};
            color: white;
        }}
    """)
    
    return box.exec()


def info(parent, text, title="Info"):
    return styled_message(parent, title, text, "info")


def warning(parent, text, title="Warning"):
    return styled_message(parent, title, text, "warning")


def error(parent, text, title="Error"):
    return styled_message(parent, title, text, "error")


def question(parent, text, title="Confirm"):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Question)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    box.setStyleSheet(f"""
        QMessageBox {{
            background-color: {theme.COLOR_BG_PANEL};
            color: {theme.COLOR_TEXT_PRIMARY};
        }}
        QMessageBox QLabel {{
            color: {theme.COLOR_TEXT_PRIMARY};
            font-size: 13px;
            background: transparent;
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
        }}
        QMessageBox QPushButton:hover {{
            background: {theme.COLOR_ACCENT};
            border-color: {theme.COLOR_ACCENT};
            color: white;
        }}
    """)
    return box.exec() == QMessageBox.Yes