# -*- coding: utf-8 -*-
"""K1 VMS — Playback engine (Phase 6.7.6: dedicated reader thread + zero-copy)"""
import os
import sys
import time
import shutil
import subprocess
import threading
import queue
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from PySide6.QtCore import QObject, QTimer, Signal
import numpy as np

IS_WIN = sys.platform.startswith("win")


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
# ★ ReaderThread — جدا از main thread
# ============================================================
class _ReaderThread(threading.Thread):
    """Thread اختصاصی برای خواندن فریم‌ها از FFmpeg stdout."""

    MAX_QUEUE = 4   # حداکثر فریم در صف (برای جلوگیری از مصرف حافظه)

    def __init__(self, proc, frame_size, out_h, out_w):
        super().__init__(daemon=True)
        self.proc = proc
        self.frame_size = frame_size
        self.out_h = out_h
        self.out_w = out_w
        self.queue = queue.Queue(maxsize=self.MAX_QUEUE)
        self._stop = threading.Event()
        self._eof = False

    def stop(self):
        self._stop.set()
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass

    def run(self):
        stdout = self.proc.stdout
        if stdout is None:
            self._eof = True
            return
        while not self._stop.is_set():
            try:
                raw = stdout.read(self.frame_size)
            except Exception:
                self._eof = True
                break
            if not raw or len(raw) != self.frame_size:
                self._eof = True
                break
            try:
                arr = np.frombuffer(raw, np.uint8).reshape(
                    (self.out_h, self.out_w, 3))
                # ★ صف پر → قدیمی را بردار (Drop Frame)
                try:
                    self.queue.put_nowait(arr)
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.queue.put_nowait(arr)
                    except queue.Full:
                        pass
            except Exception:
                continue

    def is_eof(self):
        return self._eof and self.queue.empty()

    def get(self, timeout=0.05):
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None


# ============================================================
# Playback Engine
# ============================================================
class PlaybackEngine(QObject):
    frame_ready = Signal(object)
    position_changed = Signal(float)
    state_changed = Signal(str)
    segment_changed = Signal(int)

    TICK_MS = 15
    SEEK_DEBOUNCE_MS = 120
    MIN_SPEED = 0.25
    MAX_SPEED = 4.0
    OUT_W = 960
    OUT_H = 540
    MAX_READS_PER_TICK = 3      # ★ کاهش: چون reader خودش buffer دارد
    GAP_JUMP_THRESHOLD_SEC = 1.5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: List[Dict[str, Any]] = []
        self._segment_idx = -1

        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[_ReaderThread] = None
        self._cap_open = False
        self._position = 0.0
        self._speed = 1.0
        self._is_playing = False
        self._state = "stopped"

        self._wall_start = 0.0
        self._wall_pos_start = 0.0
        self._fps = 25.0
        self._frame_size = self.OUT_W * self.OUT_H * 3
        self._last_frame = None

        self._pending_seek: Optional[float] = None
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._apply_debounced_seek)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)

        self._ffmpeg = _find_ffmpeg()
        if self._ffmpeg:
            print(f"[PlaybackEngine] ffmpeg: {self._ffmpeg}")
        else:
            print("[PlaybackEngine] ffmpeg not found")

        self._active_trace = None

    # ============================================================
    # Public API
    # ============================================================
    def set_segments(self, segments):
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
        try:
            if self._active_trace:
                self._active_trace.mark("PLAY_ENTER")
        except Exception:
            pass
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
        self._pending_seek = float(seconds)
        self.position_changed.emit(self._pending_seek)
        self._seek_timer.start(self.SEEK_DEBOUNCE_MS)

    def seek(self, seconds):
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
        seconds = max(0.0, float(seconds))
        was_playing = self._is_playing and not force_pause
        try:
            if self._active_trace:
                self._active_trace.mark("SEEK_APPLY_BEGIN",
                                        target=f"{seconds:.2f}")
        except Exception:
            pass

        self._is_playing = False
        self._tick_timer.stop()

        self._close_proc()
        try:
            if self._active_trace:
                self._active_trace.mark("OLD_PROC_CLOSED")
        except Exception:
            pass

        idx = self._find_segment(seconds)
        if idx is None:
            try:
                if self._active_trace:
                    self._active_trace.mark("GAP_NO_SEGMENT",
                                            at=f"{seconds:.2f}")
                    self._active_trace.end("END_GAP")
            except Exception:
                pass
            self._position = seconds
            self._set_state("gap")
            self._last_frame = None
            self.frame_ready.emit(None)
            self.position_changed.emit(self._position)
            return

        self._segment_idx = idx
        seg = self._segments[idx]

        if seg.get("fps") and seg["fps"] > 1.0:
            self._fps = float(seg["fps"])
        else:
            self._fps = 25.0

        ok = self._open_segment(seg, offset=seconds - seg["start"])
        try:
            if self._active_trace:
                self._active_trace.mark("OPEN_SEGMENT_RESULT", ok=ok)
        except Exception:
            pass
        if not ok:
            self._set_state("error")
            return

        self._position = seconds
        self.position_changed.emit(self._position)
        self.segment_changed.emit(idx)

        try:
            if self._active_trace:
                self._active_trace.mark("READ_FIRST_FRAME_BEGIN")
        except Exception:
            pass

        # ★ اول فریم از reader
        frame = self._get_frame_from_reader()
        try:
            if self._active_trace:
                self._active_trace.mark("READ_FIRST_FRAME_END",
                                        ok=(frame is not None))
        except Exception:
            pass

        if frame is not None:
            self._emit_rgb(frame)

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
            frame = self._get_frame_from_reader()
            if frame is not None:
                self._position += 1.0 / max(1.0, self._fps)
                self._emit_rgb(frame)
                self.position_changed.emit(self._position)

    # ============================================================
    # Internals
    # ============================================================
    def _find_segment(self, seconds) -> Optional[int]:
        for i, s in enumerate(self._segments):
            if s["start"] <= seconds < s["end"]:
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
            "-"
        ]

        try:
            if self._active_trace:
                self._active_trace.mark("FFMPEG_SPAWN_BEGIN",
                                        file=os.path.basename(path),
                                        offset=f"{offset:.2f}")
        except Exception:
            pass

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self._frame_size * 8,
                **_spawn_kwargs()
            )
            self._cap_open = True

            # ★ Reader thread اختصاصی
            self._reader = _ReaderThread(
                self._proc, self._frame_size,
                self.OUT_H, self.OUT_W)
            self._reader.start()

            try:
                if self._active_trace:
                    self._active_trace.mark("FFMPEG_SPAWN_END",
                                            pid=self._proc.pid)
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"[PlaybackEngine] spawn failed: {e}")
            self._proc = None
            self._cap_open = False
            self._reader = None
            return False

    def _close_proc(self):
        # ★ اول reader را متوقف کن
        r = self._reader
        self._reader = None
        if r is not None:
            try:
                r.stop()
            except Exception:
                pass

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
        # Do not block the Qt GUI for a long process shutdown.
        # stdout is already closed and the reader has been asked to stop.
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=0.08)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def _get_frame_from_reader(self):
        """★ فقط از صف reader می‌خواند — بدون I/O در main thread."""
        r = self._reader
        if r is None:
            return None
        arr = r.get(timeout=0.05)
        return arr

    def _emit_rgb(self, bgr_frame):
        try:
            if self._active_trace:
                self._active_trace.mark_once("FIRST_FRAME_EMITTED")
        except Exception:
            pass
        try:
            rgb = bgr_frame[:, :, ::-1]
        except Exception:
            return
        self._last_frame = rgb
        self.frame_ready.emit(rgb)

    def _set_state(self, state):
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)

    # ============================================================
    # Segment advancement
    # ============================================================
    def _advance_to_next_segment(self) -> bool:
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
            self._wall_start = time.monotonic()
            self._wall_pos_start = self._position

            frame = self._get_frame_from_reader()
            if frame is not None:
                self._emit_rgb(frame)

            self._set_state("playing")
            return True

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
        self._wall_start = time.monotonic()
        self._wall_pos_start = self._position

        frame = self._get_frame_from_reader()
        if frame is not None:
            self._emit_rgb(frame)

        return True

    # ============================================================
    # Tick
    # ============================================================
    def _tick(self):
        if not self._is_playing or self._reader is None:
            return
        try:
            wall_elapsed = time.monotonic() - self._wall_start
            target_pos = self._wall_pos_start + wall_elapsed * self._speed
            frame_interval = 1.0 / max(1.0, self._fps)
            behind = target_pos - self._position

            if behind < frame_interval * 0.4:
                return

            frames_needed = int(behind / frame_interval)
            if frames_needed < 1:
                frames_needed = 1
            if frames_needed > self.MAX_READS_PER_TICK:
                frames_needed = self.MAX_READS_PER_TICK

            self._wall_start = time.monotonic()
            self._wall_pos_start = target_pos

            last_frame = None
            got_any = False
            for _ in range(frames_needed):
                frame = self._get_frame_from_reader()
                if frame is None:
                    # ★ اگر reader تمام شد → segment بعد
                    if self._reader is not None and self._reader.is_eof():
                        if not self._advance_to_next_segment():
                            return
                        return
                    break
                last_frame = frame
                got_any = True
                self._position += frame_interval

            if got_any and last_frame is not None:
                self._emit_rgb(last_frame)
                self.position_changed.emit(self._position)

        except Exception as e:
            print(f"[PlaybackEngine] tick error: {e}")

    # ============================================================
    # Trace management
    # ============================================================
    def set_active_trace(self, trace):
        self._active_trace = trace

    def get_active_trace(self):
        return self._active_trace

    def clear_active_trace(self):
        self._active_trace = None