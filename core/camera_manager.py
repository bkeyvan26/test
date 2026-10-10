# -*- coding: utf-8 -*-
"""K1 VMS — Camera Manager (persistent list of cameras)

Phase 8.0:
  - find_by_nvr_channel برای جلوگیری از تکرار کانال‌های NVR
  - بدون auto-cleanup (دوربین‌ها فقط با دستور کاربر حذف می‌شوند)
"""
import json
import re
import threading
from pathlib import Path
from typing import List, Optional

from core.models import CameraConfig, new_uid
import config


CAMERAS_FILE = config.DATA_DIR / "cameras.json"


class CameraManager:
    """مدیر دوربین‌ها — Singleton با data persistence."""
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._cameras: List[CameraConfig] = []
        self._load()
        self._initialized = True

    # ============================================================
    # IO
    # ============================================================
    def _load(self):
        if not CAMERAS_FILE.exists():
            self._cameras = []
            return
        try:
            data = json.loads(CAMERAS_FILE.read_text(encoding="utf-8"))
            items = data.get("cameras") if isinstance(data, dict) else data
            self._cameras = [CameraConfig.from_dict(d) for d in (items or [])]
            print(f"[CameraManager] loaded {len(self._cameras)} camera(s)")
        except Exception as e:
            print(f"[CameraManager] load failed: {e}")
            self._cameras = []

    def _save(self):
        try:
            payload = {"cameras": [c.to_dict() for c in self._cameras]}
            CAMERAS_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[CameraManager] save failed: {e}")

    # ============================================================
    # Public API
    # ============================================================
    def reload(self):
        """بازخوانی از دیسک."""
        with self._lock:
            self._load()

    def save(self):
        with self._lock:
            self._save()

    def all(self) -> List[CameraConfig]:
        with self._lock:
            return list(self._cameras)

    def get(self, uid: str) -> Optional[CameraConfig]:
        with self._lock:
            for c in self._cameras:
                if c.uid == uid:
                    return c
            return None

    def add(self, cam: CameraConfig) -> CameraConfig:
        with self._lock:
            if not cam.uid:
                cam.uid = new_uid()
            self._cameras.append(cam)
            self._save()
            return cam

    def update(self, uid: str, cam: CameraConfig):
        with self._lock:
            for i, c in enumerate(self._cameras):
                if c.uid == uid:
                    cam.uid = uid
                    self._cameras[i] = cam
                    self._save()
                    return

    def delete(self, uid: str):
        with self._lock:
            self._cameras = [c for c in self._cameras if c.uid != uid]
            self._save()

    def delete_many(self, uids: List[str]):
        with self._lock:
            s = set(uids)
            self._cameras = [c for c in self._cameras if c.uid not in s]
            self._save()

    def count(self) -> int:
        with self._lock:
            return len(self._cameras)

    def all_uids(self) -> List[str]:
        with self._lock:
            return [c.uid for c in self._cameras]

    def find_by_name(self, name: str) -> Optional[CameraConfig]:
        with self._lock:
            for c in self._cameras:
                if c.name == name:
                    return c
            return None

    # ============================================================
    # NVR-related helpers
    # ============================================================
    def cameras_of_nvr(self, nvr_uid: str) -> List[CameraConfig]:
        with self._lock:
            return [c for c in self._cameras if c.nvr_uid == nvr_uid]

    def find_by_nvr_channel(self, nvr_uid: str,
                            channel_id: int) -> Optional[CameraConfig]:
        """
        پیدا کردن دوربین با nvr_uid و شماره کانال مشخص.
        برای جلوگیری از افزودن تکراری کانال‌های NVR.
        """
        if not nvr_uid or channel_id <= 0:
            return None
        with self._lock:
            for c in self._cameras:
                if c.nvr_uid != nvr_uid:
                    continue
                m = re.search(r"دوربین\s+(\d+)\s*$", c.name or "")
                if m and int(m.group(1)) == channel_id:
                    return c
            return None