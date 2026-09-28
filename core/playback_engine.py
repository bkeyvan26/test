# -*- coding: utf-8 -*-
"""K1 VMS — Playback engine (Phase C)

Features:
  • Seek debouncing       — no dozen FFmpeg spawns per second
  • Gap handling          — "NO RECORDING" state, no stale frames
  • Segment transitions   — seamless when contiguous, gap-aware when not
  • Real FPS from metadata (fallback to default)
  • Proper process lifecycle (no zombie ffmpeg)
  • Aspect-ratio preservation (letterbox on canvas)
  • Fast wall-clock pacing for 0.25x – 4x speed
"""
import os
import sys
import time
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from PySide6.QtCore import QObject, QTimer, Signal

import numpy as np

IS_WIN = sys.platform.startswith("win")


# ============================================================
# Helpers
# ============================================================
def _spawn_kwargs():
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def _find_ffmpeg() -> Optional[str]:
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


# ============================================================
# Playback Engine
# ============================================================
class PlaybackEngine(QObject):
    """Reads recorded .ts segments via FFmpeg subprocess (HEVC-safe)."""

    frame_ready      = Signal(object)      # RGB numpy array OR None (gap)
    position_changed = Signal(float)       # seconds from midnight
    state_changed    = Signal(str)         # playing|paused|stopped|seeking|gap|ended
    segment_changed  = Signal(int)

    # ---- Timing ----
    TICK_MS          = 15
    SEEK_DEBOUNCE_MS = 120

    # ---- Speed ----
    MIN_SPEED = 0.25
    MAX_SPEED = 4.0

    # ---- Output canvas ----
    OUT_W = 960
    OUT_H = 540

    # ---- Safety ----
    MAX_READS_PER_TICK = 12
    GAP_JUMP_THRESHOLD_SEC = 1.5    # gaps smaller than this are ignored

    def __init__(self, parent=None):
        super().__init__(parent)

        # Segments
        self._segments: List[Dict[str, Any]] = []
        self._segment_idx = -1

        # State
        self._proc: Optional[subprocess.Popen] = None
        self._cap_open = False
        self._position = 0.0
        self._speed = 1.0
        self._is_playing = False
        self._state = "stopped"

        # Timing
        self._wall_start = 0.0
        self._wall_pos_start = 0.0
        self._fps = 25.0
        self._frame_size = self.OUT_W * self.OUT_H * 3

        # Last valid frame (for playback during gap skip)
        self._last_frame = None

        # Seek debouncing
        self._pending_seek: Optional[float] = None
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._apply_debounced_seek)

        # Playback tick
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)

        # Detect ffmpeg
        self._ffmpeg = _find_ffmpeg()
        if self._ffmpeg:
            print(f"[PlaybackEngine] ffmpeg: {self._ffmpeg}")
        else:
            print("[PlaybackEngine] ⚠ ffmpeg not found")

    # ============================================================
    # Public API
    # ============================================================
    def set_segments(self, segments):
        """segments: list of dicts with at least {start, duration} or {start, end}."""
        parsed = []
        for s in segments or []:
            try:
                start = float(s.get("start", 0))
                if "end" in s and s["end"] is not None:
                    end = float(s["end"])
                else:
                    end = start + float(s.get("duration") or 0)
                if end <= start:
                    continue

                # FPS from metadata (fallback chain)
                fps = None
                try:
                    if s.get("fps") and float(s["fps"]) > 1.0:
                        fps = float(s["fps"])
                except Exception:
                    fps = None

                parsed.append({
                    "path": str(s.get("path", "")),
                    "start": start,
                    "end": end,
                    "duration": end - start,
                    "fps": fps,
                    "state": s.get("state", ""),
                })
            except Exception:
                continue

        parsed.sort(key=lambda x: x["start"])
        self._segments = parsed
        self._segment_idx = -1
        self._position = parsed[0]["start"] if parsed else 0.0

    def get_segments(self):
        return list(self._segments)

    def get_total_duration(self):
        if not self._segments:
            return 0.0
        return max(s["end"] for s in self._segments)

    def get_position(self):
        return self._position

    def get_speed(self):
        return self._speed

    def is_playing(self):
        return self._is_playing

    def has_content(self):
        return len(self._segments) > 0

    def get_current_segment(self) -> Optional[Dict[str, Any]]:
        if 0 <= self._segment_idx < len(self._segments):
            return self._segments[self._segment_idx]
        return None

    # ============================================================
    # Play / Pause / Stop
    # ============================================================
    def play(self):
        if not self._segments:
            return
        if self._proc is None and self._state != "gap":
            self._apply_seek(self._position, force_pause=True)
        self._is_playing = True
        self._set_state("playing")
        self._wall_start = time.monotonic()
        self._wall_pos_start = self._position
        self._tick_timer.start(self.TICK_MS)

    def pause(self):
        if self._is_playing:
            try:
                elapsed = time.monotonic() - self._wall_start
                self._position = self._wall_pos_start + elapsed * self._speed
            except Exception:
                pass
        self._is_playing = False
        self._tick_timer.stop()
        self._set_state("paused")

    def stop(self):
        self._is_playing = False
        self._tick_timer.stop()
        self._seek_timer.stop()
        self._pending_seek = None
        self._close_proc()
        self._last_frame = None
        self._state = "stopped"
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

    # ============================================================
    # Seek
    # ============================================================
    def request_seek(self, seconds):
        """Called during timeline drag. Debounced."""
        self._pending_seek = float(seconds)
        self.position_changed.emit(self._pending_seek)
        self._seek_timer.start(self.SEEK_DEBOUNCE_MS)

    def seek(self, seconds):
        """Immediate seek (button, programmatic, single click)."""
        self._pending_seek = float(seconds)
        self._seek_timer.stop()
        self._apply_debounced_seek()

    def _apply_debounced_seek(self):
        if self._pending_seek is None:
            return
        target = self._pending_seek
        self._pending_seek = None
        self._apply_seek(target)

    def _apply_seek(self, seconds, force_pause=False):
        """Do the real seek: kill ffmpeg, decide segment, spawn new one."""
        seconds = max(0.0, float(seconds))

        was_playing = self._is_playing and not force_pause
        self._is_playing = False
        self._tick_timer.stop()

        # Kill any existing ffmpeg
        self._close_proc()

        # Find segment covering target
        idx = self._find_segment(seconds)

        if idx is None:
            # GAP — no segment covers this position
            self._position = seconds
            self._set_state("gap")
            self._last_frame = None
            self.frame_ready.emit(None)
            self.position_changed.emit(self._position)
            # Do NOT auto-resume playing during gap
            return

        self._segment_idx = idx
        seg = self._segments[idx]

        # FPS
        if seg.get("fps") and seg["fps"] > 1.0:
            self._fps = float(seg["fps"])
        else:
            self._fps = 25.0

        # Popen ffmpeg
        ok = self._open_segment(seg, offset=seconds - seg["start"])
        if not ok:
            self._set_state("error")
            return

        self._position = seconds
        self.position_changed.emit(self._position)
        self.segment_changed.emit(idx)

        # Read first frame immediately
        frame = self._read_one_frame()
        if frame is not None:
            self._emit_rgb(frame)

        # Resume playing if we were
        if was_playing:
            self._is_playing = True
            self._set_state("playing")
            self._wall_start = time.monotonic()
            self._wall_pos_start = self._position
            self._tick_timer.start(self.TICK_MS)
        else:
            self._set_state("paused")

    def step_frame(self, direction=1):
        if direction < 0:
            new_pos = max(0.0, self._position - 1.0 / max(1.0, self._fps))
            self._apply_seek(new_pos, force_pause=True)
        else:
            if self._proc and self._proc.stdout:
                frame = self._read_one_frame()
                if frame is not None:
                    self._position += 1.0 / max(1.0, self._fps)
                    self._emit_rgb(frame)
                    self.position_changed.emit(self._position)

    # ============================================================
    # Internals
    # ============================================================
    def _find_segment(self, seconds) -> Optional[int]:
        """Return segment index covering `seconds`, or None if in gap."""
        for i, s in enumerate(self._segments):
            if s["start"] <= seconds < s["end"]:
                return i
        return None

    def _find_next_segment(self, after_sec) -> Optional[int]:
        for i, s in enumerate(self._segments):
            if s["start"] >= after_sec:
                return i
        return None

    def _open_segment(self, seg, offset):
        if not self._ffmpeg:
            return False
        path = seg["path"]
        if not os.path.isfile(path):
            return False

        vf = (
            f"scale={self.OUT_W}:{self.OUT_H}:force_original_aspect_ratio=decrease,"
            f"pad={self.OUT_W}:{self.OUT_H}:(ow-iw)/2:(oh-ih)/2,"
            f"format=bgr24"
        )

        cmd = [
            self._ffmpeg,
            "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",
            "-ss", f"{max(0.0, offset):.3f}",
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
            self._cap_open = True
            return True
        except Exception as e:
            print(f"[PlaybackEngine] spawn failed: {e}")
            self._proc = None
            self._cap_open = False
            return False

    def _close_proc(self):
        p = self._proc
        self._proc = None
        self._cap_open = False
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

    def _read_one_frame(self):
        if not self._proc or not self._proc.stdout:
            return None
        try:
            raw = self._proc.stdout.read(self._frame_size)
        except Exception:
            return None
        if len(raw) != self._frame_size:
            return None
        return np.frombuffer(raw, np.uint8).reshape(
            (self.OUT_H, self.OUT_W, 3)).copy()

    def _emit_rgb(self, bgr_frame):
        try:
            rgb = bgr_frame[:, :, ::-1].copy()   # BGR → RGB fast
        except Exception:
            return
        self._last_frame = rgb
        self.frame_ready.emit(rgb)

    def _set_state(self, state):
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)

    # ============================================================
    # Segment advancement (with gap awareness)
    # ============================================================
    def _advance_to_next_segment(self) -> bool:
        """Move to the next segment. Auto-skip small gaps, emit 'gap' for big ones."""
        self._close_proc()

        cur = self._segments[self._segment_idx] if 0 <= self._segment_idx < len(self._segments) else None
        if cur is None:
            return False

        nxt_idx = self._segment_idx + 1
        if nxt_idx >= len(self._segments):
            self._is_playing = False
            self._tick_timer.stop()
            self._set_state("ended")
            return False

        nxt = self._segments[nxt_idx]
        gap_sec = nxt["start"] - cur["end"]

        # Big gap → emit gap state, jump instantly to next segment
        if gap_sec > self.GAP_JUMP_THRESHOLD_SEC:
            self._set_state("gap")
            self.frame_ready.emit(None)
            self._position = nxt["start"]
            self.position_changed.emit(self._position)

        self._segment_idx = nxt_idx
        if nxt.get("fps") and nxt["fps"] > 1.0:
            self._fps = float(nxt["fps"])
        else:
            self._fps = 25.0

        ok = self._open_segment(nxt, offset=0.0)
        if not ok:
            self._is_playing = False
            self._tick_timer.stop()
            self._set_state("error")
            return False

        self._position = nxt["start"]
        self.position_changed.emit(self._position)
        self.segment_changed.emit(nxt_idx)

        # reset wall clock for smoother pacing after jump
        self._wall_start = time.monotonic()
        self._wall_pos_start = self._position

        # First frame
        frame = self._read_one_frame()
        if frame is not None:
            self._emit_rgb(frame)

        self._set_state("playing")
        return True

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

            # Not yet time for next frame
            if behind < frame_interval * 0.4:
                return

            frames_needed = int(behind / frame_interval)
            if frames_needed < 1:
                frames_needed = 1
            if frames_needed > self.MAX_READS_PER_TICK:
                frames_needed = self.MAX_READS_PER_TICK
                # reset wall clock to avoid permanent lag
                self._wall_start = time.monotonic()
                self._wall_pos_start = target_pos

            last_frame = None
            for _ in range(frames_needed):
                frame = self._read_one_frame()
                if frame is None:
                    # End of current segment → try next
                    if not self._advance_to_next_segment():
                        return
                    return
                last_frame = frame
                self._position += frame_interval

            if last_frame is not None:
                self._emit_rgb(last_frame)
                self.position_changed.emit(self._position)

        except Exception as e:
            print(f"[PlaybackEngine] tick error: {e}")