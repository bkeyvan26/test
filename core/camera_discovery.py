# -*- coding: utf-8 -*-
"""K1 VMS — Camera auto-discovery (ONVIF + RTSP path guessing)"""
import socket
import urllib.parse
import cv2
from typing import Optional, List, Dict

# لیست مسیرهای رایج RTSP بر اساس برند
COMMON_RTSP_PATHS = {
    "Hikvision": ["/Streaming/Channels/101", "/Streaming/Channels/102"],
    "Dahua": ["/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1"],
    "Uniview": ["/media/video1", "/media/video2"],
    "Axis": ["/axis-media/media.amp"],
    "Hanwha": ["/profile1/media.smp", "/profile2/media.smp"],
    "Foscam": ["/videoMain", "/videoSub"],
    "Amcrest": ["/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1"],
}

def _get_rtsp_url(ip: str, user: str, password: str, path: str) -> str:
    """Builds a full RTSP URL."""
    u = urllib.parse.quote(user or "", safe="")
    p = urllib.parse.quote(password or "", safe="")
    if not path.startswith("/"):
        path = "/" + path
    return f"rtsp://{u}:{p}@{ip}:554{path}"

def _test_rtsp_url(url: str, timeout: float = 3.0) -> bool:
    """Tests if an RTSP URL is accessible."""
    try:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        # Set a timeout for opening to avoid long hangs
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout * 1000))
        opened = cap.isOpened()
        cap.release()
        return opened
    except Exception:
        return False

def _find_rtsp_path_guessing(ip: str, user: str, password: str, brand: str = None) -> Optional[Dict[str, str]]:
    """Tries common RTSP paths for a given brand (or all)."""
    paths_to_try = []
    if brand and brand in COMMON_RTSP_PATHS:
        paths_to_try = COMMON_RTSP_PATHS[brand]
    else:
        # Try all known paths
        for brand_paths in COMMON_RTSP_PATHS.values():
            paths_to_try.extend(brand_paths)

    for path in paths_to_try:
        url = _get_rtsp_url(ip, user, password, path)
        if _test_rtsp_url(url):
            return {"main": path, "url": url}
    return None

# --- ONVIF Discovery ---
try:
    from wsdiscovery import WSDiscovery
    from onvif import ONVIFCamera
    _HAS_ONVIF = True
except ImportError:
    _HAS_ONVIF = False

def discover_onvif_cameras(timeout: int = 5) -> List[Dict]:
    """Discovers ONVIF cameras on the local network using WS-Discovery."""
    if not _HAS_ONVIF:
        return []

    cameras = []
    try:
        wsd = WSDiscovery()
        wsd.start()
        services = wsd.searchServices(timeout=timeout)
        for service in services:
            # Extract IP address from the service's XAddrs
            # Typically something like http://192.168.1.37/onvif/device_service
            xaddrs = service.getXAddrs()
            for xaddr in xaddrs:
                # A simple parse to get the host
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

def get_onvif_stream_uris(ip: str, user: str, password: str) -> Optional[Dict[str, str]]:
    """Connects to an ONVIF camera and retrieves its stream URIs."""
    if not _HAS_ONVIF:
        return None
    try:
        # We'll try to connect to the ONVIF service on the common port 80
        # A more robust implementation would use the discovered xaddr
        cam = ONVIFCamera(ip, 80, user, password)
        media_service = cam.create_media_service()
        profiles = media_service.GetProfiles()

        uris = {}
        for i, profile in enumerate(profiles):
            # Create the request for the stream URI
            req = media_service.create_type('GetStreamUri')
            req.ProfileToken = profile.token
            req.StreamSetup = {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}}
            
            stream_uri = media_service.GetStreamUri(req)
            # The URI comes as a string; we need to inject credentials
            # (ONVIF often returns a URI without user:pass)
            raw_uri = stream_uri.Uri
            # Replace the IP with user:pass@IP
            parsed = urllib.parse.urlparse(raw_uri)
            auth_uri = parsed._replace(netloc=f"{user}:{password}@{parsed.hostname}:{parsed.port or 554}").geturl()
            
            uris[f"profile_{i}"] = {
                "token": profile.token,
                "name": profile.Name,
                "uri": auth_uri,
            }
        return uris
    except Exception as e:
        print(f"[ONVIF] Error getting streams for {ip}: {e}")
        return None