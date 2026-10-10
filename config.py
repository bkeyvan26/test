# -*- coding: utf-8 -*-
"""K1Motion — تنظیمات مرکزی برنامه"""
import sys
from pathlib import Path

APP_NAME = "K1Motion"
APP_VERSION = "1.0.0"
APP_DIR = Path(__file__).parent.resolve()

STORAGE_ROOT = Path(r"D:\recorde\k1Motion")
DATA_DIR = STORAGE_ROOT / "data"
RECORDINGS_DIR = STORAGE_ROOT / "recordings"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.json"
USERS_FILE = DATA_DIR / "users.json"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
CAMERAS_FILE = DATA_DIR / "cameras.json"
LIVE_STATE_FILE = DATA_DIR / "live_state.json"

MOTION_DIR = RECORDINGS_DIR / "motion"
CONTINUOUS_DIR = RECORDINGS_DIR / "continuous"
SNAPSHOTS_DIR = RECORDINGS_DIR / "snapshots"
EXPORTS_DIR = RECORDINGS_DIR / "exports"
for _p in (MOTION_DIR, CONTINUOUS_DIR, SNAPSHOTS_DIR, EXPORTS_DIR):
    _p.mkdir(parents=True, exist_ok=True)

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
# 📡 Live View
# ============================================================
LIVE_USE_MEDIAMTX = False
LIVE_RTSP_TRANSPORT = "udp"

LIVE_MAX_SINGLE_READERS = 1
LIVE_SINGLE_IDLE_SEC = 600

LIVE_PREWARM_COALESCE_MS = 0
LIVE_PREWARM_STAGGER_MS = 0
LIVE_PREWARM_STUCK_SEC = 30
LIVE_PREWARM_CONCURRENCY = 2
LIVE_PREWARM_TOP_N = 0

LIVE_SINGLE_FPS_CAP = 15

# ★★★ Focus Mode (Phase 6.7) ★★★
# وقتی روی یک دوربین دابل‌کلیک می‌کنی (Single)، بقیه Grid readerها
# موقتاً متوقف می‌شوند تا CPU برای دوربین focused آزاد شود.
# با خروج از Single، خودکار برمی‌گردند.
LIVE_FOCUS_OPTIMIZE = True

# ★★★ Grid Budget — محدودیت decoder فعال در Live Grid ★★★
# اگر تعداد کاشی‌های Live از این عدد بیشتر شد، decoderهای پشت‌تر
# متوقف می‌شوند تا CPU و RAM آزاد شود. ۰ = بدون محدودیت.
LIVE_GRID_MAX_READERS = 16

# ============================================================
# ★ Transport — 3-level priority:
#   1. per-camera (Camera Dialog)  ← highest
#   2. this file (global)          ← middle
#   3. auto based on bitrate       ← lowest
# Values: "auto" | "tcp" | "udp"
# ============================================================
LIVE_SINGLE_FORCE_TCP = "tcp"
LIVE_GRID_FORCE_TCP   = "tcp"
LIVE_GRID_TCP_BITRATE_THRESHOLD = 2000   # kbps

# ============================================================
# ★ Auto-Sub (Smart)
# ============================================================
LIVE_AUTO_SUB_ENABLED = True
LIVE_AUTO_SUB_ABOVE_MP = 5.0
LIVE_AUTO_SUB_MIN_RATIO = 4.0

# ============================================================
# 🖥️ UI
# ============================================================
IS_WINDOWS = sys.platform.startswith("win")
WINDOW_MIN_WIDTH = 1200
WINDOW_MIN_HEIGHT = 700

SENSITIVITY_MAX = 400
SENSITIVITY_DEFAULT = 100

AI_ENABLED_DEFAULT = False
AI_MODEL_PATH = APP_DIR / "models" / "nanodet.onnx"
AI_CONFIDENCE_THRESHOLD = 0.45
AI_INPUT_SIZE = 320

COLOR_BG_DARK = "#0d1014"
COLOR_BG_PANEL = "#16191e"
COLOR_BG_CARD = "#1a1d23"
COLOR_ACCENT = "#16a085"
COLOR_ACCENT_HOVER = "#1abc9c"
COLOR_TEXT_PRIMARY = "#ecf0f1"
COLOR_TEXT_SECONDARY = "#8899a6"
COLOR_DANGER = "#e74c3c"
COLOR_WARNING = "#f39c12"