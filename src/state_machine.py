"""
Machine à états pour la détection faciale.
Gère les transitions entre IDLE, SECURE, PASSING, THREAT, GRACE, SURFER.
"""
import time
import winsound
from typing import Optional

from src.logger import logger


class State:
    IDLE = "IDLE"
    SECURE = "SECURE"
    PASSING = "PASSING"
    THREAT = "THREAT"
    GRACE = "GRACE"
    SURFER = "SURFER"
    CAMERA_LOST = "CAMERA_LOST"


# Couleurs par état pour la GUI
STATE_COLORS = {
    State.SECURE:     "#2ecc71",  # Vert
    State.IDLE:       "#888888",  # Gris
    State.PASSING:    "#f39c12",  # Orange
    State.THREAT:     "#e74c3c",  # Rouge
    State.GRACE:      "#e67e22",  # Orange foncé
    State.SURFER:     "#9b59b6",  # Violet
    State.CAMERA_LOST: "#e74c3c",  # Rouge
}


class StateMachine:
    """
    Machine à états pour la gestion de la sécurité faciale.
    Prend les résultats de FaceAnalyzer et décide de l'état courant.
    """

    def __init__(
        self,
        sec_mode: str = "PEDAGO",
        threat_lock_delay: float = 2.0,
        no_owner_lock_delay: float = 10.0,
        shoulder_grace_period: float = 5.0,
        camera_lost_lock_delay: float = 3.0,
    ):
        self.sec_mode = sec_mode
        self.threat_lock_delay = threat_lock_delay
        self.no_owner_lock_delay = no_owner_lock_delay
        self.shoulder_grace_period = shoulder_grace_period
        self.camera_lost_lock_delay = camera_lost_lock_delay

        self.current_state = State.IDLE
        self.threat_start: Optional[float] = None
        self.no_owner_start: Optional[float] = None
        self.shoulder_surfer_grace_end: Optional[float] = None
        self.was_shoulder_surfer = False
        self.camera_lost_time: Optional[float] = None
        self.alert_cooldown: float = 0

    def update(self, situation: dict, can_lock: bool) -> dict:
        """
        Met à jour l'état en fonction de la situation faciale.
        Retourne un dict avec : state, countdown, should_lock, lock_reason
        """
        owner = situation["owner"]
        threat = situation["threat"]
        passing = situation["passing"]
        now = time.time()

        countdown = ""
        should_lock = False
        lock_reason = ""

        if owner and threat:
            # Shoulder surfer — owner + inconnu simultanés
            self.current_state = State.SURFER
            self.was_shoulder_surfer = True
            self.shoulder_surfer_grace_end = None
            self.threat_start = None
            self.no_owner_start = None

            if now > self.alert_cooldown:
                winsound.Beep(1500, 200)
                logger.face("SHOULDER SURFER détecté — owner + inconnu")
                self.alert_cooldown = now + 2

        elif owner:
            # Owner seul — tout va bien
            self.current_state = State.SECURE
            self.was_shoulder_surfer = False
            self.shoulder_surfer_grace_end = None
            self.threat_start = None
            self.no_owner_start = None

        elif threat:
            # Inconnu regarde l'écran
            self.no_owner_start = None

            if self.was_shoulder_surfer:
                # Grace period post-shoulder-surfer
                if not self.shoulder_surfer_grace_end:
                    self.shoulder_surfer_grace_end = now + self.shoulder_grace_period
                    logger.face(f"Grace period {self.shoulder_grace_period}s")

                if now < self.shoulder_surfer_grace_end:
                    self.current_state = State.GRACE
                    countdown = f"Grace: {self.shoulder_surfer_grace_end - now:.1f}s"
                else:
                    self.was_shoulder_surfer = False
                    self.shoulder_surfer_grace_end = None
                    self.threat_start = now
            else:
                # Threat directe
                self.current_state = State.THREAT
                if not self.threat_start:
                    self.threat_start = now
                    logger.face("MENACE détectée — inconnu regarde l'écran")
                elif now - self.threat_start > self.threat_lock_delay and can_lock:
                    should_lock = True
                    lock_reason = "Inconnu détecté"
                else:
                    remaining = self.threat_lock_delay - (now - self.threat_start)
                    countdown = f"LOCK: {remaining:.1f}s"

        else:
            # Personne ou passant seulement
            self.threat_start = None
            self.was_shoulder_surfer = False
            self.shoulder_surfer_grace_end = None

            if self.sec_mode == "SECURE":
                # FIX 4 — En mode SECURE, lock si pas d'owner après délai
                if not self.no_owner_start:
                    self.no_owner_start = now
                elif now - self.no_owner_start > self.no_owner_lock_delay and can_lock:
                    should_lock = True
                    lock_reason = "Aucun propriétaire"
                else:
                    remaining = self.no_owner_lock_delay - (now - self.no_owner_start)
                    countdown = f"Lock: {remaining:.1f}s"
                self.current_state = State.PASSING if passing else State.IDLE
            else:
                # FIX 4 — En mode PEDAGO, pas de lock auto sans owner
                self.no_owner_start = None
                self.current_state = State.PASSING if passing else State.IDLE

        return {
            "state": self.current_state,
            "countdown": countdown,
            "should_lock": should_lock,
            "lock_reason": lock_reason,
        }

    def on_camera_lost(self, can_lock: bool) -> dict:
        """Gère la perte de caméra."""
        now = time.time()
        self.current_state = State.CAMERA_LOST

        if self.camera_lost_time is None:
            self.camera_lost_time = now
            logger.camera("Caméra perdue !")

        elapsed = now - self.camera_lost_time

        if elapsed > self.camera_lost_lock_delay and can_lock:
            return {
                "state": State.CAMERA_LOST,
                "countdown": "",
                "should_lock": True,
                "lock_reason": "Caméra perdue",
            }

        return {
            "state": State.CAMERA_LOST,
            "countdown": f"CAM: {self.camera_lost_lock_delay - elapsed:.1f}s",
            "should_lock": False,
            "lock_reason": "",
        }

    def on_camera_ok(self):
        """Réinitialise le timer de perte caméra."""
        self.camera_lost_time = None

    def reset(self):
        """Reset complet de la machine à états."""
        self.current_state = State.IDLE
        self.threat_start = None
        self.no_owner_start = None
        self.shoulder_surfer_grace_end = None
        self.was_shoulder_surfer = False
        self.camera_lost_time = None
