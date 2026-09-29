# -*- coding: utf-8 -*-
"""K1 VMS — Central theme (all colors, sizes, fonts)"""
import sys

# === Backgrounds ===
COLOR_BG_APP      = "#0a0d11"
COLOR_BG_DARK     = "#0a0d11"
COLOR_BG_SHELL    = "#0f1318"
COLOR_BG_RAIL     = "#0c1015"
COLOR_BG_HEADER   = "#0f1318"
COLOR_BG_PANEL    = "#131820"
COLOR_BG_CARD     = "#1a2028"
COLOR_BG_HOVER    = "#1f2630"
COLOR_BG_ACTIVE   = "#252d3a"
COLOR_BORDER      = "#232a35"

# === Text ===
COLOR_TEXT_PRIMARY   = "#e6edf3"
COLOR_TEXT_SECONDARY = "#8b96a5"
COLOR_TEXT_MUTED     = "#5a6674"
COLOR_TEXT_INVERSE   = "#0a0d11"

# === Accent ===
COLOR_ACCENT         = "#16a085"
COLOR_ACCENT_HOVER   = "#1abc9c"
COLOR_ACCENT_PRESSED = "#0e8c73"

# === Status ===
COLOR_STATUS_ONLINE  = "#2ecc71"
COLOR_STATUS_WARNING = "#f39c12"
COLOR_STATUS_ERROR   = "#e74c3c"
COLOR_STATUS_OFFLINE = "#5a6674"

# === Sizes ===
RAIL_WIDTH    = 64
PANEL_WIDTH   = 250
HEADER_HEIGHT = 48

# === Font ===
FONT_FAMILY = "Segoe UI" if sys.platform.startswith("win") else "Sans"