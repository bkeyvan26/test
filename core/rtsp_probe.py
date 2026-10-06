# -*- coding: utf-8 -*-
"""K1Motion — Auto RTSP URL Discovery (Phase 6.0: cancellable)

Changes vs Phase 5.1:
- subprocess.Popen + poll loop instead of subprocess.run(timeout=)
- threading.Event cancellation (cooperative)
- Per-candidate progress callback
- ffprobe terminated synchronously on cancel
- Order prioritized: ONVIF → vendor hint → generic fallback
"""
import shutil
import subprocess
import threading
import time
from urllib.parse import quote


# ============================================================
# Vendor patterns
# ============================================================
VENDOR_PATTERNS = {
    "axis": [
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/axis-media/media.amp"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/axis-media/media.amp?videocodec=h264"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/axis-media/media.amp?videocodec=h264&resolution=640x480"),
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
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/media/video{ch}"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/media/video{ch}_sub"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/stream{ch}"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/stream{ch}_sub"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/video{ch}"),
        ("sub",  "rtsp://{user}:{pass}@{ip}:{port}/video{ch}_sub"),
        ("main", "rtsp://{user}:{pass}@{ip}:{port}/live"),
    ],
}

RTSP_PORTS = [554, 5540]
PROBE_TIMEOUT = 4.0          # per-candidate timeout (seconds)
POLL_INTERVAL = 0.05         # poll loop interval


# ============================================================
# Helpers
# ============================================================
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
    kwargs = {
        "ip": ip,
        "port": port,
        "user": _encode(user),
        "pass": _encode(password),
        "ch": channel,
        "ch2": f"{channel:02d}",
    }
    return pattern.format(**kwargs)


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


def _spawn_kwargs():
    import sys
    if sys.platform.startswith("win"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def _kill_proc(proc):
    """Terminate then kill if needed. Unblocks reads."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=0.4)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=0.3)
    except Exception:
        pass


# ============================================================
# FFprobe single-transport probe with cancellation
# ============================================================
def _probe_single_transport(url, transport, timeout, cancel_event):
    fp = _find_ffprobe()
    if not fp:
        return None

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

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_spawn_kwargs(),
        )
    except Exception:
        return None

    deadline = time.monotonic() + float(timeout)
    while True:
        # ★ cancellation check
        if cancel_event is not None and cancel_event.is_set():
            _kill_proc(proc)
            return None

        try:
            rc = proc.poll()
        except Exception:
            rc = None

        if rc is not None:
            break

        if time.monotonic() > deadline:
            _kill_proc(proc)
            return None

        time.sleep(POLL_INTERVAL)

    # Process finished
    out = b""
    err = b""
    try:
        if proc.stdout:
            out = proc.stdout.read() or b""
        if proc.stderr:
            err = proc.stderr.read() or b""
    except Exception:
        pass

    # Cleanup
    try:
        if proc.stdout:
            proc.stdout.close()
    except Exception:
        pass
    try:
        if proc.stderr:
            proc.stderr.close()
    except Exception:
        pass

    if proc.returncode != 0:
        err_text = err.decode("utf-8", errors="replace").strip().lower()
        reason = ""
        if "401" in err_text or "unauthorized" in err_text or "authentication failed" in err_text:
            reason = "AUTH_FAILED"
        elif "403" in err_text or "forbidden" in err_text:
            reason = "AUTH_FORBIDDEN"
        elif "connection refused" in err_text or "actively refused" in err_text:
            reason = "CONNECTION_REFUSED"
        elif "timed out" in err_text or "timeout" in err_text:
            reason = "TIMEOUT"
        elif "404" in err_text or "not found" in err_text:
            reason = "RTSP_PATH_NOT_FOUND"
        else:
            reason = "RTSP_PROBE_FAILED"
        return {"_error": reason, "_stderr": err_text[:500]}

    if not out:
        return {"_error": "NO_STREAM_DATA"}

    text = out.decode("utf-8", errors="replace")
    info = _parse_ffprobe_output(text)
    if info and info.get("codec") and info.get("width"):
        info["transport"] = transport
        return info
    return {"_error": "NO_VIDEO_STREAM"}

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


def test_rtsp_url(url: str, timeout: float = PROBE_TIMEOUT,
                  cancel_event=None):
    """Try UDP then TCP and retain the most useful failure reason."""
    last_error = "RTSP_PROBE_FAILED"
    for transport in ("udp", "tcp"):
        if cancel_event is not None and cancel_event.is_set():
            return None
        info = _probe_single_transport(url, transport, timeout, cancel_event)
        if not info:
            continue
        if info.get("_error"):
            last_error = info["_error"]
            continue
        return info
    return {"_error": last_error}

# ============================================================
# Main discovery
# ============================================================
def probe_device(ip: str, user: str, password: str,
                 onvif_port: int = 80, onvif_result=None,
                 log=None, progress=None, cancel_event=None):
    """
    Full auto-discovery.

    Args:
        ip, user, password: credentials
        onvif_result: dict or None — {main_url, sub_url, vendor, model}
        log(msg): callback
        progress(current, total, message): callback
        cancel_event: threading.Event (or None)
    """
    def _log(msg):
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    def _prog(cur, tot, msg=""):
        if progress is not None:
            try:
                progress(cur, tot, msg)
            except Exception:
                pass

    def _cancelled():
        return cancel_event is not None and cancel_event.is_set()

    failure_codes = []

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

    # ---- Step 1: ONVIF-provided URL (highest priority) ----
    if onvif_result and not _cancelled():
        main_url = (onvif_result.get("main_url") or "").strip()
        sub_url = (onvif_result.get("sub_url") or "").strip() or None
        if main_url:
            _log(f"[AUTO-DETECT] testing ONVIF URL")
            _prog(0, 1, "ONVIF URL")
            info = test_rtsp_url(main_url, cancel_event=cancel_event)
            result["attempts"] += 1
            if _cancelled():
                return result
            if info and not info.get("_error"):
                result["success"] = True
                result["main_url"] = main_url
                result["sub_url"] = sub_url
                result["main_info"] = info
                result["transport"] = info.get("transport", "udp")
                result["vendor"] = (onvif_result.get("vendor")
                                    or "onvif").lower()
                if sub_url and not _cancelled():
                    sinfo = test_rtsp_url(sub_url, cancel_event=cancel_event)
                    result["attempts"] += 1
                    if sinfo:
                        result["sub_info"] = sinfo
                return result
            else:
                reason = (info or {}).get("_error", "RTSP_PROBE_FAILED")
                _log(f"[AUTO-DETECT] ONVIF URL failed reason={reason}, using patterns")

    # ---- Step 2: vendor guess ----
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
    _log(f"[AUTO-DETECT] vendor guess: {vendor}")

    # ---- Step 3: build candidate list ----
    order = [vendor]
    for v in ("axis", "hikvision", "dahua", "uniview", "hkv",
              "reolink", "foscam", "vivotek", "generic"):
        if v not in order:
            order.append(v)

    candidates = []
    for port in RTSP_PORTS:
        for vendor_key in order:
            patterns = VENDOR_PATTERNS.get(vendor_key, [])
            for kind, pattern in patterns:
                if kind != "main":
                    continue
                is_nvr = "{ch}" in pattern
                channels = [1, 2] if is_nvr else [1]
                for ch in channels:
                    url = _make_url(pattern, ip, port, user, password, ch)
                    candidates.append({
                        "url": url,
                        "vendor": vendor_key,
                        "port": port,
                        "ch": ch,
                        "patterns": patterns,
                    })

    total = len(candidates)
    _log(f"[AUTO-DETECT] RTSP PROBE START candidates={total}")

    # ---- Step 4: probe candidates ----
    for i, cand in enumerate(candidates, start=1):
        if _cancelled():
            _log(f"[AUTO-DETECT] cancelled during probe")
            return result

        _prog(i - 1, total,
              f"#{i}/{total}  {cand['vendor']}  port={cand['port']}")
        _log(f"[AUTO-DETECT] PROBE #{i} vendor={cand['vendor']} "
             f"port={cand['port']} ch={cand['ch']}")

        info = test_rtsp_url(cand["url"], timeout=PROBE_TIMEOUT,
                             cancel_event=cancel_event)
        result["attempts"] = i

        if _cancelled():
            _log(f"[AUTO-DETECT] cancelled during candidate #{i}")
            return result

        if info and not info.get("_error"):
            _log(f"[AUTO-DETECT] PROBE #{i} SUCCESS codec={info.get('codec')} "
                 f"{info.get('width')}x{info.get('height')} "
                 f"{info.get('fps')}fps")
            result["success"] = True
            result["rtsp_port"] = cand["port"]
            result["main_url"] = cand["url"]
            result["main_info"] = info
            result["transport"] = info.get("transport", "udp")
            result["vendor"] = cand["vendor"]

            # Try sub
            sub_url = _build_sub_url(
                cand["vendor"], cand["patterns"],
                ip, cand["port"], user, password, cand["ch"]
            )
            if sub_url and not _cancelled():
                sinfo = test_rtsp_url(sub_url, cancel_event=cancel_event)
                if sinfo and not sinfo.get("_error"):
                    result["sub_url"] = sub_url
                    result["sub_info"] = sinfo
                    _log(f"[AUTO-DETECT] sub OK "
                         f"{sinfo.get('width')}x{sinfo.get('height')}")

            _prog(total, total, "done")
            return result

        reason = (info or {}).get("_error", "RTSP_PROBE_FAILED")
        failure_codes.append(reason)
        _log(f"[AUTO-DETECT] PROBE #{i} FAILED reason={reason}")

    result["attempts"] = total
    priority = (
        "AUTH_FAILED", "AUTH_FORBIDDEN", "CONNECTION_REFUSED",
        "TIMEOUT", "RTSP_PATH_NOT_FOUND", "NO_VIDEO_STREAM",
        "NO_STREAM_DATA", "RTSP_PROBE_FAILED"
    )
    result["error_code"] = next(
        (code for code in priority if code in failure_codes),
        "NO_MATCH"
    )
    result["error"] = result["error_code"]

    _prog(total, total, "failed")
    return result


def _build_sub_url(vendor_key, patterns, ip, port, user, password, ch):
    for kind, pattern in patterns:
        if kind == "sub":
            return _make_url(pattern, ip, port, user, password, ch)
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
    if vendor_key == "generic":
        return f"rtsp://{u}:{p}@{ip}:{port}/media/video{ch}_sub"
    return None
