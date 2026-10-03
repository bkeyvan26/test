# -*- coding: utf-8 -*-
"""K1 VMS — Persian (Jalali) calendar with recording-day highlighting"""
import datetime
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGridLayout
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from ui import theme
from ui.icons import make_icon


# ============================================================
# Gregorian ↔ Jalali conversion
# ============================================================
def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100)
            + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1])
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_to_gregorian(jy, jm, jd):
    jy += 1595
    days = (-355668 + (365 * jy) + ((jy // 33) * 8)
            + (((jy % 33) + 3) // 4) + jd)
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    is_leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    sal_a = [0, 31, 29 if is_leap else 28, 31, 30, 31, 30,
             31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def jalali_month_length(jy, jm):
    if jm <= 6: return 31
    if jm <= 11: return 30
    # اسفند: کبیسه‌ـیات تقریبی
    # if leap: 30 else 29
    gy, _, _ = jalali_to_gregorian(jy, 1, 1)
    # leap-year rule for Jalali: uses 33-year cycle approximation
    r = (jy + 1595) % 33
    is_leap = r in (1, 5, 9, 13, 17, 22, 26, 30)
    return 30 if is_leap else 29


PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
PERSIAN_WEEKDAYS_SHORT = ["ش", "ی", "د", "س", "چ", "پ", "ج"]


# ============================================================
# Persian Calendar Widget
# ============================================================
class PersianCalendar(QWidget):
    """Compact Jalali calendar. Days with recordings are highlighted."""
    date_selected = Signal(object)   # datetime.date

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jy = 1405
        self._jm = 1
        self._selected = datetime.date.today()
        self._recording_dates = set()

        today = datetime.date.today()
        jy, jm, jd = gregorian_to_jalali(today.year, today.month, today.day)
        self._jy, self._jm = jy, jm

        self._build()
        self._render()

    # ------------------------------------------------------------
    def set_recording_dates(self, dates_set):
        """dates_set: set of datetime.date"""
        self._recording_dates = set(dates_set or [])
        self._render()

    def set_selected_date(self, date_obj):
        self._selected = date_obj
        jy, jm, jd = gregorian_to_jalali(date_obj.year, date_obj.month, date_obj.day)
        self._jy, self._jm = jy, jm
        self._render()

    def selected_date(self):
        return self._selected

    # ------------------------------------------------------------
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        # Header: month/year + prev/next
        head = QHBoxLayout()
        head.setSpacing(4)

        self.prev_btn = QPushButton("‹")
        self.prev_btn.setFixedSize(24, 24)
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.clicked.connect(self._prev_month)
        self.prev_btn.setStyleSheet(self._nav_btn_qss())
        head.addWidget(self.prev_btn)

        self.month_lbl = QLabel("—")
        self.month_lbl.setAlignment(Qt.AlignCenter)
        self.month_lbl.setStyleSheet(f"""
            color: {theme.COLOR_TEXT_PRIMARY};
            font-size: 12px;
            font-weight: 700;
            background: {theme.COLOR_BG_CARD};
            border-radius: 5px;
            padding: 3px 8px;
        """)
        head.addWidget(self.month_lbl, 1)

        self.next_btn = QPushButton("›")
        self.next_btn.setFixedSize(24, 24)
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.clicked.connect(self._next_month)
        self.next_btn.setStyleSheet(self._nav_btn_qss())
        head.addWidget(self.next_btn)

        v.addLayout(head)

        # Weekday labels
        wd = QHBoxLayout()
        wd.setSpacing(2)
        for name in PERSIAN_WEEKDAYS_SHORT:
            l = QLabel(name)
            l.setAlignment(Qt.AlignCenter)
            l.setStyleSheet(
                f"color: {theme.COLOR_TEXT_SECONDARY}; font-size: 10px; font-weight: 700;")
            wd.addWidget(l, 1)
        v.addLayout(wd)

        # Grid of day buttons
        self.grid = QGridLayout()
        self.grid.setSpacing(2)
        self.day_btns = {}
        for r in range(6):
            for c in range(7):
                b = QPushButton("")
                b.setFixedSize(28, 28)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda checked=False, rr=r, cc=c: self._on_day_click(rr, cc))
                b.setStyleSheet(self._day_btn_qss("normal"))
                self.grid.addWidget(b, r, c)
                self.day_btns[(r, c)] = b
        v.addLayout(self.grid)

        v.addStretch(1)

    def _nav_btn_qss(self):
        return f"""
            QPushButton {{
                background: {theme.COLOR_BG_CARD};
                color: {theme.COLOR_TEXT_PRIMARY};
                border: 1px solid {theme.COLOR_BORDER};
                border-radius: 4px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_ACCENT};
                color: white;
            }}
        """

    def _day_btn_qss(self, state):
        if state == "recording":
            return f"""
                QPushButton {{
                    background: {theme.COLOR_ACCENT};
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: {theme.COLOR_ACCENT_HOVER};
                }}
            """
        if state == "selected":
            return f"""
                QPushButton {{
                    background: transparent;
                    color: #ff4d4d;
                    border: 2px solid #ff4d4d;
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 800;
                }}
                QPushButton:hover {{
                    background: #ff4d4d;
                    color: white;
                }}
            """
        if state == "today":
            return f"""
                QPushButton {{
                    background: {theme.COLOR_BG_HOVER};
                    color: {theme.COLOR_TEXT_PRIMARY};
                    border: 1px solid {theme.COLOR_ACCENT};
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: {theme.COLOR_ACCENT};
                    color: white;
                }}
            """
        if state == "empty":
            return f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    color: transparent;
                }}
            """
        # normal (no recording)
        return f"""
            QPushButton {{
                background: transparent;
                color: {theme.COLOR_TEXT_MUTED};
                border: none;
                border-radius: 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {theme.COLOR_BG_HOVER};
                color: {theme.COLOR_TEXT_PRIMARY};
            }}
        """

    # ------------------------------------------------------------
    def _render(self):
        self.month_lbl.setText(f"{PERSIAN_MONTHS[self._jm - 1]} {self._jy}")

        # clear grid
        for r in range(6):
            for c in range(7):
                b = self.day_btns[(r, c)]
                b.setText("")
                b.setEnabled(False)
                b.setStyleSheet(self._day_btn_qss("empty"))
                b.setProperty("day", None)

        # first day of Persian month
        gy, gm, gd = jalali_to_gregorian(self._jy, self._jm, 1)
        first_greg = datetime.date(gy, gm, gd)

        # Saturday = 0 in Persian week. Python: Monday=0, Sunday=6
        # Persian: Saturday=0 → python: (weekday+2) % 7
        py_wd = first_greg.weekday()  # Mon=0
        # Saturday is py_wd=5, so shift: Sat→0, Sun→1, ..., Fri→6
        persian_col = (py_wd + 2) % 7

        n_days = jalali_month_length(self._jy, self._jm)
        today = datetime.date.today()

        for d in range(1, n_days + 1):
            idx = persian_col + (d - 1)
            r = idx // 7
            c = idx % 7
            if r > 5:
                continue
            gy, gm, gd = jalali_to_gregorian(self._jy, self._jm, d)
            greg = datetime.date(gy, gm, gd)

            b = self.day_btns[(r, c)]
            b.setText(str(d))
            # Only days that actually contain recordings are selectable.
            b.setEnabled(greg in self._recording_dates)
            b.setProperty("day", greg)

            # Priority: recording > selected > today > normal
            if greg in self._recording_dates:
                # if selected AND has recording → still show as recording but with red border
                if greg == self._selected:
                    b.setStyleSheet(f"""
                        QPushButton {{
                            background: {theme.COLOR_ACCENT};
                            color: white;
                            border: 2px solid #ff4d4d;
                            border-radius: 4px;
                            font-size: 11px;
                            font-weight: 800;
                        }}
                    """)
                else:
                    b.setStyleSheet(self._day_btn_qss("recording"))
            elif greg == self._selected:
                b.setStyleSheet(self._day_btn_qss("selected"))
            elif greg == today:
                b.setStyleSheet(self._day_btn_qss("today"))
            else:
                b.setStyleSheet(self._day_btn_qss("normal"))

    # ------------------------------------------------------------
    def _prev_month(self):
        self._jm -= 1
        if self._jm < 1:
            self._jm = 12
            self._jy -= 1
        self._render()

    def _next_month(self):
        self._jm += 1
        if self._jm > 12:
            self._jm = 1
            self._jy += 1
        self._render()

    def _on_day_click(self, r, c):
        b = self.day_btns[(r, c)]
        day = b.property("day")
        if day is None:
            return
        self._selected = day
        self._render()
        self.date_selected.emit(day)