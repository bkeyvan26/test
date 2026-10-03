# -*- coding: utf-8 -*-
"""K1Motion — تعریف مجوزهای دسترسی"""

# ساختار: (کلید, نام فارسی, نام انگلیسی, گروه)
PERMISSIONS = [
    # --- دوربین‌ها ---
    ("camera_add",       "افزودن دوربین",           "Add camera",            "cameras"),
    ("camera_edit",      "ویرایش دوربین",           "Edit camera",           "cameras"),
    ("camera_delete",    "حذف دوربین",              "Delete camera",         "cameras"),
    ("camera_connect",   "اتصال / قطع دوربین",       "Connect / disconnect",  "cameras"),
    ("camera_view_all",  "دیدن همه دوربین‌ها",       "View all cameras",      "cameras"),
    ("camera_stream_q",  "تنظیم کیفیت لایو",        "Live quality settings", "cameras"),

    # --- ضبط ---
    ("record_motion",    "ضبط هنگام حرکت",          "Motion recording",      "recording"),
    ("record_continuous","ضبط دائم",                 "Continuous recording",  "recording"),
    ("record_quality",   "تنظیم کیفیت ضبط",         "Record quality",        "recording"),
    ("record_path",      "تغییر مسیر ضبط",          "Change record path",    "recording"),
    ("record_delete",    "حذف فیلم‌ها",              "Delete recordings",     "recording"),

    # --- تشخیص حرکت ---
    ("motion_toggle",    "روشن/خاموش تشخیص",         "Toggle motion",         "motion"),
    ("motion_set_roi",   "تعیین محدوده ROI",         "Set ROI",               "motion"),
    ("motion_settings",  "تنظیم حساسیت",             "Motion sensitivity",    "motion"),
    ("alarm_test",       "تست آلارم",                "Test alarm",            "motion"),

    # --- کاربران ---
    ("users_manage",     "مدیریت کاربران",           "Manage users",          "users"),
    ("users_password",   "تغییر رمز کاربران",        "Change user password",  "users"),
    ("users_permissions","تعیین سطح دسترسی",         "Set user permissions",  "users"),

    # --- تنظیمات سیستم ---
    ("settings_general", "تنظیمات عمومی",            "General settings",      "settings"),
    ("settings_alarm",   "تنظیمات آلارم",            "Alarm settings",        "settings"),
    ("settings_sound",   "تنظیمات صدا",              "Sound settings",        "settings"),
    ("settings_window",  "تنظیمات پنجره",            "Window settings",       "settings"),
    ("settings_autostart","اجرای خودکار",            "Auto-start",            "settings"),
    ("settings_network", "تنظیمات شبکه",             "Network settings",      "settings"),
    ("settings_ai",      "تنظیمات AI",               "AI settings",           "settings"),

    # --- سیستم ---
    ("sys_audit_log",    "مشاهده لاگ رویدادها",       "View audit log",        "system"),
    ("sys_logout",       "خروج از حساب",             "Logout",                "system"),
    ("sys_exit",         "بستن برنامه",              "Exit app",              "system"),
]

ALL_PERMISSION_KEYS = [k for k, _, _, _ in PERMISSIONS]

# گروه‌بندی برای نمایش در UI
PERMISSION_GROUPS = {
    "cameras":   "📹 دوربین‌ها",
    "recording": "🎬 ضبط",
    "motion":    "⚠ تشخیص حرکت",
    "users":     "👥 کاربران",
    "settings":  "⚙ تنظیمات",
    "system":    "🖥 سیستم",
}

# مجوزهای پیش‌فرض برای کاربر عادی
DEFAULT_USER_PERMISSIONS = {k: False for k in ALL_PERMISSION_KEYS}
DEFAULT_USER_PERMISSIONS["sys_logout"] = True     # خروج از حساب را همیشه دارد

# مجوزهای پیش‌فرض برای ادمین (همه چیز)
DEFAULT_ADMIN_PERMISSIONS = {k: True for k in ALL_PERMISSION_KEYS}


def get_permission_label(key, lang="fa"):
    """گرفتن نام فارسی/انگلیسی یک مجوز"""
    for k, fa, en, _ in PERMISSIONS:
        if k == key:
            return fa if lang == "fa" else en
    return key


def get_permission_group(key):
    """گروه یک مجوز"""
    for k, _, _, group in PERMISSIONS:
        if k == key:
            return group
    return "system"