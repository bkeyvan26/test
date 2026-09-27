# -*- coding: utf-8 -*-
"""K1 VMS — Camera add/edit dialog with auto-detect RTSP/ONVIF"""
import urllib.parse

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QSpinBox,
    QComboBox, QCheckBox, QPushButton, QDialogButtonBox, QLabel, QGroupBox,
    QSlider, QMessageBox, QTabWidget, QWidget, QProgressDialog
)
from PySide6.QtCore import Qt

from core.models import CameraConfig
from core import camera_discovery
import config


# ============================================================
# Dialog-wide dark stylesheet
# ============================================================
DIALOG_STYLE = f"""
QDialog {{
    background-color: {config.COLOR_BG_DARK};
}}
QLabel {{
    color: {config.COLOR_TEXT_PRIMARY};
    background: transparent;
    font-size: 12px;
}}
QLineEdit, QSpinBox, QComboBox {{
    background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c;
    border-radius: 5px;
    padding: 6px 8px;
    font-size: 12px;
    min-height: 20px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {config.COLOR_ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY};
    selection-background-color: {config.COLOR_ACCENT};
    border: 1px solid #2c333c;
    outline: none;
}}
QCheckBox {{
    color: {config.COLOR_TEXT_PRIMARY};
    font-size: 12px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    background: {config.COLOR_BG_CARD};
    border: 1px solid #2c333c;
    border-radius: 3px;
}}
QCheckBox::indicator:checked {{
    background: {config.COLOR_ACCENT};
    border: 1px solid {config.COLOR_ACCENT};
}}
QGroupBox {{
    color: {config.COLOR_TEXT_PRIMARY};
    font-weight: bold;
    font-size: 12px;
    border: 1px solid #2c333c;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {config.COLOR_ACCENT};
}}
QTabWidget::pane {{
    border: 1px solid #2c333c;
    border-radius: 5px;
    background: {config.COLOR_BG_PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_SECONDARY};
    border: 1px solid #2c333c;
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 8px 16px;
    margin-right: 2px;
    font-size: 12px;
    font-weight: bold;
}}
QTabBar::tab:selected {{
    background: {config.COLOR_ACCENT};
    color: white;
    border-color: {config.COLOR_ACCENT};
}}
QTabBar::tab:hover:!selected {{
    background: #2c333c;
    color: {config.COLOR_TEXT_PRIMARY};
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: #2c333c;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {config.COLOR_ACCENT};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: white;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QDialogButtonBox QPushButton {{
    background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c;
    border-radius: 5px;
    padding: 8px 20px;
    font-size: 12px;
    font-weight: bold;
    min-width: 80px;
}}
QDialogButtonBox QPushButton:hover {{
    background: #2c333c;
}}
"""


# ============================================================
# Camera Dialog
# ============================================================
class CameraDialog(QDialog):
    """Add/Edit camera dialog with 5 tabs and auto-discovery."""

    def __init__(self, camera: CameraConfig = None, parent=None):
        super().__init__(parent)
        self.is_new = camera is None
        self.camera = camera if camera else CameraConfig()

        self.setWindowTitle(
            "➕ افزودن دوربین جدید" if self.is_new
            else f"✏ ویرایش: {self.camera.name}"
        )
        self.resize(640, 720)
        self.setStyleSheet(DIALOG_STYLE)

        self._build_ui()

    # ============================================================
    # UI
    # ============================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._tab_connection(), "🔌 اتصال")
        tabs.addTab(self._tab_live(), "📺 لایو")
        tabs.addTab(self._tab_recording(), "🎬 ضبط")
        tabs.addTab(self._tab_motion(), "⚠ حرکت")
        tabs.addTab(self._tab_ai(), "🤖 AI")
        layout.addWidget(tabs, 1)

        # Buttons
        buttons = QDialogButtonBox()
        save_btn = buttons.addButton("💾 ذخیره", QDialogButtonBox.AcceptRole)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {config.COLOR_ACCENT};
                color: white;
                padding: 8px 20px;
                border-radius: 5px;
                font-weight: bold;
                border: none;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background: {config.COLOR_ACCENT_HOVER};
            }}
        """)
        buttons.addButton("انصراف", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------
    # Tab 1: Connection
    # ------------------------------------------------------------
    def _tab_connection(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Name
        self.name_edit = QLineEdit(self.camera.name)
        self.name_edit.setPlaceholderText("مثلاً: درب ورودی")
        form.addRow("نام دوربین:", self.name_edit)

        # Enabled
        self.enabled_chk = QCheckBox("دوربین فعال باشد")
        self.enabled_chk.setChecked(self.camera.enabled)
        form.addRow("", self.enabled_chk)

        # IP
        self.ip_edit = QLineEdit(self.camera.ip)
        self.ip_edit.setPlaceholderText("192.168.1.100")
        form.addRow("آدرس IP:", self.ip_edit)

        # Port
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self.camera.port)
        form.addRow("پورت RTSP:", self.port_spin)

        # User
        self.user_edit = QLineEdit(self.camera.user)
        form.addRow("نام کاربری:", self.user_edit)

        # Password
        self.pass_edit = QLineEdit(self.camera.password)
        self.pass_edit.setEchoMode(QLineEdit.Password)
        form.addRow("رمز عبور:", self.pass_edit)

        # --- Auto-Detect Button (prominent) ---
        self.auto_detect_btn = QPushButton("🔍  تشخیص خودکار مسیر RTSP / ONVIF")
        self.auto_detect_btn.setMinimumHeight(38)
        self.auto_detect_btn.setCursor(Qt.PointingHandCursor)
        self.auto_detect_btn.setStyleSheet(f"""
            QPushButton {{
                background: {config.COLOR_ACCENT};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background: {config.COLOR_ACCENT_HOVER};
            }}
            QPushButton:disabled {{
                background: #2c333c;
                color: {config.COLOR_TEXT_SECONDARY};
            }}
        """)
        self.auto_detect_btn.clicked.connect(self._on_auto_detect)
        form.addRow("", self.auto_detect_btn)

        # --- RTSP Paths ---
        self.path_main_edit = QLineEdit(self.camera.rtsp_path_main)
        self.path_main_edit.setPlaceholderText("/Streaming/Channels/101")
        form.addRow("مسیر اصلی (ضبط):", self.path_main_edit)

        self.path_sub_edit = QLineEdit(self.camera.rtsp_path_sub)
        self.path_sub_edit.setPlaceholderText("/Streaming/Channels/102 (اختیاری)")
        form.addRow("مسیر فرعی (لایو):", self.path_sub_edit)

        # Hint
        hint = QLabel(
            "💡 <b>نکته:</b> فقط IP و رمز را وارد کن و روی «تشخیص خودکار» بزن. "
            "برنامه خودش مسیر RTSP را از طریق ONVIF یا الگوهای رایج برندها پیدا می‌کند."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"""
            color: {config.COLOR_TEXT_SECONDARY};
            font-size: 11px;
            background: {config.COLOR_BG_CARD};
            padding: 10px;
            border-radius: 5px;
            border-left: 3px solid {config.COLOR_ACCENT};
        """)
        form.addRow(hint)

        return w

    # ------------------------------------------------------------
    # Tab 2: Live quality
    # ------------------------------------------------------------
    def _tab_live(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        grp_grid = QGroupBox("🔲 حالت شبکه (چند دوربین همزمان)")
        g1 = QFormLayout(grp_grid)
        g1.setSpacing(8)

        self.live_fps_grid = QSpinBox()
        self.live_fps_grid.setRange(1, 30)
        self.live_fps_grid.setValue(self.camera.live_fps_grid)
        self.live_fps_grid.setSuffix(" fps")
        g1.addRow("نرخ فریم:", self.live_fps_grid)

        self.live_quality_grid = QComboBox()
        for label, val in [("کم (سبک‌ترین)", "low"),
                            ("متوسط", "medium"),
                            ("بالا", "high")]:
            self.live_quality_grid.addItem(label, val)
        idx = self.live_quality_grid.findData(self.camera.live_quality_grid)
        if idx >= 0:
            self.live_quality_grid.setCurrentIndex(idx)
        g1.addRow("کیفیت:", self.live_quality_grid)
        form.addRow(grp_grid)

        grp_single = QGroupBox("🎯 حالت تکی (تمام صفحه)")
        g2 = QFormLayout(grp_single)
        g2.setSpacing(8)

        self.live_fps_single = QSpinBox()
        self.live_fps_single.setRange(1, 60)
        self.live_fps_single.setValue(self.camera.live_fps_single)
        self.live_fps_single.setSuffix(" fps")
        g2.addRow("نرخ فریم:", self.live_fps_single)

        self.live_quality_single = QComboBox()
        for label, val in [("متوسط", "medium"),
                            ("بالا", "high"),
                            ("اورجینال", "original")]:
            self.live_quality_single.addItem(label, val)
        idx = self.live_quality_single.findData(self.camera.live_quality_single)
        if idx >= 0:
            self.live_quality_single.setCurrentIndex(idx)
        g2.addRow("کیفیت:", self.live_quality_single)
        form.addRow(grp_single)

        return w

    # ------------------------------------------------------------
    # Tab 3: Recording
    # ------------------------------------------------------------
    def _tab_recording(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.rec_motion_chk = QCheckBox("ضبط هنگام تشخیص حرکت")
        self.rec_motion_chk.setChecked(self.camera.record_enabled_motion)
        form.addRow("", self.rec_motion_chk)

        self.rec_cont_chk = QCheckBox("ضبط دائمی (۲۴ ساعته)")
        self.rec_cont_chk.setChecked(self.camera.record_enabled_continuous)
        form.addRow("", self.rec_cont_chk)

        grp_q = QGroupBox("🎞 کیفیت ضبط")
        gq = QFormLayout(grp_q)
        gq.setSpacing(8)

        self.rec_stream_combo = QComboBox()
        self.rec_stream_combo.addItem("🎥 استریم اصلی (کیفیت اصلی)", "main")
        self.rec_stream_combo.addItem("📉 استریم فرعی (کم‌حجم)", "sub")
        idx = self.rec_stream_combo.findData(getattr(self.camera, "record_stream", "main"))
        if idx >= 0:
            self.rec_stream_combo.setCurrentIndex(idx)
        gq.addRow("ضبط از:", self.rec_stream_combo)

        self.rec_quality_combo = QComboBox()
        for label, val in [("اورجینال (بدون تغییر)", "original"),
                            ("بالا", "high"),
                            ("متوسط", "medium"),
                            ("پایین (کم‌حجم)", "low")]:
            self.rec_quality_combo.addItem(label, val)
        idx = self.rec_quality_combo.findData(self.camera.record_quality)
        if idx >= 0:
            self.rec_quality_combo.setCurrentIndex(idx)
        gq.addRow("کیفیت:", self.rec_quality_combo)

        form.addRow(grp_q)

        self.rec_seg_spin = QSpinBox()
        self.rec_seg_spin.setRange(0, 120)
        self.rec_seg_spin.setValue(self.camera.record_segment_minutes)
        self.rec_seg_spin.setSuffix(" دقیقه")
        self.rec_seg_spin.setSpecialValueText("استفاده از سراسری")
        form.addRow("طول هر قطعه:", self.rec_seg_spin)

        hint = QLabel(
            "💡 <b>نکته:</b> «اورجینال» یعنی بیت‌استریم دوربین بدون تغییر روی دیسک نوشته می‌شود.\n"
            "هیچ افت کیفیتی ندارد و CPU را هم درگیر نمی‌کند."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"""
            color: {config.COLOR_TEXT_SECONDARY};
            font-size: 11px;
            background: {config.COLOR_BG_CARD};
            padding: 10px;
            border-radius: 5px;
            border-left: 3px solid {config.COLOR_ACCENT};
        """)
        form.addRow(hint)

        self.rec_path_edit = QLineEdit(self.camera.record_path_override)
        self.rec_path_edit.setPlaceholderText("خالی = مسیر سراسری از تنظیمات")
        form.addRow("مسیر اختصاصی:", self.rec_path_edit)

        return w

    # ------------------------------------------------------------
    # Tab 4: Motion
    # ------------------------------------------------------------
    def _tab_motion(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        sens_row = QHBoxLayout()
        self.sens_slider = QSlider(Qt.Horizontal)
        self.sens_slider.setRange(0, config.SENSITIVITY_MAX)
        self.sens_slider.setValue(self.camera.sensitivity)
        self.sens_val_lbl = QLabel(str(self.camera.sensitivity))
        self.sens_val_lbl.setFixedWidth(40)
        self.sens_val_lbl.setStyleSheet(f"""
            color: {config.COLOR_ACCENT};
            font-weight: bold;
            font-size: 13px;
            background: {config.COLOR_BG_CARD};
            border-radius: 4px;
            padding: 3px;
            qproperty-alignment: AlignCenter;
        """)
        self.sens_slider.valueChanged.connect(
            lambda v: self.sens_val_lbl.setText(str(v)))
        sens_row.addWidget(self.sens_slider)
        sens_row.addWidget(self.sens_val_lbl)
        form.addRow("حساسیت (۰=حساس):", sens_row)

        self.area_spin = QSpinBox()
        self.area_spin.setRange(20, 200000)
        self.area_spin.setValue(self.camera.min_area)
        form.addRow("حداقل ناحیه حرکت:", self.area_spin)

        self.detect_every_spin = QSpinBox()
        self.detect_every_spin.setRange(1, 30)
        self.detect_every_spin.setValue(self.camera.detect_every)
        form.addRow("بررسی هر N فریم:", self.detect_every_spin)

        hint = QLabel("💡 تنظیمات ROI در پنجره جداگانه‌ای قابل ویرایش است.")
        hint.setStyleSheet(f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px;")
        form.addRow(hint)

        return w

    # ------------------------------------------------------------
    # Tab 5: AI
    # ------------------------------------------------------------
    def _tab_ai(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.ai_enabled_chk = QCheckBox("AI روی این دوربین فعال باشد")
        self.ai_enabled_chk.setChecked(self.camera.ai_enabled)
        form.addRow("", self.ai_enabled_chk)

        self.ai_filter_person_chk = QCheckBox("فقط رویدادهای دارای «شخص» آلارم بدهند")
        self.ai_filter_person_chk.setChecked(self.camera.ai_filter_person_only)
        form.addRow("", self.ai_filter_person_chk)

        hint = QLabel(
            "🤖 <b>AI (فاز ۶):</b> با فعال کردن این گزینه، برنامه فقط زمانی آلارم می‌دهد "
            "که موجودیت‌های واقعی (شخص، خودرو، حیوان) تشخیص داده شوند.\n"
            "این کار آلارم‌های کاذب (حرکت برگ، باران، سایه) را حذف می‌کند."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"""
            color: {config.COLOR_TEXT_SECONDARY};
            font-size: 11px;
            background: {config.COLOR_BG_CARD};
            padding: 12px;
            border-radius: 5px;
            border-left: 3px solid {config.COLOR_WARNING};
        """)
        form.addRow(hint)

        return w

    # ============================================================
    # Auto-Detect logic
    # ============================================================
    def _on_auto_detect(self):
        ip = self.ip_edit.text().strip()
        user = self.user_edit.text().strip()
        password = self.pass_edit.text()

        if not ip:
            QMessageBox.warning(self, "خطا", "لطفاً ابتدا آدرس IP را وارد کنید.")
            return
        if not user:
            QMessageBox.warning(self, "خطا", "لطفاً نام کاربری را وارد کنید.")
            return

        # Progress dialog
        progress = QProgressDialog(
            "در حال جستجوی دوربین...\nمرحله ۱ از ۲: تلاش ONVIF",
            "لغو", 0, 100, self
        )
        progress.setWindowTitle("تشخیص خودکار")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(5)
        progress.setStyleSheet(DIALOG_STYLE)

        # --- Step 1: ONVIF ---
        try:
            onvif_uris = camera_discovery.get_onvif_stream_uris(ip, user, password)
        except Exception as e:
            print(f"[AutoDetect] ONVIF failed: {e}")
            onvif_uris = None

        if onvif_uris:
            # Use first profile as main
            first = next(iter(onvif_uris.values()))
            main_uri = first["uri"]
            parsed = urllib.parse.urlparse(main_uri)

            # Set main path
            self.path_main_edit.setText(parsed.path or "")

            # Try to find a sub stream (second profile)
            if len(onvif_uris) >= 2:
                second = list(onvif_uris.values())[1]
                sub_parsed = urllib.parse.urlparse(second["uri"])
                self.path_sub_edit.setText(sub_parsed.path or "")

            # Set port from URL
            if parsed.port:
                self.port_spin.setValue(parsed.port)

            # Auto-name if empty
            if not self.name_edit.text().strip():
                self.name_edit.setText(f"ONVIF-{ip}")

            # Save brand/type info if available
            # (not stored in model, but we could log it)

            progress.close()

            profiles_info = "\n".join(
                f"  • {p['name'] or p['token']}" for p in onvif_uris.values()
            )
            QMessageBox.information(
                self, "✅ موفق — ONVIF",
                f"دوربین از طریق ONVIF شناسایی شد.\n\n"
                f"پروفایل‌های موجود:\n{profiles_info}\n\n"
                f"مسیر اصلی به صورت خودکار پر شد."
            )
            return

        # --- Step 2: RTSP path guessing ---
        progress.setLabelText(
            "در حال جستجوی دوربین...\nمرحله ۲ از ۲: تست الگوهای رایج RTSP"
        )
        progress.setValue(50)
        try:
            result = camera_discovery._find_rtsp_path_guessing(ip, user, password)
        except Exception as e:
            print(f"[AutoDetect] RTSP guessing failed: {e}")
            result = None

        progress.close()

        if result:
            self.path_main_edit.setText(result["main"])

            # Try to find a sub path by testing known sub paths
            sub_candidates = self._get_sub_candidates(result["main"])
            for sub_path in sub_candidates:
                from core.camera_discovery import _test_rtsp_url, _get_rtsp_url
                url = _get_rtsp_url(ip, user, password, sub_path)
                if _test_rtsp_url(url):
                    self.path_sub_edit.setText(sub_path)
                    break

            if not self.name_edit.text().strip():
                self.name_edit.setText(f"IPCam-{ip}")

            QMessageBox.information(
                self, "✅ موفق — RTSP",
                f"مسیر RTSP با الگوی رایج پیدا شد:\n\n"
                f"مسیر اصلی: {result['main']}\n"
                f"مسیر فرعی: {self.path_sub_edit.text() or '(پیدا نشد)'}\n\n"
                f"اگر مسیر اشتباه است، دستی اصلاح کن."
            )
            return

        # --- Failed ---
        QMessageBox.warning(
            self, "❌ ناموفق",
            "هیچ مسیر RTSP یا ONVIF پیدا نشد.\n\n"
            "لطفاً:\n"
            "  • صحت IP را بررسی کن\n"
            "  • صحت نام کاربری و رمز را چک کن\n"
            "  • یا مسیر را دستی وارد کن\n\n"
            "مسیرهای رایج:\n"
            "  • Hikvision: /Streaming/Channels/101\n"
            "  • Dahua: /cam/realmonitor?channel=1&subtype=0\n"
            "  • Uniview: /media/video1"
        )

    def _get_sub_candidates(self, main_path: str) -> list:
        """Given a main path, suggest sub path variants."""
        candidates = []
        if "/Streaming/Channels/101" in main_path:
            candidates.append(main_path.replace("101", "102"))
        if "subtype=0" in main_path:
            candidates.append(main_path.replace("subtype=0", "subtype=1"))
        if main_path.endswith("/video1"):
            candidates.append(main_path[:-1] + "2")
        if main_path.endswith("/media/video1"):
            candidates.append(main_path[:-1] + "2")
        if main_path.endswith("Main"):
            candidates.append(main_path[:-4] + "Sub")
        if main_path.endswith("/main"):
            candidates.append(main_path[:-4] + "sub")
        if main_path.endswith("/profile1/media.smp"):
            candidates.append(main_path.replace("profile1", "profile2"))
        return candidates

    # ============================================================
    # Save
    # ============================================================
    def _on_save(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "خطا", "نام دوربین را وارد کنید.")
            return
        if not self.ip_edit.text().strip():
            QMessageBox.warning(self, "خطا", "آدرس IP را وارد کنید.")
            return

        c = self.camera
        c.name = self.name_edit.text().strip()
        c.enabled = self.enabled_chk.isChecked()
        c.ip = self.ip_edit.text().strip()
        c.port = self.port_spin.value()
        c.user = self.user_edit.text().strip()
        c.password = self.pass_edit.text()
        c.rtsp_path_main = self.path_main_edit.text().strip()
        c.rtsp_path_sub = self.path_sub_edit.text().strip()

        c.live_fps_grid = self.live_fps_grid.value()
        c.live_quality_grid = self.live_quality_grid.currentData()
        c.live_fps_single = self.live_fps_single.value()
        c.live_quality_single = self.live_quality_single.currentData()

        c.record_enabled_motion = self.rec_motion_chk.isChecked()
        c.record_enabled_continuous = self.rec_cont_chk.isChecked()
        if hasattr(self, "record_stream_combo"):
            c.record_stream = self.record_stream_combo.currentData() or "main"
        c.record_quality = self.rec_quality_combo.currentData()
        c.record_segment_minutes = self.rec_seg_spin.value()
        c.record_path_override = self.rec_path_edit.text().strip()

        c.sensitivity = self.sens_slider.value()
        c.min_area = self.area_spin.value()
        c.detect_every = self.detect_every_spin.value()

        c.ai_enabled = self.ai_enabled_chk.isChecked()
        c.ai_filter_person_only = self.ai_filter_person_chk.isChecked()

        self.accept()

    def get_camera(self) -> CameraConfig:
        return self.camera