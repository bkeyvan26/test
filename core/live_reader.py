# -*- coding: utf-8 -*-
"""
K1 VMS — Live reader (Phase 4: persistent + state machine + latest-frame cache)

KEY CHANGES:
- State machine: CREATED → CONNECTING → WAITING → READY → RUNNING → ...
- Latest frame cache (for instant view switch)
- first_frame_ready signal (fired once per connection)
- UDP transport (fast for LAN)
- Exponential backoff for reconnects (up to 30s)
- Wall-clock timestamps for smooth live
- HEVC-friendly probe sizes
- Preserves the persistent read buffer (byte alignment fix)

NO camera settings changed. NO forced H.264. Works for both H264 and H265.
"""
import os
import sys
import time
import shutil
import subprocess
import threading
from PySide6.QtCore import QThread, Signal
import numpy as np

IS_WIN = sys.platform.startswith("win")

MAX_CONSECUTIVE_FAILS = 10

# Diagnostic mode: capture stderr and log ffmpeg messages
READER_DIAG_MODE = True


# ============================================================
# State machine
# ============================================================
ST_CREATED       = "created"
ST_CONNECTING    = "connecting"
ST_WAITING       = "waiting"        # ffmpeg running, no frame yet
ST_READY         = "ready"          # at least 1 frame arrived
ST_RUNNING       = "running"        # producing frames continuously
ST_RECONNECTING  = "reconnecting"
ST_ERROR         = "error"
ST_STOPPED       = "stopped"


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
        creds, hostpart = rest.split("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{hostpart}"
        return f"{scheme}://***@{hostpart}"
    except Exception:
        return url


class LiveReader(QThread):
    # ---- Public signals ----
    frame_ready = Signal(str, object)       # uid, (seq, rgb)
    status = Signal(str, str)               # uid, human-readable status text
    state_changed = Signal(str, str)        # uid, state
    first_frame_ready = Signal(str)         # uid — once per successful connect

    def __init__(self, uid, url, target_fps=5, out_w=640, out_h=360,
                 role="grid", parent=None):
        super().__init__(parent)
        self.uid = uid
        self.url = url
        self.target_fps = max(1, int(target_fps))
        self.out_w = out_w
        self.out_h = out_h
        self.role = role  # "grid" | "single"

        self._running = True
        self._seq = 0
        self._proc = None
        self._ffmpeg = _find_ffmpeg()
        self._frame_size = out_w * out_h * 3

        # persistent read buffer for byte-exact frame alignment
        self._read_buf = bytearray()

        # state
        self._state = ST_CREATED
        self._state_lock = threading.Lock()

        # latest frame cache
        self._latest_frame = None       # numpy RGB
        self._latest_frame_seq = -1
        self._first_frame_emitted = False

        # trace
        self.trace = None
        self.trace_label = ""

        # stderr capture (diagnostic)
        self._stderr_thread = None
        self._stderr_lines = []

    # ============================================================
    # Public API
    # ============================================================
    def get_state(self) -> str:
        with self._state_lock:
            return self._state

    def is_ready(self) -> bool:
        with self._state_lock:
            return self._state in (ST_READY, ST_RUNNING)

    def get_latest_frame(self):
        """Return latest decoded RGB frame or None."""
        return self._latest_frame

    def get_latest_frame_seq(self) -> int:
        return self._latest_frame_seq

    def stop(self):
        self._running = False
        self._kill_proc()

    def is_alive(self) -> bool:
        return self._running and self.isRunning()

    def proc_alive(self) -> bool:
        p = self._proc
        return bool(p is not None and p.poll() is None)

    def proc_pid(self):
        p = self._proc
        return getattr(p, "pid", None) if p is not None else None

    # ============================================================
    # Internals
    # ============================================================
    def _set_state(self, state):
        with self._state_lock:
            if self._state == state:
                return
            self._state = state
        try:
            self.state_changed.emit(self.uid, state)
        except Exception:
            pass

    def _t(self, event, **ctx):
        try:
            if self.trace is not None:
                self.trace.mark(event, **ctx)
        except Exception:
            pass

    def _isleep(self, seconds):
        end = time.monotonic() + float(seconds)
        while self._running and time.monotonic() < end:
            self.msleep(50)

    def _kill_proc(self):
        p = self._proc
        self._proc = None
        try:
            self._read_buf.clear()
        except Exception:
            pass
        if p is None:
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
            return
        if IS_WIN:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                    capture_output=True, timeout=2,
                    creationflags=0x08000000
                )
                return
            except Exception:
                pass
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=0.3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    # ============================================================
    # FFmpeg command
    # ============================================================
    def _build_cmd(self):
        vf = (
            f"fps={self.target_fps},"
            f"scale={self.out_w}:{self.out_h}:force_original_aspect_ratio=decrease,"
            f"pad={self.out_w}:{self.out_h}:(ow-iw)/2:(oh-ih)/2,"
            f"format=bgr24"
        )
        loglevel = "info" if READER_DIAG_MODE else "error"
        return [
            self._ffmpeg,
            "-y", "-hide_banner", "-loglevel", loglevel,
            # ---- UDP for LAN (fast) ----
            "-rtsp_transport", "udp",
            # ---- Low latency ----
            "-fflags", "nobuffer+discardcorrupt+genpts",
            "-flags", "low_delay",
            "-avioflags", "direct",
            "-max_delay", "0",
            # ---- Wall-clock timestamps (smooth live) ----
            "-use_wallclock_as_timestamps", "1",
            # ---- HEVC-friendly probe sizes (VPS/SPS/PPS) ----
            "-analyzeduration", "2000000",
            "-probesize", "2000000",
            "-i", self.url,
            "-an", "-sn",
            "-vf", vf,
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-"
        ]

    # ============================================================
    # stderr capture (diagnostic)
    # ============================================================
    def _stderr_reader_loop(self):
        try:
            p = self._proc
            if p is None or p.stderr is None:
                return
            t0 = time.monotonic()
            max_lines = 40
            for i, line in enumerate(iter(p.stderr.readline, b"")):
                if i >= max_lines:
                    break
                try:
                    s = line.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    s = repr(line)
                if s:
                    elapsed_ms = (time.monotonic() - t0) * 1000.0
                    self._stderr_lines.append((elapsed_ms, s))
                    self._t("FFMPEG_STDERR",
                            line_no=i + 1,
                            t_ms=f"{elapsed_ms:.0f}",
                            text=s[:200])
        except Exception as e:
            self._t("FFMPEG_STDERR_LOOP_ERROR", err=str(e))

    # ============================================================
    # Frame read (byte-exact, persistent buffer)
    # ============================================================
    def _read_frame(self, pid, instrument=True):
        expected = self._frame_size
        chunk_size = 65536
        thresholds = [0.01, 0.25, 0.50, 0.75, 1.00]
        tidx = 0
        first_byte_logged = (len(self._read_buf) > 0)

        if instrument:
            self._t("PIPE_READ_MODE",
                    mode="chunked+persistent",
                    chunk_size=chunk_size,
                    expected=expected,
                    buffered_at_start=len(self._read_buf))

        while len(self._read_buf) < expected:
            try:
                chunk = self._proc.stdout.read(chunk_size)
            except Exception as e:
                self._t("READ_EXCEPTION", err=str(e))
                return b"", False

            if not chunk:
                self._t("READ_EOF", collected=len(self._read_buf))
                return b"", False

            if not first_byte_logged and instrument:
                self._t("FIRST_BYTE_AVAILABLE",
                        n=len(chunk), pid=pid)
                first_byte_logged = True

            self._read_buf.extend(chunk)

            if instrument:
                pct = len(self._read_buf) / expected
                while tidx < len(thresholds) and pct >= thresholds[tidx]:
                    pct_val = int(thresholds[tidx] * 100)
                    self._t(f"FRAME_PROGRESS_{pct_val}",
                            bytes=len(self._read_buf),
                            expected=expected)
                    tidx += 1

        raw = bytes(self._read_buf[:expected])
        del self._read_buf[:expected]
        if instrument:
            self._t("FIRST_FRAME_BUFFER_COMPLETE",
                    n=len(raw), expected=expected,
                    leftover=len(self._read_buf))
        return raw, True

    # ============================================================
    # Main loop
    # ============================================================
    def run(self):
        if not self._ffmpeg:
            self._set_state(ST_ERROR)
            self.status.emit(self.uid, "ffmpeg not found")
            return

        consecutive_fails = 0

        while self._running:
            self._read_buf.clear()
            self._first_frame_emitted = False

            # ---- backoff for repeated failures ----
            if consecutive_fails > 0:
                delay = min(2 ** min(consecutive_fails, 5), 30.0)
                self._t("RETRY_SLEEP", sec=f"{delay:.1f}",
                        attempt=consecutive_fails)
                self._isleep(delay)
                if not self._running:
                    break

            self._set_state(ST_CONNECTING if consecutive_fails == 0
                            else ST_RECONNECTING)
            self._t("RETRY_ATTEMPT",
                    n=consecutive_fails + 1,
                    max=MAX_CONSECUTIVE_FAILS,
                    role=self.role)
            self.status.emit(self.uid, "connecting…" if consecutive_fails == 0
                             else f"reconnecting… ({consecutive_fails})")

            cmd = self._build_cmd()
            self._t("FFMPEG_COMMAND_DUMP",
                    loglevel=cmd[3],
                    url_redacted=_redact_url(self.url),
                    out_w=self.out_w, out_h=self.out_h,
                    target_fps=self.target_fps,
                    frame_size=self._frame_size,
                    transport="udp")

            self._t("SINGLE_FFMPEG_SPAWN_BEGIN",
                    attempt=consecutive_fails + 1)
            try:
                if READER_DIAG_MODE:
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        bufsize=self._frame_size * 4, **_spawn_kwargs()
                    )
                else:
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        bufsize=self._frame_size * 4, **_spawn_kwargs()
                    )
            except Exception as e:
                print(f"[LiveReader:{self.uid}] spawn error: {e}")
                self._t("SINGLE_FFMPEG_SPAWN_FAILED", err=str(e))
                consecutive_fails += 1
                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    self._set_state(ST_ERROR)
                    self.status.emit(self.uid, "offline")
                    break
                continue

            pid = self._proc.pid
            self._t("SINGLE_FFMPEG_SPAWN_END", pid=pid)

            if READER_DIAG_MODE:
                try:
                    self._stderr_thread = threading.Thread(
                        target=self._stderr_reader_loop, daemon=True
                    )
                    self._stderr_thread.start()
                except Exception:
                    self._stderr_thread = None

            # ---- wait for first frame ----
            self._set_state(ST_WAITING)
            self._t("SINGLE_FIRST_BYTES_BEGIN", pid=pid)
            first_raw, ok = self._read_frame(pid, instrument=True)
            try:
                poll = self._proc.poll() if self._proc else None
            except Exception:
                poll = None

            if not ok or not first_raw:
                self._t("SINGLE_FIRST_BYTES_READ",
                        n=len(first_raw) if first_raw else 0,
                        expected=self._frame_size,
                        proc_poll=poll, ok=False)
                self._kill_proc()
                consecutive_fails += 1
                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    self._set_state(ST_ERROR)
                    self.status.emit(self.uid, "offline")
                    break
                continue

            # ---- success ----
            self._t("SINGLE_FIRST_BYTES_READ",
                    n=len(first_raw), expected=self._frame_size, ok=True)
            self._t("SINGLE_RTSP_CONNECT_END", pid=pid)
            consecutive_fails = 0

            # decode first frame
            try:
                bgr = np.frombuffer(first_raw, np.uint8).reshape(
                    self.out_h, self.out_w, 3
                ).copy()
                rgb = bgr[:, :, ::-1].copy()
                self._seq = 1
                self._latest_frame = rgb
                self._latest_frame_seq = 1
                self._first_frame_emitted = True
                self._set_state(ST_READY)
                self._t("SINGLE_FIRST_DECODED_FRAME", seq=1)
                self._t("SINGLE_FRAME_EMITTED", seq=1)
                try:
                    self.first_frame_ready.emit(self.uid)
                except Exception:
                    pass
                self.frame_ready.emit(self.uid, (1, rgb))
                self.status.emit(self.uid, "online")
                self._t("SINGLE_STATUS_ONLINE", pid=pid)
            except Exception as e:
                self._t("SINGLE_FIRST_DECODED_FRAME_FAILED", err=str(e))
                self._kill_proc()
                consecutive_fails += 1
                continue

            # ---- continuous loop ----
            self._set_state(ST_RUNNING)
            while self._running and self._proc:
                raw, ok = self._read_frame(pid, instrument=False)
                if not ok:
                    break
                try:
                    bgr = np.frombuffer(raw, np.uint8).reshape(
                        self.out_h, self.out_w, 3
                    ).copy()
                    rgb = bgr[:, :, ::-1].copy()
                    self._seq += 1
                    self._latest_frame = rgb
                    self._latest_frame_seq = self._seq
                    self.frame_ready.emit(self.uid, (self._seq, rgb))
                except Exception:
                    pass

            self._t("STREAM_LOOP_ENDED", seq=self._seq)
            self._kill_proc()
            if not self._running:
                break

            self._set_state(ST_RECONNECTING)
            self.status.emit(self.uid, "reconnecting…")
            self._t("RECONNECT_AFTER_LOSS", seq=self._seq)
            self._isleep(1.0)

        self._set_state(ST_STOPPED)
        self.status.emit(self.uid, "stopped")
        self._t("READER_THREAD_STOPPED")