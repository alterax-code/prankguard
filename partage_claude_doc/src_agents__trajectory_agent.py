# -*- coding: utf-8 -*-
"""
Trajectory Agent — PrankGuard v3.0

Compare la taille de la bounding box du visage entre frames successifs
pour déterminer si la personne s'approche ou s'éloigne de l'écran.

  - Bbox qui grandit → la personne s'approche → suspect
  - Bbox stable ou qui diminue → immobile ou s'éloigne → passage innocent

Ultra-léger : aucune dépendance externe, quelques lignes de calcul.
Thread : actif seulement (déclenché par le motion_agent).
Profil requis : tous.

Dépendances : numpy (optionnel, pour le lissage)
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("prankguard.trajectory_agent")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

class Trajectory(str, Enum):
    """Direction de trajectoire détectée."""
    APPROACHING = "APPROACHING"  # La personne s'approche de l'écran
    STABLE = "STABLE"            # Immobile ou mouvement négligeable
    RECEDING = "RECEDING"        # La personne s'éloigne
    UNKNOWN = "UNKNOWN"          # Pas assez de données

# Seuil de variation de surface pour considérer un mouvement significatif
# En pourcentage de croissance de la bbox entre deux frames
_APPROACH_THRESHOLD_PCT = 3.0   # +3% → s'approche
_RECEDE_THRESHOLD_PCT = -3.0    # -3% → s'éloigne

# Nombre de frames à conserver pour le lissage (évite les faux positifs)
_HISTORY_SIZE = 5

# Nombre minimum de frames avant de pouvoir émettre un verdict
_MIN_FRAMES_FOR_VERDICT = 3


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryResult:
    """Résultat de l'analyse de trajectoire."""
    trajectory: Trajectory = Trajectory.UNKNOWN
    bbox_area_current: float = 0.0        # Surface actuelle de la bbox (px²)
    bbox_area_delta_pct: float = 0.0      # Variation moyenne en % sur l'historique
    approaching: bool = False              # Raccourci : la personne s'approche ?
    frames_analyzed: int = 0
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Trajectory Agent
# ---------------------------------------------------------------------------

class TrajectoryAgent:
    """
    Agent de trajectoire basé sur le delta de bounding box.

    Compare la surface de la bounding box du visage entre frames successifs.
    Utilise un historique glissant pour lisser les variations et éviter
    les faux positifs liés aux micro-mouvements.

    Utilisation :
        agent = TrajectoryAgent()
        result = agent.update(bbox=(x1, y1, x2, y2))
        if result.approaching:
            print("La personne s'approche")
    """

    def __init__(
        self,
        approach_threshold_pct: float = _APPROACH_THRESHOLD_PCT,
        recede_threshold_pct: float = _RECEDE_THRESHOLD_PCT,
        history_size: int = _HISTORY_SIZE,
    ) -> None:
        self._approach_threshold = approach_threshold_pct
        self._recede_threshold = recede_threshold_pct
        self._history_size = history_size

        # Historique des surfaces de bbox
        self._area_history: deque[float] = deque(maxlen=history_size)

    # ----- Propriétés publiques -----

    @property
    def history_size(self) -> int:
        """Nombre de frames dans l'historique."""
        return len(self._area_history)

    # ----- API principale -----

    def update(self, bbox: tuple[int, int, int, int]) -> TrajectoryResult:
        """
        Met à jour l'agent avec une nouvelle bounding box et retourne
        le résultat de trajectoire.

        Args:
            bbox: Bounding box du visage (x1, y1, x2, y2) en pixels.

        Returns:
            TrajectoryResult avec la trajectoire détectée.
        """
        result = TrajectoryResult(timestamp=time.monotonic())

        # Calculer la surface de la bbox
        x1, y1, x2, y2 = bbox
        area = max(0.0, float((x2 - x1) * (y2 - y1)))
        result.bbox_area_current = area

        # Ajouter à l'historique
        self._area_history.append(area)
        result.frames_analyzed = len(self._area_history)

        # Pas assez de données pour un verdict
        if len(self._area_history) < _MIN_FRAMES_FOR_VERDICT:
            result.trajectory = Trajectory.UNKNOWN
            return result

        # Calculer la variation moyenne sur l'historique
        delta_pct = self._compute_average_delta()
        result.bbox_area_delta_pct = round(delta_pct, 2)

        # Déterminer la trajectoire
        if delta_pct >= self._approach_threshold:
            result.trajectory = Trajectory.APPROACHING
            result.approaching = True
        elif delta_pct <= self._recede_threshold:
            result.trajectory = Trajectory.RECEDING
        else:
            result.trajectory = Trajectory.STABLE

        logger.debug(
            "Trajectoire : %s (delta=%.1f%%, area=%.0f px²)",
            result.trajectory.value, delta_pct, area,
        )

        return result

    def update_from_faces(
        self, faces: list[tuple[int, int, int, int]]
    ) -> Optional[TrajectoryResult]:
        """
        Variante qui accepte une liste de bboxes (plusieurs visages).
        Analyse la plus grande bbox (visage le plus proche).
        Retourne None si la liste est vide.
        """
        if not faces:
            return None

        # Prendre la bbox avec la plus grande surface
        largest = max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        return self.update(largest)

    def reset(self) -> None:
        """Réinitialise l'historique (par ex. lors du retour en phase VEILLE)."""
        self._area_history.clear()
        logger.debug("Historique de trajectoire réinitialisé")

    # ----- Calcul interne -----

    def _compute_average_delta(self) -> float:
        """
        Calcule la variation moyenne de surface entre frames consécutifs
        en pourcentage. Un résultat positif = la bbox grandit (approche).
        """
        if len(self._area_history) < 2:
            return 0.0

        deltas = []
        items = list(self._area_history)

        for i in range(1, len(items)):
            prev = items[i - 1]
            curr = items[i]

            if prev <= 0:
                continue

            delta_pct = ((curr - prev) / prev) * 100.0
            deltas.append(delta_pct)

        if not deltas:
            return 0.0

        return sum(deltas) / len(deltas)


# ---------------------------------------------------------------------------
# Exécution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    print("Trajectory Agent — test avec des bboxes simulées")
    print("=" * 50)

    agent = TrajectoryAgent()

    # Simulation : une personne qui s'approche (bbox grandissante)
    test_bboxes = [
        (100, 100, 150, 160),  # 50×60 = 3000
        (95, 95, 155, 165),    # 60×70 = 4200
        (90, 90, 160, 170),    # 70×80 = 5600
        (85, 85, 165, 175),    # 80×90 = 7200
        (80, 80, 170, 180),    # 90×100 = 9000
    ]

    print("\nSimulation : personne qui s'approche")
    for i, bbox in enumerate(test_bboxes):
        result = agent.update(bbox)
        print(
            f"  Frame {i+1}: {result.trajectory.value:12s} "
            f"delta={result.bbox_area_delta_pct:+6.1f}% "
            f"area={result.bbox_area_current:.0f} px²"
        )

    agent.reset()

    # Simulation : une personne qui s'éloigne (bbox rétrécissante)
    test_bboxes_recede = list(reversed(test_bboxes))

    print("\nSimulation : personne qui s'éloigne")
    for i, bbox in enumerate(test_bboxes_recede):
        result = agent.update(bbox)
        print(
            f"  Frame {i+1}: {result.trajectory.value:12s} "
            f"delta={result.bbox_area_delta_pct:+6.1f}% "
            f"area={result.bbox_area_current:.0f} px²"
        )

    print(f"\n{'=' * 50}")
    print("Test terminé.")
