# -*- coding: utf-8 -*-
"""K1 VMS — Entry point"""
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

import config
from ui import theme
from ui.global_style import get_global_qss
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont(theme.FONT_FAMILY, 10))

    # ★ Global QSS — styles tooltips, dialogs, message boxes everywhere
    app.setStyleSheet(get_global_qss())

    print(f"[K1 VMS] v{config.APP_VERSION}")
    print(f"[K1 VMS] Data dir:       {config.DATA_DIR}")
    print(f"[K1 VMS] Recordings dir: {config.RECORDINGS_DIR}")

    # Check MediaMTX presence early
    if not config.MEDIAMTX_EXE.exists():
        print(f"[K1 VMS] ⚠ MediaMTX NOT FOUND at {config.MEDIAMTX_EXE}")
        print(f"[K1 VMS]   Recording will not work until you place mediamtx.exe there.")
    else:
        print(f"[K1 VMS] ✓ MediaMTX found at {config.MEDIAMTX_EXE}")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())