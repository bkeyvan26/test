# -*- coding: utf-8 -*-
"""K1 VMS — Camera Diagnostic (run: python debug_cameras.py)"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from core.camera_manager import CameraManager
from core import settings_manager
from core.nvr_engine import MediaMTXConfigBuilder, _camera_path_name


print("=" * 62)
print("  K1 VMS — Camera Diagnostic")
print("=" * 62)

# ============ 1. Settings file ============
print("\n[1] Settings file")
print(f"    Path: {config.SETTINGS_FILE}")
print(f"    Exists: {config.SETTINGS_FILE.exists()}")

if config.SETTINGS_FILE.exists():
    try:
        raw = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
        print(f"    Top-level keys: {list(raw.keys())}")
        print(f"    Cameras in file: {len(raw.get('cameras', []))}")
        print(f"    Has 'settings' key: {'settings' in raw}")
    except Exception as e:
        print(f"    ❌ Cannot parse: {e}")

# ============ 2. Cameras via CameraManager ============
print("\n[2] Cameras as seen by CameraManager")
cams = CameraManager().all()
print(f"    Count: {len(cams)}")

if not cams:
    print("    ⚠️  NO CAMERAS LOADED — this is why MediaMTX has no paths.")
    print("    → You need to add a camera in the K1 VMS Cameras page.")
    print("      Then click 'Apply to NVR'.")

for c in cams:
    print(f"\n    📷 {c.name!r}")
    print(f"        uid:               {c.uid}")
    print(f"        enabled:           {c.enabled}")
    print(f"        ip:                {c.ip!r}")
    print(f"        port:              {c.port}")
    print(f"        user:              {c.user!r}")
    print(f"        password set:      {bool(c.password)}")
    print(f"        rtsp_path_main:    {c.rtsp_path_main!r}")
    print(f"        rtsp_path_sub:     {c.rtsp_path_sub!r}")
    print(f"        record_enabled_cont: {c.record_enabled_continuous}")
    print(f"        record_enabled_mot:  {c.record_enabled_motion}")

    # Filter checks (matches MediaMTXConfigBuilder logic)
    print(f"\n        FILTER CHECKS (what MediaMTXConfigBuilder does):")
    ok1 = bool(c.enabled)
    ok2 = bool(c.ip and (c.rtsp_path_main or c.rtsp_path_sub))
    print(f"          enabled == True:                 {'✅' if ok1 else '❌ SKIP'}")
    print(f"          ip non-empty:                    {'✅' if c.ip else '❌ SKIP'}")
    print(f"          rtsp_path_main or sub non-empty: {'✅' if (c.rtsp_path_main or c.rtsp_path_sub) else '❌ SKIP'}")
    if ok1 and ok2:
        print(f"          → INCLUDED in yml as: {_camera_path_name(c)!r}")
    else:
        print(f"          → ❌ EXCLUDED from yml")

# ============ 3. Global settings ============
print("\n[3] Global settings")
g = settings_manager.load_settings()
print(f"    record_enabled:         {g.record_enabled}")
print(f"    record_segment_minutes: {g.record_segment_minutes}")
print(f"    retention_days:         {g.retention_days}")
print(f"    record_format:          {g.record_format}")
print(f"    recording_root:         {g.recording_root!r}")

# ============ 4. Simulated config ============
print("\n[4] What the config builder WOULD produce right now:")
import tempfile
tmp = Path(tempfile.gettempdir()) / "k1_debug_mediamtx.yml"
MediaMTXConfigBuilder(cams, g).build(tmp)
content = tmp.read_text(encoding="utf-8")
print("    " + "-" * 58)
for line in content.splitlines():
    print(f"    {line}")
print("    " + "-" * 58)

# ============ 5. Actual mediamtx.yml ============
print(f"\n[5] Actual mediamtx.yml on disk: {config.MEDIAMTX_YML}")
if config.MEDIAMTX_YML.exists():
    print("    " + "-" * 58)
    for line in config.MEDIAMTX_YML.read_text(encoding="utf-8").splitlines():
        print(f"    {line}")
    print("    " + "-" * 58)
else:
    print("    ❌ NOT FOUND")

# ============ 6. mediamtx.log ============
print(f"\n[6] mediamtx.log (last 20 lines): {config.MEDIAMTX_LOG}")
if config.MEDIAMTX_LOG.exists():
    try:
        lines = config.MEDIAMTX_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        print("    " + "-" * 58)
        for line in lines[-20:]:
            print(f"    {line}")
        print("    " + "-" * 58)
    except Exception as e:
        print(f"    ❌ Cannot read: {e}")
else:
    print("    (no log file)")

print("\n" + "=" * 62)
print("  End of diagnostic")
print("=" * 62)