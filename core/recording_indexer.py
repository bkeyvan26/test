# -*- coding: utf-8 -*-
"""K1 VMS — Recording Indexer

Reliable, cached index of recording segments.

Phase 8.0:
  - _camera_dir از nvr_xxx/ch_NN پشتیبانی می‌کند
  - هم مسیر جدید (nvr_xxx/ch_NN) و هم مسیر قدیمی (cam_xxx) شناسایی می‌شود
"""
import os
import re
import time
import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

import config
from core.db_manager import DBManager
from core import ffprobe_util


# ---- Constants ----
STATE_WRITING = "WRITING"
STATE_FINALIZED = "FINALIZED"
STATE_UNKNOWN = "UNKNOWN"

FINALIZE_THRESHOLD_SEC = 30.0
CONTIGUOUS_TOLERANCE_SEC = 3.0
PROBE_CACHE_SEC = 60 * 60 * 24 * 7   # 7 days
TS_PROBE_READ_BYTES = 256 * 1024
PCR_CLOCK_HZ = 27_000_000.0
PCR_WRAP_TICKS = float((1 << 33) * 300)


# ---- Data model ----
@dataclass
class Segment:
    file_path: str
    camera_name: str
    date: str
    start_sec: int
    end_sec: Optional[int]
    duration: Optional[float]
    state: str
    codec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    file_size: int = 0
    file_mtime: float = 0.0

    def is_active(self) -> bool:
        return self.state == STATE_WRITING


@dataclass
class Gap:
    start_sec: float
    end_sec: float
    duration: float


@dataclass
class DayIndex:
    camera_name: str
    date: str
    segments: List[Segment] = field(default_factory=list)
    gaps: List[Gap] = field(default_factory=list)
    total_recording: float = 0.0
    total_gap: float = 0.0


# ---- Indexer ----
class RecordingIndexer:
    _instance = None

    @classmethod
    def instance(cls, nvr_engine=None) -> "RecordingIndexer":
        if cls._instance is None:
            cls._instance = cls(nvr_engine)
        return cls._instance

    def __init__(self, nvr_engine=None):
        self.db = DBManager.instance()
        self.nvr = nvr_engine

    # ---------- paths ----------
    def _recording_root(self) -> str:
        if self.nvr:
            try:
                return self.nvr.get_recording_root()
            except Exception:
                pass
        return str(config.CONTINUOUS_DIR)

    # ============================================================
    # ★ _camera_dir — پشتیبانی از ساختار NVR-grouped
    # ============================================================
    def _camera_dir(self, cam_name: str) -> Path:
        """
        مسیر پوشه ضبط — پشتیبانی از nvr_xxx/ch_NN و cam_xxx.
        ساختار جدید:
          continuous/nvr_3287fa49/ch_01/
          continuous/cam_5e6be882/
        """
        from core.camera_manager import CameraManager

        root = Path(self._recording_root())

        # تلاش ۱: پیدا کردن camera از نام (تطبیق دقیق یا از uid)
        cam = None
        try:
            cm = CameraManager()
            for c in cm.all():
                if (c.name or "").strip() == cam_name.strip():
                    cam = c
                    break
            if cam is None:
                cam = cm.get(cam_name)
        except Exception as e:
            print(f"[Indexer] camera lookup failed: {e}")

        if cam is not None:
            uid = cam.uid or ""
            ascii_uid = re.sub(r"[^a-zA-Z0-9]", "", uid)[:12] or "unknown"

            # اگر NVR باشد
            if cam.nvr_uid:
                nvr_ascii = re.sub(r"[^a-zA-Z0-9]", "",
                                     cam.nvr_uid or "")[:8]
                if nvr_ascii:
                    m = re.search(r"دوربین\s+(\d+)", cam.name or "")
                    if m:
                        ch = int(m.group(1))
                        new_path = root / f"nvr_{nvr_ascii}" / f"ch_{ch:02d}"
                        if new_path.exists():
                            return new_path
                    # fallback: cam_xxx داخل nvr folder
                    nested = root / f"nvr_{nvr_ascii}" / f"cam_{ascii_uid}"
                    if nested.exists():
                        return nested

            # standalone
            standalone_path = root / f"cam_{ascii_uid}"
            if standalone_path.exists():
                return standalone_path

            # مسیر قدیمی: cam_xxx در _data
            data_path = root / "_data" / f"cam_{ascii_uid}"
            if data_path.exists():
                return data_path

        # تلاش ۲: اگر cam_name از قبل نام پوشه است
        direct = root / cam_name
        if direct.exists():
            return direct

        # تلاش ۳: _data/cam_name
        data_direct = root / "_data" / cam_name
        if data_direct.exists():
            return data_direct

        # fallback نهایی: همان root/cam_name
        return root / cam_name

    def _day_dir(self, cam_name: str, date_str: str) -> Path:
        return self._camera_dir(cam_name) / date_str

    # ---------- public ----------
    def get_day_index(self, cam_name: str, day: datetime.date) -> DayIndex:
        date_str = day.strftime("%Y-%m-%d")

        should_sync = day >= datetime.date.today()
        if not should_sync:
            row = self.db.execute(
                "SELECT COUNT(*) AS n FROM segments "
                "WHERE camera_name=? AND date=?",
                (cam_name, date_str),
            ).fetchone()
            should_sync = not row or int(row["n"] or 0) == 0

        if should_sync:
            self.sync_day(cam_name, date_str)

        segments = self._load_segments(cam_name, date_str)
        self._schedule_legacy_repair(cam_name, date_str)
        gaps = self._compute_gaps(segments)
        print(f"[Indexer] {cam_name} {date_str}: "
              f"{len(segments)} segments, {len(gaps)} gaps")
        return DayIndex(
            camera_name=cam_name,
            date=date_str,
            segments=segments,
            gaps=gaps,
            total_recording=sum(s.duration or 0 for s in segments),
            total_gap=sum(g.duration for g in gaps),
        )

    def get_recording_dates(self, cam_name: str) -> set:
        """برگرداندن روزهایی که حداقل یک فایل TS دارند."""
        cam_dir = self._camera_dir(cam_name)
        dates = set()
        if not cam_dir.exists():
            return dates
        try:
            for d in cam_dir.iterdir():
                if not d.is_dir():
                    continue
                try:
                    day = datetime.datetime.strptime(d.name, "%Y-%m-%d").date()
                except ValueError:
                    continue
                try:
                    has_ts = any(
                        f.is_file() and f.suffix.lower() == ".ts"
                        and f.stat().st_size > 0
                        for f in d.iterdir()
                    )
                except Exception:
                    has_ts = False
                if has_ts:
                    dates.add(day)
        except Exception:
            pass
        return dates

    # ---------- sync ----------
    def sync_day(self, cam_name: str, date_str: str):
        day_dir = self._day_dir(cam_name, date_str)
        if not day_dir.exists():
            self.db.execute(
                "DELETE FROM segments WHERE camera_name=? AND date=?",
                (cam_name, date_str),
            )
            return

        disk_files: Dict[str, Dict[str, Any]] = {}
        try:
            for f in day_dir.glob("*.ts"):
                try:
                    st = f.stat()
                except Exception:
                    continue
                disk_files[str(f)] = {
                    "path": str(f),
                    "name": f.name,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
        except Exception as e:
            print(f"[Indexer] scan error: {e}")
            return

        cur = self.db.execute(
            "SELECT file_path, file_size, file_mtime, probe_timestamp, state "
            "FROM segments WHERE camera_name=? AND date=?",
            (cam_name, date_str),
        )
        cached = {row["file_path"]: dict(row) for row in cur.fetchall()}

        now = time.time()
        inserts, updates = [], []

        for path, info in disk_files.items():
            old = cached.pop(path, None)
            state = self._detect_state(info["mtime"], now)

            if old is None:
                inserts.append((path, info, state))
                continue

            changed = (
                old["file_size"] != info["size"]
                or abs(old["file_mtime"] - info["mtime"]) > 0.5
                or old["state"] != state
            )
            if changed:
                updates.append((path, info, state, old))

        for path in list(cached.keys()):
            self.db.execute("DELETE FROM segments WHERE file_path=?", (path,))

        for path, info, state in inserts:
            self._insert_segment(cam_name, date_str, info, state, now)
        for path, info, state, old in updates:
            self._update_segment(path, info, state, old, now)

        probe_candidate = next(
            (info["path"] for _, info, state in inserts
             if state == STATE_FINALIZED),
            None,
        )
        if probe_candidate:
            try:
                probe = ffprobe_util.probe_media(probe_candidate, timeout=8.0)
            except Exception:
                probe = None
            if probe:
                self.db.execute(
                    """
                    UPDATE segments SET
                        codec=?, width=?, height=?, fps=?,
                        probe_method=CASE
                            WHEN probe_method IS NULL OR probe_method IN ('none', 'mtime_fast')
                            THEN 'day_probe'
                            ELSE probe_method
                        END,
                        probe_timestamp=?,
                        updated_at=?
                    WHERE camera_name=? AND date=?
                      AND codec IS NULL
                    """,
                    (
                        probe.get("codec"),
                        probe.get("width"),
                        probe.get("height"),
                        probe.get("fps"),
                        now,
                        now,
                        cam_name,
                        date_str,
                    ),
                )

    # ---------- state ----------
    @staticmethod
    def _detect_state(mtime: float, now: float) -> str:
        if now - mtime < FINALIZE_THRESHOLD_SEC:
            return STATE_WRITING
        return STATE_FINALIZED

    # ---------- filename ----------
    @staticmethod
    def _filename_start_sec(name: str) -> Optional[int]:
        try:
            parts = Path(name).stem.split("-")
            if len(parts) < 3:
                return None
            hh, mm, ss = int(parts[0]), int(parts[1]), int(parts[2])
            if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
                return None
            return hh * 3600 + mm * 60 + ss
        except Exception:
            return None

    # ---------- insert ----------
    def _insert_segment(self, cam_name, date_str, info, state, now):
        start_sec = self._filename_start_sec(info["name"])
        if start_sec is None:
            return

        duration = None
        end_sec = None
        codec = width = height = fps = None
        probe_method = "none"
        probe_ts = None

        if state == STATE_FINALIZED:
            dur = self._ts_duration_fast(info["path"])
            if dur is None:
                dur = self._mtime_duration(info["mtime"], start_sec)
                probe_method = "mtime_fast" if dur else "none"
            else:
                probe_method = "ts_pcr"
            if dur and dur > 0:
                duration = float(dur)
                end_sec = start_sec + duration

        self.db.execute(
            """
            INSERT OR REPLACE INTO segments
            (camera_name, date, file_path, file_name,
             start_sec, end_sec, duration,
             file_size, file_mtime, state,
             codec, width, height, fps,
             probe_method, probe_timestamp,
             discovered_at, updated_at)
            VALUES (?,?,?,?, ?,?,?, ?,?,?, ?,?,?,?, ?,?, ?,?)
            """,
            (
                cam_name, date_str, info["path"], info["name"],
                start_sec, end_sec, duration,
                info["size"], info["mtime"], state,
                codec, width, height, fps,
                probe_method, probe_ts,
                now, now,
            ),
        )

    # ---------- update ----------
    def _update_segment(self, path, info, state, old, now):
        needs_probe = False

        if state == STATE_FINALIZED:
            if old["state"] != STATE_FINALIZED:
                needs_probe = True
            elif (old["file_size"] != info["size"]
                  or abs(old["file_mtime"] - info["mtime"]) > 0.5):
                needs_probe = True
            elif old.get("probe_timestamp"):
                if now - old["probe_timestamp"] > PROBE_CACHE_SEC:
                    needs_probe = True

        if needs_probe:
            dur = self._ts_duration_fast(info["path"])
            if dur and dur > 0:
                cur = self.db.execute(
                    "SELECT start_sec FROM segments WHERE file_path=?", (path,)
                )
                row = cur.fetchone()
                if row:
                    start_sec = row["start_sec"]
                    self.db.execute(
                        """
                        UPDATE segments SET
                            file_size=?, file_mtime=?, state=?,
                            end_sec=?, duration=?,
                            probe_method='ts_pcr', probe_timestamp=?,
                            updated_at=?
                        WHERE file_path=?
                        """,
                        (
                            info["size"], info["mtime"], state,
                            float(start_sec + dur), float(dur),
                            now, now, path,
                        ),
                    )
                    return

        self.db.execute(
            """
            UPDATE segments SET
                file_size=?, file_mtime=?, state=?, updated_at=?
            WHERE file_path=?
            """,
            (info["size"], info["mtime"], state, now, path),
        )

    def _schedule_legacy_repair(self, cam_name: str, date_str: str):
        key = (cam_name, date_str)
        if not hasattr(self, "_repair_jobs"):
            self._repair_jobs = set()
        if key in self._repair_jobs:
            return
        try:
            row = self.db.execute(
                "SELECT COUNT(*) AS n FROM segments "
                "WHERE camera_name=? AND date=? "
                "AND probe_method IN ('mtime_fast','day_probe')",
                (cam_name, date_str),
            ).fetchone()
            if not row or int(row["n"] or 0) == 0:
                return
        except Exception:
            return

        self._repair_jobs.add(key)

        import threading
        def worker():
            try:
                repaired = self._repair_legacy_day(cam_name, date_str)
                if repaired:
                    print(f"[Indexer] background repaired {repaired} "
                          f"legacy durations for {cam_name} {date_str}")
            except Exception as e:
                print(f"[Indexer] background legacy repair error: {e}")
            finally:
                self._repair_jobs.discard(key)

        threading.Thread(
            target=worker,
            name=f"k1-index-repair-{cam_name}-{date_str}",
            daemon=True,
        ).start()

    def _repair_legacy_day(self, cam_name: str, date_str: str) -> int:
        cur = self.db.execute(
            """
            SELECT file_path, start_sec
            FROM segments
            WHERE camera_name=? AND date=? AND probe_method IN ('mtime_fast','day_probe')
            ORDER BY start_sec ASC
            """,
            (cam_name, date_str),
        )
        rows = cur.fetchall()
        if not rows:
            return 0

        now = time.time()
        updates = []
        repaired = 0
        for row in rows:
            path = row["file_path"]
            try:
                st = os.stat(path)
                if st.st_size <= 0:
                    continue
            except Exception:
                continue

            dur = self._ts_duration_fast(path)
            if dur is None or dur <= 0:
                continue

            start_sec = int(row["start_sec"])
            updates.append((
                float(dur),
                int(start_sec + float(dur)),
                int(st.st_size),
                float(st.st_mtime),
                "ts_pcr",
                now,
                now,
                path,
            ))
            repaired += 1

        if updates:
            self.db.executemany(
                """
                UPDATE segments SET
                    duration=?, end_sec=?,
                    file_size=?, file_mtime=?,
                    probe_method=?, probe_timestamp=?, updated_at=?
                WHERE file_path=?
                """,
                updates,
            )
        return repaired

    @staticmethod
    def _ts_duration_fast(path: str) -> Optional[float]:
        """Estimate MPEG-TS duration from PCR without spawning ffprobe."""
        try:
            size = os.path.getsize(path)
            if size < 188:
                return None

            read_size = min(TS_PROBE_READ_BYTES, size)
            with open(path, "rb") as fh:
                first_buf = fh.read(read_size)
                if size > read_size:
                    fh.seek(max(0, size - read_size), os.SEEK_SET)
                    last_buf = fh.read(read_size)
                else:
                    last_buf = first_buf

            def find_pcr(buf: bytes, reverse: bool = False) -> Optional[int]:
                if len(buf) < 188:
                    return None

                offsets = range(len(buf) - 188, -1, -1) if reverse \
                    else range(0, len(buf) - 187)
                for off in offsets:
                    if buf[off] != 0x47:
                        continue
                    if off + 188 < len(buf) and buf[off + 188] != 0x47:
                        continue

                    packet = buf[off:off + 188]
                    if len(packet) < 188:
                        continue
                    afc = (packet[3] >> 4) & 0x03
                    if afc not in (2, 3):
                        continue
                    afl = packet[4]
                    if afl < 7 or 5 + afl > 188:
                        continue
                    flags = packet[5]
                    if not (flags & 0x10):
                        continue

                    b0, b1, b2, b3, b4, b5 = packet[6:12]
                    base = (
                        (b0 << 25) |
                        (b1 << 17) |
                        (b2 << 9) |
                        (b3 << 1) |
                        (b4 >> 7)
                    )
                    ext = ((b4 & 0x01) << 8) | b5
                    return int(base * 300 + ext)
                return None

            first = find_pcr(first_buf, reverse=False)
            last = find_pcr(last_buf, reverse=True)
            if first is None or last is None:
                return None

            ticks = float(last - first)
            if ticks < 0:
                ticks += PCR_WRAP_TICKS

            duration = ticks / PCR_CLOCK_HZ
            if duration < 0.05 or duration > 7200.0:
                return None
            return duration
        except Exception:
            return None

    @staticmethod
    def _mtime_duration(mtime: float, start_sec: int) -> Optional[float]:
        try:
            dt = datetime.datetime.fromtimestamp(float(mtime))
            day_start = datetime.datetime(
                dt.year, dt.month, dt.day, 0, 0, 0
            ).timestamp()
            mt_sec = float(mtime) - float(day_start)
            dur = mt_sec - float(start_sec)
            if dur < 0:
                dur += 86400.0
            if dur < 0.25 or dur > 3600 * 2:
                return None
            return float(dur)
        except Exception:
            return None

    # ---------- load ----------
    def _load_segments(self, cam_name: str, date_str: str) -> List[Segment]:
        cur = self.db.execute(
            """
            SELECT file_path, camera_name, date, start_sec, end_sec, duration,
                   state, codec, width, height, fps, file_size, file_mtime,
                   probe_method
            FROM segments
            WHERE camera_name=? AND date=?
            ORDER BY start_sec ASC
            """,
            (cam_name, date_str),
        )
        rows = cur.fetchall()
        out = []

        for i, row in enumerate(rows):
            start_sec = int(row["start_sec"])
            duration = row["duration"]
            method = row["probe_method"]

            if method in ("mtime_fast", "day_probe"):
                if i + 1 < len(rows):
                    next_start = int(rows[i + 1]["start_sec"])
                    provisional = float(next_start - start_sec)
                    if provisional > 0:
                        duration = provisional

            precise_end = (
                float(start_sec) + float(duration)
                if duration is not None and float(duration) > 0
                else row["end_sec"]
            )
            out.append(Segment(
                file_path=row["file_path"],
                camera_name=row["camera_name"],
                date=row["date"],
                start_sec=start_sec,
                end_sec=precise_end,
                duration=duration,
                state=row["state"],
                codec=row["codec"],
                width=row["width"],
                height=row["height"],
                fps=row["fps"],
                file_size=row["file_size"],
                file_mtime=row["file_mtime"],
            ))

        # ★ بازنویسی durationهای نادرست با فاصله واقعی فایل‌ها
        return self._repair_timeline_durations(out)

    @staticmethod
    def _repair_timeline_durations(segments: List[Segment]) -> List[Segment]:
        """Repair legacy/incorrect durations using actual spacing."""
        if len(segments) < 2:
            return segments

        ordered = sorted(segments, key=lambda s: float(s.start_sec))
        deltas = []
        for a, b in zip(ordered, ordered[1:]):
            d = float(b.start_sec) - float(a.start_sec)
            if 0 < d <= 900.0:
                deltas.append(d)

        if not deltas:
            return ordered

        deltas.sort()
        nominal = deltas[len(deltas) // 2]

        if nominal < 1.0 or nominal > 900.0:
            return ordered

        join_limit = nominal + CONTIGUOUS_TOLERANCE_SEC

        for i, seg in enumerate(ordered[:-1]):
            delta = float(ordered[i + 1].start_sec) - float(seg.start_sec)
            if delta <= 0:
                continue

            if delta <= join_limit:
                repaired = delta
            else:
                repaired = nominal

            seg.duration = float(repaired)
            seg.end_sec = float(seg.start_sec) + float(repaired)

        last = ordered[-1]
        if last.duration is not None and float(last.duration) > 0:
            last.end_sec = float(last.start_sec) + float(last.duration)

        return ordered

    def _compute_gaps(self, segments: List[Segment]) -> List[Gap]:
        known = [
            s for s in segments
            if s.end_sec is not None and s.duration and s.duration > 0
        ]
        known.sort(key=lambda s: s.start_sec)
        gaps = []
        for i in range(len(known) - 1):
            a, b = known[i], known[i + 1]
            gs, ge = a.end_sec, b.start_sec
            gd = ge - gs
            if gd > CONTIGUOUS_TOLERANCE_SEC:
                gaps.append(Gap(
                    start_sec=float(gs),
                    end_sec=float(ge),
                    duration=float(gd),
                ))
        return gaps