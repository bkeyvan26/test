# -*- coding: utf-8 -*-
"""K1 VMS — Export engine (Unicode-safe, ffmpeg-based)"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

IS_WIN = os.name == "nt"


def _spawn_kwargs():
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def _find_ffmpeg():
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    try:
        base = Path(__file__).parent.parent
        for name in ("ffmpeg.exe", "ffmpeg"):
            p = base / name
            if p.exists():
                return str(p)
    except Exception:
        pass
    return shutil.which("ffmpeg")


def export_range(files_with_start, out_path, start_sec, end_sec,
                 progress_cb=None):
    """Export range via ffmpeg. Uses ASCII temp paths to avoid Unicode issues.

    Steps:
      1. Pick all segments overlapping [start_sec, end_sec].
      2. Concatenate them into a temp ASCII .ts file.
      3. Run ffmpeg to trim exact range and remux.
      4. Move the result to the Unicode destination.
    """
    print(f"[export] request: range={start_sec}..{end_sec}s, files={len(files_with_start)}")

    # --- 1. Pick overlapping segments ---
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
            "duration": dur,
            "end": fe,
        })

    print(f"[export] overlapping segments: {len(selected)}")
    if not selected:
        return False, "No segments in range"

    # --- Determine output extension ---
    ext = Path(out_path).suffix.lower()
    if not ext:
        ext = ".mp4"
        out_path = str(out_path) + ext

    # --- 2. Prepare ASCII temp workspace ---
    tmp_dir = tempfile.mkdtemp(prefix="k1export_")
    concat_ts = os.path.join(tmp_dir, "concat.ts")
    tmp_out = os.path.join(tmp_dir, "output" + ext)

    try:
        # --- Concatenate segments (binary-safe) ---
        try:
            with open(concat_ts, "wb") as out:
                for i, seg in enumerate(selected):
                    with open(seg["path"], "rb") as src:
                        while True:
                            chunk = src.read(1024 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                    if progress_cb:
                        progress_cb(int((i + 1) / len(selected) * 30))
        except Exception as e:
            print(f"[export] concat failed: {e}")
            return False, f"Concat failed: {e}"

        concat_size = os.path.getsize(concat_ts)
        print(f"[export] concat size: {concat_size} bytes")
        if concat_size < 1000:
            return False, "Concatenated file too small"

        # --- Relative start ---
        first_start = selected[0]["start"]
        relative_start = max(0.0, start_sec - first_start)
        total_dur = float(end_sec) - float(start_sec)

        # --- 3. Find ffmpeg ---
        ffmpeg = _find_ffmpeg()
        print(f"[export] ffmpeg: {ffmpeg}")

        if not ffmpeg:
            # No ffmpeg → copy concat as final (full segments)
            print("[export] no ffmpeg — using raw concat")
            try:
                shutil.move(concat_ts, out_path)
                if progress_cb:
                    progress_cb(100)
                return True, "OK (full segments, no ffmpeg)"
            except Exception as e:
                return False, f"Move failed: {e}"

        # --- Determine ffmpeg format ---
        fmt_map = {
            ".mp4": "mp4",
            ".mkv": "matroska",
            ".avi": "avi",
            ".ts": "mpegts",
        }
        out_fmt = fmt_map.get(ext, "mp4")

        # --- Build command (ASCII paths only) ---
        # `-ss` before `-i` → fast keyframe seek
        # `-t` after `-i` → limit duration
        cmd_stream = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
            "-ss", f"{relative_start:.3f}",
            "-i", concat_ts,
            "-t", f"{total_dur:.3f}",
            "-c", "copy",
            "-f", out_fmt,
        ]
        if ext == ".mp4":
            cmd_stream += ["-movflags", "+faststart"]
        cmd_stream.append(tmp_out)

        print(f"[export] trying stream-copy…")
        ok = _run_ffmpeg(cmd_stream, timeout=300, tmp_out=tmp_out)
        if progress_cb:
            progress_cb(70)

        if not ok:
            # --- Re-encode fallback ---
            print("[export] stream-copy failed, re-encoding…")
            cmd_reenc = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
                "-ss", f"{relative_start:.3f}",
                "-i", concat_ts,
                "-t", f"{total_dur:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-f", out_fmt,
            ]
            if ext == ".mp4":
                cmd_reenc += ["-movflags", "+faststart"]
            cmd_reenc.append(tmp_out)
            ok = _run_ffmpeg(cmd_reenc, timeout=1800, tmp_out=tmp_out)

        if progress_cb:
            progress_cb(95)

        if not ok or not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) < 1000:
            return False, "ffmpeg produced no valid output"

        # --- 4. Move to Unicode destination ---
        try:
            # Ensure parent dir exists
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            # Remove existing file (avoid locked handle issue)
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            shutil.move(tmp_out, out_path)
            print(f"[export] ✅ saved: {out_path}")
        except Exception as e:
            return False, f"Move to destination failed: {e}"

        if progress_cb:
            progress_cb(100)
        return True, "OK"

    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def _run_ffmpeg(cmd, timeout=600, tmp_out=None):
    """Run ffmpeg command. Returns True if exit code == 0."""
    try:
        print(f"[export] cmd: {' '.join(str(c) for c in cmd)}")
        r = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            **_spawn_kwargs(),
        )
        print(f"[export] ffmpeg return code: {r.returncode}")
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "ignore")[:500]
            print(f"[export] ffmpeg stderr: {err}")
            return False
        if tmp_out and (not os.path.isfile(tmp_out)
                        or os.path.getsize(tmp_out) < 1000):
            print(f"[export] tmp_out missing or too small")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("[export] ffmpeg TIMEOUT")
        return False
    except Exception as e:
        print(f"[export] ffmpeg exception: {e}")
        return False