"""Réglages persistants (JSON atomique, validation par bornes)."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import paths

# Bornes (min, max) des réglages numériques — tout ce qui sort est ramené dedans.
_BOUNDS = {
    "match_threshold": (0.25, 0.70),
    "stranger_delay": (0.3, 30.0),
    "surfer_delay": (0.5, 60.0),
    "absence_delay": (3.0, 600.0),
    "camera_lost_delay": (1.0, 120.0),
    "rearm_delay": (0.0, 60.0),
    "min_face_size": (0.04, 0.6),
    "max_yaw": (0.1, 1.0),
    "gaze_yaw": (-1.0, 1.0),
    "gaze_pitch": (-1.0, 1.0),
    "camera_index": (0, 9),
    "camera_width": (320, 1920),
    "camera_height": (240, 1080),
    "alarm_volume": (0, 100),
}

_CHOICES = {
    "surfer_action": {"off", "warn", "lock"},
    "device_action": {"off", "notify", "lock_if_away", "lock"},
}


@dataclass
class Settings:
    # Reconnaissance
    match_threshold: float = 0.42     # similarité cosinus SFace min pour reconnaître un visage enrôlé
    anti_spoof: bool = True           # ignore photos / écrans (posters, téléphone tendu vers la caméra)
    min_face_size: float = 0.10       # hauteur visage / hauteur image pour considérer qu'il "lit" l'écran
    max_yaw: float = 0.45             # tolérance de rotation de tête autour de l'axe du regard vers l'écran
    gaze_yaw: float = 0.0             # axe "regarde l'écran" vu par la caméra (calibré à l'enrôlement)
    gaze_pitch: float = 0.0

    # Règles de verrouillage
    lock_on_stranger: bool = True
    stranger_delay: float = 1.5
    surfer_action: str = "warn"       # off | warn | lock
    surfer_delay: float = 3.0
    lock_on_absence: bool = False
    absence_delay: float = 20.0
    lock_on_camera_lost: bool = True
    camera_lost_delay: float = 6.0
    rearm_delay: float = 3.0          # délai après déverrouillage avant de réarmer

    # Périphériques
    device_action: str = "lock_if_away"   # off | notify | lock_if_away | lock
    trusted_devices: tuple = ()

    # Caméra
    camera_index: int = 0
    camera_width: int = 640
    camera_height: int = 480

    # Alertes
    alarm_enabled: bool = False
    alarm_volume: int = 70
    save_snapshots: bool = True
    notifications: bool = True

    # Système
    start_with_windows: bool = False
    start_minimized: bool = True
    armed_on_start: bool = True
    onboarding_done: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trusted_devices"] = list(self.trusted_devices)
        return d


_FIELDS = {f.name: f for f in fields(Settings)}


def _coerce(name: str, value, default):
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        raise ValueError(name)
    if isinstance(default, int):
        v = int(value)
    elif isinstance(default, float):
        v = float(value)
    elif isinstance(default, tuple):
        if not isinstance(value, (list, tuple)):
            raise ValueError(name)
        return tuple(str(x) for x in value)[:500]
    else:
        v = str(value)
        if name in _CHOICES and v not in _CHOICES[name]:
            raise ValueError(name)
        return v
    lo, hi = _BOUNDS.get(name, (None, None))
    if lo is not None:
        v = type(v)(min(hi, max(lo, v)))
    return v


class ConfigStore:
    """Réglages thread-safe avec sauvegarde atomique et abonnés aux changements."""

    def __init__(self, path: Path = paths.CONFIG_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._listeners = []
        self.settings = self._load()

    def _load(self) -> Settings:
        s = Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        if not isinstance(data, dict):
            return s
        for k, v in data.items():
            if k in _FIELDS:
                try:
                    setattr(s, k, _coerce(k, v, getattr(Settings(), k)))
                except (ValueError, TypeError):
                    pass
        return s

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.settings.to_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def update(self, changes: dict) -> dict:
        """Applique des changements validés, sauvegarde, notifie. Retourne les clés appliquées."""
        applied = {}
        defaults = Settings()
        with self._lock:
            for k, v in changes.items():
                if k not in _FIELDS:
                    continue
                try:
                    nv = _coerce(k, v, getattr(defaults, k))
                except (ValueError, TypeError):
                    continue
                if getattr(self.settings, k) != nv:
                    setattr(self.settings, k, nv)
                    applied[k] = nv
            if applied:
                self.save()
        for cb in list(self._listeners):
            try:
                cb(applied)
            except Exception:
                pass
        return applied

    def subscribe(self, cb) -> None:
        self._listeners.append(cb)

    @property
    def s(self) -> Settings:
        return self.settings
