# -*- coding: utf-8 -*-
"""K1 VMS — Recording Folder Organizer (real move, no junctions)

MediaMTX الزام دارد path فقط ASCII باشد (path = cam_xxx).
این ماژول فایل‌های بسته شده را از _data/cam_xxx به پوشه‌های
فارسی منتقل می‌کند (transfer واقعی، نه junction).

ساختار نهایی:
  continuous/
    نگهبانی/           ← NVR
      دوربین_1/         ← channel
        2026-10-07/
          13-14-47.ts
    داخلی/
      دوربین_1/
    حراست/              ← standalone
    _data/              ← پوشه فنی (مخفی، فایل‌های در حال نوشتن)
      cam_xxx/
        2026-10-07/
          (فقط فایل‌های اخیر)
"""
import os
import re
import sys
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from core.camera_manager import CameraManager
from core.nvr_manager import NVRManager
import config

IS_WIN = sys.platform.startswith("win")
CREATE_NO_WINDOW = 0x08000000

DATA_DIR_NAME = "_data"
# فایل‌هایی که کمتر از این مقدار از mtime گذشته، در حال نوشتن هستند
WRITE_GRACE_SEC = 90.0


def _sanitize_fs_name(name: str) -> str:
    if not name:
        return ""
    safe = re.sub(r'[<>:"/\\|?*]', "", name)
    safe = re.sub(r"\s+", "_", safe).strip("._ ")
    return safe[:60] or "unnamed"


def _camera_path_name(cam_uid: str) -> str:
    ascii_uid = re.sub(r"[^a-zA-Z0-9]", "", cam_uid or "")[:16]
    return f"cam_{ascii_uid}" if ascii_uid else "cam_unknown"


def _build_friendly_path(cam, nvr_names: dict) -> Optional[Path]:
    root = config.CONTINUOUS_DIR
    if cam.nvr_uid and cam.nvr_uid in nvr_names:
        nvr_name = _sanitize_fs_name(nvr_names[cam.nvr_uid]) or "NVR"
        nvr_folder = root / nvr_name
        m = re.search(r"دوربین\s+(\d+)", cam.name or "")
        if m:
            ch_label = f"دوربین_{m.group(1)}"
        else:
            ch_label = _sanitize_fs_name(cam.name) or cam.uid[:8]
        return nvr_folder / ch_label
    else:
        friendly = _sanitize_fs_name(cam.name) or cam.uid[:8]
        return root / friendly


def _hide_folder(path: Path):
    if not IS_WIN or not path.exists():
        return
    try:
        subprocess.run(
            ["attrib", "+h", str(path)],
            capture_output=True, timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def organize_recordings(log_fn=None) -> dict:
    """
    ★ انتقال واقعی فایل‌های بسته شده از _data/cam_xxx به پوشه فارسی.
    """
    if log_fn is None:
        log_fn = lambda msg: None

    cam_manager = CameraManager()
    nvr_manager = NVRManager.instance()
    nvr_names = {n.uid: n.name for n in nvr_manager.all()}

    root = config.CONTINUOUS_DIR
    data_root = root / DATA_DIR_NAME

    if not data_root.exists():
        return {"moved": 0, "skipped": 0, "failed": 0}

    # نقشه: cam_xxx → مسیر مرتب
    path_to_friendly = {}
    for cam in cam_manager.all():
        ascii_name = _camera_path_name(cam.uid)
        friendly = _build_friendly_path(cam, nvr_names)
        if friendly:
            path_to_friendly[ascii_name] = friendly

    cutoff = time.time() - WRITE_GRACE_SEC
    stats = {"moved": 0, "skipped": 0, "failed": 0}

    for cam_folder in list(data_root.iterdir()):
        if not cam_folder.is_dir():
            continue
        if cam_folder.name not in path_to_friendly:
            continue

        friendly = path_to_friendly[cam_folder.name]
        try:
            friendly.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_fn(f"[Organizer] mkdir failed {friendly}: {e}")
            stats["failed"] += 1
            continue

        for date_folder in list(cam_folder.iterdir()):
            if not date_folder.is_dir():
                continue
            target_date = friendly / date_folder.name
            try:
                target_date.mkdir(parents=True, exist_ok=True)
            except Exception:
                continue

            for ts_file in list(date_folder.glob("*.ts")):
                try:
                    # فایل در حال نوشتن؟
                    if ts_file.stat().st_mtime > cutoff:
                        stats["skipped"] += 1
                        continue
                    target = target_date / ts_file.name
                    if target.exists():
                        stats["skipped"] += 1
                        continue
                    shutil.move(str(ts_file), str(target))
                    stats["moved"] += 1
                except (PermissionError, OSError):
                    # فایل قفل است → skip
                    stats["skipped"] += 1
                except Exception:
                    stats["failed"] += 1

    # پاک کردن پوشه‌های خالی در _data
    try:
        for cam_folder in list(data_root.iterdir()):
            if not cam_folder.is_dir():
                continue
            for date_folder in list(cam_folder.iterdir()):
                if not date_folder.is_dir():
                    continue
                try:
                    if not any(date_folder.iterdir()):
                        date_folder.rmdir()
                except Exception:
                    pass
            try:
                if not any(cam_folder.iterdir()):
                    cam_folder.rmdir()
            except Exception:
                pass
    except Exception:
        pass

    return stats


class RecordingOrganizer(threading.Thread):
    """داون‌مون هر ۳۰ ثانیه فایل‌ها را منتقل می‌کند."""
    INTERVAL_SEC = 30

    def __init__(self):
        super().__init__(daemon=True, name="k1-recording-organizer")
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        # بار اول با تأخیر
        try:
            time.sleep(60)
            stats = organize_recordings(log_fn=lambda m: print(m))
            print(f"[Organizer] first run: {stats}")
        except Exception as e:
            print(f"[Organizer] error on first run: {e}")

        while not self._stop_event.wait(self.INTERVAL_SEC):
            try:
                stats = organize_recordings()
                if stats.get("moved", 0) > 0:
                    print(f"[Organizer] moved {stats['moved']} files "
                          f"(skipped={stats['skipped']})")
            except Exception as e:
                print(f"[Organizer] run error: {e}")


def migrate_old_recordings(log_fn=None):
    """
    انتقال پوشه‌های قدیمی (cam_xxx در ریشه) به _data.
    ★ پوشه‌های junction قبلی را هم حذف می‌کند.
    """
    if log_fn is None:
        log_fn = lambda msg: None

    root = config.CONTINUOUS_DIR
    data_root = root / DATA_DIR_NAME
    data_root.mkdir(parents=True, exist_ok=True)
    _hide_folder(data_root)

    moved = 0

    # ★ حذف junctionهای قدیمی (اسم فارسی که junction هستند)
    for p in list(root.iterdir()):
        if not p.is_dir():
            continue
        if p.name == DATA_DIR_NAME:
            continue
        if p.name.startswith("cam_"):
            # این پوشه واقعی است، منتقل کن
            try:
                target = data_root / p.name
                if target.exists():
                    for item in p.iterdir():
                        sub_target = target / item.name
                        if sub_target.exists():
                            continue
                        shutil.move(str(item), str(sub_target))
                    try:
                        p.rmdir()
                    except Exception:
                        pass
                    continue
                shutil.move(str(p), str(target))
                moved += 1
                log_fn(f"[Organizer] moved {p.name} → _data/")
            except Exception as e:
                log_fn(f"[Organizer] move failed for {p.name}: {e}")
            continue

        # حذف junction قدیمی (اگر اسم NVR یا دوربین است)
        # junction معمولاً حجم صفر و محتوای خالی دارد
        try:
            # بررسی اینکه آیا junction است
            if p.is_symlink() or _is_junction(p):
                try:
                    p.rmdir()
                    log_fn(f"[Organizer] removed old junction {p.name}")
                except Exception as e:
                    log_fn(f"[Organizer] junction remove failed {p.name}: {e}")
        except Exception:
            pass

    if moved:
        log_fn(f"[Organizer] migrated {moved} old folders to _data/")
    return moved


def _is_junction(path: Path) -> bool:
    """چک کردن اینکه آیا مسیر یک junction است."""
    if not IS_WIN:
        return False
    try:
        # junctionها با os.path.islink شناخته نمی‌شوند در ویندوز
        # ولی st_reparse_tag نشون می‌ده
        st = os.lstat(str(path))
        return bool(st.st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except Exception:
        return False