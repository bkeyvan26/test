# -*- coding: utf-8 -*-
"""
K1 VMS — Standalone FFmpeg diagnostic

Run:
    cd /d D:\\k1motion
    venv\\Scripts\\activate
    python diag_ffmpeg_test.py

Tests each camera:
1. Direct camera RTSP (bypasses MediaMTX)
2. MediaMTX loopback RTSP

Measures: spawn, first byte, 25/50/75/100% frame, total.
Prints first 15 stderr lines from ffmpeg.
"""
import os
import time
import shutil
import subprocess
import threading


# ===================== EDIT THESE =====================
CAMERAS = [
    # (label, direct_camera_url, mediamtx_path)
    ("harasat",  "rtsp://admin:12345678M@192.168.1.37:554/media/video1",     "cam_3059f25c"),
    ("test",     "rtsp://admin:12345678M@192.168.1.41:554/media/video1",     "cam_5064cd2c"),
    ("fanavari", "rtsp://admin:12345678M%40@192.168.1.73:554/media/video1",  "cam_1e437edf"),
]
MEDIAMTX_HOST = "127.0.0.1:8554"
OUT_W = 1280
OUT_H = 720
FRAME_SIZE = OUT_W * OUT_H * 3
# ======================================================


def find_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")


def redact(url):
    try:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        creds, hostpart = rest.split("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{hostpart}"
        return f"{scheme}://***@{hostpart}"
    except Exception:
        return url


def run_test(label, url, ffmpeg):
    print("=" * 78)
    print(f"[TEST] {label}  {redact(url)}")
    print("=" * 78)

    vf = (
        f"fps=1,"
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2,"
        f"format=bgr24"
    )
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "info",
        "-rtsp_transport", "tcp",
        "-rtsp_flags", "prefer_tcp",
        "-fflags", "nobuffer+discardcorrupt+genpts",
        "-flags", "low_delay",
        "-avioflags", "direct",
        "-max_delay", "500000",
        "-analyzeduration", "300000",
        "-probesize", "500000",
        "-i", url,
        "-an", "-sn",
        "-vf", vf,
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-"
    ]

    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=FRAME_SIZE * 4,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except Exception as e:
        print(f"[TEST] spawn failed: {e}")
        return None

    spawn_ms = (time.monotonic() - t0) * 1000.0
    print(f"[TEST] spawn_ok pid={proc.pid} spawn_ms={spawn_ms:.1f}")

    stderr_lines = []
    def _rd():
        try:
            for line in iter(proc.stderr.readline, b""):
                stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
        except Exception:
            pass
    threading.Thread(target=_rd, daemon=True).start()

    buf = bytearray()
    first_byte_ms = None
    thresholds = [0.01, 0.25, 0.50, 0.75, 1.00]
    tidx = 0
    chunk_size = 65536

    while len(buf) < FRAME_SIZE:
        try:
            chunk = proc.stdout.read(chunk_size)
        except Exception:
            break
        if not chunk:
            break
        if first_byte_ms is None:
            first_byte_ms = (time.monotonic() - t0) * 1000.0
            print(f"[TEST] FIRST_BYTE_AVAILABLE  +{first_byte_ms:.0f}ms")
        buf.extend(chunk)
        pct = len(buf) / FRAME_SIZE
        while tidx < len(thresholds) and pct >= thresholds[tidx]:
            ms = (time.monotonic() - t0) * 1000.0
            print(f"[TEST] {int(thresholds[tidx]*100):3d}%  "
                  f"+{ms:.0f}ms  bytes={len(buf)}")
            tidx += 1

    total_ms = (time.monotonic() - t0) * 1000.0
    print(f"[TEST] DONE total_ms={total_ms:.0f} collected={len(buf)}")

    print(f"[TEST] --- stderr (first 15 of {len(stderr_lines)}) ---")
    for line in stderr_lines[:15]:
        print(f"        {line}")

    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    return {
        "name": label,
        "spawn_ms": spawn_ms,
        "first_byte_ms": first_byte_ms,
        "total_ms": total_ms,
        "collected": len(buf),
    }


def main():
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("ffmpeg not found")
        return
    print(f"[MAIN] ffmpeg: {ffmpeg}")
    print(f"[MAIN] frame_size = {FRAME_SIZE}")
    print()

    results = []

    print("\n### DIRECT CAMERA RTSP ###\n")
    for name, direct_url, _ in CAMERAS:
        r = run_test(f"{name}-direct", direct_url, ffmpeg)
        if r:
            results.append(r)

    print("\n### MEDIAMTX LOOPBACK RTSP ###\n")
    for name, _, mt_path in CAMERAS:
        url = f"rtsp://{MEDIAMTX_HOST}/{mt_path}"
        r = run_test(f"{name}-mediamtx", url, ffmpeg)
        if r:
            results.append(r)

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"{'test':<26} {'spawn':>8} {'1byte':>8} {'total':>8} {'bytes':>10}")
    print("-" * 78)
    for r in results:
        fb = f"{r['first_byte_ms']:.0f}" if r['first_byte_ms'] is not None else "—"
        print(f"{r['name']:<26} {r['spawn_ms']:>8.0f} {fb:>8} "
              f"{r['total_ms']:>8.0f} {r['collected']:>10}")
    print("=" * 78)


if __name__ == "__main__":
    main()