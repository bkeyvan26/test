# -*- coding: utf-8 -*-
"""K1Motion — مدیریت دوربین‌ها"""
from typing import List, Optional
from core.models import CameraConfig
from core import settings_manager


class CameraManager:
    """مدیریت لیست دوربین‌ها با ذخیره‌سازی خودکار"""

    def __init__(self):
        self._cameras: List[CameraConfig] = []
        self.reload()

    # ---- بارگذاری ----
    def reload(self):
        self._cameras = settings_manager.load_cameras()

    def _persist(self):
        settings_manager.save_cameras(self._cameras)

    # ---- خواندن ----
    def all(self) -> List[CameraConfig]:
        return list(self._cameras)

    def get(self, uid: str) -> Optional[CameraConfig]:
        for c in self._cameras:
            if c.uid == uid:
                return c
        return None

    def count(self) -> int:
        return len(self._cameras)

    def enabled(self) -> List[CameraConfig]:
        return [c for c in self._cameras if c.enabled]

    # ---- نوشتن ----
    def add(self, cam: CameraConfig) -> CameraConfig:
        """افزودن دوربین جدید"""
        self._cameras.append(cam)
        self._persist()
        return cam

    def update(self, uid: str, new_cam: CameraConfig) -> bool:
        """ویرایش دوربین"""
        for i, c in enumerate(self._cameras):
            if c.uid == uid:
                new_cam.uid = uid    # UID حفظ شود
                self._cameras[i] = new_cam
                self._persist()
                return True
        return False

    def delete(self, uid: str) -> bool:
        """حذف دوربین"""
        for i, c in enumerate(self._cameras):
            if c.uid == uid:
                del self._cameras[i]
                self._persist()
                return True
        return False

    def set_enabled(self, uid: str, enabled: bool) -> bool:
        """فعال/غیرفعال کردن دوربین"""
        c = self.get(uid)
        if c is None:
            return False
        c.enabled = enabled
        self._persist()
        return True

    # ---- جستجو ----
    def find_by_name(self, name: str) -> Optional[CameraConfig]:
        for c in self._cameras:
            if c.name == name:
                return c
        return None