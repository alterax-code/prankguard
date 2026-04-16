"""
Challenge-response anti-spoof — Vague 5.
Utilise les landmarks nommés de face_recognition (dict, pas tableau 68pts).
"""
import math
import random
import time
from typing import Optional

EAR_SUSPECT_THRESHOLD = 0.21   # Seuil EAR pour détecter un spoof potentiel
SUSPECT_FRAMES_TRIGGER = 15    # Nb de frames consécutives sous le seuil → challenge
CHALLENGE_TIMEOUT = 5.0        # Secondes max pour valider le challenge

CHALLENGES = [
    "Tournez la tete a gauche",
    "Tournez la tete a droite",
    "Ouvrez la bouche",
]


def _mean_x(pts) -> float:
    return sum(p[0] for p in pts) / len(pts) if pts else 0.0


def _dist(p1, p2) -> float:
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


class ChallengeResponse:
    """Gère un challenge de vérification de pose après détection de spoof suspect."""

    def __init__(self):
        self._suspect_frames: int = 0
        self._active: bool = False
        self._passed: bool = False
        self._failed: bool = False
        self._challenge: Optional[str] = None
        self._start_time: float = 0.0

    def update_ear(self, ear_left: float, ear_right: float) -> bool:
        """
        Met à jour le compteur EAR suspect.
        Retourne True si le seuil de frames est atteint → déclencher start_challenge().
        Ne fait rien si un challenge est déjà actif.
        """
        if self._active:
            return False
        avg_ear = (ear_left + ear_right) / 2.0
        if avg_ear < EAR_SUSPECT_THRESHOLD:
            self._suspect_frames += 1
        else:
            self._suspect_frames = 0
        return self._suspect_frames >= SUSPECT_FRAMES_TRIGGER

    def start_challenge(self):
        """Lance un challenge aléatoire parmi les 3 disponibles."""
        self._challenge = random.choice(CHALLENGES)
        self._start_time = time.time()
        self._active = True
        self._passed = False
        self._failed = False
        self._suspect_frames = 0

    def validate_pose(self, landmarks_dict: dict) -> bool:
        """
        Valide la pose courante par rapport au challenge actif.
        landmarks_dict : dict retourné par face_recognition.face_landmarks()
        Retourne True si le challenge est validé.
        Pose _failed=True si timeout dépassé.
        """
        if not self._active:
            return False

        if time.time() - self._start_time > CHALLENGE_TIMEOUT:
            self._failed = True
            self._active = False
            return False

        left_eye = landmarks_dict.get("left_eye", [])
        right_eye = landmarks_dict.get("right_eye", [])
        nose_tip = landmarks_dict.get("nose_tip", [])
        top_lip = landmarks_dict.get("top_lip", [])
        bottom_lip = landmarks_dict.get("bottom_lip", [])

        eye_span = abs(_mean_x(left_eye) - _mean_x(right_eye)) if (left_eye and right_eye) else 0.0

        if self._challenge in ("Tournez la tete a gauche", "Tournez la tete a droite"):
            if left_eye and right_eye and len(nose_tip) > 2:
                eye_cx = (_mean_x(left_eye) + _mean_x(right_eye)) / 2.0
                nose_x = nose_tip[2][0]
                threshold = eye_span * 0.15
                if self._challenge == "Tournez la tete a gauche":
                    ok = nose_x < eye_cx - threshold
                else:
                    ok = nose_x > eye_cx + threshold
                if ok:
                    self._passed = True
                    self._active = False
                    return True

        elif self._challenge == "Ouvrez la bouche":
            if len(top_lip) > 9 and len(bottom_lip) > 9:
                d = _dist(top_lip[9], bottom_lip[9])
                ok = (d / eye_span > 0.25) if eye_span > 0 else (d > 15)
                if ok:
                    self._passed = True
                    self._active = False
                    return True

        return False

    def get_instruction(self) -> str:
        """Retourne le texte à afficher sur la frame (challenge + temps restant)."""
        if not self._active:
            return ""
        remaining = max(0.0, CHALLENGE_TIMEOUT - (time.time() - self._start_time))
        return f"{self._challenge}  ({remaining:.1f}s)"

    def is_active(self) -> bool:
        return self._active

    def is_passed(self) -> bool:
        return self._passed

    def is_failed(self) -> bool:
        return self._failed

    def reset(self):
        """Réinitialise complètement l'état du challenge."""
        self._suspect_frames = 0
        self._active = False
        self._passed = False
        self._failed = False
        self._challenge = None
        self._start_time = 0.0
