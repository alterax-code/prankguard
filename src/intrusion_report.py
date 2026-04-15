"""
Rapport d'intrusion détaillé avec niveaux de criticité.
Criticité :
  INFO     — durée < 5s
  WARNING  — durée 5-30s
  CRITICAL — durée > 30s OU 3+ intrusions/heure OU device branché OU spoof détecté
"""
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class IntrusionType(Enum):
    UNKNOWN_FACE   = "Visage inconnu"
    SHOULDER_SURF  = "Shoulder surfing"
    DEVICE_PLUGGED = "Périphérique branché"
    SPOOF_DETECTED = "Spoof détecté"


class Criticality(Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class IntrusionEvent:
    intrusion_type: IntrusionType
    start_time: float
    end_time: float = 0.0
    face_distances: List[float] = field(default_factory=list)
    devices_plugged: List[str] = field(default_factory=list)
    spoof_detected: bool = False
    actions_taken: List[str] = field(default_factory=list)
    criticality: Criticality = Criticality.INFO
    pending_email: bool = False  # Sprint 2 : envoi email CRITICAL

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    def to_log_line(self) -> str:
        ts = datetime.fromtimestamp(self.start_time).strftime("%Y-%m-%d %H:%M:%S")
        dur = f"{self.duration:.1f}s"
        crit = self.criticality.value
        itype = self.intrusion_type.value
        extras = []
        if self.devices_plugged:
            extras.append(f"devices={','.join(self.devices_plugged)}")
        if self.spoof_detected:
            extras.append("SPOOF")
        if self.actions_taken:
            extras.append(f"actions={','.join(self.actions_taken)}")
        extra_str = f" [{', '.join(extras)}]" if extras else ""
        return f"[{ts}] {crit} | {itype} | durée:{dur}{extra_str}"


class IntrusionReporter:
    """
    Suit les intrusions en cours et génère des rapports.
    Thread-safe (Lock interne).
    """

    def __init__(self, log_path: str = "intrusion_log.txt"):
        self._log_path = log_path
        self._lock = threading.Lock()
        self._current: Optional[IntrusionEvent] = None
        # Timestamps des intrusions terminées dans la dernière heure
        self._hourly: deque = deque()

    def start_intrusion(self, intrusion_type: IntrusionType) -> None:
        """Démarre l'enregistrement d'une nouvelle intrusion."""
        with self._lock:
            self._current = IntrusionEvent(
                intrusion_type=intrusion_type,
                start_time=time.time(),
            )

    def update_current(
        self,
        face_distance: Optional[float] = None,
        device: Optional[str] = None,
        spoof: bool = False,
        action: Optional[str] = None,
    ) -> None:
        """Met à jour l'intrusion en cours (appelé à chaque frame d'analyse)."""
        with self._lock:
            if self._current is None:
                return
            if face_distance is not None:
                self._current.face_distances.append(face_distance)
            if device:
                if device not in self._current.devices_plugged:
                    self._current.devices_plugged.append(device)
            if spoof:
                self._current.spoof_detected = True
            if action:
                if action not in self._current.actions_taken:
                    self._current.actions_taken.append(action)

    def end_intrusion(self) -> Optional[IntrusionEvent]:
        """
        Clôture l'intrusion en cours, calcule la criticité, écrit le log.
        Retourne l'événement terminé (ou None si aucune intrusion active).
        """
        with self._lock:
            if self._current is None:
                return None
            event = self._current
            self._current = None

        event.end_time = time.time()

        # Nettoyer la fenêtre glissante d'une heure
        cutoff = time.time() - 3600
        while self._hourly and self._hourly[0] < cutoff:
            self._hourly.popleft()

        # Calculer la criticité
        event.criticality = self._compute_criticality(event, len(self._hourly))

        # Marquer les CRITICAL pour l'envoi email (Sprint 2)
        event.pending_email = (event.criticality == Criticality.CRITICAL)

        # Enregistrer le timestamp dans la fenêtre horaire
        self._hourly.append(event.start_time)

        # Écrire dans le fichier de log
        self._write_log(event)

        return event

    def get_recent_summary(self, n: int = 5) -> str:
        """Retourne un résumé des N dernières intrusions depuis le fichier log."""
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            recent = lines[-n:] if len(lines) >= n else lines
            return "".join(recent).strip()
        except FileNotFoundError:
            return "(aucune intrusion enregistrée)"
        except Exception as exc:
            return f"(erreur lecture log: {exc})"

    @property
    def active(self) -> bool:
        """True si une intrusion est en cours."""
        return self._current is not None

    @staticmethod
    def _compute_criticality(event: IntrusionEvent, hourly_count: int) -> Criticality:
        """Détermine le niveau de criticité d'un événement."""
        if (event.duration > 30.0
                or hourly_count >= 2  # 3 incl. celle-ci → seuil 2 déjà enregistrés
                or event.devices_plugged
                or event.spoof_detected):
            return Criticality.CRITICAL
        if event.duration >= 5.0:
            return Criticality.WARNING
        return Criticality.INFO

    def _write_log(self, event: IntrusionEvent) -> None:
        """Ajoute l'événement au fichier de log (append)."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(event.to_log_line() + "\n")
        except Exception:
            pass
