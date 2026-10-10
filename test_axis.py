# -*- coding: utf-8 -*-
"""تست مستقیم RTSP URL با نمایش خطای دقیق."""
import subprocess
import sys
import shutil


def find_ffprobe():
    p = shutil.which("ffprobe")
    if p:
        return p
    try:
        from core.ffprobe_util import get_ffprobe_exe
        return get_ffprobe_exe()
    except Exception:
        return None


def test(url, timeout=8):
    fp = find_ffprobe()
    if not fp:
        print("ffprobe not found")
        return

    print(f"\n{'='*70}")
    print(f"URL: {url[:60]}...")
    print('=' * 70)

    cmd = [
        fp, "-v", "warning",
        "-rtsp_transport", "tcp",
        "-analyzeduration", "3000000",
        "-probesize", "3000000",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate",
        "-of", "default=noprint_wrappers=1",
        url,
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=(0x08000000 if sys.platform.startswith("win") else 0),
        )
        print(f"Return code: {r.returncode}")
        if r.stdout:
            print("STDOUT:")
            print(r.stdout)
        if r.stderr:
            print("STDERR (real reason):")
            # Show all stderr - this is the important part
            for line in r.stderr.splitlines():
                print(f"  {line}")
    except subprocess.TimeoutExpired:
        print("TIMEOUT — camera not responding")
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    print("\nEnter test URL. Examples:")
    print("  rtsp://admin:PASSWORD@172.20.34.113:554/axis-media/media.amp")
    print("  rtsp://admin:PASSWORD@172.20.34.113:554/axis-media/media.amp?videocodec=h264")
    print()

    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("URL: ").strip()

    if not url:
        print("Empty URL")
        sys.exit(1)

    test(url)