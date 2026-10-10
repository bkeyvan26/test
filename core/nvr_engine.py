# -*- coding: utf-8 -*-
"""K1 VMS — NVR Engine (MediaMTX detached process manager)

Phase 8.1:
  - path از record_folder_name ادمین استفاده می‌کند (اگر داده شده)
  - اگر نداد، از uid خودکار (nvr_xxx/ch_NN) استفاده می‌کند
  - نتیجه روی دیسک:
      continuous/negahbani/ch_01/2026-10-07/13-14-47.ts
      continuous/dakheli/ch_15/2026-10-07/13-14-47.ts
      continuous/harasat/2026-10-07/13-14-47.ts
"""
import os, sys, re, json, time, socket, subprocess, threading, urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Callable

from core.models import CameraConfig, GlobalSettings
import config

IS_WIN = sys.platform.startswith("win")

DETACHED_PROCESS           = 0x00000008
CREATE_NEW_PROCESS_GROUP   = 0x00000200
CREATE_BREAKAWAY_FROM_JOB  = 0x01000000
CREATE_NO_WINDOW           = 0x08000000

MEDIAMTX_PID_FILE = config.APP_DIR / "mediamtx.pid"
STARTUP_TIMEOUT_SEC = 20.0


# ============================================================
# Helpers
# ============================================================
def _spawn_detached_kwargs():
    if IS_WIN:
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        return {"creationflags": flags}
    return {"start_new_session": True}


def _spawn_hidden_kwargs():
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": CREATE_NO_WINDOW}
    return {}


def _port_in_use(port: int, host: str = "127.0.0.1",
                 timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# ============================================================
# ★ Path name — اولویت: record_folder_name ادمین
# ============================================================
def _sanitize_ascii_path(name: str) -> str:
    """حذف کاراکترهای غیر ASCII از نام پوشه (MediaMTX محدودیت دارد)."""
    if not name:
        return ""
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "", name)
    return safe.strip("_-")[:40]


def _camera_path_name(cam: CameraConfig) -> str:
    """
    MediaMTX path (ASCII).
    
    اولویت:
      1. اگر cam.record_folder_name داده شده:
         - NVR: {folder}/ch_NN
         - standalone: {folder}
      2. اگر NVR و record_folder_name خالی:
         - nvr_{nvr_uid}/ch_NN
      3. standalone بدون نام:
         - cam_{uid}
    """
    uid = (cam.uid or "").strip()
    if not uid:
        return "cam_unknown"
    ascii_uid = re.sub(r"[^a-zA-Z0-9]", "", uid)[:12] or "unknown"

    # ★ نام پوشه‌ی ادمین
    folder = _sanitize_ascii_path(cam.record_folder_name or "")

    if cam.nvr_uid:
        # اگر ادمین نام داده، از آن استفاده کن
        if folder:
            nvr_folder = folder
        else:
            nvr_ascii = re.sub(r"[^a-zA-Z0-9]", "", cam.nvr_uid or "")[:8]
            nvr_folder = f"nvr_{nvr_ascii}" if nvr_ascii else f"nvr_{ascii_uid}"

        # شماره کانال
        m = re.search(r"دوربین\s+(\d+)", cam.name or "")
        if m:
            ch = int(m.group(1))
            return f"{nvr_folder}/ch_{ch:02d}"
        return f"{nvr_folder}/cam_{ascii_uid}"

    # standalone
    if folder:
        return folder
    return f"cam_{ascii_uid}"


def _build_rtsp_url(cam: CameraConfig, use_sub: bool = False) -> str:
    import urllib.parse
    path = (cam.rtsp_path_sub if use_sub and cam.rtsp_path_sub
            else cam.rtsp_path_main) or "/"
    if path.startswith("rtsp://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    u = urllib.parse.quote(cam.user or "", safe="")
    pw = urllib.parse.quote(cam.password or "", safe="")
    return f"rtsp://{u}:{pw}@{cam.ip}:{cam.port}{path}"


def _pick_recording_url(cam: CameraConfig) -> str:
    try:
        url = cam.get_recording_url()
        if url:
            return url
    except Exception:
        pass
    return _build_rtsp_url(cam, use_sub=False)


def _pick_motion_url(cam: CameraConfig) -> str:
    try:
        url = cam.get_motion_url()
        if url:
            return url
    except Exception:
        pass
    return _build_rtsp_url(cam, use_sub=bool(cam.rtsp_path_sub))


def _read_log_tail(path: str, max_lines: int = 30) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192), os.SEEK_SET)
            data = f.read().decode("utf-8", errors="replace")
        lines = [l for l in data.splitlines() if l.strip()]
        return "\n".join(lines[-max_lines:])
    except Exception as e:
        return f"(could not read log: {e})"


# ============================================================
# MediaMTX Config Builder
# ============================================================
class MediaMTXConfigBuilder:
    def __init__(self, cameras: List[CameraConfig],
                 settings: GlobalSettings):
        self.cameras = cameras
        self.settings = settings

    def build(self, output_path) -> str:
        lines = self._header()
        lines.append("paths:")

        written = 0
        seen_names = set()
        for cam in self.cameras:
            if not cam.enabled:
                continue
            if not cam.has_connection_info():
                continue
            pname = _camera_path_name(cam)
            base_pname = pname
            n = 2
            while pname in seen_names:
                pname = f"{base_pname}_{n}"
                n += 1
            seen_names.add(pname)
            lines.extend(self._camera_entry(cam, pname))
            written += 1

        if written == 0:
            lines.append("  __placeholder__:")
            lines.append("    source: publisher")
            lines.append("")

        Path(output_path).write_text("\n".join(lines), encoding="utf-8")
        return str(output_path)

    def _header(self):
        return [
            "logLevel: info",
            "logDestinations: [stdout]",
            "",
            "rtsp: yes",
            f"rtspAddress: :{config.MEDIAMTX_RTSP_PORT}",
            "rtspTransports: [tcp]",
            "",
            "api: yes",
            f"apiAddress: :{config.MEDIAMTX_API_PORT}",
            "",
        ]

    def _camera_entry(self, cam: CameraConfig, pname: str) -> List[str]:
        rec_enabled = bool(
            self.settings.record_enabled
            and (cam.record_enabled_continuous or cam.record_enabled_motion)
        )

        rec_profile = cam.get_recording_profile() if hasattr(
            cam, "get_recording_profile") else None
        rec_profile_name = rec_profile.name if rec_profile else "legacy(main)"

        print(f"[NVR] {pname}: record={rec_enabled} "
              f"(cont={cam.record_enabled_continuous}, "
              f"motion={cam.record_enabled_motion}, "
              f"global={self.settings.record_enabled}) "
              f"rec_profile={rec_profile_name}")

        rec_root = (
            cam.record_path_override
            or self.settings.recording_root
            or str(config.CONTINUOUS_DIR)
        )
        rec_pattern = str(
            Path(rec_root) / "%path" / "%Y-%m-%d" / "%H-%M-%S"
        )
        seg_min = cam.record_segment_minutes or self.settings.record_segment_minutes
        seg_min = max(1, min(30, int(seg_min)))
        retention_h = max(1, int(self.settings.retention_days)) * 24

        main_url = _pick_recording_url(cam)

        lines = []
        lines.append(f"  {pname}:")
        lines.append(f'    source: "{main_url}"')
        lines.append("    rtspTransport: tcp")
        lines.append("    sourceOnDemand: no")
        if rec_enabled:
            lines.append("    record: yes")
            lines.append(f"    recordPath: {rec_pattern}")
            lines.append("    recordFormat: mpegts")
            lines.append(f"    recordSegmentDuration: {seg_min}m")
            lines.append(f"    recordDeleteAfter: {retention_h}h")
        else:
            lines.append("    record: no")
        lines.append("")

        sub_url = _pick_motion_url(cam)
        if sub_url and sub_url != main_url:
            lines.append(f"  {pname}_sub:")
            lines.append(f'    source: "{sub_url}"')
            lines.append("    rtspTransport: tcp")
            lines.append("    sourceOnDemand: no")
            lines.append("")

        return lines


# ============================================================
# MediaMTX API client
# ============================================================
class MediaMTXApi:
    def __init__(self, host="127.0.0.1", port=config.MEDIAMTX_API_PORT):
        self.base = f"http://{host}:{port}"

    def _get(self, path: str, timeout: float = 2.0):
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def is_healthy(self, timeout: float = 2.0) -> bool:
        return self._get("/v3/config/global/get", timeout=timeout) is not None

    def list_paths(self) -> Dict[str, dict]:
        data = self._get("/v3/paths/list")
        if not data:
            return {}
        result = {}
        for item in data.get("items", []) or []:
            name = item.get("name") or item.get("confName") or ""
            if name:
                result[name] = item
        return result

    def get_config_paths(self) -> Dict[str, dict]:
        data = self._get("/v3/config/paths/list")
        if not data:
            return {}
        result = {}
        for item in data.get("items", []) or []:
            name = item.get("name") or ""
            if name:
                result[name] = item
        return result


# ============================================================
# MediaMTX Process manager
# ============================================================
class MediaMTXProcess:
    def __init__(self, exe, cfg, log, pid_file):
        self.exe = str(exe)
        self.cfg = str(cfg)
        self.log = str(log)
        self.pid_file = Path(pid_file)
        self._proc: Optional[subprocess.Popen] = None
        self._pid: Optional[int] = None
        self._external = False

    def detect_existing(self) -> bool:
        api = MediaMTXApi()
        if api.is_healthy(timeout=0.5):
            self._external = True
            if self.pid_file.exists():
                try:
                    self._pid = int(self.pid_file.read_text().strip())
                except Exception:
                    self._pid = None
            return True
        if self.pid_file.exists():
            try:
                self.pid_file.unlink()
            except Exception:
                pass
        return False

    def _pid_alive(self, pid: int) -> bool:
        if not IS_WIN:
            try:
                os.kill(pid, 0)
                return True
            except Exception:
                return False
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=3,
                **_spawn_hidden_kwargs()
            )
            return "mediamtx" in (r.stdout or "").lower()
        except Exception:
            return False

    def _kill_pid(self, pid: int):
        if IS_WIN:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                    **_spawn_hidden_kwargs()
                )
            except Exception:
                pass
        else:
            try:
                os.kill(pid, 15)
            except Exception:
                pass

    def _kill_by_name(self):
        if not IS_WIN:
            return
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "mediamtx.exe"],
                capture_output=True, timeout=5,
                **_spawn_hidden_kwargs()
            )
        except Exception:
            pass

    def start(self, force: bool = False) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        if not force:
            if self._external and MediaMTXApi().is_healthy(timeout=0.5):
                return True
            if self.detect_existing():
                return True

        if not Path(self.exe).exists():
            raise FileNotFoundError(f"MediaMTX not found at {self.exe}")

        if force:
            self._kill_by_name()
            time.sleep(0.5)
        else:
            if (_port_in_use(config.MEDIAMTX_API_PORT)
                    or _port_in_use(config.MEDIAMTX_RTSP_PORT)):
                print("[NVR] ports busy → killing leftover mediamtx.exe")
                self._kill_by_name()
                time.sleep(0.8)

        try:
            if Path(self.log).exists():
                Path(self.log).unlink()
        except Exception:
            pass

        try:
            log_f = open(self.log, "wb")
        except Exception:
            log_f = None

        try:
            kw = _spawn_detached_kwargs()
            popen_kwargs = dict(
                stdin=subprocess.DEVNULL,
                stdout=log_f if log_f is not None else subprocess.DEVNULL,
                stderr=(subprocess.STDOUT if log_f is not None
                        else subprocess.DEVNULL),
                cwd=str(Path(self.exe).parent),
                close_fds=True,
            )
            popen_kwargs.update(kw)
            self._proc = subprocess.Popen([self.exe, self.cfg], **popen_kwargs)
        finally:
            if log_f is not None:
                try:
                    log_f.close()
                except Exception:
                    pass

        self._pid = self._proc.pid
        try:
            self.pid_file.write_text(str(self._pid))
        except Exception:
            pass
        self._external = False

        deadline = time.time() + STARTUP_TIMEOUT_SEC
        while time.time() < deadline:
            time.sleep(0.15)
            if self._proc.poll() is not None:
                return False
            if _port_in_use(config.MEDIAMTX_API_PORT):
                return True
        return False

    def stop(self, wait_for_death: bool = True):
        if self._pid and self._pid_alive(self._pid):
            self._kill_pid(self._pid)
        elif self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

        if wait_for_death:
            api = MediaMTXApi()
            for _ in range(50):
                if not api.is_healthy(timeout=0.3):
                    break
                time.sleep(0.1)

        try:
            self.pid_file.unlink()
        except Exception:
            pass
        self._proc = None
        self._pid = None
        self._external = False

    def is_running(self) -> bool:
        return MediaMTXApi().is_healthy(timeout=0.5)


# ============================================================
# NVR Engine — top-level orchestrator
# ============================================================
class NVREngine:
    def __init__(self):
        self._cameras: List[CameraConfig] = []
        self._settings: Optional[GlobalSettings] = None
        self._builder: Optional[MediaMTXConfigBuilder] = None
        self._process: Optional[MediaMTXProcess] = None
        self._api = MediaMTXApi()
        self._lock = threading.Lock()
        self._status_cache: Dict[str, dict] = {}
        self._last_poll = 0.0
        self._started = False
        self._last_error = ""
        self._listeners: List[Callable] = []

    def start(self, cameras: List[CameraConfig],
              settings: GlobalSettings) -> bool:
        with self._lock:
            self._cameras = list(cameras)
            self._settings = settings

            self._builder = MediaMTXConfigBuilder(self._cameras, self._settings)
            self._builder.build(config.MEDIAMTX_YML)

            self._process = MediaMTXProcess(
                exe=config.MEDIAMTX_EXE,
                cfg=config.MEDIAMTX_YML,
                log=config.MEDIAMTX_LOG,
                pid_file=MEDIAMTX_PID_FILE,
            )
            try:
                ok = self._process.start(force=False)
            except Exception as e:
                self._last_error = str(e)
                self._started = False
                print(f"[NVR] start exception: {e}")
                return False

            self._started = bool(ok)
            if not ok:
                self._last_error = "MediaMTX failed to start (check mediamtx.log)"
                tail = _read_log_tail(config.MEDIAMTX_LOG, max_lines=25)
                print("[NVR] ───── mediamtx.log tail ─────")
                print(tail)
                print("[NVR] ─────────────────────────────")
            else:
                print("[NVR] MediaMTX started")
            return self._started

    def stop(self):
        with self._lock:
            if self._process:
                self._process.stop(wait_for_death=False)
            self._process = None
            self._started = False

    def reload(self, cameras: Optional[List[CameraConfig]] = None,
               settings: Optional[GlobalSettings] = None) -> bool:
        print("[NVR] reload requested…")

        if cameras is not None:
            self._cameras = list(cameras)
        if settings is not None:
            self._settings = settings
        if self._settings is None:
            print("[NVR] reload aborted: no settings")
            return False

        self._builder = MediaMTXConfigBuilder(self._cameras, self._settings)
        self._builder.build(config.MEDIAMTX_YML)
        print(f"[NVR] config written → {config.MEDIAMTX_YML}")

        if self._process:
            print("[NVR] stopping old MediaMTX…")
            self._process.stop(wait_for_death=True)

        self._process = MediaMTXProcess(
            exe=config.MEDIAMTX_EXE,
            cfg=config.MEDIAMTX_YML,
            log=config.MEDIAMTX_LOG,
            pid_file=MEDIAMTX_PID_FILE,
        )
        try:
            self._started = bool(self._process.start(force=True))
        except Exception as e:
            self._last_error = str(e)
            self._started = False
            print(f"[NVR] reload failed: {e}")
            return False

        if not self._started:
            tail = _read_log_tail(config.MEDIAMTX_LOG, max_lines=25)
            print("[NVR] ───── mediamtx.log tail ─────")
            print(tail)
            print("[NVR] ─────────────────────────────")

        print(f"[NVR] reload result: {self._started}")
        return self._started

    def is_running(self) -> bool:
        return self._api.is_healthy(timeout=0.5)

    def is_started(self) -> bool:
        return self._started

    def get_last_error(self) -> str:
        return self._last_error

    def poll_status(self):
        now = time.time()
        if now - self._last_poll < 3.0:
            return
        self._last_poll = now

        if not self.is_running():
            return

        paths = self._api.list_paths()
        new_cache = {}
        for cam in self._cameras:
            pname = _camera_path_name(cam)
            pinfo = paths.get(pname)
            if pinfo is None:
                new_cache[cam.uid] = {"status": "starting", "info": None}
                continue
            ready = bool(pinfo.get("ready"))
            last_err = pinfo.get("lastError") or ""
            bytes_recv = int(pinfo.get("bytesReceived") or 0)
            if not ready and last_err:
                st = "error"
            elif not ready:
                st = "reconnecting"
            elif ready and bytes_recv > 0:
                st = "recording"
            elif ready:
                st = "online"
            else:
                st = "unknown"
            new_cache[cam.uid] = {
                "status": st,
                "info": pinfo,
                "bytes_received": bytes_recv,
                "last_error": last_err,
            }

        old_summary = {uid: info.get("status")
                       for uid, info in self._status_cache.items()}
        new_summary = {uid: info.get("status")
                       for uid, info in new_cache.items()}
        self._status_cache = new_cache

        if old_summary != new_summary:
            for cb in list(self._listeners):
                try:
                    cb(new_cache)
                except Exception:
                    pass

    def get_status(self, cam_uid: str) -> str:
        return self._status_cache.get(cam_uid, {}).get("status", "unknown")

    def get_all_statuses(self) -> Dict[str, str]:
        return {
            uid: info.get("status", "unknown")
            for uid, info in self._status_cache.items()
        }

    def get_recording_count(self) -> int:
        return sum(
            1 for i in self._status_cache.values()
            if i.get("status") == "recording"
        )

    def get_error_count(self) -> int:
        return sum(
            1 for i in self._status_cache.values()
            if i.get("status") == "error"
        )

    def register_listener(self, cb: Callable):
        if cb not in self._listeners:
            self._listeners.append(cb)

    def unregister_listener(self, cb: Callable):
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    def get_recording_root(self) -> str:
        if self._settings and self._settings.recording_root:
            return self._settings.recording_root
        return str(config.CONTINUOUS_DIR)

    def get_recorded_camera_uids(self) -> List[str]:
        if not self._settings or not self._settings.record_enabled:
            return []
        return [
            c.uid for c in self._cameras
            if c.enabled and c.has_connection_info()
            and (c.record_enabled_continuous or c.record_enabled_motion)
        ]