 # -*- coding: utf-8 -*-
"""K1 VMS — Main Window (Phase 2: NVR integrated)"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QFrame, QLabel, QMessageBox, QApplication, QLineEdit, QTextEdit,
    QPlainTextEdit, QSpinBox, QDoubleSpinBox
)
from PySide6.QtCore import Qt, QTimer, QObject, QEvent
from PySide6.QtGui import QKeySequence, QShortcut
import datetime

from ui import theme
from ui.header import GlobalHeader
from ui.icon_rail import IconRail
from ui.context_panel import ContextPanel
from ui.command_bar import CommandBar
from ui.pages.dashboard_page import DashboardPage
from ui.pages.cameras_page import CamerasPage
from ui.pages.playback_page import PlaybackPage
from ui.pages.placeholders import (
    LivePage, AlarmsPage, AIPage, SearchPage,
    MapPage, ReportsPage, SettingsPage, UsersPage
)
from core.camera_manager import CameraManager
from core.nvr_engine import NVREngine
from core import settings_manager


class KeyboardFilter(QObject):
    NAV_KEYS = {
        Qt.Key_D: "dashboard", Qt.Key_L: "live",   Qt.Key_C: "cameras",
        Qt.Key_P: "playback",  Qt.Key_A: "alarms", Qt.Key_I: "ai",
        Qt.Key_S: "search",    Qt.Key_M: "map",    Qt.Key_R: "reports",
    }
    def __init__(self, window):
        super().__init__(window); self.window = window
    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit,
                                QSpinBox, QDoubleSpinBox)):
                return False
            if event.modifiers() == Qt.NoModifier:
                page = self.NAV_KEYS.get(event.key())
                if page:
                    self.window._on_page_changed(page); return True
        return False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("K1 VMS")
        self.setMinimumSize(1280, 720)
        self.resize(1440, 860)
        self.setStyleSheet(f"background: {theme.COLOR_BG_APP};")

        self.cam_manager = CameraManager()
        self.settings = settings_manager.load_settings()
        self.nvr = NVREngine()
        self._pages = {}

        self._setup_ui()
        self._setup_shortcuts()
        self._start_nvr()
        self._refresh_header()

        # Polling timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(2000)

    # ============================================================
    def _setup_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        self.header = GlobalHeader()
        self.header.search_requested.connect(self._open_command_bar)
        self.header.user_menu_requested.connect(self._show_user_menu)
        root.addWidget(self.header)

        body = QHBoxLayout(); body.setSpacing(0); body.setContentsMargins(0, 0, 0, 0)

        self.rail = IconRail()
        self.rail.page_changed.connect(self._on_page_changed)
        body.addWidget(self.rail)

        self.panel = ContextPanel()
        body.addWidget(self.panel)

        self.workspace = QStackedWidget()
        self.workspace.setStyleSheet(f"background: {theme.COLOR_BG_APP};")
        body.addWidget(self.workspace, 1)
        root.addLayout(body, 1)

        status = QFrame(); status.setFixedHeight(24)
        status.setStyleSheet(f"""
            background: {theme.COLOR_BG_SHELL};
            border-top: 1px solid {theme.COLOR_BORDER};
        """)
        sb = QHBoxLayout(status); sb.setContentsMargins(12, 0, 12, 0)
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;")
        sb.addWidget(self.status_lbl); sb.addStretch(1)
        self.clock_lbl = QLabel("")
        self.clock_lbl.setStyleSheet(
            f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 11px;")
        sb.addWidget(self.clock_lbl)
        root.addWidget(status)
        self.setCentralWidget(central)

        # Register pages
        pages = [
            DashboardPage(),
            LivePage(),
            CamerasPage(nvr_engine=self.nvr),
            PlaybackPage(nvr_engine=self.nvr),
            AlarmsPage(), AIPage(), SearchPage(), MapPage(),
            ReportsPage(), SettingsPage(), UsersPage(),
        ]
        for page in pages:
            self._pages[page.PAGE_KEY] = page
            self.workspace.addWidget(page)

        for key, page in self._pages.items():
            content = page.build_panel_content()
            if content is None:
                content = self._empty_panel(page.PANEL_TITLE)
            self.panel.register_content(key, content, page.PANEL_TITLE)

        self._on_page_changed("dashboard")

    def _empty_panel(self, title):
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(16, 16, 16, 16)
        l = QLabel(f"No options for {title}")
        l.setStyleSheet(f"color: {theme.COLOR_TEXT_MUTED}; font-size: 12px;")
        l.setAlignment(Qt.AlignCenter)
        v.addWidget(l); v.addStretch(1)
        return w

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+K"), self, self._open_command_bar)
        QShortcut(QKeySequence("F11"), self, self._toggle_fullscreen)
        self._kb = KeyboardFilter(self)
        QApplication.instance().installEventFilter(self._kb)

    # ============================================================
    # NVR lifecycle
    # ============================================================
    def _start_nvr(self):
        if not self.settings.mediamtx_enabled:
            print("[NVR] disabled in settings")
            return
        cameras = self.cam_manager.all()
        # Only include enabled cameras with connection info
        try:
            ok = self.nvr.start(cameras, self.settings)
            if ok:
                print("[NVR] MediaMTX started")
            else:
                print(f"[NVR] start failed: {self.nvr.get_last_error()}")
        except Exception as e:
            print(f"[NVR] exception: {e}")

    # ============================================================
    def _on_tick(self):
        """Periodic: NVR status, header, clock."""
        # Poll NVR
        try:
            self.nvr.poll_status()
        except Exception as e:
            print(f"[NVR poll] {e}")
        self._refresh_header()

    def _refresh_header(self):
        cams = self.cam_manager.all()
        total = len(cams)
        enabled = sum(1 for c in cams if c.enabled)

        self.header.set_online(enabled, total)

        rec_count = 0
        err_count = 0
        if self.nvr.is_started():
            rec_count = self.nvr.get_recording_count()
            err_count = self.nvr.get_error_count()
        self.header.set_recording(rec_count, enabled)

        self.header.set_alarms(err_count)

        ai_status = "disabled"
        if self.settings.ai_enabled:
            ai_status = "ready"
        self.header.set_ai_status(ai_status)

        self.clock_lbl.setText(
            datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

        # Dashboard update
        if "dashboard" in self._pages:
            self._pages["dashboard"].update_stats(
                enabled, total, err_count, rec_count, ai_status)

    # ============================================================
    def _on_page_changed(self, key):
        if key == "exit":
            self.close(); return
        if key not in self._pages: return
        cur = self.workspace.currentWidget()
        if hasattr(cur, "on_hide"): cur.on_hide()
        page = self._pages[key]
        self.workspace.setCurrentWidget(page)
        self.rail.set_active_page(key)
        self.panel.show_content(key)
        self.status_lbl.setText(page.PAGE_TITLE)
        # Only call on_show if we're actually entering this page (not re-showing)
        if hasattr(page, "on_show"):
            page.on_show()

    # ============================================================
    def _open_command_bar(self):
        cb = CommandBar(self)
        cb.command_selected.connect(self._on_page_changed)
        g = self.geometry()
        cb.move(g.center().x() - cb.width() // 2,
                g.center().y() - cb.height() // 2)
        cb.exec()

    def _show_user_menu(self):
        QMessageBox.information(self, "User",
            "User management coming in Phase 7.")

    def _toggle_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    # ============================================================
    # IMPORTANT: Do NOT stop MediaMTX on window close.
    # Recording must survive UI close (per Phase 2 spec).
    # ============================================================
    def closeEvent(self, event):
        # Intentionally do NOT call self.nvr.stop().
        # MediaMTX continues running as a detached process.
        try:
            self._timer.stop()
        except Exception: pass
        super().closeEvent(event)
