"""
Détecteur de mouvement pour pré-filtrer les frames statiques.
Vague 3 — évite d'appeler face_recognition sur scènes immobiles.
Skip uniquement quand dernier état connu = owner OK (safe).
Force l'analyse toutes les force_interval frames analysables.
"""
import cv2
import numpy as np


class MotionDetector:
    """
    Pré-filtre basé sur absdiff + seuil pixel.
    force_interval compte en frames ANALYSABLES (pas frames totales).
    """

    def __init__(
        self,
        threshold: int = 25,
        min_ratio: float = 0.005,
        force_interval: int = 30,
    ):
        self.threshold = threshold
        self.min_ratio = min_ratio
        self.force_interval = force_interval

        self._prev_gray: np.ndarray = None
        self._analyze_count: int = 0   # Nombre de frames analysables vues
        self._owner_safe: bool = False  # True = dernier état = owner OK

    def set_owner_safe(self, safe: bool) -> None:
        """Met à jour l'état courant — appelé après chaque analyse faciale."""
        self._owner_safe = safe

    def should_analyze(self, frame: np.ndarray) -> bool:
        """
        True si face_recognition doit être appelé sur ce frame analysable.
        Règles (par priorité) :
          1. État dangereux (intrus / aucun visage) → toujours analyser
          2. Toutes les force_interval frames analysables → forcer
          3. Scène statique (ratio < min_ratio) et owner_safe → skipper
        """
        self._analyze_count += 1

        # Règle 1 — état dangereux : skip interdit
        if not self._owner_safe:
            self._update_prev(frame)
            return True

        # Règle 2 — force périodique
        if self._analyze_count % self.force_interval == 0:
            self._update_prev(frame)
            return True

        # Règle 3 — motion check sur miniature 160×120
        gray = cv2.cvtColor(
            cv2.resize(frame, (160, 120)), cv2.COLOR_BGR2GRAY
        )
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        if self._prev_gray is None:
            self._prev_gray = gray
            return True

        diff = cv2.absdiff(self._prev_gray, gray)
        _, thresh = cv2.threshold(diff, self.threshold, 255, cv2.THRESH_BINARY)
        ratio = np.count_nonzero(thresh) / thresh.size

        self._prev_gray = gray
        return ratio >= self.min_ratio

    def _update_prev(self, frame: np.ndarray) -> None:
        """Met à jour la frame précédente sans faire le calcul complet."""
        gray = cv2.cvtColor(
            cv2.resize(frame, (160, 120)), cv2.COLOR_BGR2GRAY
        )
        self._prev_gray = cv2.GaussianBlur(gray, (5, 5), 0)

    def reset(self) -> None:
        """Reset complet — après ré-enrollment ou reprise de pause."""
        self._prev_gray = None
        self._analyze_count = 0
        self._owner_safe = False
