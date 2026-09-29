# -*- coding: utf-8 -*-
"""K1 VMS — Live reader (Phase 4.2: err-detect for weak streams)"""
import os
import sys
import time
import shutil
import subprocess
import threading
from urllib.parse import unquote, quote
from PySide6.QtCore import QThread, Signal
import numpy as np

IS_WIN = sys.platform.startswith("win")
MAX_CONSECUTIVE_FAILS = 10

ST_CREATED   = "created"
ST_STARTING  = "starting"
ST_WAITING   = "waiting"
ST_READY     = "ready"
ST_RUNNING   = "running"
ST_STOPPING  = "stopping"
ST_STOPPED   = "stopped"
ST_ERROR     = "error"


def _spawn_kwargs():
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def _find_ffmpeg():
    try:
        from core.ffprobe_util import get_ffmpeg_exe
        exe = get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _redact_url(url: str) -> str:
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


def _sanitize_rtsp_url(url: str) -> str:
    try:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        creds, hostpart = rest.rsplit("@", 1)
        if ":" in creds:
            user, pw = creds.split(":", 1)
            user_enc = quote(unquote(user), safe="")
            pw_enc = quote(unquote(pw), safe="")
            return f"{scheme}://{user_enc}:{pw_enc}@{hostpart}"
        else:
            user_enc = quote(unquote(creds), safe="")
            return f"{scheme}://{user_enc}@{hostpart}"
    except Exception:
        return url


def _is_mediamtx_url(url: str) -> bool:
    try:
        low = url.lower()
        return ("127.0.0.1" in low) or ("localhost" in low)
    except Exception:
        return False


class LiveReader(QThread):
    frame_ready = Signal(str, object)
    status = Signal(str, str)
    state_changed = Signal(str, str)
    first_frame_ready = Signal(str)

    def __init__(self, uid, url, target_fps=5, out_w=640, out_h=360,
                 role="grid", transport="udp", parent=None):
        super().__init__(parent)
        self.uid = uid
        self.url = _sanitize_rtsp_url(url)
        self.target_fps = max(1, int(target_fps))
        self.out_w = out_w
        self.out_h = out_h
        self.role = role
        self.transport = transport if transport in ("udp", "tcp") else "udp"
        self._force_tcp = _is_mediamtx_url(self.url)

        self._running = threading.Event()
        self._running.set()
        self._stop_lock = threading.Lock()
        self._closing = False

        self._seq = 0
        self._proc = None
        self._proc_lock = threading.Lock()
        self._ffmpeg = _find_ffmpeg()
        self._frame_size = out_w * out_h * 3

        self._read_buf = bytearray()
        self._state = ST_CREATED
        self._state_lock = threading.Lock()
        self._latest_frame = None
        self._latest_frame_seq = -1

        self._stderr_thread = None
        self._attempt = 0

        self._created_at = time.monotonic()
        self._spawn_begin_at = None
        self._spawn_end_at = None
        self._first_byte_at = None
        self._first_frame_at = None
        self._udp_failed_461 = False
        self._hevc_join_detected = False

        self._warmup_total = 1
        self._warmup_done = 0

    def get_state(self) -> str:
        with self._state_lock:
            return self._state

    def is_ready(self) -> bool:
        if self._latest_frame is None:
            return False
        with self._state_lock:
            return self._state in (ST_READY, ST_RUNNING)

    def get_latest_frame(self):
        return self._latest_frame

    def get_latest_frame_seq(self) -> int:
        return self._latest_frame_seq

    def created_elapsed(self) -> float:
        return time.monotonic() - self._created_at

    def first_frame_elapsed(self):
        if self._first_frame_at is None:
            return None
        return self._first_frame_at - self._created_at

    def stop(self):
        with self._stop_lock:
            self._running.clear()
        self._closing = True
        self._set_state(ST_STOPPING)
        self._kill_proc_internal()

    def is_alive(self) -> bool:
        try:
            return self.isRunning()
        except RuntimeError:
            return False

    def proc_alive(self) -> bool:
        with self._proc_lock:
            p = self._proc
        return bool(p is not None and p.poll() is None)

    def proc_pid(self):
        with self._proc_lock:
            p = self._proc
        return getattr(p, "pid", None) if p is not None else None

    def effective_transport(self) -> str:
        if self._force_tcp:
            return "tcp"
        return self.transport

    def _set_state(self, state):
        with self._state_lock:
            if self._state == state:
                return
            self._state = state
        try:
            self.state_changed.emit(self.uid, state)
        except Exception:
            pass

    def _isleep(self, seconds):
        end = time.monotonic() + float(seconds)
        while self._running.is_set() and time.monotonic() < end:
            self.msleep(50)

    def _kill_proc_internal(self):
        self._closing = True
        with self._proc_lock:
            p = self._proc
            self._proc = None
        try:
            self._read_buf.clear()
        except Exception:
            pass
        if p is None:
            self._closing = False
            return
        try:
            if p.stdout:
                p.stdout.close()
        except Exception:
            pass
        try:
            if p.stderr:
                p.stderr.close()
        except Exception:
            pass
        if p.poll() is not None:
            self._closing = False
            return
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=0.4)
            self._closing = False
            return
        except Exception:
            pass
        try:
            p.kill()
        except Exception:
            pass
        try:
            p.wait(timeout=0.3)
            self._closing = False
            return
        except Exception:
            pass
        if IS_WIN:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(p.pid)],
                    capture_output=True, timeout=1,
                    creationflags=0x08000000)
            except Exception:
                pass
        self._closing = False

    def _build_vf(self):
        return (f"fps={self.target_fps},"
                f"scale={self.out_w}:{self.out_h}:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={self.out_w}:{self.out_h}:(ow-iw)/2:(oh-ih)/2,"
                f"format=rgb24")

    def _build_cmd(self):
        vf = self._build_vf()
        transport = self.effective_transport()

        if self.role == "grid":
            threads = "1"
            analyzedur = "800000"
            probesize = "1200000"
            fflags = "nobuffer+discardcorrupt+genpts+igndts"
        else:
            threads = "3"
            analyzedur = "1500000"
            probesize = "2000000"
            fflags = "discardcorrupt+genpts+igndts"

        args = [
            self._ffmpeg,
            "-y", "-hide_banner", "-loglevel", "error",
            "-threads", threads,
            "-rtsp_transport", transport,
            "-fflags", fflags,
            "-flags", "low_delay",
            # ★ Phase 4.2: تحمل خطاهای دیکد (برای دوربین‌های ضعیف/NVR)
            "-err_detect", "ignore_err",
        ]
        if self.role == "single":
            args += ["-use_wallclock_as_timestamps", "1"]
        args += [
            "-analyzeduration", analyzedur,
            "-probesize", probesize,
            "-i", self.url,
            "-an", "-sn",
            "-vf", vf,
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-"
        ]
        return args

    def _stderr_reader_loop(self):
        try:
            with self._proc_lock:
                p = self._proc
            if p is None or p.stderr is None:
                return
            logged = 0
            for line in iter(p.stderr.readline, b""):
                if not self._running.is_set() or self._closing:
                    break
                try:
                    s = line.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    continue
                if not s:
                    continue
                if ("VPS" in s and "does not exist" in s) or \
                   ("PPS" in s and "out of range" in s):
                    if not self._hevc_join_detected:
                        self._hevc_join_detected = True
                        print(f"[PREWARM] HEVC_JOIN uid={self.uid}")
                if logged < 5:
                    print(f"[Reader:{self.uid}:{self.role}] STDERR {s[:180]}")
                    logged += 1
                if "461" in s and "Unsupported Transport" in s:
                    if not self._force_tcp:
                        self._udp_failed_461 = True
        except Exception:
            pass

    def _read_frame(self, pid):
        expected = self._frame_size
        chunk_size = 262144
        with self._proc_lock:
            proc = self._proc
        if proc is None:
            return b"", False
        stdout = proc.stdout
        if stdout is None:
            return b"", False
        while len(self._read_buf) < expected:
            if not self._running.is_set() or self._closing:
                return b"", False
            try:
                chunk = stdout.read(chunk_size)
            except Exception:
                return b"", False
            if not chunk:
                return b"", False
            if self._first_byte_at is None:
                self._first_byte_at = time.monotonic()
                if self.role == "single" and self._spawn_begin_at is not None:
                    fb_ms = (self._first_byte_at - self._spawn_begin_at) * 1000
                    prewarm_ms = (self._first_byte_at
                                  - self._created_at) * 1000
                    print(f"[PREWARM] FIRST_BYTES uid={self.uid} "
                          f"prewarm_elapsed_ms={prewarm_ms:.0f} "
                          f"spawn_to_bytes_ms={fb_ms:.0f}")
            self._read_buf.extend(chunk)
        raw = bytes(self._read_buf[:expected])
        del self._read_buf[:expected]
        return raw, True

    def run(self):
        if not self._ffmpeg:
            self._set_state(ST_ERROR)
            try:
                self.status.emit(self.uid, "ffmpeg not found")
            except Exception:
                pass
            return
        consecutive_fails = 0
        try:
            while self._running.is_set():
                self._read_buf.clear()
                self._attempt = consecutive_fails + 1
                self._warmup_done = 0
                self._first_byte_at = None

                if consecutive_fails > 0:
                    delay = min(2 ** min(consecutive_fails, 5), 30.0)
                    self._isleep(delay)
                    if not self._running.is_set():
                        break

                if self._udp_failed_461:
                    self._force_tcp = True

                self._set_state(ST_STARTING if consecutive_fails == 0
                                else "reconnecting")
                try:
                    self.status.emit(self.uid,
                                     "connecting…" if consecutive_fails == 0
                                     else f"reconnecting… ({consecutive_fails})")
                except Exception:
                    pass

                cmd = self._build_cmd()
                self._spawn_begin_at = time.monotonic()
                try:
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        bufsize=self._frame_size * 2, **_spawn_kwargs())
                except Exception as e:
                    print(f"[Reader:{self.uid}] spawn error: {e}")
                    consecutive_fails += 1
                    if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                        self._set_state(ST_ERROR)
                        try:
                            self.status.emit(self.uid, "offline")
                        except Exception:
                            pass
                        break
                    continue

                self._spawn_end_at = time.monotonic()
                with self._proc_lock:
                    self._proc = proc

                if self.role == "single":
                    print(f"[PREWARM] SPAWN uid={self.uid} pid={proc.pid}")

                try:
                    self._stderr_thread = threading.Thread(
                        target=self._stderr_reader_loop, daemon=True)
                    self._stderr_thread.start()
                except Exception:
                    pass

                self._set_state(ST_WAITING)

                warm_ok = True
                for _ in range(self._warmup_total):
                    if not self._running.is_set():
                        warm_ok = False
                        break
                    _raw, ok = self._read_frame(proc.pid)
                    if not ok:
                        warm_ok = False
                        break
                    self._warmup_done += 1

                if not warm_ok:
                    self._kill_proc_internal()
                    if not self._running.is_set():
                        break
                    consecutive_fails += 1
                    if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                        self._set_state(ST_ERROR)
                        try:
                            self.status.emit(self.uid, "offline")
                        except Exception:
                            pass
                        break
                    continue

                first_raw, ok = self._read_frame(proc.pid)
                if not ok or not first_raw:
                    self._kill_proc_internal()
                    if not self._running.is_set():
                        break
                    consecutive_fails += 1
                    if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                        self._set_state(ST_ERROR)
                        try:
                            self.status.emit(self.uid, "offline")
                        except Exception:
                            pass
                        break
                    continue

                consecutive_fails = 0

                try:
                    rgb = np.frombuffer(first_raw, np.uint8).reshape(
                        self.out_h, self.out_w, 3)
                    self._seq = 1
                    self._latest_frame = rgb
                    self._latest_frame_seq = 1
                    self._first_frame_at = time.monotonic()
                    self._set_state(ST_READY)
                    try:
                        self.first_frame_ready.emit(self.uid)
                    except Exception:
                        pass
                    self.frame_ready.emit(self.uid, (1, rgb))
                    try:
                        self.status.emit(self.uid, "online")
                    except Exception:
                        pass
                    if self.role == "single":
                        pf = (self._first_frame_at
                              - self._created_at) * 1000
                        print(f"[PREWARM] FIRST_FRAME uid={self.uid} "
                              f"prewarm_elapsed_ms={pf:.0f}")
                except Exception:
                    self._kill_proc_internal()
                    consecutive_fails += 1
                    continue

                self._set_state(ST_RUNNING)
                while self._running.is_set():
                    raw, ok = self._read_frame(proc.pid)
                    if not ok:
                        break
                    try:
                        rgb = np.frombuffer(raw, np.uint8).reshape(
                            self.out_h, self.out_w, 3)
                        self._seq += 1
                        self._latest_frame = rgb
                        self._latest_frame_seq = self._seq
                        self.frame_ready.emit(self.uid, (self._seq, rgb))
                    except Exception:
                        pass

                self._kill_proc_internal()
                if not self._running.is_set():
                    break

                self._set_state("reconnecting")
                try:
                    self.status.emit(self.uid, "reconnecting…")
                except Exception:
                    pass
                self._isleep(1.0)
        finally:
            try:
                self._kill_proc_internal()
            except Exception:
                pass
            self._set_state(ST_STOPPED)
            if not self._closing:
                try:
                    self.status.emit(self.uid, "stopped")
                except Exception:
                    pass