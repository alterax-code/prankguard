"""Journal d'événements SQLite (WAL) + photos d'intrusion."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,       -- lock | warn | device | session | system | enroll
  level TEXT NOT NULL,      -- info | warning | critical
  title TEXT NOT NULL,
  detail TEXT DEFAULT '',
  snapshot TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""


class EventStore:
    def __init__(self, db: Path = paths.EVENTS_DB, snapshots: Path = paths.SNAPSHOTS_DIR, retention_days: int = 60):
        self.snapshots = snapshots
        snapshots.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        self._listeners = []
        self.prune(retention_days)

    def subscribe(self, cb) -> None:
        self._listeners.append(cb)

    def add(self, kind: str, level: str, title: str, detail: str = "", frame: np.ndarray | None = None) -> dict:
        ts = time.time()
        snap = ""
        if frame is not None:
            snap = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts)) + f"-{int(ts * 1000) % 1000:03d}.jpg"
            cv2.imwrite(str(self.snapshots / snap), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(ts,kind,level,title,detail,snapshot) VALUES(?,?,?,?,?,?)",
                (ts, kind, level, title, detail, snap))
            ev = {"id": cur.lastrowid, "ts": ts, "kind": kind, "level": level,
                  "title": title, "detail": detail, "snapshot": snap}
        for cb in list(self._listeners):
            try:
                cb(ev)
            except Exception:
                pass
        return ev

    def recent(self, limit: int = 100, before_id: int | None = None, kind: str | None = None) -> list[dict]:
        q, args = "SELECT id,ts,kind,level,title,detail,snapshot FROM events", []
        conds = []
        if before_id:
            conds.append("id < ?")
            args.append(before_id)
        if kind:
            conds.append("kind = ?")
            args.append(kind)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(500, limit)))
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        keys = ("id", "ts", "kind", "level", "title", "detail", "snapshot")
        return [dict(zip(keys, r)) for r in rows]

    def stats(self, since: float) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT kind, COUNT(*) FROM events WHERE ts >= ? GROUP BY kind", (since,)).fetchall()
        return {k: n for k, n in rows}

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM events")
        for f in self.snapshots.glob("*.jpg"):
            f.unlink(missing_ok=True)

    def prune(self, days: int) -> None:
        cutoff = time.time() - days * 86400
        with self._lock:
            old = self._db.execute("SELECT snapshot FROM events WHERE ts < ? AND snapshot != ''", (cutoff,)).fetchall()
            self._db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        for (snap,) in old:
            (self.snapshots / snap).unlink(missing_ok=True)

    def snapshot_path(self, name: str) -> Path | None:
        p = (self.snapshots / name).resolve()
        if p.parent != self.snapshots.resolve() or not p.is_file():
            return None
        return p

    def close(self) -> None:
        with self._lock:
            self._db.close()
