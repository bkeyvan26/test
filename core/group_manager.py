# -*- coding: utf-8 -*-
"""K1 VMS — Custom Camera Groups Manager"""
import json
import threading
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field, asdict

from core.models import new_uid
import config


GROUP_FILE = config.DATA_DIR / "groups.json"


@dataclass
class CameraGroup:
    uid: str = field(default_factory=new_uid)
    name: str = ""
    camera_uids: List[str] = field(default_factory=list)
    color: str = "#f39c12"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CameraGroup":
        g = CameraGroup()
        for k, v in (d or {}).items():
            if hasattr(g, k):
                setattr(g, k, v)
        if not isinstance(g.camera_uids, list):
            g.camera_uids = []
        if not g.uid:
            g.uid = new_uid()
        return g


class GroupManager:
    _instance = None
    _lock = threading.RLock()

    @classmethod
    def instance(cls) -> "GroupManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._groups: List[CameraGroup] = []
        self._load()

    def _load(self):
        if not GROUP_FILE.exists():
            self._groups = []
            return
        try:
            data = json.loads(GROUP_FILE.read_text(encoding="utf-8"))
            items = data.get("groups") if isinstance(data, dict) else data
            self._groups = [CameraGroup.from_dict(d) for d in (items or [])]
            print(f"[GroupManager] loaded {len(self._groups)} group(s)")
        except Exception as e:
            print(f"[GroupManager] load failed: {e}")
            self._groups = []

    def _save(self):
        try:
            payload = {"groups": [g.to_dict() for g in self._groups]}
            GROUP_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[GroupManager] save failed: {e}")

    # ---------- CRUD ----------
    def all(self) -> List[CameraGroup]:
        return list(self._groups)

    def get(self, uid: str) -> Optional[CameraGroup]:
        for g in self._groups:
            if g.uid == uid:
                return g
        return None

    def add(self, grp: CameraGroup) -> CameraGroup:
        if not grp.uid:
            grp.uid = new_uid()
        self._groups.append(grp)
        self._save()
        return grp

    def update(self, uid: str, grp: CameraGroup):
        for i, g in enumerate(self._groups):
            if g.uid == uid:
                grp.uid = uid
                self._groups[i] = grp
                self._save()
                return

    def delete(self, uid: str):
        self._groups = [g for g in self._groups if g.uid != uid]
        self._save()

    def remove_camera(self, cam_uid: str):
        """حذف دوربین از همه‌ی گروه‌ها (وقتی دوربین حذف می‌شود)."""
        changed = False
        for g in self._groups:
            if cam_uid in g.camera_uids:
                g.camera_uids = [u for u in g.camera_uids if u != cam_uid]
                changed = True
        if changed:
            self._save()

    def groups_containing(self, cam_uid: str) -> List[CameraGroup]:
        return [g for g in self._groups if cam_uid in g.camera_uids]

    def count(self) -> int:
        return len(self._groups)