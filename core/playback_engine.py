# -*- coding: utf-8 -*-
"""K1 VMS — Professional Playback Engine.

Design:
- One decoder session per contiguous recording run, not one process per TS segment.
- GUI thread never waits for FFmpeg.
- Playback clock is frame-paced, so 1x is 1x and 2x/4x are intentional.
- Gaps are first-class: the engine never invents frames inside a gap.
"""
import os
import sys
import time
import shutil
import tempfile
import subprocess
import threading
import queue
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


class _FrameReader(threading.Thread):
    """Blocking stdout reader isolated from Qt's GUI thread."""

    def __init__(self, proc, frame_size, out_h, out_w):
        super().__init__(daemon=True)
        self.proc = proc
        self.frame_size = frame_size
        self.out_h = out_h
        self.out_w = out_w
        self.queue = queue.Queue(maxsize=12)
        self._halt = threading.Event()
        self._eof = False

    def stop(self):
        self._halt.set()
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
        while not self._halt.is_set():
            try:
                raw = stdout.read(self.frame_size)
            except Exception:
                self._eof = True
                break
            if not raw or len(raw) != self.frame_size:
                self._eof = True
                break
            try:
                frame = np.frombuffer(raw, np.uint8).reshape(
                    (self.out_h, self.out_w, 3))
                try:
                    # Never discard decoded frames. Dropping from the head makes
                    # the Python playback clock point at one timestamp while
                    # the queue displays another, which causes apparent 2x/4x
                    # playback and makes speed changes ineffective.
                    self.queue.put(frame, timeout=0.25)
                except Exception:
                    if self._halt.is_set():
                        break
            except Exception:
                continue

    def get_nowait(self):
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None

    def is_eof(self):
        return self._eof and self.queue.empty()


class PlaybackEngine(QObject):
    frame_ready = Signal(object)
    position_changed = Signal(float)
    state_changed = Signal(str)
    segment_changed = Signal(int)

    TICK_MS = 16
    SEEK_DEBOUNCE_MS = 90
    MIN_SPEED = 0.25
    MAX_SPEED = 16.0
    OUT_W = 640
    OUT_H = 360
    GAP_TOLERANCE_SEC = 1.25

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: List[Dict[str, Any]] = []
        self._segment_starts = []
        self._groups = []
        self._group_starts = []
        self._segment_idx = -1
        self._group_idx = -1

        self._proc = None
        self._reader = None
        self._manifest_path = None

        self._position = 0.0
        self._speed = 1.0
        self._fps = 25.0
        self._is_playing = False
        self._state = "stopped"

        self._last_frame = None
        self._frame_accumulator = 0.0
        self._last_tick = 0.0
        self._play_anchor_wall = 0.0
        self._play_anchor_position = 0.0
        self._decode_origin_position = 0.0
        self._decoded_frames = 0
        self._awaiting_first_frame = False

        self._pending_seek = None
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

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------
    def set_segments(self, segments):
        self.stop()

        parsed = []
        for s in segments or []:
            try:
                start = float(s.get("start", 0))
                end = float(s.get("end", start + float(s.get("duration") or 0)))
                if end <= start:
                    continue
                fps = None
                try:
                    value = float(s.get("fps") or 0)
                    if value > 1:
                        fps = value
                except Exception:
                    pass
                parsed.append({
                    "path": str(s.get("path", "")),
                    "start": start,
                    "end": end,
                    "duration": end - start,
                    "fps": fps,
                    "state": s.get("state", ""),
                    "codec": s.get("codec"),
                    "width": s.get("width"),
                    "height": s.get("height"),
                })
            except Exception:
                continue

        parsed.sort(key=lambda x: x["start"])
        self._segments = parsed
        self._segment_starts = [s["start"] for s in parsed]
        self._groups = self._build_groups(parsed)
        self._group_starts = [g["start"] for g in self._groups]
        self._segment_idx = -1
        self._group_idx = -1
        self._position = parsed[0]["start"] if parsed else 0.0
        self._set_state("idle" if parsed else "stopped")
        if parsed:
            self.position_changed.emit(self._position)

    @staticmethod
    def _build_groups(segments):
        groups = []
        for seg in segments:
            if not groups:
                groups.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "segments": [seg],
                })
                continue
            g = groups[-1]
            gap = seg["start"] - g["end"]
            if gap <= PlaybackEngine.GAP_TOLERANCE_SEC:
                g["segments"].append(seg)
                g["end"] = max(g["end"], seg["end"])
            else:
                groups.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "segments": [seg],
                })
        return groups

    def get_segments(self):
        return list(self._segments)

    def get_total_duration(self):
        return max((s["end"] for s in self._segments), default=0.0)

    def get_position(self):
        return self._position

    def set_position_hint(self, seconds):
        try:
            self._position = max(0.0, float(seconds))
            self.position_changed.emit(self._position)
        except (TypeError, ValueError):
            pass

    def get_speed(self):
        return self._speed

    def is_playing(self):
        return self._is_playing

    def has_content(self):
        return bool(self._segments)

    def get_current_segment(self):
        if 0 <= self._segment_idx < len(self._segments):
            return self._segments[self._segment_idx]
        return None

    def set_active_trace(self, trace):
        self._active_trace = trace

    # ------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------
    def play(self):
        if not self._segments:
            return
        if self._proc is None:
            self._apply_seek(self._position, force_pause=True)
            if self._proc is None:
                return
        self._is_playing = True
        self._set_state("playing")
        now = time.monotonic()
        self._play_anchor_wall = now
        self._play_anchor_position = self._position
        self._last_tick = now
        self._frame_accumulator = 0.0
        self._tick_timer.start(self.TICK_MS)

    def pause(self):
        if not self._is_playing:
            return
        if self._is_playing and self._play_anchor_wall > 0:
            now = time.monotonic()
            self._position = self._play_anchor_position + (now - self._play_anchor_wall) * self._speed
            self.position_changed.emit(self._position)
        self._is_playing = False
        self._tick_timer.stop()
        self._last_tick = 0.0
        self._play_anchor_wall = 0.0
        self._frame_accumulator = 0.0
        self._set_state("paused")

    def stop(self):
        self._is_playing = False
        self._tick_timer.stop()
        self._seek_timer.stop()
        self._pending_seek = None
        self._close_session()
        self._last_frame = None
        self._segment_idx = -1
        self._group_idx = -1
        if self._segments:
            self._set_state("idle")
        else:
            self._set_state("stopped")

    def toggle(self):
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def set_speed(self, speed):
        try:
            value = float(speed)
        except (TypeError, ValueError):
            return
        value = max(self.MIN_SPEED, min(self.MAX_SPEED, value))
        if abs(value - self._speed) < 1e-9:
            return
        if self._is_playing and self._play_anchor_wall > 0:
            now = time.monotonic()
            self._position = self._play_anchor_position + (now - self._play_anchor_wall) * self._speed
            self.position_changed.emit(self._position)
            self._play_anchor_position = self._position
            self._play_anchor_wall = now
        self._speed = value
        self._frame_accumulator = 0.0
        self._last_tick = time.monotonic() if self._is_playing else 0.0
        if self._is_playing:
            # The decoder may already have buffered frames for the old speed.
            # Restart exactly at the current logical position so the first
            # frame after a speed change belongs to that position.
            current = self._position
            self._apply_seek(current, force_pause=False)
            print(f"[PlaybackEngine] speed={self._speed:g}x (restarted at {current:.3f})")
            return
        print(f"[PlaybackEngine] speed={self._speed:g}x")

    # ------------------------------------------------------------
    # Seek
    # ------------------------------------------------------------
    def request_seek(self, seconds):
        try:
            self._pending_seek = float(seconds)
        except (TypeError, ValueError):
            return
        self.position_changed.emit(self._pending_seek)
        self._seek_timer.start(self.SEEK_DEBOUNCE_MS)

    def seek(self, seconds):
        try:
            self._pending_seek = float(seconds)
        except (TypeError, ValueError):
            return
        self._seek_timer.stop()
        self._apply_debounced_seek()

    def _apply_debounced_seek(self):
        if self._pending_seek is None:
            return
        target = self._pending_seek
        self._pending_seek = None
        self._apply_seek(target)

    def _find_segment(self, seconds):
        if not self._segments:
            return None
        lo, hi = 0, len(self._segments)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._segments[mid]["start"] <= seconds:
                lo = mid + 1
            else:
                hi = mid
        idx = lo - 1
        if 0 <= idx < len(self._segments):
            seg = self._segments[idx]
            if seg["start"] <= seconds < seg["end"]:
                return idx
        return None

    def _find_group(self, seconds):
        if not self._groups:
            return None
        lo, hi = 0, len(self._groups)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._groups[mid]["start"] <= seconds:
                lo = mid + 1
            else:
                hi = mid
        idx = lo - 1
        if 0 <= idx < len(self._groups):
            g = self._groups[idx]
            if g["start"] <= seconds < g["end"]:
                return idx
        return None

    def _apply_seek(self, seconds, force_pause=False):
        target = max(0.0, float(seconds))
        was_playing = self._is_playing and not force_pause
        self._is_playing = False
        self._tick_timer.stop()

        try:
            if self._active_trace:
                self._active_trace.mark("SEEK_APPLY_BEGIN", target=f"{target:.2f}")
        except Exception:
            pass

        seg_idx = self._find_segment(target)
        group_idx = self._find_group(target)

        if seg_idx is None or group_idx is None:
            self._close_session()
            self._position = target
            self._set_state("gap")
            self.frame_ready.emit(None)
            self.position_changed.emit(self._position)
            try:
                if self._active_trace:
                    self._active_trace.mark("GAP_NO_SEGMENT", at=f"{target:.2f}")
                    self._active_trace.end("END_GAP")
            except Exception:
                pass
            return

        seg = self._segments[seg_idx]
        group = self._groups[group_idx]
        self._segment_idx = seg_idx
        self._group_idx = group_idx
        self._fps = float(seg.get("fps") or 25.0)
        self._position = target
        self._decode_origin_position = target
        self._decoded_frames = 0

        # Reuse the existing session whenever the seek stays in the same
        # contiguous run. This avoids needless FFmpeg churn.
        # Open only from the selected recording segment onward.
        # Seeking against a 6+ hour concat manifest makes FFmpeg scan a huge
        # virtual timeline before producing the first frame. Starting the
        # manifest at the target segment keeps seek latency bounded to the
        # current TS file while preserving seamless continuation.
        if self._group_idx != getattr(self, "_session_group_idx", -1):
            self._open_from_segment(
                group, seg_idx, target - seg["start"], group_idx)
        else:
            self._restart_from_segment(
                group, seg_idx, target - seg["start"], group_idx)

        if self._proc is None:
            self._set_state("error")
            return

        self.position_changed.emit(self._position)
        self.segment_changed.emit(seg_idx)
        self._awaiting_first_frame = True

        # Strictly non-blocking. The reader thread will deliver the first
        # requested frame on the next Qt tick.
        frame = self._reader.get_nowait() if self._reader else None
        if frame is not None:
            self._awaiting_first_frame = False
            self._emit_frame(frame)

        if was_playing:
            self._is_playing = True
            self._set_state("playing")
            now = time.monotonic()
            self._play_anchor_wall = now
            self._play_anchor_position = self._position
            self._last_tick = now
            self._frame_accumulator = 0.0
            self._tick_timer.start(self.TICK_MS)
        else:
            self._set_state("paused")

    # ------------------------------------------------------------
    # Session / FFmpeg
    # ------------------------------------------------------------
    @staticmethod
    def _escape_concat_path(path):
        p = os.path.abspath(path).replace("\\", "/")
        return p.replace("'", "'\\''")

    def _write_manifest(self, group):
        fd, path = tempfile.mkstemp(prefix="k1pb_", suffix=".ffconcat")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("ffconcat version 1.0\n")
                for seg in group["segments"]:
                    f.write(f"file '{self._escape_concat_path(seg['path'])}'\n")
                    if seg.get("duration"):
                        f.write(f"duration {float(seg['duration']):.6f}\n")
            return path
        except Exception:
            try:
                os.remove(path)
            except Exception:
                pass
            return None

    def _build_ffmpeg_cmd(self, manifest, offset):
        vf = (
            f"scale={self.OUT_W}:{self.OUT_H}:"
            f"force_original_aspect_ratio=decrease:flags=fast_bilinear,"
            f"pad={self.OUT_W}:{self.OUT_H}:(ow-iw)/2:(oh-ih)/2,"
            f"format=rgb24"
        )
        return [
            self._ffmpeg,
            "-hide_banner", "-loglevel", "error",
            "-threads", "2",
            "-fflags", "+genpts+discardcorrupt",
            "-f", "concat", "-safe", "0",
            "-ss", f"{max(0.0, offset):.3f}",
            "-i", manifest,
            "-an", "-sn",
            "-vf", vf,
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-"
        ]

    def _write_manifest_from_segment(self, group, start_idx):
        """Create a bounded concat manifest beginning at one real TS segment.

        The old implementation built a manifest for the entire contiguous
        run and then used -ss against that virtual timeline. For long runs
        this can make a seek depend on hours of media before the target.
        Starting at the target segment makes random access predictable.
        """
        segments = group.get("segments") or []
        start_idx = max(0, min(int(start_idx), len(segments)))
        if start_idx >= len(segments):
            return None

        fd, path = tempfile.mkstemp(prefix="k1pb_", suffix=".ffconcat")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("ffconcat version 1.0\n")
                for seg in segments[start_idx:]:
                    f.write(f"file '{self._escape_concat_path(seg['path'])}'\n")
                    if seg.get("duration"):
                        f.write(f"duration {float(seg['duration']):.6f}\n")
            return path
        except Exception:
            try:
                os.remove(path)
            except Exception:
                pass
            return None

    def _open_from_segment(self, group, seg_idx, offset, group_idx):
        """Start decoding from a real segment near the requested position."""
        self._close_session()
        if not self._ffmpeg:
            return

        group_segments = group.get("segments") or []
        try:
            local_idx = next(
                i for i, seg in enumerate(group_segments)
                if seg is self._segments[seg_idx]
            )
        except Exception:
            # Fall back to matching by path/start when object identity is not
            # available.
            local_idx = 0
            if 0 <= seg_idx < len(self._segments):
                target = self._segments[seg_idx]
                for i, candidate in enumerate(group_segments):
                    if (candidate.get("path") == target.get("path") and
                            abs(float(candidate.get("start", 0)) -
                                float(target.get("start", 0))) < 0.001):
                        local_idx = i
                        break

        manifest = self._write_manifest_from_segment(group, local_idx)
        if not manifest:
            return

        self._manifest_path = manifest
        try:
            proc = subprocess.Popen(
                self._build_ffmpeg_cmd(manifest, max(0.0, offset)),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.OUT_W * self.OUT_H * 3 * 8,
                **_spawn_kwargs()
            )
            self._proc = proc
            self._reader = _FrameReader(
                proc, self.OUT_W * self.OUT_H * 3,
                self.OUT_H, self.OUT_W)
            self._reader.start()
            self._decode_origin_position = self._position
            self._decoded_frames = 0
            self._session_group_idx = group_idx
            try:
                if self._active_trace:
                    self._active_trace.mark(
                        "FFMPEG_SPAWN_END", pid=proc.pid,
                        group=f"{group['start']:.2f}-{group['end']:.2f}",
                        segment_index=str(seg_idx),
                        seek_offset=f"{float(offset):.3f}")
            except Exception:
                pass
        except Exception as exc:
            print(f"[PlaybackEngine] session spawn failed: {exc}")
            self._close_session()

    def _restart_from_segment(self, group, seg_idx, offset, group_idx):
        """Restart a seek without rebuilding the whole day's concat timeline."""
        self._close_process_only()
        group_segments = group.get("segments") or []
        local_idx = 0
        if 0 <= seg_idx < len(self._segments):
            target = self._segments[seg_idx]
            for i, candidate in enumerate(group_segments):
                if (candidate.get("path") == target.get("path") and
                        abs(float(candidate.get("start", 0)) -
                            float(target.get("start", 0))) < 0.001):
                    local_idx = i
                    break

        manifest = self._write_manifest_from_segment(group, local_idx)
        if not manifest:
            return
        self._manifest_path = manifest
        try:
            proc = subprocess.Popen(
                self._build_ffmpeg_cmd(manifest, max(0.0, offset)),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.OUT_W * self.OUT_H * 3 * 8,
                **_spawn_kwargs()
            )
            self._proc = proc
            self._reader = _FrameReader(
                proc, self.OUT_W * self.OUT_H * 3,
                self.OUT_H, self.OUT_W)
            self._reader.start()
            self._session_group_idx = group_idx
            try:
                if self._active_trace:
                    self._active_trace.mark(
                        "FFMPEG_SEEK_SPAWN_END", pid=proc.pid,
                        segment_index=str(seg_idx),
                        seek_offset=f"{float(offset):.3f}")
            except Exception:
                pass
        except Exception as exc:
            print(f"[PlaybackEngine] seek spawn failed: {exc}")
            self._close_session()

    def _open_group(self, group, offset, group_idx):
        self._close_session()
        if not self._ffmpeg:
            return
        manifest = self._write_manifest(group)
        if not manifest:
            return
        self._manifest_path = manifest
        try:
            proc = subprocess.Popen(
                self._build_ffmpeg_cmd(manifest, offset),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.OUT_W * self.OUT_H * 3 * 8,
                **_spawn_kwargs()
            )
            self._proc = proc
            self._reader = _FrameReader(
                proc, self.OUT_W * self.OUT_H * 3,
                self.OUT_H, self.OUT_W)
            self._reader.start()
            self._session_group_idx = group_idx
            try:
                if self._active_trace:
                    self._active_trace.mark(
                        "FFMPEG_SPAWN_END", pid=proc.pid,
                        group=f"{group['start']:.2f}-{group['end']:.2f}")
            except Exception:
                pass
        except Exception as exc:
            print(f"[PlaybackEngine] session spawn failed: {exc}")
            self._close_session()

    def _restart_group_seek(self, group, offset):
        # A seek must reposition the existing logical group. FFmpeg's concat
        # demuxer performs the actual media seek; the GUI remains non-blocking.
        self._close_process_only()
        manifest = self._manifest_path or self._write_manifest(group)
        if not manifest:
            return
        self._manifest_path = manifest
        try:
            proc = subprocess.Popen(
                self._build_ffmpeg_cmd(manifest, offset),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.OUT_W * self.OUT_H * 3 * 8,
                **_spawn_kwargs()
            )
            self._proc = proc
            self._reader = _FrameReader(
                proc, self.OUT_W * self.OUT_H * 3,
                self.OUT_H, self.OUT_W)
            self._reader.start()
        except Exception as exc:
            print(f"[PlaybackEngine] seek spawn failed: {exc}")
            self._close_session()

    def _close_process_only(self):
        r = self._reader
        self._reader = None
        if r:
            r.stop()
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
            p.wait(timeout=0.08)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def _close_session(self):
        self._close_process_only()
        path = self._manifest_path
        self._manifest_path = None
        if path:
            try:
                os.remove(path)
            except Exception:
                pass
        self._session_group_idx = -1

    # ------------------------------------------------------------
    # Frame clock
    # ------------------------------------------------------------
    def _tick(self):
        if not self._is_playing:
            return

        now = time.monotonic()
        if self._play_anchor_wall <= 0:
            self._play_anchor_wall = now
            self._play_anchor_position = self._position

        # Wall-clock master: FFmpeg decode speed can no longer accelerate
        # playback. Speed changes alter only the presentation clock.
        target = self._play_anchor_position + (now - self._play_anchor_wall) * self._speed
        fps = max(1.0, min(60.0, float(self._fps or 25.0)))
        desired = int(max(0.0, target - self._decode_origin_position) * fps)
        needed = desired - self._decoded_frames

        last = None
        if needed > 0:
            needed = min(needed, 256)
            for _ in range(needed):
                frame = self._reader.get_nowait() if self._reader else None
                if frame is None:
                    break
                last = frame
                self._decoded_frames += 1

        if last is not None:
            self._awaiting_first_frame = False
            self._emit_frame(last)

        self._position = target
        self.position_changed.emit(self._position)
        self._update_segment_from_position()

        reader = self._reader
        if reader is not None and reader.is_eof():
            self._advance_after_eof()

    def _update_segment_from_position(self):
        idx = self._find_segment(self._position)
        if idx is not None and idx != self._segment_idx:
            self._segment_idx = idx
            self.segment_changed.emit(idx)
            seg = self._segments[idx]
            self._fps = float(seg.get("fps") or self._fps or 25.0)

    def _advance_after_eof(self):
        if self._group_idx < 0:
            return
        next_group = self._group_idx + 1
        if next_group >= len(self._groups):
            self._is_playing = False
            self._tick_timer.stop()
            self._set_state("ended")
            return

        group = self._groups[next_group]
        # A real gap is never fabricated. Playback jumps to the first
        # recorded frame of the next run, like professional VMS clients.
        self._group_idx = next_group
        next_idx = self._find_segment(group["start"])
        if next_idx is None:
            self._is_playing = False
            self._tick_timer.stop()
            self._set_state("ended")
            return
        self._segment_idx = next_idx
        self._position = group["start"]
        self._decode_origin_position = self._position
        self._decoded_frames = 0
        self._play_anchor_position = self._position
        self._play_anchor_wall = time.monotonic()
        self._fps = float(
            self._segments[next_idx].get("fps") or self._fps or 25.0)
        self._open_from_segment(group, next_idx, 0.0, next_group)
        self._awaiting_first_frame = True
        self.position_changed.emit(self._position)

    def _emit_frame(self, frame):
        try:
            if hasattr(frame, "flags") and not frame.flags["C_CONTIGUOUS"]:
                frame = np.ascontiguousarray(frame)
            self._last_frame = frame
            try:
                if self._active_trace:
                    self._active_trace.mark_once("FIRST_FRAME_EMITTED")
            except Exception:
                pass
            self.frame_ready.emit(frame)
        except Exception:
            pass

    def _set_state(self, state):
        if self._state == state:
            return
        self._state = state
        try:
            self.state_changed.emit(state)
        except Exception:
            pass

    def step_frame(self, direction=1):
        self.pause()
        if direction < 0:
            self._apply_seek(
                max(0.0, self._position - 1.0 / max(1.0, self._fps)),
                force_pause=True)
            return
        frame = self._reader.get_nowait() if self._reader else None
        if frame is not None:
            self._position += 1.0 / max(1.0, self._fps)
            self._emit_frame(frame)
            self.position_changed.emit(self._position)
            self._update_segment_from_position()

    def close(self):
        self.stop()
