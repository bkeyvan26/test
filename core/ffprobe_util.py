# -*- coding: utf-8 -*-
"""K1 VMS — FFprobe utility (auto-detect + safe probe)"""
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

IS_WIN = sys.platform.startswith("win")


def _spawn_hidden_kwargs():
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def _find_ffmpeg() -> Optional[str]:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    base = Path(__file__).parent.parent
    for name in ("ffmpeg.exe", "ffmpeg"):
        p = base / name
        if p.exists():
            return str(p)
    return shutil.which("ffmpeg")


def _find_ffprobe() -> Optional[str]:
    # 1) same folder as imageio_ffmpeg's ffmpeg
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        if ff:
            for name in ("ffprobe.exe", "ffprobe"):
                p = Path(ff).parent / name
                if p.exists():
                    return str(p)
    except Exception:
        pass

    # 2) next to k1motion.py
    base = Path(__file__).parent.parent
    for name in ("ffprobe.exe", "ffprobe"):
        p = base / name
        if p.exists():
            return str(p)

    # 3) PATH
    found = shutil.which("ffprobe")
    if found:
        return found

    # 4) WinGet locations
    if IS_WIN:
        try:
            local = os.environ.get("LOCALAPPDATA", "")
            if local:
                winget = Path(local) / "Microsoft" / "WinGet" / "Packages"
                if winget.exists():
                    for pkg in winget.glob("Gyan.FFmpeg*"):
                        for bin_dir in pkg.glob("ffmpeg-*/bin"):
                            for name in ("ffprobe.exe", "ffprobe"):
                                p = bin_dir / name
                                if p.exists():
                                    return str(p)
        except Exception:
            pass

    return None


_FFPROBE_CACHE = None
_FFMPEG_CACHE = None


def get_ffprobe_exe() -> Optional[str]:
    global _FFPROBE_CACHE
    if _FFPROBE_CACHE is None:
        _FFPROBE_CACHE = _find_ffprobe()
        if _FFPROBE_CACHE:
            print(f"[ffprobe] found: {_FFPROBE_CACHE}")
        else:
            print("[ffprobe] NOT FOUND — using mtime fallback")
    return _FFPROBE_CACHE

def get_ffmpeg_exe() -> Optional[str]:
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE is None:
        _FFMPEG_CACHE = _find_ffmpeg()
    return _FFMPEG_CACHE


def is_available() -> bool:
    return get_ffprobe_exe() is not None


def probe_media(path: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Run ffprobe and return metadata. None on failure."""
    ffprobe = get_ffprobe_exe()
    if not ffprobe or not os.path.isfile(path):
        return None

    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-select_streams", "v:0",
        path,
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, **_spawn_hidden_kwargs()
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
    except Exception:
        return None

    streams = data.get("streams", [])
    if not streams:
        return None
    stream = streams[0]
    fmt = data.get("format", {})

    # duration
    duration = None
    for key in ("duration", "DURATION"):
        v = fmt.get(key) or stream.get(key)
        if v:
            try:
                duration = float(v); break
            except (TypeError, ValueError):
                pass
    if duration is None:
        return None

    # fps
    fps = None
    try:
        rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
        if "/" in rate:
            num, den = rate.split("/")
            num, den = float(num), float(den)
            if den > 0:
                fps = num / den
        else:
            fps = float(rate)
    except Exception:
        fps = None

    return {
        "duration": duration,
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": fps,
        "size_bytes": int(fmt.get("size") or 0),
        "format_name": fmt.get("format_name"),
    }


def probe_has_video_stream(path: str, timeout: float = 15.0) -> bool:
    """Return True if ffprobe can open file AND has a video stream."""
    return probe_media(path, timeout=timeout) is not None
