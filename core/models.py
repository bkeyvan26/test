# -*- coding: utf-8 -*-
"""K1 VMS — Data models (Phase B: 4-role stream architecture)"""
import uuid
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from core.permissions import (
    ALL_PERMISSION_KEYS, DEFAULT_USER_PERMISSIONS, DEFAULT_ADMIN_PERMISSIONS
)


# ============================================================
# Stream profile roles (Phase B: 4 roles)
# ============================================================
ROLE_RECORDING = "RECORDING"    # ضبط دائم → MediaMTX (main feed)
ROLE_MOTION    = "MOTION"       # تشخیص حرکت → Motion Detector
ROLE_LIVE_VIEW = "LIVE_VIEW"    # پخش لایو (نمای تکی)
ROLE_GRID      = "GRID"         # نمای شبکه (چند کاشی)

ALL_ROLES = [ROLE_RECORDING, ROLE_MOTION, ROLE_LIVE_VIEW, ROLE_GRID]


def new_uid() -> str:
    return uuid.uuid4().hex[:12]


# ============================================================
# StreamProfile
# ============================================================
@dataclass
class StreamProfile:
    """
    یک استریم RTSP که دوربین ارائه می‌دهد.
    Metadata واقعی از ONVIF / camera query خوانده می‌شود.
    """
    id: str = ""
    name: str = ""
    rtsp_path: str = ""
    url: str = ""
    resolution: str = ""       # "1920x1080" — فقط برای نمایش سریع
    width: int = 0
    height: int = 0
    codec: str = ""            # "H264", "H265", "MJPEG", "MPEG4"
    fps: int = 0               # ★ Source FPS واقعی دوربین
    bitrate_kbps: int = 0
    roles: List[str] = field(default_factory=list)
    discovered_by: str = ""    # "onvif" | "manual" | "legacy" | "guess"

    # ---- ONVIF / PTZ metadata (Phase G آماده‌سازی) ----
    onvif_profile_token: str = ""    # token برای عملیات PTZ
    has_ptz: bool = False            # pan/tilt/zoom کامل
    has_zoom_only: bool = False      # فقط زوم اپتیکال
    has_presets: bool = False        # دارای preset position

    # ---------- serialization ----------
    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "StreamProfile":
        p = StreamProfile()
        for k, v in (d or {}).items():
            if hasattr(p, k):
                setattr(p, k, v)
        if not isinstance(p.roles, list):
            p.roles = []
        # normalize numeric fields
        try:
            p.width = int(p.width or 0)
            p.height = int(p.height or 0)
            p.fps = int(p.fps or 0)
            p.bitrate_kbps = int(p.bitrate_kbps or 0)
        except Exception:
            pass
        # regenerate resolution if needed
        if not p.resolution and p.width and p.height:
            p.resolution = f"{p.width}x{p.height}"
        return p

    # ---------- helpers ----------
    def has_role(self, role: str) -> bool:
        return role in (self.roles or [])

    def add_role(self, role: str):
        if not self.has_role(role):
            self.roles = list(self.roles or []) + [role]

    def remove_role(self, role: str):
        self.roles = [r for r in (self.roles or []) if r != role]

    def resolution_or(self, fallback: str = "—") -> str:
        if self.resolution:
            return self.resolution
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return fallback

    def pixel_count(self) -> int:
        return int(self.width or 0) * int(self.height or 0)

    def summary(self) -> str:
        parts = []
        res = self.resolution_or("")
        if res:
            parts.append(res)
        if self.codec:
            parts.append(self.codec.upper())
        if self.fps:
            parts.append(f"{self.fps}fps")
        return " • ".join(parts) if parts else "—"


# ============================================================
# CameraConfig (Phase B)
# ============================================================
@dataclass
class CameraConfig:
    # ---- identity ----
    uid: str = field(default_factory=new_uid)
    name: str = ""
    enabled: bool = True

    # ---- connection ----
    ip: str = ""
    port: int = 554
    user: str = "admin"
    password: str = ""

    # ---- legacy RTSP fields (auto-migrated to profiles) ----
    rtsp_path_main: str = ""
    rtsp_path_sub: str = ""

    # ---- NVR ----
    nvr_uid: str = ""
    nvr_name: str = "—"

    # ============================================================
    # Stream Profiles (Phase 3)
    # ============================================================
    stream_profiles: List[StreamProfile] = field(default_factory=list)

    # ------------------------------------------------------------
    # ★ FOUR INDEPENDENT ROLES (Phase B)
    # ------------------------------------------------------------
    # ضبط → MediaMTX (کاملاً مستقل از LiveStreamManager)
    recording_profile_id: str = ""

    # تشخیص حرکت → Motion Detector (Phase 4)
    motion_profile_id: str = ""

    # پخش لایو → Single View
    live_profile_id: str = ""

    # ★ NEW — نمای شبکه → Grid
    grid_profile_id: str = ""

    # ------------------------------------------------------------
    # ★ CONSUMER FPS (0 = auto از profile.fps)
    # ------------------------------------------------------------
    # Recording: FPS = source fps (MediaMTX re-encode نمی‌کند)
    #   این فیلد فقط برای اطلاع/نمایش است — مقدار 0 یعنی "از دوربین"
    recording_fps_note: int = 0     # خوانده می‌شود از recording_profile.fps

    # Live Display FPS (Single)
    live_display_fps: int = 0       # 0 = auto (از live_profile.fps)

    # Grid Display FPS
    grid_display_fps: int = 0       # 0 = auto (از grid_profile.fps)
    # ★ Transport: "auto" | "tcp" | "udp"
    #   auto → در کد تصمیم گرفته می‌شود (بر اساس bitrate)
    #   tcp  → اجبار به TCP (پایدارتر، CPU بالاتر)
    #   udp  → اجبار به UDP (سریع‌تر، ریسک packet loss)
    live_transport: str = "auto"
    grid_transport: str = "auto"
    # Motion Processing FPS (ممکن است کمتر از motion_profile.fps)
    motion_processing_fps: int = 0  # 0 = auto (از motion_profile.fps)

    # ------------------------------------------------------------
    # ★ CONSUMER RESOLUTION OVERRIDE ("" = auto از profile)
    # ------------------------------------------------------------
    live_resolution_override: str = ""   # مثلاً "1920x1080" یا ""
    grid_resolution_override: str = ""   # مثلاً "640x360" یا ""

    # ------------------------------------------------------------
    # Recording settings
    # ------------------------------------------------------------
    record_enabled_continuous: bool = False
    record_enabled_motion: bool = True
    record_segment_minutes: int = 0     # 0 = استفاده از سراسری
    record_path_override: str = ""

    # ------------------------------------------------------------
    # Motion detection settings
    # ------------------------------------------------------------
    sensitivity: int = 100
    min_area: int = 400
    detect_every: int = 3
    roi_regions: List[dict] = field(default_factory=list)
    motion_pre_buffer_sec: int = 5
    motion_post_buffer_sec: int = 5

    # ------------------------------------------------------------
    # AI
    # ------------------------------------------------------------
    ai_enabled: bool = False
    ai_filter_person_only: bool = False

    # ============================================================
    # Serialization
    # ============================================================
    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CameraConfig":
        c = CameraConfig()
        d = dict(d or {})

        # Extract stream_profiles first
        raw_profiles = d.pop("stream_profiles", None)

        # ---- Migration map: old field name → new field name ----
        # Old fields that were removed in Phase B
        OLD_TO_NEW = {
            "live_fps_single": "live_display_fps",
            "live_fps_grid": "grid_display_fps",
        }

        for k, v in d.items():
            # Apply rename if old name
            new_k = OLD_TO_NEW.get(k, k)
            if hasattr(c, new_k):
                setattr(c, new_k, v)
            elif k == "live_resolution_single":
                # Old value "auto" → new ""
                if isinstance(v, str) and v != "auto":
                    c.live_resolution_override = v
            elif k == "live_resolution_grid":
                if isinstance(v, str) and v != "auto":
                    c.grid_resolution_override = v

        # ---- Load profiles ----
        if raw_profiles:
            for pd in raw_profiles:
                if isinstance(pd, dict):
                    c.stream_profiles.append(StreamProfile.from_dict(pd))
                elif isinstance(pd, StreamProfile):
                    c.stream_profiles.append(pd)

        if not c.uid:
            c.uid = new_uid()

        # ---- Legacy → profiles migration ----
        c.migrate_legacy_to_profiles()

        # ---- Ensure role defaults (including grid) ----
        c._ensure_profile_defaults()

        return c

    # ============================================================
    # Migration
    # ============================================================
    def migrate_legacy_to_profiles(self):
        """اگر stream_profiles خالی باشد، از rtsp_path_* بسازد."""
        if not self.stream_profiles:
            if self.rtsp_path_main:
                self.stream_profiles.append(StreamProfile(
                    id="main",
                    name="Main Stream",
                    rtsp_path=self.rtsp_path_main,
                    url=self._build_url(self.rtsp_path_main),
                    roles=[ROLE_RECORDING, ROLE_LIVE_VIEW],
                    discovered_by="legacy",
                ))
            if self.rtsp_path_sub:
                self.stream_profiles.append(StreamProfile(
                    id="sub",
                    name="Sub Stream",
                    rtsp_path=self.rtsp_path_sub,
                    url=self._build_url(self.rtsp_path_sub),
                    roles=[ROLE_MOTION, ROLE_GRID, ROLE_LIVE_VIEW],
                    discovered_by="legacy",
                ))

    def _ensure_profile_defaults(self):
        """اطمینان از اینکه همه 4 role یک profile پیش‌فرض دارند."""
        if not self.stream_profiles:
            return

        # ---- Recording ----
        if not self.recording_profile_id:
            for p in self.stream_profiles:
                if p.has_role(ROLE_RECORDING):
                    self.recording_profile_id = p.id
                    break
            if not self.recording_profile_id:
                # Prefer highest resolution
                highest = max(
                    self.stream_profiles,
                    key=lambda p: p.pixel_count() or 0
                )
                self.recording_profile_id = highest.id

        # ---- Motion ----
        if not self.motion_profile_id:
            for p in self.stream_profiles:
                if p.has_role(ROLE_MOTION):
                    self.motion_profile_id = p.id
                    break
            if not self.motion_profile_id:
                # Prefer lowest resolution (Sub) for CPU
                lowest = min(
                    self.stream_profiles,
                    key=lambda p: p.pixel_count() or 999999999
                )
                self.motion_profile_id = lowest.id

        # ---- Live ----
        if not self.live_profile_id:
            for p in self.stream_profiles:
                if p.has_role(ROLE_LIVE_VIEW):
                    self.live_profile_id = p.id
                    break
            if not self.live_profile_id:
                # Prefer recording profile (usually Main)
                self.live_profile_id = (self.recording_profile_id
                                        or self.stream_profiles[0].id)

        # ---- Grid (NEW) ----
        if not self.grid_profile_id:
            for p in self.stream_profiles:
                if p.has_role(ROLE_GRID):
                    self.grid_profile_id = p.id
                    break
            if not self.grid_profile_id:
                # Fallback: motion profile (usually Sub), else lowest res
                if self.motion_profile_id:
                    self.grid_profile_id = self.motion_profile_id
                elif len(self.stream_profiles) > 1:
                    lowest = min(
                        self.stream_profiles,
                        key=lambda p: p.pixel_count() or 999999999
                    )
                    self.grid_profile_id = lowest.id
                else:
                    self.grid_profile_id = self.stream_profiles[0].id

    # ============================================================
    # Helpers
    # ============================================================
    def _build_url(self, path: str) -> str:
        if not path:
            return ""
        if path.startswith("rtsp://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        u = urllib.parse.quote(self.user or "", safe="")
        pw = urllib.parse.quote(self.password or "", safe="")
        return f"rtsp://{u}:{pw}@{self.ip}:{self.port}{path}"

    def get_profile_by_id(self, pid: str) -> Optional[StreamProfile]:
        if not pid:
            return None
        for p in self.stream_profiles:
            if p.id == pid:
                return p
        return None

    # ---- role-specific getters ----
    def get_recording_profile(self) -> Optional[StreamProfile]:
        return self.get_profile_by_id(self.recording_profile_id)

    def get_motion_profile(self) -> Optional[StreamProfile]:
        return self.get_profile_by_id(self.motion_profile_id)

    def get_live_profile(self) -> Optional[StreamProfile]:
        return self.get_profile_by_id(self.live_profile_id)

    def get_grid_profile(self) -> Optional[StreamProfile]:
        return self.get_profile_by_id(self.grid_profile_id)

    # ---- role-specific URLs ----
    def get_recording_url(self) -> str:
        p = self.get_recording_profile()
        if p and p.url:
            return p.url
        return self._build_url(self.rtsp_path_main) if self.rtsp_path_main else ""

    def get_motion_url(self) -> str:
        p = self.get_motion_profile()
        if p and p.url:
            return p.url
        path = self.rtsp_path_sub or self.rtsp_path_main
        return self._build_url(path) if path else ""

    def get_live_url(self) -> str:
        p = self.get_live_profile()
        if p and p.url:
            return p.url
        return self._build_url(self.rtsp_path_main) if self.rtsp_path_main else ""

    def get_grid_url(self) -> str:
        p = self.get_grid_profile()
        if p and p.url:
            return p.url
        path = self.rtsp_path_sub or self.rtsp_path_main
        return self._build_url(path) if path else ""

    # ---- role FPS getters (source fps از profile) ----
    def get_recording_source_fps(self) -> int:
        p = self.get_recording_profile()
        return int(p.fps) if p else 0

    def get_motion_source_fps(self) -> int:
        p = self.get_motion_profile()
        return int(p.fps) if p else 0

    def get_live_source_fps(self) -> int:
        p = self.get_live_profile()
        return int(p.fps) if p else 0

    def get_grid_source_fps(self) -> int:
        p = self.get_grid_profile()
        return int(p.fps) if p else 0

    # ---- consumer FPS (0 = auto from source) ----
    def effective_live_fps(self) -> int:
        if self.live_display_fps > 0:
            return self.live_display_fps
        return self.get_live_source_fps() or 15

    def effective_grid_fps(self) -> int:
        if self.grid_display_fps > 0:
            return self.grid_display_fps
        return self.get_grid_source_fps() or 5

    def effective_motion_fps(self) -> int:
        if self.motion_processing_fps > 0:
            return self.motion_processing_fps
        return self.get_motion_source_fps() or 5

    # ---- PTZ helper ----
    def supports_ptz(self) -> bool:
        """اگر هر کدام از profileها PTZ داشته باشند."""
        return any(
            (p.has_ptz or p.has_zoom_only) for p in self.stream_profiles
        )

    def ptz_profile(self) -> Optional[StreamProfile]:
        for p in self.stream_profiles:
            if p.has_ptz or p.has_zoom_only:
                return p
        return None

    # ---- connection check ----
    def has_connection_info(self) -> bool:
        return bool(self.ip and (
            self.rtsp_path_main or self.rtsp_path_sub or self.stream_profiles
        ))


# ============================================================
# UserConfig — unchanged
# ============================================================
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


# ============================================================
# GlobalSettings — unchanged
# ============================================================
@dataclass
class GlobalSettings:
    recording_root: str = ""
    record_folder_by_camera: bool = True
    record_folder_by_day: bool = True
    record_folder_by_hour: bool = False

    record_enabled: bool = True
    record_segment_minutes: int = 5
    retention_days: int = 7
    record_format: str = ".ts"
    autostart_recording_on_boot: bool = True

    alarm_duration_sec: int = 8
    cooldown_sec: int = 5

    sound_enabled: bool = True
    sound_volume: int = 80

    always_on_top: bool = False
    autostart: bool = False
    language: str = "fa"

    max_login_attempts: int = 5
    lock_duration_min: int = 15

    mediamtx_enabled: bool = True
    ai_enabled: bool = False

    motion_pre_buffer_sec: int = 5
    motion_post_buffer_sec: int = 5

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "GlobalSettings":
        g = GlobalSettings()
        for k, v in (d or {}).items():
            if hasattr(g, k): setattr(g, k, v)
        g.record_segment_minutes = max(1, min(30, int(g.record_segment_minutes)))
        g.retention_days = max(1, min(365, int(g.retention_days)))
        if g.record_format not in (".ts", ".mp4"):
            g.record_format = ".ts"
        return g
