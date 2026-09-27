# -*- coding: utf-8 -*-
"""K1 VMS — Playback engine (ffmpeg-based, full HEVC support)"""
import os
import shutil
import subprocess
import time
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

IS_WIN = os.name == "nt"


def _spawn_kwargs():
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def _find_ffmpeg():
    """Locate ffmpeg binary."""
    # 1) imageio_ffmpeg (bundled pip package)
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    # 2) alongside k1motion.py
    try:
        from pathlib import Path
        base = Path(__file__).parent.parent
        for name in ("ffmpeg.exe", "ffmpeg"):
            p = base / name
            if p.exists():
                return str(p)
    except Exception:
        pass
    # 3) system PATH
    return shutil.which("ffmpeg")


class PlaybackEngine(QObject):
    """Reads recorded .ts segments via ffmpeg subprocess (HEVC-safe)."""

    frame_ready      = Signal(object)
    position_changed = Signal(float)
    state_changed    = Signal(str)
    segment_changed  = Signal(int)

    TICK_MS          = 15
    MIN_SPEED        = 0.25
    MAX_SPEED        = 4.0
    OUT_W            = 960
    OUT_H            = 540
    MAX_READS_PER_TICK = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = None
        self._segments = []
        self._segment_idx = 0
        self._position = 0.0
        self._speed = 1.0
        self._is_playing = False
        self._fps = 25.0
        self._wall_start = 0.0
        self._wall_pos_start = 0.0
        self._frame_size = self.OUT_W * self.OUT_H * 3
        self._ffmpeg = _find_ffmpeg()
        self._last_frame = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        if self._ffmpeg:
            print(f"[PlaybackEngine] ffmpeg: {self._ffmpeg}")
        else:
            print("[PlaybackEngine] ⚠ ffmpeg NOT FOUND")

    # ============================================================
    # Public API
    # ============================================================
    def set_segments(self, segments):
        self._segments = sorted(segments or [],
                                key=lambda s: float(s.get("start", 0)))
        self._segment_idx = 0

    def get_segments(self):
        return list(self._segments)

    def get_total_duration(self):
        if not self._segments:
            return 0.0
        return max(float(s["start"]) + float(s["duration"])
                   for s in self._segments)

    def get_position(self):
        return self._position

    def get_speed(self):
        return self._speed

    def is_playing(self):
        return self._is_playing

    def has_content(self):
        return len(self._segments) > 0

    # ============================================================
    # Control
    # ============================================================
    def play(self):
        if not self._segments:
            return
        if self._proc is None:
            self._seek_internal(self._position)
        self._is_playing = True
        self._wall_start = time.monotonic()
        self._wall_pos_start = self._position
        self.state_changed.emit("playing")
        self._timer.start(self.TICK_MS)

    def pause(self):
        if self._is_playing:
            try:
                elapsed = time.monotonic() - self._wall_start
                self._position = self._wall_pos_start + elapsed * self._speed
            except Exception:
                pass
        self._is_playing = False
        self._timer.stop()
        self.state_changed.emit("paused")

    def stop(self):
        self._is_playing = False
        self._timer.stop()
        self._close_proc()
        self.state_changed.emit("stopped")

    def toggle(self):
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def set_speed(self, speed):
        try:
            s = float(speed)
        except (TypeError, ValueError):
            return
        s = max(self.MIN_SPEED, min(self.MAX_SPEED, s))

        if self._is_playing:
            try:
                elapsed = time.monotonic() - self._wall_start
                self._position = self._wall_pos_start + elapsed * self._speed
            except Exception:
                pass

        self._speed = s
        self._wall_start = time.monotonic()
        self._wall_pos_start = self._position

    def seek(self, seconds):
        was_playing = self._is_playing
        self._is_playing = False
        self._timer.stop()
        self.state_changed.emit("seeking")

        try:
            self._seek_internal(float(seconds))
        except Exception as e:
            print(f"[PlaybackEngine] seek error: {e}")

        if was_playing and self._proc is not None:
            self._wall_start = time.monotonic()
            self._wall_pos_start = self._position
            self._is_playing = True
            self.state_changed.emit("playing")
            self._timer.start(self.TICK_MS)
        else:
            self.state_changed.emit("paused")

    def step_frame(self, direction=1):
        if direction < 0:
            new_pos = max(0.0, self._position - 1.0 / max(1.0, self._fps))
            self._seek_internal(new_pos)
        else:
            if self._proc and self._proc.stdout:
                try:
                    raw = self._proc.stdout.read(self._frame_size)
                    if len(raw) == self._frame_size:
                        frame = np.frombuffer(raw, np.uint8).reshape(
                            (self.OUT_H, self.OUT_W, 3)).copy()
                        self._position += 1.0 / max(1.0, self._fps)
                        self._emit_rgb(frame)
                        self.position_changed.emit(self._position)
                except Exception:
                    pass

    # ============================================================
    # Internals
    # ============================================================
    def _seek_internal(self, seconds):
        self._close_proc()

        if not self._ffmpeg:
            return

        idx = self._segment_index_for(seconds)
        if idx < 0:
            return

        self._segment_idx = idx
        seg = self._segments[idx]
        path = seg["path"]

        if not os.path.isfile(path):
            return

        offset = max(0.0, seconds - float(seg["start"]))

        vf = (f"scale={self.OUT_W}:{self.OUT_H}:"
              f"force_original_aspect_ratio=decrease,"
              f"pad={self.OUT_W}:{self.OUT_H}:(ow-iw)/2:(oh-ih)/2,"
              f"format=bgr24")

        # Use -ss before -i for fast keyframe seek
        cmd = [
            self._ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", f"{offset:.3f}",
            "-i", path,
            "-an", "-sn",
            "-vf", vf,
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-",
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self._frame_size * 4,
                **_spawn_kwargs()
            )
        except Exception as e:
            print(f"[PlaybackEngine] spawn failed: {e}")
            self._proc = None
            return

        self._fps = 25.0
        self._position = float(seconds)

        # Read first frame
        try:
            raw = self._proc.stdout.read(self._frame_size)
            if len(raw) == self._frame_size:
                frame = np.frombuffer(raw, np.uint8).reshape(
                    (self.OUT_H, self.OUT_W, 3)).copy()
                self._emit_rgb(frame)
        except Exception:
            pass

        self.segment_changed.emit(idx)
        self.position_changed.emit(self._position)

    def _segment_index_for(self, seconds):
        for i, s in enumerate(self._segments):
            s0 = float(s["start"])
            s1 = s0 + float(s["duration"])
            if s0 <= seconds <= s1:
                return i
        for i, s in enumerate(self._segments):
            if float(s["start"]) > seconds:
                return i
        return len(self._segments) - 1

    def _close_proc(self):
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            if p.stdout:
                p.stdout.close()
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=1)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def _emit_rgb(self, bgr_frame):
        try:
            import cv2
            rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        except Exception:
            rgb = bgr_frame[:, :, ::-1].copy()  # fast fallback
        self._last_frame = rgb
        self.frame_ready.emit(rgb)

    def _advance_segment(self):
        self._close_proc()
        nxt = self._segment_idx + 1
        if nxt >= len(self._segments):
            self._is_playing = False
            self._timer.stop()
            self.state_changed.emit("ended")
            return False
        seg = self._segments[nxt]
        self._segment_idx = nxt
        self._seek_internal(float(seg["start"]))
        self._wall_start = time.monotonic()
        self._wall_pos_start = self._position
        return self._proc is not None

    # ============================================================
    # Tick
    # ============================================================
    def _tick(self):
        if not self._is_playing or self._proc is None:
            return
        try:
            wall_elapsed = time.monotonic() - self._wall_start
            target_pos = self._wall_pos_start + wall_elapsed * self._speed
            frame_interval = 1.0 / max(1.0, self._fps)
            behind = target_pos - self._position

            if behind < frame_interval * 0.4:
                return

            frames_needed = max(1, int(behind / frame_interval))
            if frames_needed > self.MAX_READS_PER_TICK:
                frames_needed = self.MAX_READS_PER_TICK
                self._wall_start = time.monotonic()
                self._wall_pos_start = target_pos

            last_frame = None
            for _ in range(frames_needed):
                if self._proc is None or self._proc.stdout is None:
                    break
                try:
                    raw = self._proc.stdout.read(self._frame_size)
                except Exception:
                    break
                if len(raw) != self._frame_size:
                    if not self._advance_segment():
                        return
                    return
                last_frame = np.frombuffer(raw, np.uint8).reshape(
                    (self.OUT_H, self.OUT_W, 3)).copy()
                self._position += frame_interval

            if last_frame is not None:
                self._emit_rgb(last_frame)
                self.position_changed.emit(self._position)
        except Exception as e:
            print(f"[PlaybackEngine] tick error: {e}")