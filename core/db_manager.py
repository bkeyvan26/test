# -*- coding: utf-8 -*-
"""K1 VMS — SQLite manager for Recording Index"""
import sqlite3
import threading
from pathlib import Path
import config


DB_FILE = config.DATA_DIR / "recordings_index.db"
print(f"[DB] Database: {DB_FILE}")
SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_name TEXT NOT NULL,
    date TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,

    start_sec INTEGER NOT NULL,
    end_sec INTEGER,
    duration REAL,

    file_size INTEGER NOT NULL,
    file_mtime REAL NOT NULL,

    state TEXT NOT NULL,

    codec TEXT,
    width INTEGER,
    height INTEGER,
    fps REAL,

    probe_method TEXT,
    probe_timestamp REAL,

    discovered_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cam_date ON segments(camera_name, date);
CREATE INDEX IF NOT EXISTS idx_state ON segments(state);
CREATE INDEX IF NOT EXISTS idx_start ON segments(camera_name, date, start_sec);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class DBManager:
    _instance = None
    _class_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "DBManager":
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._conn = None
        self._lock = threading.RLock()
        self._open()

    def _open(self):
        DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(DB_FILE),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.executescript(SCHEMA_SQL)
        self._check_version(cur)

    def _check_version(self, cur):
        cur.execute("SELECT value FROM meta WHERE key='schema_version'")
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        else:
            try:
                old = int(row["value"])
            except Exception:
                old = SCHEMA_VERSION
            if old != SCHEMA_VERSION:
                print(f"[DB] schema version {old} → {SCHEMA_VERSION}")
                cur.execute(
                    "UPDATE meta SET value=? WHERE key='schema_version'",
                    (str(SCHEMA_VERSION),),
                )

    def execute(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq):
        with self._lock:
            return self._conn.executemany(sql, seq)

    def close(self):
        with self._lock:
            if self._conn:
                try: self._conn.close()
                except Exception: pass
                self._conn = None
