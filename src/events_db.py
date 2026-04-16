"""
Base SQLite WAL pour les transitions d'événements PrankGuard (Vague 3).
Complément de intrusion_reports.json — ne le remplace pas.
Fonctions module-level, pas de classe.
Événements loggés : transitions d'état seulement (pas chaque frame).
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from src import paths as _paths

_DB_PATH: Path = _paths.LOGS_DIR / "events.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    type    TEXT    NOT NULL,
    details TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_schema() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA_SQL)


# Initialiser le schéma au premier import
try:
    _ensure_schema()
except Exception:
    pass


def log_event(event_type: str, details: dict = None) -> None:
    """Enregistre un événement (transitions, locks, alarms, modes)."""
    if details is None:
        details = {}
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO events (ts, type, details) VALUES (?, ?, ?)",
                (time.time(), event_type, json.dumps(details, ensure_ascii=False)),
            )
    except Exception:
        pass


def get_events(
    event_type: Optional[str] = None,
    since: Optional[float] = None,
    limit: int = 50,
) -> list:
    """Retourne les derniers événements (plus récents en premier)."""
    try:
        clauses: list = []
        params: list = []
        if event_type:
            clauses.append("type = ?")
            params.append(event_type)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT ts, type, details FROM events {where} "
                f"ORDER BY ts DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            {"ts": r[0], "type": r[1], "details": json.loads(r[2])}
            for r in rows
        ]
    except Exception:
        return []


def get_stats() -> dict:
    """Retourne un résumé : nb d'événements par type."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT type, COUNT(*) FROM events GROUP BY type ORDER BY COUNT(*) DESC"
            ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def cleanup_old_events(days: int = 90) -> int:
    """Supprime les événements de plus de `days` jours. Retourne le nb supprimé."""
    cutoff = time.time() - days * 86400
    try:
        with _connect() as conn:
            cursor = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            return cursor.rowcount
    except Exception:
        return 0
