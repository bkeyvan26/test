# -*- coding: utf-8 -*-
"""تلاش برای پیدا کردن پسورد/یوزر صحیح RTSP برای دوربین Axis."""
import subprocess
import shutil
import sys
from urllib.parse import quote


def find_ffprobe():
    p = shutil.which("ffprobe")
    if p:
        return p
    try:
        from core.ffprobe_util import get_ffprobe_exe
        return get_ffprobe_exe()
    except Exception:
        return None


def try_creds(ip, user, password):
    fp = find_ffprobe()
    if not fp:
        return None
    u = quote(user, safe="")
    p = quote(password, safe="")
    url = f"rtsp://{u}:{p}@{ip}:554/axis-media/media.amp"
    cmd = [
        fp, "-v", "error",
        "-rtsp_transport", "tcp",
        "-analyzeduration", "2000000",
        "-probesize", "2000000",
        "-show_entries", "stream=codec_name,width,height,r_frame_rate",
        "-of", "default=noprint_wrappers=1",
        url,
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=6,
            creationflags=(0x08000000 if sys.platform.startswith("win") else 0),
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else input("IP: ").strip()

    # CANDIDATES — همه ترکیبات محتمل
    candidates = [
        ("admin", "12345678M@"),
        ("admin", "12345678M"),
        ("admin", "12345678m@"),
        ("admin", "12345678m"),
        ("admin", "12345678"),
        ("admin", "12345678@"),
        ("root", "12345678M@"),
        ("root", "12345678M"),
        ("root", "12345678"),
        ("root", "root"),
        ("root", "pass"),
        ("admin", "admin"),
        ("admin", "Admin@123"),
        ("admin", "Admin123"),
        ("admin", "Axis@123"),
        ("admin", "Axis123"),
        ("viewer", "viewer"),
        ("viewer", "12345678M@"),
        ("viewer", "12345678M"),
        ("operator", "12345678M@"),
        ("operator", "12345678M"),
    ]

    print(f"\nTesting {len(candidates)} credential combos on {ip}\n")
    print("=" * 60)
    for user, password in candidates:
        result = try_creds(ip, user, password)
        masked_pw = password[:3] + "***"
        if result:
            print(f"\n✅ SUCCESS!!!")
            print(f"   user     = {user}")
            print(f"   password = {password}")
            print(f"\nStream info:")
            for line in result.splitlines():
                print(f"   {line}")
            print("=" * 60)
            return
        else:
            print(f"❌ {user}:{masked_pw}")

    print("\n" + "=" * 60)
    print("❌ هیچ ترکیبی کار نکرد.")
    print("\nبررسی کن:")
    print("1. کاربر admin در Axis دسترسی RTSP دارد؟")
    print("2. RTSP روی پورت 554 در Axis فعال است؟")
    print("3. مجوز admin چیست؟")


if __name__ == "__main__":
    main()