# -*- coding: utf-8 -*-
"""K1 VMS — Settings / Users / Audit persistence"""
import json, datetime, threading
from pathlib import Path
from typing import List, Optional

from core.models import CameraConfig, UserConfig, GlobalSettings
import config

_lock = threading.Lock()


# ---------- دوربین‌ها ----------
def load_cameras() -> List[CameraConfig]:
    with _lock:
        data = _load_json(config.SETTINGS_FILE, {})
        return [CameraConfig.from_dict(c) for c in data.get("cameras", [])]


def save_cameras(cameras: List[CameraConfig]):
    with _lock:
        data = _load_json(config.SETTINGS_FILE, {})
        data["cameras"] = [c.to_dict() for c in cameras]
        _save_json(config.SETTINGS_FILE, data)


# ---------- تنظیمات سراسری ----------
def load_settings() -> GlobalSettings:
    with _lock:
        data = _load_json(config.SETTINGS_FILE, {})
        g = GlobalSettings.from_dict(data.get("settings", {}))
        if not g.recording_root:
            g.recording_root = str(config.CONTINUOUS_DIR)
        return g


def save_settings(g: GlobalSettings):
    with _lock:
        data = _load_json(config.SETTINGS_FILE, {})
        data["settings"] = g.to_dict()
        _save_json(config.SETTINGS_FILE, data)


# ---------- کاربران ----------
def load_users() -> List[UserConfig]:
    with _lock:
        data = _load_json(config.USERS_FILE, {})
        return [UserConfig.from_dict(u) for u in data.get("users", [])]


def save_users(users: List[UserConfig]):
    with _lock:
        _save_json(config.USERS_FILE, {"users": [u.to_dict() for u in users]})


# ---------- Audit ----------
def append_audit(action: str, user: str = "-", details: Optional[dict] = None):
    try:
        entry = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "user": user,
            "action": action,
            "details": details or {},
        }
        with _lock:
            with open(config.AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[audit] {e}")


# ---------- helpers ----------
def _load_json(path: Path, default):
    if not path.exists(): return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[load] {path}: {e}"); return default


def _save_json(path: Path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except Exception as e:
        print(f"[save] {path}: {e}")