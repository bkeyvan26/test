# -*- coding: utf-8 -*-
"""K1 VMS — Camera auto-discovery (ONVIF + RTSP path guessing)"""
import socket
import urllib.parse
import cv2
from typing import Optional, List, Dict


COMMON_RTSP_PATHS = {
    "Hikvision": ["/Streaming/Channels/101", "/Streaming/Channels/102"],
    "Dahua": ["/cam/realmonitor?channel=1&subtype=0",
              "/cam/realmonitor?channel=1&subtype=1"],
    "Uniview": ["/media/video1", "/media/video2"],
    "Axis": ["/axis-media/media.amp"],
    "Hanwha": ["/profile1/media.smp", "/profile2/media.smp"],
    "Foscam": ["/videoMain", "/videoSub"],
    "Amcrest": ["/cam/realmonitor?channel=1&subtype=0",
                "/cam/realmonitor?channel=1&subtype=1"],
}


def _get_rtsp_url(ip, user, password, path, port=554):
    u = urllib.parse.quote(user or "", safe="")
    p = urllib.parse.quote(password or "", safe="")
    if not path.startswith("/"):
        path = "/" + path
    return f"rtsp://{u}:{p}@{ip}:{port}{path}"


def _test_rtsp_url(url, timeout=3.0):
    try:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout * 1000))
        opened = cap.isOpened()
        cap.release()
        return opened
    except Exception:
        return False


def _find_rtsp_path_guessing(ip, user, password, brand=None):
    paths_to_try = []
    if brand and brand in COMMON_RTSP_PATHS:
        paths_to_try = COMMON_RTSP_PATHS[brand]
    else:
        for brand_paths in COMMON_RTSP_PATHS.values():
            paths_to_try.extend(brand_paths)

    for path in paths_to_try:
        url = _get_rtsp_url(ip, user, password, path)
        if _test_rtsp_url(url):
            return {"main": path, "url": url}
    return None


# --- ONVIF ---
try:
    from wsdiscovery import WSDiscovery
    from onvif import ONVIFCamera
    _HAS_ONVIF = True
except ImportError:
    _HAS_ONVIF = False


def discover_onvif_cameras(timeout: int = 5) -> List[Dict]:
    if not _HAS_ONVIF:
        return []
    cameras = []
    try:
        wsd = WSDiscovery()
        wsd.start()
        services = wsd.searchServices(timeout=timeout)
        for service in services:
            for xaddr in service.getXAddrs():
                ip = xaddr.split("//")[1].split("/")[0].split(":")[0]
                cameras.append({
                    "ip": ip,
                    "xaddr": xaddr,
                    "type": service.getTypes(),
                    "scopes": service.getScopes(),
                })
        wsd.stop()
    except Exception as e:
        print(f"[ONVIF Discovery] Error: {e}")
    return cameras


def _inject_credentials(raw_uri: str, user: str, password: str) -> str:
    """Take an ONVIF URI and inject user:pass + ensure port."""
    try:
        parsed = urllib.parse.urlparse(raw_uri)
        u = urllib.parse.quote(user or "", safe="")
        p = urllib.parse.quote(password or "", safe="")
        port = parsed.port or 554
        netloc = f"{u}:{p}@{parsed.hostname}:{port}"
        return parsed._replace(netloc=netloc).geturl()
    except Exception:
        return raw_uri


def get_onvif_stream_profiles(ip, user, password, port=80,
                               timeout: float = 6.0) -> Optional[List[Dict]]:
    """
    Query ONVIF camera, return list of profile dicts:
        {
            id, name, url, rtsp_path,
            resolution, width, height, codec, fps, bitrate_kbps
        }
    or None on failure.
    """
    if not _HAS_ONVIF:
        return None
    try:
        cam = ONVIFCamera(ip, port, user, password)
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if not profiles:
            return None

        out = []
        for i, profile in enumerate(profiles):
            info = {
                "id": profile.token,
                "name": profile.Name or f"Profile {i + 1}",
                "url": "",
                "rtsp_path": "",
                "resolution": "",
                "width": 0,
                "height": 0,
                "codec": "",
                "fps": 0,
                "bitrate_kbps": 0,
            }

            # ---- Stream URI ----
            try:
                req = media.create_type('GetStreamUri')
                req.ProfileToken = profile.token
                req.StreamSetup = {
                    'Stream': 'RTP-Unicast',
                    'Transport': {'Protocol': 'RTSP'}
                }
                stream_uri = media.GetStreamUri(req)
                raw_uri = stream_uri.Uri
                parsed = urllib.parse.urlparse(raw_uri)
                info["rtsp_path"] = parsed.path or ""
                info["url"] = _inject_credentials(raw_uri, user, password)
            except Exception as e:
                print(f"[ONVIF] GetStreamUri failed for {profile.token}: {e}")
                continue

            # ---- Video encoder config ----
            try:
                vec = getattr(profile, "VideoEncoderConfiguration", None)
                # Method 1: read directly from profile (fastest)
                if vec is not None:
                    info["codec"] = (vec.Encoding or "").upper()
                    if getattr(vec, "Resolution", None):
                        w = int(vec.Resolution.Width or 0)
                        h = int(vec.Resolution.Height or 0)
                        info["width"], info["height"] = w, h
                        info["resolution"] = f"{w}x{h}"
                    if getattr(vec, "RateControl", None):
                        info["fps"] = int(vec.RateControl.FrameRateLimit or 0)
                        info["bitrate_kbps"] = int(
                            vec.RateControl.BitrateLimit or 0
                        )

                # Method 2: explicit query (some cameras need this)
                if not info["codec"] and vec is not None:
                    try:
                        vreq = media.create_type(
                            'GetVideoEncoderConfiguration'
                        )
                        vreq.ConfigurationToken = vec.token
                        vconf = media.GetVideoEncoderConfiguration(vreq)
                        info["codec"] = (vconf.Encoding or "").upper()
                        if getattr(vconf, "Resolution", None):
                            w = int(vconf.Resolution.Width or 0)
                            h = int(vconf.Resolution.Height or 0)
                            info["width"], info["height"] = w, h
                            info["resolution"] = f"{w}x{h}"
                        if getattr(vconf, "RateControl", None):
                            info["fps"] = int(
                                vconf.RateControl.FrameRateLimit or 0
                            )
                            info["bitrate_kbps"] = int(
                                vconf.RateControl.BitrateLimit or 0
                            )
                    except Exception as e:
                        print(f"[ONVIF] GetVideoEncoderConfig failed: {e}")

            except Exception as e:
                print(f"[ONVIF] metadata parse error: {e}")

            out.append(info)

        return out if out else None

    except Exception as e:
        print(f"[ONVIF] Error getting streams for {ip}: {e}")
        return None


# Backward-compat alias
def get_onvif_stream_uris(ip, user, password, port=80):
    """Legacy wrapper — returns {token: {token, name, uri}} or None."""
    profiles = get_onvif_stream_profiles(ip, user, password, port=port)
    if not profiles:
        return None
    out = {}
    for p in profiles:
        out[p["id"]] = {
            "token": p["id"],
            "name": p["name"],
            "uri": p["url"],
        }
    return out


def guess_profile_roles(profile: Dict, index: int, total: int) -> List[str]:
    """
    Heuristic: largest profile → RECORDING + LIVE_VIEW
    Second profile → MOTION + LIVE_VIEW
    """
    if index == 0:
        return ["RECORDING", "LIVE_VIEW"]
    if index == 1:
        return ["MOTION", "LIVE_VIEW"]
    return ["LIVE_VIEW"]