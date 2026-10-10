# -*- coding: utf-8 -*-
"""Pure helper functions for LivePage (Phase 6.6)."""


def parse_size(s):
    try:
        s = str(s).lower().replace("×", "x")
        w, h = s.split("x")
        return int(w.strip()), int(h.strip())
    except Exception:
        return (0, 0)


def redact_url(url: str) -> str:
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


def get_live_transport() -> str:
    try:
        import config
        t = getattr(config, "LIVE_RTSP_TRANSPORT", None)
        if t in ("udp", "tcp"):
            return t
    except Exception:
        pass
    return "udp"


def build_stream_url(cam, profile_id: str, nvr) -> str:
    profile = cam.get_profile_by_id(profile_id) if profile_id else None
    if profile is None:
        return ""
    try:
        import config
        use_mtx = bool(getattr(config, "LIVE_USE_MEDIAMTX", False))
    except Exception:
        use_mtx = False
    if not use_mtx or not nvr:
        return profile.url or ""
    try:
        from core.nvr_engine import _camera_path_name
        name = _camera_path_name(cam)
        rec_pid = cam.recording_profile_id
        mot_pid = cam.motion_profile_id
        if profile_id == rec_pid:
            return f"rtsp://127.0.0.1:8554/{name}"
        if profile_id == mot_pid:
            return f"rtsp://127.0.0.1:8554/{name}_sub"
    except Exception:
        pass
    return profile.url or ""


def compute_output_size_for(cam, role: str):
    if role == "grid":
        profile_id = cam.grid_profile_id
        override = getattr(cam, "grid_resolution_override", "") or ""
        default = (640, 360)
    else:
        profile_id = cam.live_profile_id
        override = getattr(cam, "live_resolution_override", "") or ""
        default = (1280, 720)
    profile = cam.get_profile_by_id(profile_id) if profile_id else None
    src_w = profile.width if profile else 0
    src_h = profile.height if profile else 0
    if not override:
        if src_w and src_h:
            if role == "single" and (src_w > 1280 or src_h > 720):
                return (1280, 720)
            return (src_w, src_h)
        return default
    des_w, des_h = parse_size(override)
    if des_w < 16 or des_h < 16:
        if src_w and src_h:
            return (src_w, src_h)
        return default
    if src_w and src_h:
        if des_w > src_w or des_h > src_h:
            return (src_w, src_h)
    return (des_w, des_h)


def compute_fps_for(cam, role: str, n_cells: int = 1) -> int:
    """Admin decides. If admin gave a number → use it. Only auto gets smart defaults."""
    if role == "grid":
        profile_id = cam.grid_profile_id
        override_fps = int(getattr(cam, "grid_display_fps", 0) or 0)
    else:
        profile_id = cam.live_profile_id
        override_fps = int(getattr(cam, "live_display_fps", 0) or 0)

    profile = cam.get_profile_by_id(profile_id) if profile_id else None
    src_fps = profile.fps if (profile and profile.fps) else 0

    if override_fps > 0:
        return max(1, override_fps)

    if role == "single":
        if src_fps > 0:
            return min(src_fps, 15)
        return 15

    if src_fps > 0:
        base = min(src_fps, 10)
    else:
        base = 5

    if n_cells > 16:
        return min(base, 2)
    if n_cells > 9:
        return min(base, 3)
    if n_cells > 4:
        return min(base, 5)
    return base


def query_mediamtx_state(nvr, cam, profile_id) -> dict:
    out = {
        "queried": False, "exists": False, "ready": False,
        "source_present": None, "readers": None,
        "bytes_received": None, "error": "", "path_name": "",
    }
    if not nvr:
        out["error"] = "no_nvr"
        return out
    try:
        from core.nvr_engine import _camera_path_name, MediaMTXApi
        rec_pid = cam.recording_profile_id
        mot_pid = cam.motion_profile_id
        name = _camera_path_name(cam)
        if profile_id == rec_pid:
            path_name = name
        elif profile_id == mot_pid:
            path_name = f"{name}_sub"
        else:
            path_name = name
        out["path_name"] = path_name
        api = MediaMTXApi()
        paths = api.list_paths()
        out["queried"] = True
        p = paths.get(path_name)
        if p is None:
            return out
        out["exists"] = True
        out["ready"] = bool(p.get("ready"))
        out["source_present"] = p.get("source") is not None
        out["readers"] = p.get("readers")
        out["bytes_received"] = int(p.get("bytesReceived") or 0)
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def collect_prewarm_state(uid, single_readers, single_ready_uids,
                           prewarm_idx, prewarm_timer_active,
                           prewarm_trace_uid) -> dict:
    state = {
        "reader_exists": False, "reader_alive": False, "reader_ready": False,
        "proc_exists": False, "proc_alive": False, "proc_pid": None,
        "prewarm_idx": prewarm_idx,
        "prewarm_timer_active": prewarm_timer_active,
        "prewarm_trace_uid": prewarm_trace_uid,
        "single_ready_count": len(single_ready_uids),
    }
    try:
        r = single_readers.get(uid)
        if r is not None:
            state["reader_exists"] = True
            state["reader_alive"] = r.is_alive()
            state["reader_ready"] = uid in single_ready_uids
            state["proc_exists"] = (r._proc is not None)
            state["proc_alive"] = r.proc_alive()
            state["proc_pid"] = r.proc_pid()
    except Exception:
        pass
    return state


def mediamtx_path_ready(nvr, cam, profile_id):
    try:
        import config
        if not getattr(config, "LIVE_USE_MEDIAMTX", False):
            return True
    except Exception:
        return True
    if not nvr:
        return True
    try:
        from core.nvr_engine import _camera_path_name, MediaMTXApi
        rec_pid = cam.recording_profile_id
        mot_pid = cam.motion_profile_id
        name = _camera_path_name(cam)
        if profile_id == rec_pid:
            path_name = name
        elif profile_id == mot_pid:
            path_name = f"{name}_sub"
        else:
            return True
        api = MediaMTXApi()
        paths = api.list_paths()
        p = paths.get(path_name)
        if p is None:
            return False
        return bool(p.get("ready"))
    except Exception:
        return None


# ============================================================
# ★ Auto-Sub smart: بر اساس مگاپیکسل، نه عرض/ارتفاع
#   دوربین‌های 4MP و کمتر ← Full Main (تفاوت واضح با Grid)
#   دوربین‌های > 5MP ← Sub (صرفه‌جویی CPU، چون Main خیلی سنگین است)
# ============================================================
def resolve_single_profile_id(cam):
    live_pid = cam.live_profile_id
    try:
        import config

        if not getattr(config, "LIVE_AUTO_SUB_ENABLED", True):
            return live_pid

        threshold_mp = float(getattr(config, "LIVE_AUTO_SUB_ABOVE_MP", 5.0))
        min_ratio = float(getattr(config, "LIVE_AUTO_SUB_MIN_RATIO", 4.0))

        lp = cam.get_profile_by_id(live_pid) if live_pid else None
        if not (lp and lp.width and lp.height):
            return live_pid

        main_mp = (lp.width * lp.height) / 1_000_000.0
        if main_mp <= threshold_mp:
            # Main کوچک است، از Sub استفاده نکن — Full کیفیت
            return live_pid

        sub_pid = cam.grid_profile_id
        if not sub_pid or sub_pid == live_pid:
            return live_pid
        sp = cam.get_profile_by_id(sub_pid) if sub_pid else None
        if not (sp and sp.width and sp.height):
            return live_pid

        sub_mp = (sp.width * sp.height) / 1_000_000.0
        if sub_mp <= 0:
            return live_pid
        ratio = main_mp / sub_mp

        if ratio < min_ratio:
            return live_pid

        print(f"[live-helper] {cam.name or cam.uid}: auto-sub "
              f"({lp.width}x{lp.height} / {main_mp:.1f}MP) → "
              f"({sp.width}x{sp.height} / {sub_mp:.1f}MP)  ratio={ratio:.1f}x")
        return sub_pid
    except Exception:
        pass
    return live_pid