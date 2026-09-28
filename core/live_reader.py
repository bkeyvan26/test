# -*- coding: utf-8 -*-
"""
K1 VMS — Live reader (low-latency RTSP)
- Fast ffmpeg startup (small probe/analyze window)
- Gentle backoff for fast recovery
"""
import os
import sys
import time
import shutil
import subprocess
from PySide6.QtCore import QThread, Signal
import numpy as np

IS_WIN = sys.platform.startswith("win")

MAX_CONSECUTIVE_FAILS = 10


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


class LiveReader(QThread):
    frame_ready = Signal(str, object)
    status = Signal(str, str)

    def __init__(self, uid, url, target_fps=5, out_w=640, out_h=360, parent=None):
        super().__init__(parent)
        self.uid = uid
        self.url = url
        self.target_fps = max(1, int(target_fps))
        self.out_w = out_w
        self.out_h = out_h
        self._running = True
        self._seq = 0
        self._proc = None
        self._ffmpeg = _find_ffmpeg()
        self._frame_size = out_w * out_h * 3

    def _isleep(self, seconds):
        end = time.monotonic() + float(seconds)
        while self._running and time.monotonic() < end:
            self.msleep(50)

    def _kill_proc(self):
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            if p.stdout:
                p.stdout.close()
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

    def stop(self):
        self._running = False
        self._kill_proc()

    def _build_cmd(self):
        vf = (
            f"fps={self.target_fps},"
            f"scale={self.out_w}:{self.out_h}:force_original_aspect_ratio=decrease,"
            f"pad={self.out_w}:{self.out_h}:(ow-iw)/2:(oh-ih)/2,"
            f"format=bgr24"
        )
        return [
            self._ffmpeg,
            "-y", "-hide_banner", "-loglevel", "error",
            # ---- Fast RTSP negotiation ----
            "-rtsp_transport", "tcp",
            "-rtsp_flags", "prefer_tcp",
            # ---- Low latency input ----
            "-fflags", "nobuffer+discardcorrupt+genpts",
            "-flags", "low_delay",
            "-avioflags", "direct",
            "-max_delay", "500000",
            # ---- ★ Small probe window for fast first frame ----
            "-analyzeduration", "300000",
            "-probesize", "500000",
            # ---- Input ----
            "-i", self.url,
            "-an", "-sn",
            "-vf", vf,
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-"
        ]

    def run(self):
        if not self._ffmpeg:
            self.status.emit(self.uid, "ffmpeg not found")
            return

        consecutive_fails = 0

        while self._running:
            if consecutive_fails == 0:
                self.status.emit(self.uid, "connecting…")
            else:
                self.status.emit(
                    self.uid, f"retry {consecutive_fails}/{MAX_CONSECUTIVE_FAILS}"
                )

            try:
                self._proc = subprocess.Popen(
                    self._build_cmd(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=self._frame_size * 4,
                    **_spawn_kwargs()
                )
            except Exception as e:
                print(f"[LiveReader:{self.uid}] spawn error: {e}")
                consecutive_fails += 1
                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    self.status.emit(self.uid, "offline")
                    break
                self._isleep(min(0.5 * consecutive_fails, 3.0))
                continue

            # Read first frame
            first_raw = None
            try:
                if self._proc.stdout:
                    first_raw = self._proc.stdout.read(self._frame_size)
            except Exception:
                first_raw = None

            if not first_raw or len(first_raw) != self._frame_size:
                self._kill_proc()
                consecutive_fails += 1
                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    self.status.emit(self.uid, "offline")
                    break
                # ★ Gentle backoff: 0.5, 1.0, 1.5, 2.0, ..., max 3s
                self._isleep(min(0.5 * consecutive_fails, 3.0))
                continue

            # Success
            consecutive_fails = 0
            self.status.emit(self.uid, "online")
            self._seq = 0

            try:
                bgr = np.frombuffer(first_raw, np.uint8).reshape(
                    self.out_h, self.out_w, 3
                ).copy()
                rgb = bgr[:, :, ::-1].copy()
                self._seq += 1
                self.frame_ready.emit(self.uid, (self._seq, rgb))
            except Exception:
                pass

            while self._running and self._proc:
                try:
                    raw = self._proc.stdout.read(self._frame_size)
                except Exception:
                    break
                if not raw or len(raw) != self._frame_size:
                    break
                try:
                    bgr = np.frombuffer(raw, np.uint8).reshape(
                        self.out_h, self.out_w, 3
                    ).copy()
                    rgb = bgr[:, :, ::-1].copy()
                    self._seq += 1
                    self.frame_ready.emit(self.uid, (self._seq, rgb))
                except Exception:
                    pass

            self._kill_proc()
            if not self._running:
                break

            self.status.emit(self.uid, "reconnecting…")
            self._isleep(1.0)

        self.status.emit(self.uid, "stopped")