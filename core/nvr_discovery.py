# -*- coding: utf-8 -*-
"""K1 VMS — NVR Auto-Discovery (Professional v4)"""
import re
import sys
import json
import time
import shutil
import subprocess
import threading
import traceback
import urllib.parse
from typing import List, Optional, Dict, Any, Set

from PySide6.QtCore import QObject, Signal, Slot

from core.models import NVRDevice, NVRChannel


ONVIF_PORT_CANDIDATES = [80, 8000, 2020, 8080, 8899, 5000]


BRAND_PATTERNS = {
    "hikvision": {
        "main": "rtsp://{user}:{pwd}@{ip}:{port}/Streaming/Channels/{ch}01",
        "sub":  "rtsp://{user}:{pwd}@{ip}:{port}/Streaming/Channels/{ch}02",
    },
    "dahua": {
        "main": "rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=0",
        "sub":  "rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=1",
    },
    "uniview": {
        "main": "rtsp://{user}:{pwd}@{ip}:{port}/unicast/c{ch}/s0/live",
        "sub":  "rtsp://{user}:{pwd}@{ip}:{port}/unicast/c{ch}/s1/live",
    },
    "axis": {
        "main": "rtsp://{user}:{pwd}@{ip}:{port}/axis-media/media.amp?camera={ch}",
        "sub":  "rtsp://{user}:{pwd}@{ip}:{port}/axis-media/media.amp?camera={ch}&resolution=640x480",
    },
    "amcrest": {
        "main": "rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=0",
        "sub":  "rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=1",
    },
    "kdt": {
        "main": "rtsp://{user}:{pwd}@{ip}:{port}/mode=real&idc={ch}&ids=1",
        "sub":  "rtsp://{user}:{pwd}@{ip}:{port}/mode=real&idc={ch}&ids=2",
    },
}


def _spawn_hidden_kwargs():
    if sys.platform.startswith("win"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def _find_ffprobe() -> Optional[str]:
    try:
        from core.ffprobe_util import get_ffprobe_exe
        exe = get_ffprobe_exe()
        if exe:
            return exe
    except Exception:
        pass
    return shutil.which("ffprobe")


def _probe_url(url: str, timeout: float = 3.0) -> Optional[dict]:
    ffprobe = _find_ffprobe()
    if not ffprobe:
        return None
    cmd = [
        ffprobe, "-v", "error",
        "-rtsp_transport", "tcp",
        "-timeout", str(int(timeout * 1_000_000)),
        "-show_entries", "stream=codec_name,width,height",
        "-of", "json", "-i", url,
    ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, timeout=timeout + 2.5,
            **_spawn_hidden_kwargs()
        )
    except Exception:
        return None
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return None
    for s in (data.get("streams") or []):
        cn = (s.get("codec_name") or "").lower()
        if cn in ("h264", "hevc", "h265", "mjpeg", "mpeg4"):
            return {
                "codec": cn.upper(),
                "width": int(s.get("width") or 0),
                "height": int(s.get("height") or 0),
            }
    return None


def _format_url(template: str, nvr: NVRDevice, ch: int,
                stream: int = 0) -> str:
    if not template:
        return ""
    user = urllib.parse.quote(nvr.user or "", safe="")
    pwd = urllib.parse.quote(nvr.password or "", safe="")
    try:
        return template.format(
            user=user, pwd=pwd, ip=nvr.ip,
            port=nvr.rtsp_port, ch=ch, stream=stream,
        )
    except Exception:
        return ""


def _detect_brand_from_token(token: str) -> str:
    if not token:
        return ""
    t = token.lower()
    if re.match(r"^token[_:]?\d+_0_\d+_1_\d+", t):
        return "hikvision"
    if re.match(r"^profile[_:]?\d+", t) and "stream" in t:
        return "dahua"
    if re.match(r"^mediaprofile\d+", t):
        return "dahua"
    if t.startswith("profile_") and "mainstream" not in t and "substream" not in t:
        return "uniview"
    if "axis" in t:
        return "axis"
    return ""


def _extract_channel_index_from_source_token(token: str,
                                             fallback_idx: int) -> int:
    if not token:
        return fallback_idx
    m = re.search(r"_0_(\d+)_1_", token)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:ch|channel|source|video)[_\-]?(\d+)", token,
                  re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)$", token)
    if m:
        return int(m.group(1))
    return fallback_idx


def _clean_channel_name(raw: str, ch_id: int) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if any(x in s.lower() for x in ("token:", "token_", "profile:")):
        return ""
    s = re.sub(r"\s*\(.*?\)\s*$", "", s).strip()
    if len(s) < 2 or re.fullmatch(r"[\W_]+", s):
        return ""
    return s


def _try_onvif(nvr: NVRDevice, log_fn,
               cancel_event) -> Optional[List[NVRChannel]]:
    try:
        from onvif import ONVIFCamera
    except ImportError:
        log_fn("[NVR] onvif-zeep نصب نیست")
        return None

    ports_to_try = []
    if nvr.onvif_port:
        ports_to_try.append(nvr.onvif_port)
    for p in ONVIF_PORT_CANDIDATES:
        if p not in ports_to_try:
            ports_to_try.append(p)

    for port in ports_to_try:
        if cancel_event.is_set():
            return None
        log_fn(f"[NVR] ONVIF → port {port} …")
        try:
            cam = ONVIFCamera(nvr.ip, port, nvr.user, nvr.password)
            media = cam.create_media_service()

            try:
                vs_configs = media.GetVideoSourceConfigurations()
            except Exception:
                vs_configs = []

            profiles = media.GetProfiles()
            if not profiles:
                log_fn(f"[NVR] ONVIF port {port}: بدون پروفایل")
                continue

            log_fn(f"[NVR] ONVIF port {port}: "
                   f"{len(profiles)} profile(s), "
                   f"{len(vs_configs)} source(s)")

            src_tok_to_ch: Dict[str, int] = {}
            for idx, cfg in enumerate(vs_configs, start=1):
                tok = getattr(cfg, "token", "") or ""
                src_tok = getattr(cfg, "SourceToken", "") or ""
                if tok:
                    src_tok_to_ch[tok] = idx
                if src_tok:
                    src_tok_to_ch[src_tok] = idx

            channel_ids: Set[int] = set()
            channel_names: Dict[int, str] = {}
            detected_brand = ""
            onvif_urls: Dict[int, Dict[str, str]] = {}

            for prof in profiles:
                if cancel_event.is_set():
                    return None
                try:
                    source_token = ""
                    try:
                        vsc = prof.VideoSourceConfiguration
                        source_token = (getattr(vsc, "SourceToken", "")
                                        or getattr(vsc, "token", "") or "")
                    except Exception:
                        pass

                    ptoken = str(getattr(prof, "token", "") or "")
                    if not detected_brand:
                        b = _detect_brand_from_token(ptoken)
                        if b:
                            detected_brand = b
                            log_fn(f"[NVR] برند از token: {b}")

                    ch_id = src_tok_to_ch.get(source_token)
                    if ch_id is None or ch_id <= 0:
                        ch_id = _extract_channel_index_from_source_token(
                            source_token, 0)
                    if ch_id is None or ch_id <= 0:
                        m = re.search(r"(?:^|[^\d])(\d+)(?:[^\d]|$)", ptoken)
                        if m:
                            ch_id = int(m.group(1))
                    if ch_id is None or ch_id <= 0:
                        continue

                    channel_ids.add(ch_id)

                    if ch_id not in channel_names:
                        pname = str(getattr(prof, "Name", "") or "")
                        clean = _clean_channel_name(pname, ch_id)
                        if clean:
                            channel_names[ch_id] = clean

                    if ch_id not in onvif_urls:
                        onvif_urls[ch_id] = {"main": "", "sub": ""}
                    try:
                        setup = {"Stream": "RTP-Unicast",
                                 "Transport": {"Protocol": "RTSP"}}
                        uri = media.GetStreamUri({
                            "ProfileToken": prof.token,
                            "StreamSetup": setup,
                        })
                        url = (uri.Uri if uri else "") or ""
                        if url:
                            if "@" not in url:
                                u = urllib.parse.quote(nvr.user, safe="")
                                p = urllib.parse.quote(nvr.password, safe="")
                                url = re.sub(r"rtsp://",
                                             f"rtsp://{u}:{p}@",
                                             url, count=1)
                            pname = (getattr(prof, "Name", "") or "").lower()
                            tok_low = ptoken.lower()
                            is_sub = ("sub" in pname or "sub" in tok_low
                                      or re.search(r"_s\d+$", tok_low))
                            if is_sub and not onvif_urls[ch_id]["sub"]:
                                onvif_urls[ch_id]["sub"] = url
                            elif not onvif_urls[ch_id]["main"]:
                                onvif_urls[ch_id]["main"] = url
                            elif not onvif_urls[ch_id]["sub"]:
                                onvif_urls[ch_id]["sub"] = url
                    except Exception:
                        pass

                except Exception as e:
                    log_fn(f"[NVR] profile parse error: {e}")

            if not channel_ids:
                log_fn(f"[NVR] ONVIF port {port}: بدون کانال")
                continue

            brand = nvr.brand if nvr.brand and nvr.brand != "auto" else None
            if not brand and detected_brand:
                brand = detected_brand
                log_fn(f"[NVR] استفاده از الگوی استاندارد {brand}")
            if brand and brand in BRAND_PATTERNS:
                use_pattern = True
            else:
                use_pattern = False
                log_fn("[NVR] برند نامشخص → URL ONVIF")

            channels: List[NVRChannel] = []
            for ch_id in sorted(channel_ids):
                name = channel_names.get(ch_id, "") or f"CH {ch_id}"
                if use_pattern:
                    pat = BRAND_PATTERNS[brand]
                    main_url = _format_url(pat["main"], nvr, ch_id, 0)
                    sub_url = _format_url(pat["sub"], nvr, ch_id, 1)
                else:
                    ou = onvif_urls.get(ch_id, {})
                    main_url = ou.get("main", "") or ""
                    sub_url = ou.get("sub", "") or ""

                channels.append(NVRChannel(
                    channel_id=ch_id,
                    name=name,
                    rtsp_url_main=main_url,
                    rtsp_url_sub=sub_url,
                    resolution="",
                    codec="",
                    enabled=True,
                ))
                log_fn(f"[NVR] ✓ CH {ch_id:>2}  "
                       f"main={'Y' if main_url else 'N'} "
                       f"sub={'Y' if sub_url else 'N'}")

            log_fn(f"[NVR] ONVIF موفق: {len(channels)} کانال "
                   f"(brand={brand or 'unknown'})")
            return channels

        except Exception as e:
            log_fn(f"[NVR] ONVIF port {port} شکست: {e}")
            continue
    return None


def _try_rtsp_patterns(nvr: NVRDevice, log_fn, cancel_event,
                       max_channels: int = 64,
                       custom_main: str = "",
                       custom_sub: str = "") -> List[NVRChannel]:
    if custom_main:
        log_fn("[NVR] RTSP custom pattern")
        channels = _probe_pattern(
            nvr, custom_main, custom_sub, log_fn, cancel_event, max_channels
        )
        if channels:
            return channels
        log_fn("[NVR] custom pattern failed")

    if nvr.brand and nvr.brand != "auto" and nvr.brand in BRAND_PATTERNS:
        brands = [nvr.brand]
    else:
        brands = list(BRAND_PATTERNS.keys())

    for b in brands:
        if cancel_event.is_set():
            break
        pat = BRAND_PATTERNS.get(b)
        if not pat:
            continue
        log_fn(f"[NVR] RTSP probing brand={b}")
        found = _probe_pattern(
            nvr, pat["main"], pat["sub"],
            log_fn, cancel_event, max_channels,
        )
        if found:
            return found
    return []


def _probe_pattern(nvr: NVRDevice, main_tpl: str, sub_tpl: str,
                   log_fn, cancel_event,
                   max_channels: int) -> List[NVRChannel]:
    if not main_tpl:
        return []
    channels: List[NVRChannel] = []
    misses = 0
    for ch in range(1, max_channels + 1):
        if cancel_event.is_set():
            break
        url = _format_url(main_tpl, nvr, ch)
        if not url:
            continue
        info = _probe_url(url, timeout=3.0)
        if info:
            sub_url = _format_url(sub_tpl, nvr, ch) if sub_tpl else ""
            channels.append(NVRChannel(
                channel_id=ch,
                name=f"CH {ch}",
                rtsp_url_main=url,
                rtsp_url_sub=sub_url,
                resolution=f"{info.get('width',0)}x{info.get('height',0)}",
                codec=info.get("codec", ""),
                enabled=True,
            ))
            misses = 0
            log_fn(f"[NVR] ✓ ch{ch}: {info.get('codec')} "
                   f"{info.get('width')}x{info.get('height')}")
        else:
            if channels:
                misses += 1
                if misses >= 4:
                    log_fn("[NVR] ۴ miss متوالی → توقف")
                    break
    return channels


class NVRDiscoveryWorker(QObject):
    log = Signal(str)
    progress = Signal(int, int, str)
    finished_ok = Signal(list)
    finished_fail = Signal(str)
    cancelled = Signal()

    def __init__(self, nvr: NVRDevice, parent=None,
                 custom_main: str = "", custom_sub: str = ""):
        super().__init__(parent)
        self.nvr = nvr
        self.custom_main = custom_main or ""
        self.custom_sub = custom_sub or ""
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    @property
    def cancelled_flag(self):
        return self._cancel_event.is_set()

    @Slot()
    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            try:
                self.log.emit(f"[NVR] EXCEPTION: {e}")
                self.log.emit(traceback.format_exc())
            except Exception:
                pass
            self.finished_fail.emit(str(e))

    def _run_inner(self):
        nvr = self.nvr
        self.log.emit(f"[NVR] START ip={nvr.ip} brand={nvr.brand}")
        if not nvr.ip:
            self.finished_fail.emit("IP وارد نشده")
            return
        if not nvr.user:
            self.finished_fail.emit("نام کاربری وارد نشده")
            return

        if self.custom_main:
            self.progress.emit(10, 100, "RTSP custom…")
            ch = _try_rtsp_patterns(
                nvr, self.log.emit, self._cancel_event,
                custom_main=self.custom_main,
                custom_sub=self.custom_sub,
            )
            if self._cancel_event.is_set():
                self.cancelled.emit()
                return
            if ch:
                self.progress.emit(100, 100, "done (custom)")
                self._emit_ok(ch)
                return
            self.finished_fail.emit("الگوی سفارشی نتیجه نداد")
            return

        self.progress.emit(5, 100, "ONVIF…")
        onvif_ch = _try_onvif(nvr, self.log.emit, self._cancel_event)
        if self._cancel_event.is_set():
            self.cancelled.emit()
            return
        if onvif_ch:
            self.progress.emit(100, 100, "done (ONVIF)")
            self._emit_ok(onvif_ch)
            return

        self.progress.emit(20, 100, "RTSP fallback…")
        self.log.emit("[NVR] ONVIF → RTSP patterns")
        rtsp_ch = _try_rtsp_patterns(nvr, self.log.emit, self._cancel_event)
        if self._cancel_event.is_set():
            self.cancelled.emit()
            return
        if rtsp_ch:
            self.progress.emit(100, 100, "done (RTSP)")
            self._emit_ok(rtsp_ch)
            return

        self.finished_fail.emit(
            "هیچ کانالی کشف نشد.\n\n"
            "• ONVIF ممکن است غیرفعال باشد\n"
            "• برند را دستی انتخاب کنید\n"
            "• یا الگوی RTSP دستی وارد کنید"
        )

    def _emit_ok(self, channels: List[NVRChannel]):
        self.finished_ok.emit([c.to_dict() for c in channels])