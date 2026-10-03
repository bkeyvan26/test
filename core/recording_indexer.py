# -*- coding: utf-8 -*-
"""K1 VMS — Recording Indexer

Reliable, cached index of recording segments.

Sources of truth:
  - Disk files           : existence, size, mtime
  - FFprobe metadata     : duration, codec, resolution, fps
  - SQLite               : cache (rebuildable)

States:
  - WRITING    : file recently modified (< 30s)
  - FINALIZED  : file stable
  - UNKNOWN    : detection failed
"""
import os
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
CONTIGUOUS_TOLERANCE_SEC = 1.0
PROBE_CACHE_SEC = 60 * 60 * 24 * 7   # 7 days


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

    def _camera_dir(self, cam_name: str) -> Path:
        return Path(self._recording_root()) / cam_name

    def _day_dir(self, cam_name: str, date_str: str) -> Path:
        return self._camera_dir(cam_name) / date_str

    # ---------- public ----------
    def get_day_index(self, cam_name: str, day: datetime.date) -> DayIndex:
        date_str = day.strftime("%Y-%m-%d")
        self.sync_day(cam_name, date_str)
        segments = self._load_segments(cam_name, date_str)
        gaps = self._compute_gaps(segments)
        print(f"[Indexer] {cam_name} {date_str}: {len(segments)} segments, {len(gaps)} gaps")
        return DayIndex(
            camera_name=cam_name,
            date=date_str,
            segments=segments,
            gaps=gaps,
            total_recording=sum(s.duration or 0 for s in segments),
            total_gap=sum(g.duration for g in gaps),
        )

    def get_recording_dates(self, cam_name: str) -> set:
        cam_dir = self._camera_dir(cam_name)
        dates = set()
        if not cam_dir.exists():
            return dates
        try:
            for d in cam_dir.iterdir():
                if not d.is_dir():
                    continue
                try:
                    dates.add(datetime.datetime.strptime(d.name, "%Y-%m-%d").date())
                except ValueError:
                    continue
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
            probe = ffprobe_util.probe_media(info["path"])
            if probe and probe.get("duration"):
                duration = float(probe["duration"])
                end_sec = int(start_sec + duration)
                codec = probe.get("codec")
                width = probe.get("width")
                height = probe.get("height")
                fps = probe.get("fps")
                probe_method = "ffprobe"
                probe_ts = now
            else:
                dur = self._mtime_duration(info["mtime"], start_sec)
                if dur and dur > 0:
                    duration = float(dur)
                    end_sec = int(start_sec + dur)
                    probe_method = "mtime"

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
            probe = ffprobe_util.probe_media(info["path"])
            if probe and probe.get("duration"):
                cur = self.db.execute(
                    "SELECT start_sec FROM segments WHERE file_path=?", (path,)
                )
                row = cur.fetchone()
                if row:
                    start_sec = row["start_sec"]
                    dur = float(probe["duration"])
                    self.db.execute(
                        """
                        UPDATE segments SET
                            file_size=?, file_mtime=?, state=?,
                            end_sec=?, duration=?,
                            codec=?, width=?, height=?, fps=?,
                            probe_method='ffprobe', probe_timestamp=?,
                            updated_at=?
                        WHERE file_path=?
                        """,
                        (
                            info["size"], info["mtime"], state,
                            int(start_sec + dur), dur,
                            probe.get("codec"),
                            probe.get("width"),
                            probe.get("height"),
                            probe.get("fps"),
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

    @staticmethod
    def _mtime_duration(mtime: float, start_sec: int) -> Optional[float]:
        try:
            dt = datetime.datetime.fromtimestamp(mtime)
            mt_sec = dt.hour * 3600 + dt.minute * 60 + dt.second
            dur = mt_sec - start_sec
            if dur < 0:
                dur += 86400
            if dur < 0.5 or dur > 3600 * 2:
                return None
            return float(dur)
        except Exception:
            return None

    # ---------- load ----------
    def _load_segments(self, cam_name: str, date_str: str) -> List[Segment]:
        cur = self.db.execute(
            """
            SELECT file_path, camera_name, date, start_sec, end_sec, duration,
                   state, codec, width, height, fps, file_size, file_mtime
            FROM segments
            WHERE camera_name=? AND date=?
            ORDER BY start_sec ASC
            """,
            (cam_name, date_str),
        )
        out = []
        for row in cur.fetchall():
            out.append(Segment(
                file_path=row["file_path"],
                camera_name=row["camera_name"],
                date=row["date"],
                start_sec=row["start_sec"],
                end_sec=row["end_sec"],
                duration=row["duration"],
                state=row["state"],
                codec=row["codec"],
                width=row["width"],
                height=row["height"],
                fps=row["fps"],
                file_size=row["file_size"],
                file_mtime=row["file_mtime"],
            ))
        return out

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