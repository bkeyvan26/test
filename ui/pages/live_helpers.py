# -*- coding: utf-8 -*-
"""Pure helper functions for LivePage (Phase 6.5).

اصل طلایی: اگر Admin عدد FPS داده، همان اجرا می‌شود.
ما فقط برای حالت «auto» تصمیم می‌گیریم.
"""


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
    """
    ★ Phase 6.5: Admin تصمیم می‌گیرد.
    - اگر admin عدد داده (override > 0): همان استفاده شود، بدون هیچ محدودیتی.
    - اگر auto: پیش‌فرض هوشمند (بسته به تعداد کاشی و fps دوربین).
    """
    if role == "grid":
        profile_id = cam.grid_profile_id
        override_fps = int(getattr(cam, "grid_display_fps", 0) or 0)
    else:
        profile_id = cam.live_profile_id
        override_fps = int(getattr(cam, "live_display_fps", 0) or 0)

    profile = cam.get_profile_by_id(profile_id) if profile_id else None
    src_fps = profile.fps if (profile and profile.fps) else 0

    # ★ اگر admin عدد داده، احترام بگذار — هیچ cap یا محدودیتی اعمال نکن
    if override_fps > 0:
        return max(1, override_fps)

    # ★ اگر auto: پیش‌فرض هوشمند

    if role == "single":
        # single: از fps دوربین، سقف 15
        if src_fps > 0:
            return min(src_fps, 15)
        return 15

    # grid auto
    if src_fps > 0:
        base = min(src_fps, 10)
    else:
        base = 5

    # فقط در حالت auto، با تعداد زیاد کاشی کاهش بده
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
# ★ Phase 6.4: انتخاب profile برای single
# ============================================================
def resolve_single_profile_id(cam):
    """اگر رزولیشن Main خیلی بالاست، برای Live از Sub استفاده کن."""
    live_pid = cam.live_profile_id
    try:
        import config
        max_w = int(getattr(config, "LIVE_AUTO_SUB_ABOVE_WIDTH", 1920))
        max_h = int(getattr(config, "LIVE_AUTO_SUB_ABOVE_HEIGHT", 1080))
        lp = cam.get_profile_by_id(live_pid) if live_pid else None
        if lp and lp.width and lp.height and \
           (lp.width > max_w or lp.height > max_h):
            sub_pid = cam.grid_profile_id
            sp = cam.get_profile_by_id(sub_pid) if sub_pid else None
            if sp and sp.width and sp.height:
                print(f"[live-helper] {cam.name or cam.uid}: using Sub "
                      f"({sp.width}x{sp.height}) instead of "
                      f"Main ({lp.width}x{lp.height})")
                return sub_pid
    except Exception:
        pass
    return live_pid