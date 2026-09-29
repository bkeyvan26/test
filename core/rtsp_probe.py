# -*- coding: utf-8 -*-
"""K1Motion — Auto RTSP URL Discovery (Phase 5.1)

Fixes:
- `pass` syntax error (Python keyword)
- Reduced ports (554, 5540) and channels
- Better AXIS patterns
- Shorter per-probe timeout
"""
import re
import shutil
import subprocess
from urllib.parse import quote


# ============================================================
# Vendor RTSP URL patterns
# ============================================================
VENDOR_PATTERNS = {
    "axis": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/axis-media/media.amp"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/axis-media/media.amp?videocodec=h264"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/axis-media/media.amp?camera=1"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/axis-media/media.amp?videocodec=h264&resolution=640x480"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/axis-media/media.amp?camera=2"),
    ],
    "hikvision": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/Streaming/Channels/{ch}01"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/Streaming/Channels/{ch}02"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/ISAPI/Streaming/channels/{ch}01"),
    ],
    "dahua": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=0"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=1"),
    ],
    "uniview": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/media/video{ch}"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/media/video{ch}_sub"),
    ],
    "reolink": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/h264Preview_{ch2}_main"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/h264Preview_{ch2}_sub"),
    ],
    "foscam": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/videoMain"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/videoSub"),
    ],
    "vivotek": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/live.sdp"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/live2.sdp"),
    ],
    "hkv": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/Streaming/Channels/101"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/Streaming/Channels/102"),
    ],
    "generic": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/stream1"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/stream2"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/video1"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/video2"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/media/video1"),
    ],
}

# ★ فقط 2 پورت پرکاربرد (سریع‌تر)
RTSP_PORTS = [554, 5540]
PROBE_TIMEOUT = 4


def _find_ffprobe():
    try:
        from core.ffprobe_util import get_ffprobe_exe
        exe = get_ffprobe_exe()
        if exe:
            return exe
    except Exception:
        pass
    return shutil.which("ffprobe")


def _encode(s: str) -> str:
    try:
        return quote(s, safe="")
    except Exception:
        return s


def _make_url(pattern: str, ip: str, port: int, user: str, password: str,
              channel: int) -> str:
    """★ FIX: kwargs dict — 'pass' string key, not Python keyword."""
    user_e = _encode(user)
    pass_e = _encode(password)
    kwargs = {
        "ip": ip,
        "port": port,
        "user": user_e,
        "pass": pass_e,
        "ch": channel,
        "ch2": f"{channel:02d}",
    }
    return pattern.format(**kwargs)


def test_rtsp_url(url: str, timeout: int = PROBE_TIMEOUT):
    """Returns dict(codec, width, height, fps, bitrate) or None."""
    fp = _find_ffprobe()
    if not fp:
        return None

    for transport in ("udp", "tcp"):
        cmd = [
            fp,
            "-v", "error",
            "-rtsp_transport", transport,
            "-analyzeduration", "2000000",
            "-probesize", "2000000",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,bit_rate",
            "-of", "default=noprint_wrappers=1",
            url,
        ]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                creationflags=(0x08000000 if shutil.os.name == "nt" else 0),
            )
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue

        if r.returncode != 0:
            continue

        out = r.stdout or ""
        if not out.strip():
            continue

        info = _parse_ffprobe_output(out)
        if info and info.get("codec") and info.get("width"):
            info["transport"] = transport
            return info

    return None


def _parse_ffprobe_output(text: str) -> dict:
    data = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()

    result = {}
    result["codec"] = data.get("codec_name", "")

    try:
        result["width"] = int(data.get("width", 0) or 0)
    except Exception:
        result["width"] = 0
    try:
        result["height"] = int(data.get("height", 0) or 0)
    except Exception:
        result["height"] = 0

    fps_raw = data.get("r_frame_rate", "0/1")
    try:
        if "/" in fps_raw:
            num, den = fps_raw.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 0.0
        else:
            fps = float(fps_raw)
    except Exception:
        fps = 0.0
    result["fps"] = int(round(fps)) if fps > 0 else 0

    try:
        result["bitrate"] = int(data.get("bit_rate", 0) or 0)
    except Exception:
        result["bitrate"] = 0

    return result


def probe_device(ip: str, user: str, password: str,
                 onvif_port: int = 80, onvif_result=None, log=None):
    """Full auto-discovery with ONVIF hint + RTSP brute-force fallback."""
    def _log(msg):
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    result = {
        "success": False,
        "vendor": "unknown",
        "rtsp_port": 0,
        "main_url": "",
        "sub_url": None,
        "main_info": None,
        "sub_info": None,
        "transport": "udp",
        "attempts": 0,
        "error": "",
    }

    # 1) ONVIF URL if available
    if onvif_result:
        main_url = (onvif_result.get("main_url") or "").strip()
        sub_url = (onvif_result.get("sub_url") or "").strip() or None
        if main_url:
            _log(f"[probe] testing ONVIF URL")
            info = test_rtsp_url(main_url)
            result["attempts"] += 1
            if info:
                result["success"] = True
                result["main_url"] = main_url
                result["sub_url"] = sub_url
                result["main_info"] = info
                result["transport"] = info.get("transport", "udp")
                result["vendor"] = (onvif_result.get("vendor") or "onvif").lower()
                if sub_url:
                    sinfo = test_rtsp_url(sub_url)
                    result["attempts"] += 1
                    if sinfo:
                        result["sub_info"] = sinfo
                return result
            else:
                _log(f"[probe] ONVIF URL failed, using patterns")

    # 2) Vendor hint from ONVIF
    vendor = "generic"
    if onvif_result:
        v = (onvif_result.get("vendor") or "").lower()
        model = (onvif_result.get("model") or "").lower()
        combined = f"{v} {model}"
        for known in ("axis", "hikvision", "dahua", "uniview",
                      "reolink", "foscam", "vivotek"):
            if known in combined:
                vendor = known
                break
        else:
            if "ds-" in combined:
                vendor = "dahua"

    result["vendor"] = vendor
    _log(f"[probe] vendor guess: {vendor}")

    # 3) Order: vendor first, then AXIS/HIK/DAHUA, then generic
    order = [vendor]
    for v in ("axis", "hikvision", "dahua", "uniview", "hkv",
              "reolink", "foscam", "vivotek"):
        if v not in order:
            order.append(v)
    order.append("generic")

    # 4) Probe
    attempts = 0
    for port in RTSP_PORTS:
        for vendor_key in order:
            patterns = VENDOR_PATTERNS.get(vendor_key, [])
            # Try each pattern (only main first)
            for kind, pattern in patterns:
                if kind != "main":
                    continue

                # ★ NVR patterns: try channel 1 and 2
                # For camera patterns: only channel 1
                is_nvr_pattern = "{ch}" in pattern
                channels = [1, 2] if is_nvr_pattern else [1]

                for ch in channels:
                    url = _make_url(pattern, ip, port, user, password, ch)
                    attempts += 1
                    _log(f"[probe] #{attempts} port={port} "
                         f"vendor={vendor_key} ch={ch}")
                    info = test_rtsp_url(url, timeout=PROBE_TIMEOUT)
                    if info:
                        result["success"] = True
                        result["rtsp_port"] = port
                        result["main_url"] = url
                        result["main_info"] = info
                        result["transport"] = info.get("transport", "udp")
                        result["attempts"] = attempts
                        _log(f"[probe] ✓ main: {info.get('codec')} "
                             f"{info.get('width')}x{info.get('height')} "
                             f"{info.get('fps')}fps")

                        # Try sub
                        sub_url = _build_sub_url(
                            vendor_key, patterns, ip, port, user, password, ch
                        )
                        if sub_url:
                            sinfo = test_rtsp_url(sub_url, timeout=PROBE_TIMEOUT)
                            if sinfo:
                                result["sub_url"] = sub_url
                                result["sub_info"] = sinfo
                                _log(f"[probe] ✓ sub: "
                                     f"{sinfo.get('width')}x{sinfo.get('height')}")

                        result["vendor"] = vendor_key
                        return result

    result["attempts"] = attempts
    result["error"] = "No RTSP URL matched"
    return result


def _build_sub_url(vendor_key, patterns, ip, port, user, password, ch):
    """Return first sub-stream candidate URL."""
    for kind, pattern in patterns:
        if kind == "sub":
            return _make_url(pattern, ip, port, user, password, ch)
    # Fallback for common vendors
    u = _encode(user)
    p = _encode(password)
    if vendor_key in ("hikvision", "hkv"):
        return f"rtsp://{u}:{p}@{ip}:{port}/Streaming/Channels/{ch}02"
    if vendor_key == "dahua":
        return f"rtsp://{u}:{p}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=1"
    if vendor_key == "uniview":
        return f"rtsp://{u}:{p}@{ip}:{port}/media/video{ch}_sub"
    if vendor_key == "axis":
        return f"rtsp://{u}:{p}@{ip}:{port}/axis-media/media.amp?camera=2"
    return None


def _redact(url: str) -> str:
    try:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        creds, hostpart = rest.rsplit("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{hostpart}"
        return f"{scheme}://***@{hostpart}"
    except Exception:
        return url