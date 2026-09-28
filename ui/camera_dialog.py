# -*- coding: utf-8 -*-
"""K1 VMS — Camera dialog (Phase B: unified 4-role panel with FPS)"""
import urllib.parse
from typing import List

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QSpinBox,
    QComboBox, QCheckBox, QPushButton, QDialogButtonBox, QLabel, QGroupBox,
    QSlider, QMessageBox, QTabWidget, QWidget, QProgressDialog,
    QListWidget, QListWidgetItem, QAbstractItemView
)
from PySide6.QtCore import Qt, QTimer

from core.models import (
    CameraConfig, StreamProfile,
    ROLE_RECORDING, ROLE_MOTION, ROLE_LIVE_VIEW, ROLE_GRID, ALL_ROLES
)
from core import camera_discovery
import config


DIALOG_STYLE = f"""
QDialog {{ background-color: {config.COLOR_BG_DARK}; }}
QLabel {{ color: {config.COLOR_TEXT_PRIMARY}; background: transparent;
         font-size: 12px; }}
QLineEdit, QSpinBox, QComboBox {{
    background: {config.COLOR_BG_CARD}; color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c; border-radius: 5px;
    padding: 6px 8px; font-size: 12px; min-height: 20px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {config.COLOR_ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {config.COLOR_BG_CARD}; color: {config.COLOR_TEXT_PRIMARY};
    selection-background-color: {config.COLOR_ACCENT};
    border: 1px solid #2c333c; outline: none; }}
QCheckBox {{ color: {config.COLOR_TEXT_PRIMARY}; font-size: 12px;
             spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px;
    background: {config.COLOR_BG_CARD}; border: 1px solid #2c333c;
    border-radius: 3px; }}
QCheckBox::indicator:checked {{ background: {config.COLOR_ACCENT};
    border: 1px solid {config.COLOR_ACCENT}; }}
QGroupBox {{ color: {config.COLOR_TEXT_PRIMARY}; font-weight: bold;
    font-size: 12px; border: 1px solid #2c333c; border-radius: 6px;
    margin-top: 12px; padding-top: 14px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
    color: {config.COLOR_ACCENT}; }}
QTabWidget::pane {{ border: 1px solid #2c333c; border-radius: 5px;
    background: {config.COLOR_BG_PANEL}; top: -1px; }}
QTabBar::tab {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_SECONDARY};
    border: 1px solid #2c333c; border-bottom: none;
    border-top-left-radius: 5px; border-top-right-radius: 5px;
    padding: 8px 16px; margin-right: 2px; font-size: 12px; font-weight: bold; }}
QTabBar::tab:selected {{ background: {config.COLOR_ACCENT}; color: white;
    border-color: {config.COLOR_ACCENT}; }}
QTabBar::tab:hover:!selected {{ background: #2c333c;
    color: {config.COLOR_TEXT_PRIMARY}; }}
QSlider::groove:horizontal {{ height: 6px; background: #2c333c;
    border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {config.COLOR_ACCENT};
    border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px; }}
QDialogButtonBox QPushButton {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY}; border: 1px solid #2c333c;
    border-radius: 5px; padding: 8px 20px; font-size: 12px;
    font-weight: bold; min-width: 80px; }}
QDialogButtonBox QPushButton:hover {{ background: #2c333c; }}
QListWidget {{ background: {config.COLOR_BG_CARD};
    color: {config.COLOR_TEXT_PRIMARY};
    border: 1px solid #2c333c; border-radius: 5px;
    padding: 4px; font-size: 12px; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 4px; }}
QListWidget::item:hover {{ background: #2c333c; }}
QListWidget::item:selected {{ background: {config.COLOR_ACCENT};
    color: white; }}
"""


# ============================================================
# Camera Dialog
# ============================================================
class CameraDialog(QDialog):
    def __init__(self, camera: CameraConfig = None, parent=None):
        super().__init__(parent)
        self.is_new = camera is None
        self.camera = camera if camera else CameraConfig()
        try:
            self.camera.migrate_legacy_to_profiles()
            self.camera._ensure_profile_defaults()
        except Exception:
            pass

        self.setWindowTitle(
            "➕ افزودن دوربین" if self.is_new
            else f"✏ ویرایش: {self.camera.name}"
        )
        self.resize(820, 880)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()

    # ============================================================
    # UI Build
    # ============================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._tab_connection(), "🔌 اتصال")
        tabs.addTab(self._tab_streams(), "📡 استریم و لایو")
        tabs.addTab(self._tab_recording(), "🎬 ضبط")
        tabs.addTab(self._tab_motion(), "⚠ حرکت")
        tabs.addTab(self._tab_ai(), "🤖 AI")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox()
        save_btn = buttons.addButton("💾 ذخیره", QDialogButtonBox.AcceptRole)
        save_btn.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_ACCENT}; color: white;
                padding: 8px 20px; border-radius: 5px; font-weight: bold;
                border: none; min-width: 80px; }}
            QPushButton:hover {{ background: {config.COLOR_ACCENT_HOVER}; }}
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

        self.name_edit = QLineEdit(self.camera.name)
        self.name_edit.setPlaceholderText("مثلاً: درب ورودی")
        form.addRow("نام دوربین:", self.name_edit)

        self.enabled_chk = QCheckBox("دوربین فعال باشد")
        self.enabled_chk.setChecked(self.camera.enabled)
        form.addRow("", self.enabled_chk)

        self.ip_edit = QLineEdit(self.camera.ip)
        self.ip_edit.setPlaceholderText("192.168.1.100")
        form.addRow("آدرس IP:", self.ip_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self.camera.port)
        form.addRow("پورت RTSP:", self.port_spin)

        self.user_edit = QLineEdit(self.camera.user)
        form.addRow("نام کاربری:", self.user_edit)

        self.pass_edit = QLineEdit(self.camera.password)
        self.pass_edit.setEchoMode(QLineEdit.Password)
        form.addRow("رمز عبور:", self.pass_edit)

        hint = QLabel(
            "💡 بعد از ورود اطلاعات، به تب <b>📡 استریم و لایو</b> برو "
            "و روی «تشخیص خودکار استریم‌ها» کلیک کن."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"""
            color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px;
            background: {config.COLOR_BG_CARD}; padding: 10px;
            border-radius: 5px; border-left: 3px solid {config.COLOR_ACCENT};""")
        form.addRow(hint)

        return w

    # ------------------------------------------------------------
    # Tab 2: Streams (Unified 4-role panel with FPS per role)
    # ------------------------------------------------------------
    def _tab_streams(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        # ---- Auto-detect ----
        self.auto_detect_btn = QPushButton(
            "🔍  تشخیص خودکار استریم‌ها (ONVIF / RTSP)"
        )
        self.auto_detect_btn.setMinimumHeight(40)
        self.auto_detect_btn.setCursor(Qt.PointingHandCursor)
        self.auto_detect_btn.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_ACCENT}; color: white;
                border: none; border-radius: 6px; font-size: 13px;
                font-weight: bold; padding: 8px 16px; }}
            QPushButton:hover {{ background: {config.COLOR_ACCENT_HOVER}; }}
        """)
        self.auto_detect_btn.clicked.connect(self._on_auto_detect)
        v.addWidget(self.auto_detect_btn)

        # ---- Profile list ----
        info = QLabel("📡 پروفایل‌های استریم دوربین")
        info.setWordWrap(True)
        info.setStyleSheet(f"""
            color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px;
            background: {config.COLOR_BG_CARD}; padding: 8px 10px;
            border-radius: 5px; border-left: 3px solid {config.COLOR_ACCENT};""")
        v.addWidget(info)

        self.profiles_list = QListWidget()
        self.profiles_list.setMaximumHeight(100)
        self.profiles_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.profiles_list.itemDoubleClicked.connect(
            lambda _: self._edit_profile()
        )
        v.addWidget(self.profiles_list)

        row = QHBoxLayout()
        add_btn = self._small_btn("➕ افزودن دستی")
        add_btn.clicked.connect(self._add_manual_profile)
        row.addWidget(add_btn)
        edit_btn = self._small_btn("✏ ویرایش")
        edit_btn.clicked.connect(self._edit_profile)
        row.addWidget(edit_btn)
        del_btn = self._small_btn("🗑 حذف")
        del_btn.clicked.connect(self._delete_profile)
        row.addWidget(del_btn)
        row.addStretch(1)
        v.addLayout(row)

        # ============================================================
        # ★ UNIFIED 4-role panel
        # ============================================================
        grp_roles = QGroupBox("🎯 چهار نقش مستقل (Stream + FPS)")
        gv = QVBoxLayout(grp_roles)
        gv.setSpacing(4)
        gv.setContentsMargins(14, 20, 14, 14)

        self.rec_profile_combo, self.rec_fps_auto, self.rec_fps_spin = \
            self._make_role_widget(
                gv, "🎬 ضبط دائم",
                self.camera.recording_profile_id,
                int(getattr(self.camera, "recording_fps_note", 0) or 0),
                "recording"
            )

        self.motion_profile_combo, self.motion_fps_auto, self.motion_fps_spin = \
            self._make_role_widget(
                gv, "⚠ تشخیص حرکت",
                self.camera.motion_profile_id,
                int(getattr(self.camera, "motion_processing_fps", 0) or 0),
                "motion"
            )

        self.live_profile_combo_local, self.live_fps_auto, self.live_fps_spin = \
            self._make_role_widget(
                gv, "📺 پخش لایو (تکی)",
                self.camera.live_profile_id,
                int(getattr(self.camera, "live_display_fps", 0) or 0),
                "live"
            )

        self.grid_profile_combo, self.grid_fps_auto, self.grid_fps_spin = \
            self._make_role_widget(
                gv, "🔲 نمای شبکه",
                self.camera.grid_profile_id,
                int(getattr(self.camera, "grid_display_fps", 0) or 0),
                "grid"
            )

        # Hint
        hint_role = QLabel(
            "💡 <b>پیشنهاد:</b> ضبط = Main (کیفیت بالا) • حرکت = Sub (کم‌حجم) • "
            "لایو = Main • شبکه = Sub\n"
            "📌 در حالت «خودکار»، نرخ فریم از خود دوربین خوانده می‌شود. "
            "برای کاهش مصرف CPU، تیک خودکار را بردار و مقدار کمتری تنظیم کن."
        )
        hint_role.setWordWrap(True)
        hint_role.setStyleSheet(
            f"color: {config.COLOR_TEXT_SECONDARY}; font-size: 10px; "
            f"padding: 8px; background: {config.COLOR_BG_CARD}; "
            f"border-radius: 4px;"
        )
        gv.addWidget(hint_role)

        v.addWidget(grp_roles)

        v.addStretch(1)

        # Populate combos + FPS
        self._refresh_profiles_list()
        return w

    def _make_role_widget(self, parent_layout, label, profile_id,
                           fps_value, role_key):
        """
        یک بخش role می‌سازد: هدر + combo + FPS (auto/manual).
        Returns (combo, auto_chk, spin).
        """
        # -------- Header --------
        hdr = QLabel(f"<b>{label}</b>")
        hdr.setStyleSheet(
            f"color: {config.COLOR_ACCENT}; font-size: 12px; "
            f"padding-top: 8px; padding-bottom: 2px;"
        )
        parent_layout.addWidget(hdr)

        # -------- Profile row --------
        prow = QHBoxLayout()
        prow.setContentsMargins(20, 0, 0, 0)
        plbl = QLabel("استریم:")
        plbl.setFixedWidth(70)
        plbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prow.addWidget(plbl)

        combo = QComboBox()
        combo.setMinimumHeight(30)
        prow.addWidget(combo, 1)
        parent_layout.addLayout(prow)

        # -------- FPS row --------
        frow = QHBoxLayout()
        frow.setContentsMargins(20, 2, 0, 6)
        flbl = QLabel("نرخ فریم:")
        flbl.setFixedWidth(70)
        flbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        frow.addWidget(flbl)

        auto_chk = QCheckBox("خودکار (از دوربین)")
        frow.addWidget(auto_chk)

        spin = QSpinBox()
        spin.setRange(1, 60)
        spin.setSuffix(" fps")
        spin.setFixedWidth(130)
        frow.addWidget(spin)
        frow.addStretch(1)
        parent_layout.addLayout(frow)

        # -------- Initial state --------
        is_auto = (fps_value <= 0)
        auto_chk.setChecked(is_auto)
        spin.setValue(fps_value if fps_value > 0 else 15)

        def apply_readonly_style(checked):
            spin.setReadOnly(checked)
            spin.setButtonSymbols(
                QSpinBox.NoButtons if checked else QSpinBox.UpDownArrows
            )
            spin.setStyleSheet(
                f"QSpinBox {{ background: "
                f"{'#1a1f26' if checked else config.COLOR_BG_CARD}; "
                f"color: {'#6b7280' if checked else config.COLOR_TEXT_PRIMARY}; "
                f"border: 1px solid #2c333c; border-radius: 5px; "
                f"padding: 6px 8px; font-size: 12px; }}"
            )

        apply_readonly_style(is_auto)

        # -------- Update from profile --------
        def refresh_from_profile():
            if not auto_chk.isChecked():
                return
            pid = combo.currentData() or ""
            p = self.camera.get_profile_by_id(pid) if pid else None
            if p and p.fps and p.fps > 0:
                spin.setValue(int(p.fps))

        # Store refresh function on widget for later external trigger
        combo.setProperty("_role_key", role_key)

        combo.currentIndexChanged.connect(lambda _: refresh_from_profile())

        def on_auto_toggle(checked):
            apply_readonly_style(checked)
            if checked:
                refresh_from_profile()

        auto_chk.toggled.connect(on_auto_toggle)

        # Store refresh function for _refresh_profiles_list
        spin.setProperty("_refresh_fn", refresh_from_profile)

        return combo, auto_chk, spin

    def _small_btn(self, text):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_BG_CARD};
                color: {config.COLOR_TEXT_PRIMARY}; border: 1px solid #2c333c;
                border-radius: 5px; padding: 6px 14px; font-size: 11px;
                font-weight: bold; }}
            QPushButton:hover {{ background: #2c333c; }}
        """)
        return b

    def _refresh_profiles_list(self):
        self.profiles_list.clear()
        for p in self.camera.stream_profiles:
            roles_str = " + ".join(p.roles or [])
            text = f"  {p.name}   —   {p.summary()}   [{roles_str}]"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, p.id)
            self.profiles_list.addItem(item)

        self._populate_role_combo(self.rec_profile_combo,
                                   self.camera.recording_profile_id)
        self._populate_role_combo(self.motion_profile_combo,
                                   self.camera.motion_profile_id)
        self._populate_role_combo(self.live_profile_combo_local,
                                   self.camera.live_profile_id,
                                   allow_empty=False)
        self._populate_role_combo(self.grid_profile_combo,
                                   self.camera.grid_profile_id,
                                   allow_empty=False)

        # ★ After combos populated, refresh FPS displays for auto modes
        QTimer.singleShot(0, self._refresh_all_fps_displays)

    def _refresh_all_fps_displays(self):
        """Force-update all FPS displays to reflect current profile selection."""
        try:
            for spin in [self.rec_fps_spin, self.motion_fps_spin,
                          self.live_fps_spin, self.grid_fps_spin]:
                fn = spin.property("_refresh_fn")
                if callable(fn):
                    fn()
        except Exception as e:
            print(f"[camera_dialog] fps refresh error: {e}")

    def _populate_role_combo(self, combo, current_id, allow_empty=False):
        combo.blockSignals(True)
        combo.clear()
        if allow_empty:
            combo.addItem("— هیچکدام —", "")
        for p in self.camera.stream_profiles:
            label = f"{p.name}  ({p.resolution_or('—')} @ {p.fps or 0}fps)"
            combo.addItem(label, p.id)
        idx = combo.findData(current_id)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _add_manual_profile(self):
        dlg = StreamProfileDialog(None, self.camera, self)
        if dlg.exec() == QDialog.Accepted:
            p = dlg.get_profile()
            self.camera.stream_profiles.append(p)
            try:
                self.camera._ensure_profile_defaults()
            except Exception:
                pass
            self._refresh_profiles_list()

    def _edit_profile(self):
        cur = self.profiles_list.currentItem()
        if cur is None:
            return
        pid = cur.data(Qt.UserRole)
        p = self.camera.get_profile_by_id(pid)
        if p is None:
            return
        dlg = StreamProfileDialog(p, self.camera, self)
        if dlg.exec() == QDialog.Accepted:
            self._refresh_profiles_list()

    def _delete_profile(self):
        cur = self.profiles_list.currentItem()
        if cur is None:
            return
        pid = cur.data(Qt.UserRole)
        self.camera.stream_profiles = [
            p for p in self.camera.stream_profiles if p.id != pid
        ]
        if self.camera.recording_profile_id == pid:
            self.camera.recording_profile_id = ""
        if self.camera.motion_profile_id == pid:
            self.camera.motion_profile_id = ""
        if self.camera.live_profile_id == pid:
            self.camera.live_profile_id = ""
        if self.camera.grid_profile_id == pid:
            self.camera.grid_profile_id = ""
        try:
            self.camera._ensure_profile_defaults()
        except Exception:
            pass
        self._refresh_profiles_list()

    # ------------------------------------------------------------
    # Tab 3: Recording
    # ------------------------------------------------------------
    def _tab_recording(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.rec_motion_chk = QCheckBox(
            "ضبط هنگام تشخیص حرکت (اگر ضبط دائم خاموش باشد، Motion-Only)"
        )
        self.rec_motion_chk.setChecked(self.camera.record_enabled_motion)
        form.addRow("", self.rec_motion_chk)

        self.rec_cont_chk = QCheckBox("ضبط دائمی (۲۴ ساعته)")
        self.rec_cont_chk.setChecked(self.camera.record_enabled_continuous)
        form.addRow("", self.rec_cont_chk)

        self.rec_seg_spin = QSpinBox()
        self.rec_seg_spin.setRange(0, 120)
        self.rec_seg_spin.setValue(self.camera.record_segment_minutes)
        self.rec_seg_spin.setSuffix(" دقیقه")
        self.rec_seg_spin.setSpecialValueText("استفاده از سراسری")
        form.addRow("طول هر قطعه:", self.rec_seg_spin)

        self.rec_path_edit = QLineEdit(self.camera.record_path_override)
        self.rec_path_edit.setPlaceholderText("خالی = مسیر سراسری")
        form.addRow("مسیر اختصاصی:", self.rec_path_edit)

        hint = QLabel(
            "💡 <b>پروفایل ضبط</b> از تب «📡 استریم و لایو» انتخاب می‌شود.\n"
            "•  ضبط کاملاً مستقل از پخش لایو است.\n"
            "•  تغییر Grid یا Single روی ضبط تأثیری ندارد."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"""
            color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px;
            background: {config.COLOR_BG_CARD}; padding: 10px;
            border-radius: 5px; border-left: 3px solid {config.COLOR_ACCENT};""")
        form.addRow(hint)

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
            color: {config.COLOR_ACCENT}; font-weight: bold; font-size: 13px;
            background: {config.COLOR_BG_CARD}; border-radius: 4px;
            padding: 3px; qproperty-alignment: AlignCenter;""")
        self.sens_slider.valueChanged.connect(
            lambda v: self.sens_val_lbl.setText(str(v)))
        sens_row.addWidget(self.sens_slider)
        sens_row.addWidget(self.sens_val_lbl)
        form.addRow("حساسیت:", sens_row)

        self.area_spin = QSpinBox()
        self.area_spin.setRange(20, 200000)
        self.area_spin.setValue(self.camera.min_area)
        form.addRow("حداقل ناحیه حرکت:", self.area_spin)

        self.detect_every_spin = QSpinBox()
        self.detect_every_spin.setRange(1, 30)
        self.detect_every_spin.setValue(self.camera.detect_every)
        form.addRow("بررسی هر N فریم:", self.detect_every_spin)

        grp_buf = QGroupBox("⏱ بافرهای زمانی (Phase 4)")
        gb = QFormLayout(grp_buf)
        self.pre_buf_spin = QSpinBox()
        self.pre_buf_spin.setRange(0, 60)
        self.pre_buf_spin.setValue(
            getattr(self.camera, "motion_pre_buffer_sec", 5)
        )
        self.pre_buf_spin.setSuffix(" ثانیه")
        gb.addRow("Pre-buffer (قبل از حرکت):", self.pre_buf_spin)

        self.post_buf_spin = QSpinBox()
        self.post_buf_spin.setRange(0, 120)
        self.post_buf_spin.setValue(
            getattr(self.camera, "motion_post_buffer_sec", 5)
        )
        self.post_buf_spin.setSuffix(" ثانیه")
        gb.addRow("Post-buffer (بعد از حرکت):", self.post_buf_spin)
        form.addRow(grp_buf)

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

        self.ai_filter_person_chk = QCheckBox("فقط رویدادهای «شخص» آلارم بدهند")
        self.ai_filter_person_chk.setChecked(self.camera.ai_filter_person_only)
        form.addRow("", self.ai_filter_person_chk)

        hint = QLabel("🤖 <b>AI (Phase 6):</b> فیلتر آلارم‌های کاذب.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"""
            color: {config.COLOR_TEXT_SECONDARY}; font-size: 11px;
            background: {config.COLOR_BG_CARD}; padding: 12px;
            border-radius: 5px; border-left: 3px solid {config.COLOR_WARNING};""")
        form.addRow(hint)
        return w

    # ============================================================
    # Auto-detect
    # ============================================================
    def _on_auto_detect(self):
        ip = self.ip_edit.text().strip()
        user = self.user_edit.text().strip()
        password = self.pass_edit.text()
        rtsp_port = self.port_spin.value()

        if not ip or not user:
            QMessageBox.warning(self, "خطا",
                "لطفاً IP و نام کاربری را در تب اتصال وارد کنید.")
            return

        progress = QProgressDialog(
            "مرحله ۱ از ۲: تلاش ONVIF...", "لغو", 0, 100, self)
        progress.setWindowTitle("تشخیص خودکار")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(5)
        progress.setStyleSheet(DIALOG_STYLE)

        onvif_profiles = None
        try:
            onvif_profiles = camera_discovery.get_onvif_stream_profiles(
                ip, user, password, port=80
            )
        except Exception as e:
            print(f"[AutoDetect] ONVIF failed: {e}")

        if onvif_profiles:
            progress.setValue(100)
            progress.close()
            self._apply_onvif_profiles(onvif_profiles)
            QMessageBox.information(self, "✅ ONVIF موفق",
                f"دوربین از طریق ONVIF شناسایی شد.\n\n"
                f"{len(onvif_profiles)} پروفایل پیدا شد.")
            return

        progress.setLabelText("مرحله ۲ از ۲: تست الگوهای رایج RTSP...")
        progress.setValue(50)
        try:
            result = camera_discovery._find_rtsp_path_guessing(
                ip, user, password)
        except Exception as e:
            print(f"[AutoDetect] RTSP guess failed: {e}")
            result = None
        progress.close()

        if result:
            p = StreamProfile(
                id="main", name="Main Stream (auto)",
                rtsp_path=result["main"],
                url=result["url"],
                roles=[ROLE_RECORDING, ROLE_LIVE_VIEW,
                       ROLE_MOTION, ROLE_GRID],
                discovered_by="guess",
            )

            sub_candidates = self._get_sub_candidates(result["main"])
            for sub_path in sub_candidates:
                from core.camera_discovery import _test_rtsp_url, _get_rtsp_url
                url = _get_rtsp_url(ip, user, password, sub_path, rtsp_port)
                if _test_rtsp_url(url):
                    sp = StreamProfile(
                        id="sub", name="Sub Stream (auto)",
                        rtsp_path=sub_path, url=url,
                        roles=[ROLE_MOTION, ROLE_GRID, ROLE_LIVE_VIEW],
                        discovered_by="guess",
                    )
                    self.camera.stream_profiles.append(sp)
                    break

            self.camera.stream_profiles.insert(0, p)
            try:
                self.camera._ensure_profile_defaults()
            except Exception:
                pass
            self._refresh_profiles_list()

            QMessageBox.information(self, "✅ RTSP موفق",
                f"مسیر اصلی پیدا شد: {result['main']}")
            return

        QMessageBox.warning(self, "❌ ناموفق",
            "هیچ استریمی پیدا نشد. IP و credentials را چک کن.")

    def _apply_onvif_profiles(self, profiles: List[dict]):
        self.camera.stream_profiles = []
        for i, pd in enumerate(profiles):
            # Role assignment by index:
            # index 0 → RECORDING + LIVE_VIEW
            # index 1 → MOTION + GRID + LIVE_VIEW
            # index 2+ → LIVE_VIEW only
            if i == 0:
                roles = [ROLE_RECORDING, ROLE_LIVE_VIEW]
            elif i == 1:
                roles = [ROLE_MOTION, ROLE_GRID, ROLE_LIVE_VIEW]
            else:
                roles = [ROLE_LIVE_VIEW]

            sp = StreamProfile(
                id=pd.get("id", f"profile_{i}"),
                name=pd.get("name", f"Profile {i+1}"),
                rtsp_path=pd.get("rtsp_path", ""),
                url=pd.get("url", ""),
                resolution=pd.get("resolution", ""),
                width=int(pd.get("width") or 0),
                height=int(pd.get("height") or 0),
                codec=pd.get("codec", ""),
                fps=int(pd.get("fps") or 0),
                bitrate_kbps=int(pd.get("bitrate_kbps") or 0),
                roles=roles,
                discovered_by="onvif",
                onvif_profile_token=pd.get("id", ""),
            )
            self.camera.stream_profiles.append(sp)
        try:
            self.camera._ensure_profile_defaults()
        except Exception:
            pass
        self._refresh_profiles_list()

    def _get_sub_candidates(self, main_path: str) -> list:
        candidates = []
        if "/Streaming/Channels/101" in main_path:
            candidates.append(main_path.replace("101", "102"))
        if "subtype=0" in main_path:
            candidates.append(main_path.replace("subtype=0", "subtype=1"))
        if main_path.endswith("video1"):
            candidates.append(main_path[:-1] + "2")
        if main_path.endswith("Main"):
            candidates.append(main_path[:-4] + "Sub")
        if main_path.endswith("/main"):
            candidates.append(main_path[:-4] + "sub")
        if "profile1" in main_path:
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

        # Sync legacy
        rec_p = c.get_recording_profile()
        if rec_p:
            c.rtsp_path_main = rec_p.rtsp_path
        mot_p = c.get_motion_profile()
        if mot_p:
            c.rtsp_path_sub = mot_p.rtsp_path

        # 4 roles
        c.recording_profile_id = self.rec_profile_combo.currentData() or ""
        c.motion_profile_id = self.motion_profile_combo.currentData() or ""
        c.live_profile_id = self.live_profile_combo_local.currentData() or ""
        c.grid_profile_id = self.grid_profile_combo.currentData() or ""

        # FPS values (0 = auto, >0 = manual)
        c.recording_fps_note = (
            0 if self.rec_fps_auto.isChecked()
            else self.rec_fps_spin.value()
        )
        c.motion_processing_fps = (
            0 if self.motion_fps_auto.isChecked()
            else self.motion_fps_spin.value()
        )
        c.live_display_fps = (
            0 if self.live_fps_auto.isChecked()
            else self.live_fps_spin.value()
        )
        c.grid_display_fps = (
            0 if self.grid_fps_auto.isChecked()
            else self.grid_fps_spin.value()
        )

        # Recording settings
        c.record_enabled_motion = self.rec_motion_chk.isChecked()
        c.record_enabled_continuous = self.rec_cont_chk.isChecked()
        c.record_segment_minutes = self.rec_seg_spin.value()
        c.record_path_override = self.rec_path_edit.text().strip()

        # Motion settings
        c.sensitivity = self.sens_slider.value()
        c.min_area = self.area_spin.value()
        c.detect_every = self.detect_every_spin.value()
        c.motion_pre_buffer_sec = self.pre_buf_spin.value()
        c.motion_post_buffer_sec = self.post_buf_spin.value()

        # AI
        c.ai_enabled = self.ai_enabled_chk.isChecked()
        c.ai_filter_person_only = self.ai_filter_person_chk.isChecked()

        self.accept()

    def get_camera(self) -> CameraConfig:
        return self.camera


# ============================================================
# StreamProfileDialog
# ============================================================
class StreamProfileDialog(QDialog):
    def __init__(self, profile: StreamProfile, camera: CameraConfig,
                 parent=None):
        super().__init__(parent)
        self.camera = camera
        self.profile = profile or StreamProfile(
            id=f"custom_{len(camera.stream_profiles) + 1}",
            name="Custom",
        )
        self.is_new = profile is None

        self.setWindowTitle(
            "➕ افزودن پروفایل" if self.is_new else "✏ ویرایش پروفایل"
        )
        self.resize(480, 600)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.id_edit = QLineEdit(self.profile.id)
        self.id_edit.setReadOnly(not self.is_new)
        form.addRow("ID:", self.id_edit)

        self.name_edit = QLineEdit(self.profile.name)
        form.addRow("نام:", self.name_edit)

        self.path_edit = QLineEdit(self.profile.rtsp_path)
        self.path_edit.setPlaceholderText("/Streaming/Channels/101")
        form.addRow("مسیر RTSP:", self.path_edit)

        res_row = QHBoxLayout()
        self.w_spin = QSpinBox(); self.w_spin.setRange(0, 8192)
        self.w_spin.setValue(self.profile.width)
        self.h_spin = QSpinBox(); self.h_spin.setRange(0, 8192)
        self.h_spin.setValue(self.profile.height)
        res_row.addWidget(QLabel("عرض:"))
        res_row.addWidget(self.w_spin)
        res_row.addWidget(QLabel("ارتفاع:"))
        res_row.addWidget(self.h_spin)
        form.addRow("رزولوشن:", res_row)

        self.codec_combo = QComboBox()
        for c in ["", "H264", "H265", "MJPEG", "MPEG4"]:
            self.codec_combo.addItem(c or "—", c)
        idx = self.codec_combo.findData((self.profile.codec or "").upper())
        if idx >= 0:
            self.codec_combo.setCurrentIndex(idx)
        form.addRow("کدک:", self.codec_combo)

        self.fps_spin = QSpinBox(); self.fps_spin.setRange(0, 240)
        self.fps_spin.setValue(self.profile.fps)
        form.addRow("FPS (منبع دوربین):", self.fps_spin)

        self.bitrate_spin = QSpinBox(); self.bitrate_spin.setRange(0, 100000)
        self.bitrate_spin.setValue(self.profile.bitrate_kbps)
        self.bitrate_spin.setSuffix(" kbps")
        form.addRow("بیت‌ریت:", self.bitrate_spin)

        v.addLayout(form)

        grp = QGroupBox("نقش‌ها (چندتایی قابل انتخاب)")
        gv = QVBoxLayout(grp)
        self.role_checks = {}
        for role in ALL_ROLES:
            cb = QCheckBox(role)
            cb.setChecked(self.profile.has_role(role))
            gv.addWidget(cb)
            self.role_checks[role] = cb
        v.addWidget(grp)

        v.addStretch(1)

        buttons = QDialogButtonBox()
        save = buttons.addButton("💾 ذخیره", QDialogButtonBox.AcceptRole)
        save.setStyleSheet(f"""
            QPushButton {{ background: {config.COLOR_ACCENT}; color: white;
                padding: 8px 20px; border-radius: 5px; font-weight: bold;
                border: none; min-width: 80px; }}
            QPushButton:hover {{ background: {config.COLOR_ACCENT_HOVER}; }}
        """)
        buttons.addButton("انصراف", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _on_save(self):
        p = self.profile
        p.id = self.id_edit.text().strip() or p.id
        p.name = self.name_edit.text().strip() or "Profile"
        p.rtsp_path = self.path_edit.text().strip()
        p.width = self.w_spin.value()
        p.height = self.h_spin.value()
        p.resolution = f"{p.width}x{p.height}" if p.width and p.height else ""
        p.codec = self.codec_combo.currentData() or ""
        p.fps = self.fps_spin.value()
        p.bitrate_kbps = self.bitrate_spin.value()
        p.roles = [r for r, cb in self.role_checks.items() if cb.isChecked()]
        if p.rtsp_path:
            p.url = self.camera._build_url(p.rtsp_path)
        if not p.discovered_by:
            p.discovered_by = "manual"
        self.accept()

    def get_profile(self) -> StreamProfile:
        return self.profile