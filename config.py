# -*- coding: utf-8 -*-
"""K1Motion — تنظیمات مرکزی برنامه"""
import sys
from pathlib import Path

# --- اطلاعات برنامه ---
APP_NAME = "K1Motion"
APP_VERSION = "1.0.0"

# پوشه نصب برنامه (کنار main.py)
APP_DIR = Path(__file__).parent.resolve()

# ============================================================
# 📁 مسیرهای ذخیره‌سازی (روی درایو D:)
# ============================================================
# دو پوشه کاملاً جدا:
#   - data/       → تنظیمات، کاربران، لاگ (فایل‌های کوچک)
#   - recordings/ → فیلم‌های ضبط‌شده (فایل‌های بزرگ)

STORAGE_ROOT = Path(r"D:\recorde\k1Motion")
DATA_DIR = STORAGE_ROOT / "data"
RECORDINGS_DIR = STORAGE_ROOT / "recordings"

# ساخت خودکار پوشه‌ها
DATA_DIR.mkdir(parents=True, exist_ok=True)
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

# --- فایل‌های تنظیمات (داخل data) ---
SETTINGS_FILE = DATA_DIR / "settings.json"
USERS_FILE = DATA_DIR / "users.json"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
CAMERAS_FILE = DATA_DIR / "cameras.json"
LIVE_STATE_FILE = DATA_DIR / "live_state.json"

# --- پوشه‌های ضبط (داخل recordings) ---
MOTION_DIR = RECORDINGS_DIR / "motion"
CONTINUOUS_DIR = RECORDINGS_DIR / "continuous"
SNAPSHOTS_DIR = RECORDINGS_DIR / "snapshots"
EXPORTS_DIR = RECORDINGS_DIR / "exports"

MOTION_DIR.mkdir(parents=True, exist_ok=True)
CONTINUOUS_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 🎥 MediaMTX
# ============================================================
MEDIAMTX_EXE = APP_DIR / "mediamtx.exe"
MEDIAMTX_YML = APP_DIR / "mediamtx.yml"
MEDIAMTX_LOG = APP_DIR / "mediamtx.log"
MEDIAMTX_PID = APP_DIR / "mediamtx.pid"
MEDIAMTX_RTSP_PORT = 8554
MEDIAMTX_API_PORT = 9997
MEDIAMTX_WEBRTC_PORT = 8889
MEDIAMTX_HLS_PORT = 8888

MEDIAMTX_HOST = "127.0.0.1"
MEDIAMTX_API_URL = f"http://{MEDIAMTX_HOST}:{MEDIAMTX_API_PORT}"
MEDIAMTX_RTSP_URL = f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_RTSP_PORT}"

# ============================================================
# 📡 Live View — Routing
# ============================================================
# False → Live/Single مستقیم به دوربین وصل می‌شود (سریع‌تر، پیش‌فرض)
# True  → از طریق MediaMTX (اتصال کمتر به دوربین)
LIVE_USE_MEDIAMTX = False

# RTSP transport: 'udp' (سریع روی LAN) یا 'tcp' (پایدارتر)
# ★ Requirement 11: UDP پیش‌فرض
LIVE_RTSP_TRANSPORT = "udp"

# ★ Requirement 8: حداکثر تعداد readerهای Single همزمان
# (فقط برای دوربین‌های visible در grid؛ بقیه cold start می‌شوند)
LIVE_MAX_SINGLE_READERS = 6

# ★ Requirement 7: زمان idle قبل از بستن reader Single (ثانیه)
# 600 ثانیه = 10 دقیقه
LIVE_SINGLE_IDLE_SEC = 600

# ============================================================
# 🖥️ رابط کاربری
# ============================================================
IS_WINDOWS = sys.platform.startswith("win")
WINDOW_MIN_WIDTH = 1200
WINDOW_MIN_HEIGHT = 700

# ============================================================
# 📊 تشخیص حرکت
# ============================================================
SENSITIVITY_MAX = 400
SENSITIVITY_DEFAULT = 100

# ============================================================
# 🤖 AI (فاز ۴)
# ============================================================
AI_ENABLED_DEFAULT = False         # به صورت پیش‌فرض خاموش
AI_MODEL_PATH = APP_DIR / "models" / "nanodet.onnx"
AI_CONFIDENCE_THRESHOLD = 0.45
AI_INPUT_SIZE = 320                # وضوح ورودی AI (کوچک = سریع‌تر)

# ============================================================
# 🎨 رنگ‌های رابط کاربری
# ============================================================
COLOR_BG_DARK = "#0d1014"
COLOR_BG_PANEL = "#16191e"
COLOR_BG_CARD = "#1a1d23"
COLOR_ACCENT = "#16a085"
COLOR_ACCENT_HOVER = "#1abc9c"
COLOR_TEXT_PRIMARY = "#ecf0f1"
COLOR_TEXT_SECONDARY = "#8899a6"
COLOR_DANGER = "#e74c3c"
COLOR_WARNING = "#f39c12"