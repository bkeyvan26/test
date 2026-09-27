# -*- coding: utf-8 -*-
"""K1 VMS — Data models"""
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from core.permissions import (
    ALL_PERMISSION_KEYS, DEFAULT_USER_PERMISSIONS, DEFAULT_ADMIN_PERMISSIONS
)


def new_uid() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class CameraConfig:
    uid: str = field(default_factory=new_uid)
    name: str = ""
    enabled: bool = True

    ip: str = ""
    port: int = 554
    user: str = "admin"
    password: str = ""
    rtsp_path_main: str = ""
    rtsp_path_sub: str = ""

    nvr_uid: str = ""
    nvr_name: str = "—"

    live_fps_grid: int = 2
    live_fps_single: int = 15
    live_quality_grid: str = "low"
    live_quality_single: str = "high"

    record_stream: str = "main"
    record_quality: str = "original"
    record_segment_minutes: int = 0     # 0 = استفاده از سراسری
    record_enabled_continuous: bool = False
    record_enabled_motion: bool = True
    record_path_override: str = ""

    sensitivity: int = 100
    min_area: int = 400
    detect_every: int = 3
    roi_regions: List[dict] = field(default_factory=list)

    ai_enabled: bool = False
    ai_filter_person_only: bool = False

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CameraConfig":
        c = CameraConfig()
        for k, v in d.items():
            if hasattr(c, k): setattr(c, k, v)
        if not c.uid: c.uid = new_uid()
        return c

    def has_connection_info(self) -> bool:
        return bool(self.ip and (self.rtsp_path_main or self.rtsp_path_sub))


@dataclass
class UserConfig:
    uid: str = field(default_factory=new_uid)
    username: str = ""
    full_name: str = ""
    password_hash: str = ""
    password_salt: str = ""
    is_admin: bool = False
    enabled: bool = True
    permissions: Dict[str, bool] = field(default_factory=dict)
    allowed_camera_uids: List[str] = field(default_factory=list)
    failed_attempts: int = 0
    locked_until: str = ""
    locked_by_admin: bool = False

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "UserConfig":
        u = UserConfig()
        for k, v in d.items():
            if hasattr(u, k): setattr(u, k, v)
        for key in ALL_PERMISSION_KEYS:
            if key not in u.permissions:
                u.permissions[key] = (
                    DEFAULT_ADMIN_PERMISSIONS.get(key, False) if u.is_admin
                    else DEFAULT_USER_PERMISSIONS.get(key, False)
                )
        return u

    def has(self, perm: str) -> bool:
        if self.is_admin: return True
        return bool(self.permissions.get(perm, False))

    def can_see_camera(self, cam: CameraConfig) -> bool:
        if self.is_admin: return True
        if not self.allowed_camera_uids: return True
        return cam.uid in self.allowed_camera_uids


@dataclass
class GlobalSettings:
    # مسیرهای ضبط
    recording_root: str = ""
    record_folder_by_camera: bool = True
    record_folder_by_day: bool = True
    record_folder_by_hour: bool = False

    # ⚙ فاز ۲ — ضبط
    record_enabled: bool = True
    record_segment_minutes: int = 5       # ۱ تا ۳۰
    retention_days: int = 7               # ۱ / ۳ / ۷ / ۱۴ / ۳۰
    record_format: str = ".ts"
    autostart_recording_on_boot: bool = True

    # آلارم
    alarm_duration_sec: int = 8
    cooldown_sec: int = 5

    # صدا
    sound_enabled: bool = True
    sound_volume: int = 80

    # پنجره
    always_on_top: bool = False
    autostart: bool = False

    language: str = "fa"

    # امنیت
    max_login_attempts: int = 5
    lock_duration_min: int = 15

    # MediaMTX
    mediamtx_enabled: bool = True

    # AI
    ai_enabled: bool = False

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "GlobalSettings":
        g = GlobalSettings()
        for k, v in d.items():
            if hasattr(g, k): setattr(g, k, v)
        # اعتبارسنجی
        g.record_segment_minutes = max(1, min(30, int(g.record_segment_minutes)))
        g.retention_days = max(1, min(365, int(g.retention_days)))
        if g.record_format not in (".ts", ".mp4"):
            g.record_format = ".ts"
        return g