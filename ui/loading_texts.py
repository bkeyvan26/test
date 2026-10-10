# -*- coding: utf-8 -*-
"""K1 VMS — Loading texts (Phase 6.6)

همه‌ی متن‌های لودینگ اینجا متمرکز شده.
برای تغییر، فقط همین فایل را ویرایش کن.

Placeholders:
  {camera}   → نام دوربین
  {camera2}  → نام دوربین دوم (در سوئیچ)
  {page}     → نام صفحه
  {date}     → تاریخ
  {time}     → زمان
  {count}    → تعداد
"""

TEXTS = {
    # ---------- Page switching ----------
    "page_switch":        "در حال باز کردن {page}…",
    "page_loading":       "بارگذاری صفحه…",

    # ---------- Live ----------
    "live_connect":       "در حال اتصال به {camera}…",
    "live_reconnect":     "اتصال مجدد به {camera}…",
    "live_prewarm":       "آماده‌سازی {camera}…",
    "live_prewarm_n":     "آماده‌سازی {count} دوربین…",
    "live_switch_single": "سوئیچ به نمای تکی {camera}…",
    "live_loading_n":     "در حال بارگذاری {count} دوربین…",
    "live_clear":         "پاک کردن کاشی‌ها…",
    "live_loading_cam":   "در حال بارگذاری {camera}…",

    # ---------- Playback ----------
    "playback_load":      "بارگذاری {camera} — {date}…",
    "playback_seek":      "جستجو در {time}…",
    "playback_scan":      "اسکن ضبط‌های {camera}…",
    "playback_export":    "آماده‌سازی خروجی…",
    "playback_generating":"در حال تولید فایل…",
    "playback_select":    "یک دوربین انتخاب کنید",

    # ---------- Camera Management ----------
    "camera_save":        "ذخیره تنظیمات {camera}…",
    "camera_detect":      "شناسایی خودکار استریم‌ها…",
    "camera_detect_long": "در حال اسکن پورت‌ها و پروتکل‌ها — صبر کنید…",
    "camera_delete":      "حذف {camera}…",
    "camera_test":        "تست اتصال {camera}…",
    "camera_adding":      "افزودن دوربین…",

    # ---------- App lifecycle ----------
    "startup":            "در حال راه‌اندازی K1 VMS…",
    "startup_db":         "بارگذاری پایگاه داده…",
    "startup_cams":       "بارگذاری دوربین‌ها…",
    "startup_nvr":        "راه‌اندازی موتور ضبط…",
    "startup_ui":         "آماده‌سازی رابط کاربری…",
    "shutdown":           "در حال خروج…",
    "shutdown_readers":   "بستن استریم‌ها…",
    "shutdown_save":      "ذخیره تنظیمات…",

    # ---------- NVR / MediaMTX ----------
    "nvr_start":          "راه‌اندازی MediaMTX…",
    "nvr_stop":           "توقف MediaMTX…",
    "nvr_reconnect":      "اتصال مجدد به MediaMTX…",

    # ---------- Errors / Special ----------
    "error_generic":      "خطا در انجام عملیات",
    "error_timeout":      "زمان انتظار تمام شد",
    "waiting":            "لطفاً صبر کنید…",
    "processing":         "در حال پردازش…",
}


def t(key: str, **kwargs) -> str:
    """دریافت متن با جایگزینی placeholders"""
    template = TEXTS.get(key, "")
    if not template:
        return ""
    try:
        return template.format(**kwargs)
    except Exception:
        return template