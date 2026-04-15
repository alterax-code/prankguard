"""
Detection de liveness par blink (Eye Aspect Ratio).
Utilise les landmarks faciaux de face_recognition (modele 68 points).
"""
import math
import time
from collections import deque
from typing import List, Tuple

import face_recognition
import numpy as np


# Seuils EAR
EAR_THRESHOLD = 0.21         # En dessous → yeux fermes
BLINK_MIN_FRAMES = 2         # Frames consecutives yeux fermes pour valider un blink
BLINK_WINDOW_SECONDS = 10.0  # Fenetre glissante de comptage
NO_BLINK_SUSPECT_SECONDS = 10.0  # Aucun blink apres Xs de suivi → spoof suspect


def _euclidean(p1: Tuple, p2: Tuple) -> float:
    """Distance euclidienne entre deux points 2D."""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def _ear(pts: List[Tuple[int, int]]) -> float:
    """
    Calcule l'Eye Aspect Ratio pour 6 points d'un oeil.
    EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)
    Indices dlib : gauche [36-41], droite [42-47].
    """
    v1 = _euclidean(pts[1], pts[5])
    v2 = _euclidean(pts[2], pts[4])
    h = _euclidean(pts[0], pts[3])
    return (v1 + v2) / (2.0 * h) if h > 1e-6 else 0.0


class AntiSpoof:
    """
    Detecte les blinks sur un visage pour valider la liveness.
    Une instance = un visage suivi en continu.
    Appeler reset() quand le visage est perdu entre deux frames.
    """

    def __init__(self):
        self._blink_timestamps: deque = deque()
        self._closed_frames: int = 0
        self._in_blink: bool = False
        self._first_seen: float = time.time()
        self._last_ear: float = 1.0
        self.is_live: bool = True
        self.spoof_suspect: bool = False

    def update(
        self,
        rgb_frame: np.ndarray,
        face_location: Tuple[int, int, int, int],
    ) -> dict:
        """
        Analyse le visage pour detecter un blink.
        face_location : (top, right, bottom, left) coordonnees full-res.
        Retourne : {'is_live', 'ear', 'blink_count', 'spoof_suspect'}.
        """
        now = time.time()

        # Recuperer les landmarks faciaux (68 points via dlib)
        landmarks_list = face_recognition.face_landmarks(
            rgb_frame, face_locations=[face_location]
        )
        if not landmarks_list:
            return self._result()

        lm = landmarks_list[0]
        left_eye = lm.get("left_eye", [])
        right_eye = lm.get("right_eye", [])

        if len(left_eye) < 6 or len(right_eye) < 6:
            return self._result()

        # EAR moyen des deux yeux
        ear = (_ear(left_eye) + _ear(right_eye)) / 2.0
        self._last_ear = ear

        # Detection blink : yeux fermes >= BLINK_MIN_FRAMES frames consecutives
        if ear < EAR_THRESHOLD:
            self._closed_frames += 1
            if self._closed_frames == BLINK_MIN_FRAMES:
                self._in_blink = True
        else:
            if self._in_blink:
                # Fin du blink validee → enregistrer le timestamp
                self._blink_timestamps.append(now)
            self._closed_frames = 0
            self._in_blink = False

        # Nettoyer la fenetre glissante
        cutoff = now - BLINK_WINDOW_SECONDS
        while self._blink_timestamps and self._blink_timestamps[0] < cutoff:
            self._blink_timestamps.popleft()

        # Evaluer la liveness apres NO_BLINK_SUSPECT_SECONDS de suivi continu
        if (now - self._first_seen) >= NO_BLINK_SUSPECT_SECONDS:
            self.spoof_suspect = len(self._blink_timestamps) == 0
            self.is_live = not self.spoof_suspect

        return self._result()

    def reset(self):
        """Remet a zero l'etat quand le visage est perdu."""
        self.__init__()

    def _result(self) -> dict:
        return {
            "is_live": self.is_live,
            "ear": round(self._last_ear, 3),
            "blink_count": len(self._blink_timestamps),
            "spoof_suspect": self.spoof_suspect,
        }
