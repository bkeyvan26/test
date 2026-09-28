# -*- coding: utf-8 -*-
"""K1 VMS — Export Engine (Phase D)

Modes:
  • FAST     — FFmpeg concat demuxer + stream copy (no re-encode)
  • PRECISE  — Re-encode only the boundary segments, stream-copy the rest

Features:
  • Real-time progress from FFmpeg -progress pipe:1
  • FFprobe validation after export
  • Gap detection and reporting
  • Unicode-safe paths (temp ASCII workspace)
  • MP4 / TS / AVI output
"""
import os
import sys
import time
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple

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


def _find_ffprobe() -> Optional[str]:
    try:
        from core.ffprobe_util import get_ffprobe_exe
        return get_ffprobe_exe()
    except Exception:
        pass
    return shutil.which("ffprobe")


# ============================================================
# Segment selection
# ============================================================
def _select_overlapping(files_with_start: List[Dict], start_sec: float,
                         end_sec: float) -> List[Dict]:
    """Return segments that overlap [start_sec, end_sec]."""
    selected = []
    for f in sorted(files_with_start, key=lambda x: float(x.get("start_sec", 0))):
        path = str(f.get("path", ""))
        if not path or not os.path.isfile(path):
            continue
        fs_ = float(f.get("start_sec", 0))
        dur = float(f.get("duration") or 0)
        if dur <= 0:
            continue
        fe = fs_ + dur
        if fe <= start_sec or fs_ >= end_sec:
            continue
        selected.append({
            "path": path,
            "start": fs_,
            "end": fe,
            "duration": dur,
        })
    return selected


def _detect_gaps(selected: List[Dict], start_sec: float,
                  end_sec: float, tolerance: float = 1.0) -> List[Dict]:
    """Return gaps larger than tolerance within [start_sec, end_sec]."""
    if not selected:
        return []

    # Build list of covered intervals
    covered = []
    for s in selected:
        a = max(s["start"], start_sec)
        b = min(s["end"], end_sec)
        if b > a:
            covered.append((a, b))

    covered.sort()
    # Merge overlapping
    merged = []
    for a, b in covered:
        if merged and a <= merged[-1][1] + tolerance:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))

    # Compute gaps
    gaps = []
    cursor = start_sec
    for a, b in merged:
        if a - cursor > tolerance:
            gaps.append({
                "start": cursor,
                "end": a,
                "duration": a - cursor,
            })
        cursor = max(cursor, b)
    if end_sec - cursor > tolerance:
        gaps.append({
            "start": cursor,
            "end": end_sec,
            "duration": end_sec - cursor,
        })
    return gaps


# ============================================================
# Public API
# ============================================================
def export_range(
    files_with_start: List[Dict],
    out_path: str,
    start_sec: float,
    end_sec: float,
    mode: str = "fast",              # "fast" | "precise"
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Tuple[bool, str]:
    """Export a range.

    Returns: (ok: bool, message: str)
    """
    print(f"[export] request: {start_sec:.1f}..{end_sec:.1f}s  mode={mode}  files={len(files_with_start)}")

    def _cb(pct: float, msg: str = ""):
        try:
            if progress_cb:
                progress_cb(int(max(0, min(100, pct))), msg)
        except Exception:
            pass
        if msg:
            print(f"[export] {int(pct):3d}%  {msg}")

    # ---- 1. Select segments ----
    _cb(0, "selecting segments…")
    selected = _select_overlapping(files_with_start, start_sec, end_sec)
    print(f"[export] selected {len(selected)} segments")
    if not selected:
        return False, "No segments in range"

    # ---- 2. Detect gaps ----
    gaps = _detect_gaps(selected, start_sec, end_sec)
    if gaps:
        total_gap = sum(g["duration"] for g in gaps)
        print(f"[export] ⚠️ {len(gaps)} gap(s) detected, total {total_gap:.1f}s")
    else:
        print(f"[export] no gaps detected")

    # ---- 3. Prepare paths ----
    ext = Path(out_path).suffix.lower()
    if ext not in (".mp4", ".ts", ".avi", ".mkv"):
        ext = ".mp4"
        out_path = str(out_path) + ext

    out_fmt_map = {
        ".mp4": "mp4",
        ".ts":  "mpegts",
        ".avi": "avi",
        ".mkv": "matroska",
    }
    out_fmt = out_fmt_map[ext]

    # Temp workspace in ASCII path
    tmp_dir = Path(tempfile.mkdtemp(prefix="k1export_"))
    tmp_out = tmp_dir / ("output" + ext)

    try:
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            return False, "FFmpeg not found"

        # ---- 4. Build concat list (or single trimmed file) ----
        # For the range: if only 1 segment or the range is inside 1 segment,
        # we can just trim that segment with -ss/-t.
        # Otherwise use concat demuxer with per-segment trim.

        if mode == "precise":
            ok, msg = _export_precise(
                ffmpeg, selected, start_sec, end_sec,
                tmp_out, out_fmt, _cb
            )
        else:
            ok, msg = _export_fast(
                ffmpeg, selected, start_sec, end_sec,
                tmp_out, out_fmt, _cb
            )

        if not ok:
            return False, msg

        # ---- 5. Validate output ----
        _cb(92, "validating output…")
        ok, meta = _validate_output(tmp_out)
        if not ok:
            return False, meta  # message is the error

        actual_dur = meta.get("duration", 0)
        wanted_dur = end_sec - start_sec
        print(f"[export] actual duration: {actual_dur:.1f}s (wanted {wanted_dur:.1f}s)")

        if actual_dur < 0.5:
            return False, "Output duration too short"

        # ---- 6. Move to destination ----
        _cb(96, "moving to destination…")
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            if os.path.exists(out_path):
                try: os.remove(out_path)
                except Exception: pass
            shutil.move(str(tmp_out), out_path)
        except Exception as e:
            return False, f"Move failed: {e}"

        _cb(100, f"saved: {Path(out_path).name}")
        print(f"[export] ✅ saved: {out_path}")
        return True, "OK"

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, str(e)
    finally:
        try: shutil.rmtree(str(tmp_dir), ignore_errors=True)
        except Exception: pass


# ============================================================
# FAST mode — concat demuxer + stream copy
# ============================================================
def _export_fast(ffmpeg, selected, start_sec, end_sec, tmp_out, out_fmt, _cb):
    """Fast mode: stream-copy the range using concat demuxer with trim."""
    _cb(5, "preparing FAST export…")

    # We use ffmpeg concat demuxer with individual inputs.
    # Each input is opened and its start/end trimmed via -ss/-t on the fly.
    # This keeps streams untouched (stream copy).

    # Build filter-free command: multiple inputs, each with -ss -t
    inputs = []
    for seg in selected:
        # segment absolute time overlap
        ss = max(0.0, start_sec - seg["start"])   # offset within file
        take = min(seg["end"], end_sec) - max(seg["start"], start_sec)
        if take <= 0:
            continue
        inputs.append((seg["path"], ss, take))

    if not inputs:
        return False, "No overlapping segments"

    # If only one input, simple trim.
    # If multiple, use concat demuxer via temporary per-segment trim files.
    if len(inputs) == 1:
        return _fast_single(ffmpeg, inputs[0], tmp_out, out_fmt, _cb)

    return _fast_multi(ffmpeg, inputs, tmp_out, out_fmt, _cb)


def _fast_single(ffmpeg, inp, tmp_out, out_fmt, _cb):
    path, ss, take = inp
    _cb(15, "stream-copy single segment…")
    cmd = [
        ffmpeg, "-y", "-hide_banner",
        "-progress", "pipe:1", "-nostats",
        "-loglevel", "error",
        "-ss", f"{ss:.3f}",
        "-i", path,
        "-t", f"{take:.3f}",
        "-c", "copy",
        "-f", out_fmt,
    ]
    if out_fmt == "mp4":
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(tmp_out))
    return _run_ffmpeg_with_progress(cmd, 15, 88, take, "stream-copy", _cb)


def _fast_multi(ffmpeg, inputs, tmp_out, out_fmt, _cb):
    """Trim each segment to a temp .ts, then concat via demuxer, then remux."""
    tmp_dir = tmp_out.parent

    # Phase 1: trim each segment (stream-copy)
    parts = []
    total_take = sum(t for _, _, t in inputs)
    for i, (path, ss, take) in enumerate(inputs):
        part = tmp_dir / f"part_{i:03d}.ts"
        _cb(5 + int((i / len(inputs)) * 40),
            f"trimming {i+1}/{len(inputs)}…")
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{ss:.3f}",
            "-i", path,
            "-t", f"{take:.3f}",
            "-c", "copy",
            "-fflags", "+genpts",
            "-avoid_negative_ts", "make_zero",
            "-f", "mpegts",
            str(part),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600,
                               **_spawn_kwargs())
        except Exception as e:
            print(f"[export] trim {i} failed: {e}")
            continue
        if r.returncode == 0 and part.exists() and part.stat().st_size > 500:
            parts.append(str(part))
        else:
            print(f"[export] trim {i} returncode={r.returncode}")

    if not parts:
        return False, "No parts could be trimmed"

    _cb(50, f"concatenating {len(parts)} parts…")

    # Phase 2: concat demuxer
    concat_file = tmp_dir / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in parts:
            # escape single quotes for ffmpeg
            safe = str(p).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    # Phase 3: remux concat.ts → final output
    cmd = [
        ffmpeg, "-y", "-hide_banner",
        "-progress", "pipe:1", "-nostats",
        "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-fflags", "+genpts",
    ]
    if out_fmt == "mp4":
        cmd += ["-movflags", "+faststart", "-bsf:a", "aac_adtstoasc"]
    cmd += ["-f", out_fmt, str(tmp_out)]

    return _run_ffmpeg_with_progress(cmd, 50, 88, total_take, "stream-copy", _cb)


# ============================================================
# PRECISE mode — re-encode boundary segments only
# ============================================================
def _export_precise(ffmpeg, selected, start_sec, end_sec, tmp_out, out_fmt, _cb):
    """Precise mode: re-encode the first and last segment boundary,
    stream-copy the middle segments.
    """
    _cb(5, "preparing PRECISE export…")

    tmp_dir = tmp_out.parent
    parts = []

    n = len(selected)
    total_take = end_sec - start_sec

    # ---- Split into: [head] [middle...] [tail] ----
    head_ss = max(0.0, start_sec - selected[0]["start"])
    head_take = min(selected[0]["end"], end_sec) - max(selected[0]["start"], start_sec)

    tail_ss = max(0.0, start_sec - selected[-1]["start"])
    tail_take = min(selected[-1]["end"], end_sec) - max(selected[-1]["start"], start_sec)

    # ---- Encode HEAD (if re-encode needed) ----
    if head_take > 0:
        _cb(10, "encoding head (precise)…")
        head = tmp_dir / "part_head.ts"
        ok = _encode_one(ffmpeg, selected[0]["path"], head_ss, head_take, head, _cb, 10, 30)
        if ok:
            parts.append(str(head))

    # ---- Stream-copy middle segments ----
    for i in range(1, n - 1):
        seg = selected[i]
        # full range inside this segment (no boundary)
        _cb(30 + int((i / max(1, n)) * 40),
            f"copying middle {i}…")
        part = tmp_dir / f"part_{i:03d}.ts"
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", seg["path"],
            "-c", "copy",
            "-fflags", "+genpts",
            "-avoid_negative_ts", "make_zero",
            "-f", "mpegts",
            str(part),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600,
                               **_spawn_kwargs())
            if r.returncode == 0 and part.exists() and part.stat().st_size > 500:
                parts.append(str(part))
        except Exception as e:
            print(f"[export] middle copy error: {e}")

    # ---- Encode TAIL (if re-encode needed) ----
    if n > 1 and tail_take > 0:
        _cb(70, "encoding tail (precise)…")
        tail = tmp_dir / "part_tail.ts"
        ok = _encode_one(ffmpeg, selected[-1]["path"], tail_ss, tail_take, tail, _cb, 70, 85)
        if ok:
            parts.append(str(tail))

    if not parts:
        return False, "No parts could be prepared"

    _cb(86, "concatenating parts…")

    # ---- Concat ----
    concat_file = tmp_dir / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in parts:
            safe = str(p).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        ffmpeg, "-y", "-hide_banner",
        "-progress", "pipe:1", "-nostats",
        "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-fflags", "+genpts",
    ]
    if out_fmt == "mp4":
        cmd += ["-movflags", "+faststart"]
    cmd += ["-f", out_fmt, str(tmp_out)]

    return _run_ffmpeg_with_progress(cmd, 86, 92, total_take, "final concat", _cb)


def _encode_one(ffmpeg, path, ss, take, out, _cb, pct_start, pct_end):
    """Re-encode one portion to H.264."""
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{ss:.3f}",
        "-i", path,
        "-t", f"{take:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        "-f", "mpegts",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=1800,
                           **_spawn_kwargs())
        return (r.returncode == 0 and out.exists() and out.stat().st_size > 500)
    except Exception as e:
        print(f"[export] encode error: {e}")
        return False


# ============================================================
# FFmpeg execution with progress
# ============================================================
def _run_ffmpeg_with_progress(cmd, pct_start, pct_end, total_dur, label, _cb):
    print(f"[export] run: {' '.join(str(x) for x in cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            **_spawn_kwargs()
        )
    except Exception as e:
        return False, f"Popen failed: {e}"

    span = pct_end - pct_start
    last_pct = pct_start

    while True:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        line = line.strip()
        if line.startswith("out_time_us="):
            try:
                t_us = int(line.split("=", 1)[1])
                t_sec = t_us / 1_000_000.0
                if total_dur > 0:
                    frac = min(1.0, max(0.0, t_sec / total_dur))
                    pct = pct_start + frac * span
                    if pct - last_pct >= 0.5:
                        _cb(pct, f"{label} {t_sec:.1f}s")
                        last_pct = pct
            except Exception:
                pass
        elif line.startswith("progress=") and line.endswith("end"):
            _cb(pct_end, f"{label} done")

    try:
        proc.wait(timeout=30)
    except Exception:
        try: proc.kill()
        except Exception: pass

    err = ""
    try:
        err = proc.stderr.read()
    except Exception:
        pass

    if proc.returncode != 0:
        print(f"[export] ffmpeg failed rc={proc.returncode}")
        print(f"[export] stderr: {err[:600]}")
        return False, f"FFmpeg failed: {err[:300]}"

    return True, "OK"


# ============================================================
# Validation via FFprobe
# ============================================================
def _validate_output(path: Path) -> Tuple[bool, Any]:
    if not path.exists():
        return False, "Output file missing"
    if path.stat().st_size < 1000:
        return False, f"Output too small ({path.stat().st_size} bytes)"

    ffprobe = _find_ffprobe()
    if not ffprobe:
        # No ffprobe → still accept (best effort)
        return True, {"duration": 0, "codec": None}

    cmd = [
        ffprobe, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=15, **_spawn_kwargs())
        if r.returncode != 0:
            return False, f"FFprobe open failed"
        import json
        data = json.loads(r.stdout)
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        if not streams:
            return False, "No streams in output"

        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not video:
            return False, "No video stream"

        try:
            duration = float(fmt.get("duration") or video.get("duration") or 0)
        except Exception:
            duration = 0

        return True, {
            "duration": duration,
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": video.get("avg_frame_rate"),
        }
    except Exception as e:
        return False, f"Validation error: {e}"


# ============================================================
# Public helper: list gaps for a range
# ============================================================
def scan_gaps(files_with_start: List[Dict], start_sec: float,
               end_sec: float) -> List[Dict]:
    """Public API used by UI to display gap info in Export Dialog."""
    selected = _select_overlapping(files_with_start, start_sec, end_sec)
    return _detect_gaps(selected, start_sec, end_sec)