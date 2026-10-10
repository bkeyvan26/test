# -*- coding: utf-8 -*-
"""بازیابی دوربین‌ها از mediamtx.yml"""
import re
import json
import urllib.parse
from pathlib import Path


YML = Path(r"D:\k1motion\mediamtx.yml")
CAMERAS_FILE = Path(r"D:\recorde\k1Motion\data\cameras.json")


# ★ نام‌های اصلی (از لاگ قبلی)
NAME_MAP = {
    "cam_3059f25c": "حراست",
    "cam_5064cd2c": "تست",
    "cam_1e437edf": "فناوری",
    "cam_4ccffac5": "تست2",
    "cam_794a5911": "تست3",
    "cam_0223960a": "تست4",
    "cam_676bf0fe": "خروجی طالقانی",
    "cam_72c8973a": "ورودی طالقانی",
    "cam_f7c077dd": "آسانسور",
}


def main():
    if not YML.exists():
        print(f"❌ {YML} پیدا نشد")
        return

    text = YML.read_text(encoding="utf-8")

    # پارس yml
    paths = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^  ([A-Za-z0-9_\-]+):\s*$", line)
        if m:
            current = m.group(1)
            paths[current] = {"source": "", "record": False}
            continue
        if current is None:
            continue
        m = re.match(r'^\s+source:\s*"([^"]+)"', line)
        if m:
            paths[current]["source"] = m.group(1)
            continue
        m = re.match(r"^\s+record:\s*(\w+)", line)
        if m:
            paths[current]["record"] = (m.group(1).lower() == "yes")

    print(f"پیدا شد {len(paths)} path در mediamtx.yml")

    # فیلتر: حذف paths که فقط عددی هستند (NVR orphan) و _sub
    valid = {}
    for name, info in paths.items():
        src = info.get("source", "")
        if not src or not src.startswith("rtsp://"):
            continue
        if name.endswith("_sub"):
            continue
        if re.fullmatch(r"\d+", name):
            print(f"  رد شد (NVR orphan): {name}")
            continue
        valid[name] = info

    print(f"\n{len(valid)} دوربین معتبر:")
    for name in valid:
        print(f"  - {name}  ({NAME_MAP.get(name, '?')})")

    # ساخت cameras.json
    cameras = []
    for name, info in valid.items():
        src = info["source"]
        # استخراج credentials
        m = re.match(r"rtsp://([^:]+):([^@]+)@([^:/]+):(\d+)(.*)", src)
        if not m:
            print(f"  ⚠ {name}: URL parse failed")
            continue
        user, pwd_enc, ip, port, path = m.groups()
        pwd = urllib.parse.unquote(pwd_enc)

        # نام مناسب
        cam_name = NAME_MAP.get(name, name)
        # استخراج uid از نام path
        if name.startswith("cam_"):
            uid_short = name[4:]
        else:
            uid_short = name

        cam = {
            "uid": f"cam_{uid_short}",
            "name": cam_name,
            "enabled": True,
            "ip": ip,
            "port": int(port),
            "user": user,
            "password": pwd,
            "rtsp_path_main": path,
            "rtsp_path_sub": "",
            "nvr_uid": "",
            "nvr_name": "—",
            "stream_profiles": [
                {
                    "id": "main",
                    "name": "Main",
                    "rtsp_path": path,
                    "url": src,
                    "resolution": "",
                    "width": 0,
                    "height": 0,
                    "codec": "",
                    "fps": 0,
                    "bitrate_kbps": 0,
                    "roles": ["RECORDING", "LIVE_VIEW"],
                    "discovered_by": "restore",
                    "onvif_profile_token": "",
                    "has_ptz": False,
                    "has_zoom_only": False,
                    "has_presets": False,
                }
            ],
            "recording_profile_id": "main",
            "motion_profile_id": "main",
            "live_profile_id": "main",
            "grid_profile_id": "main",
            "recording_fps_note": 0,
            "live_display_fps": 0,
            "grid_display_fps": 0,
            "live_transport": "auto",
            "grid_transport": "auto",
            "motion_processing_fps": 0,
            "live_resolution_override": "",
            "grid_resolution_override": "",
            "record_enabled_continuous": True,
            "record_enabled_motion": False,
            "record_segment_minutes": 0,
            "record_path_override": "",
            "sensitivity": 100,
            "min_area": 400,
            "detect_every": 3,
            "roi_regions": [],
            "motion_pre_buffer_sec": 5,
            "motion_post_buffer_sec": 5,
            "ai_enabled": False,
            "ai_filter_person_only": False,
        }
        cameras.append(cam)

    if not cameras:
        print("\n❌ هیچ دوربینی برای بازیابی پیدا نشد")
        return

    # backup از cameras فعلی
    if CAMERAS_FILE.exists():
        backup = CAMERAS_FILE.with_suffix(".json.pre_restore")
        try:
            backup.write_text(
                CAMERAS_FILE.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            print(f"\nبکاپ قبلی: {backup}")
        except Exception:
            pass

    # ذخیره
    CAMERAS_FILE.write_text(
        json.dumps({"cameras": cameras}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n✅ {len(cameras)} دوربین بازیابی شد به {CAMERAS_FILE}")
    print("حالا برنامه را اجرا کنید.")


if __name__ == "__main__":
    main()