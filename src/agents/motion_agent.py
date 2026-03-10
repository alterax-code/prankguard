# -*- coding: utf-8 -*-
"""
Motion Agent — PrankGuard v3.0

Gardien de la phase VEILLE. Capture la webcam en permanence et applique
l'algorithme MOG2 (Mixture of Gaussians v2) d'OpenCV pour détecter tout
mouvement significatif dans le champ de vision.

Quand un mouvement est détecté → émet un événement pour déclencher la phase ACTIVE.
Quand aucun mouvement pendant 3 secondes → émet un événement de retour en VEILLE.

Consommation : < 1% CPU en permanence.
Thread : permanent (tourne tant que l'application est active).

Dépendances : opencv-python, numpy
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

import cv2
import numpy as np

logger = logging.getLogger("prankguard.motion_agent")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    """Phase courante de PrankGuard."""
    VEILLE = "VEILLE"
    ACTIVE = "ACTIVE"


# Paramètres MOG2 par défaut
_DEFAULT_MOG2_HISTORY = 500           # Nombre de frames pour le modèle de fond
_DEFAULT_MOG2_VAR_THRESHOLD = 16.0    # Seuil de variance pour la détection
_DEFAULT_MOG2_DETECT_SHADOWS = False  # Ignorer les ombres (économie CPU)

# Seuil de surface minimale de mouvement (en % de la surface totale de l'image)
_DEFAULT_MOTION_THRESHOLD_PCT = 0.5

# Délai de retour en veille : 3 secondes sans mouvement (section 2.1 du plan)
_RETURN_TO_SLEEP_DELAY_S = 3.0

# Intervalle entre deux analyses en phase veille (ms)
_DEFAULT_ANALYSIS_INTERVAL_MS = 100  # ~10 FPS pour MOG2, très léger


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------

@dataclass
class MotionEvent:
    """Événement émis par le motion agent."""
    phase: Phase
    motion_percent: float    # Pourcentage de pixels en mouvement
    timestamp: float         # time.monotonic()
    frame: Optional[np.ndarray] = None  # Frame courante (pour les agents actifs)


# Type des callbacks
MotionCallback = Callable[[MotionEvent], None]


# ---------------------------------------------------------------------------
# Motion Agent
# ---------------------------------------------------------------------------

class MotionAgent:
    """
    Agent de détection de mouvement basé sur OpenCV MOG2.

    Tourne dans un thread dédié. Capture la webcam, applique MOG2,
    et notifie les listeners quand la phase change (VEILLE ↔ ACTIVE).

    Utilisation :
        agent = MotionAgent(camera_index=0)
        agent.on_phase_change(callback)
        agent.start()
        ...
        agent.stop()
    """

    def __init__(
        self,
        camera_index: int = 0,
        motion_threshold_pct: float = _DEFAULT_MOTION_THRESHOLD_PCT,
        analysis_interval_ms: int = _DEFAULT_ANALYSIS_INTERVAL_MS,
        return_to_sleep_delay: float = _RETURN_TO_SLEEP_DELAY_S,
    ) -> None:
        self._camera_index = camera_index
        self._motion_threshold_pct = motion_threshold_pct
        self._analysis_interval_ms = analysis_interval_ms
        self._return_to_sleep_delay = return_to_sleep_delay

        # État interne
        self._phase = Phase.VEILLE
        self._last_motion_time: float = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Callbacks
        self._phase_callbacks: list[MotionCallback] = []
        self._frame_callbacks: list[Callable[[np.ndarray], None]] = []

        # MOG2
        self._mog2 = cv2.createBackgroundSubtractorMOG2(
            history=_DEFAULT_MOG2_HISTORY,
            varThreshold=_DEFAULT_MOG2_VAR_THRESHOLD,
            detectShadows=_DEFAULT_MOG2_DETECT_SHADOWS,
        )

        # Capture vidéo (initialisée au start)
        self._cap: Optional[cv2.VideoCapture] = None

    # ----- Propriétés publiques -----

    @property
    def phase(self) -> Phase:
        """Phase courante (VEILLE ou ACTIVE)."""
        return self._phase

    @property
    def is_running(self) -> bool:
        """True si l'agent tourne."""
        return self._running

    # ----- Enregistrement des callbacks -----

    def on_phase_change(self, callback: MotionCallback) -> None:
        """Enregistre un callback appelé à chaque changement de phase."""
        self._phase_callbacks.append(callback)

    def on_frame(self, callback: Callable[[np.ndarray], None]) -> None:
        """Enregistre un callback appelé à chaque frame capturée (pour la GUI)."""
        self._frame_callbacks.append(callback)

    # ----- Contrôle du cycle de vie -----

    def start(self) -> None:
        """Démarre l'agent dans un thread dédié."""
        if self._running:
            logger.warning("Motion agent déjà en cours d'exécution")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="MotionAgent",
            daemon=True,
        )
        self._thread.start()
        logger.info("Motion agent démarré (caméra %d)", self._camera_index)

    def stop(self) -> None:
        """Arrête proprement l'agent et libère la caméra."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        self._release_camera()
        logger.info("Motion agent arrêté")

    # ----- Configuration dynamique -----

    def set_motion_threshold(self, pct: float) -> None:
        """Modifie le seuil de sensibilité (en % de surface)."""
        with self._lock:
            self._motion_threshold_pct = max(0.01, pct)
        logger.info("Seuil de mouvement ajusté à %.2f%%", self._motion_threshold_pct)

    def set_analysis_interval(self, ms: int) -> None:
        """Modifie l'intervalle entre deux analyses (en ms)."""
        with self._lock:
            self._analysis_interval_ms = max(30, ms)

    # ----- Boucle principale -----

    def _run_loop(self) -> None:
        """Boucle principale du thread : capture → MOG2 → décision de phase."""
        self._cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)

        if not self._cap.isOpened():
            logger.error(
                "Impossible d'ouvrir la caméra %d. Motion agent désactivé.",
                self._camera_index,
            )
            self._running = False
            return

        # Réduire la résolution de capture pour économiser le CPU
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

        logger.info("Caméra %d ouverte — phase VEILLE", self._camera_index)

        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                logger.warning("Frame perdue, nouvelle tentative...")
                time.sleep(0.1)
                continue

            # Notifier les listeners de la frame brute (pour affichage GUI)
            for cb in self._frame_callbacks:
                try:
                    cb(frame)
                except Exception:
                    pass

            # Appliquer MOG2
            motion_pct = self._compute_motion(frame)

            # Décision de phase
            self._update_phase(motion_pct, frame)

            # Attendre avant la prochaine analyse
            with self._lock:
                interval = self._analysis_interval_ms
            time.sleep(interval / 1000.0)

        self._release_camera()

    def _compute_motion(self, frame: np.ndarray) -> float:
        """
        Applique MOG2 sur la frame et retourne le pourcentage de pixels
        en mouvement par rapport à la surface totale.
        """
        # Convertir en niveaux de gris pour réduire la charge
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Appliquer un léger flou pour réduire le bruit
        blurred = cv2.GaussianBlur(gray, (21, 21), 0)

        # Masque de mouvement via MOG2
        fg_mask = self._mog2.apply(blurred)

        # Compter les pixels en mouvement (valeur > 0 dans le masque)
        motion_pixels = np.count_nonzero(fg_mask)
        total_pixels = fg_mask.shape[0] * fg_mask.shape[1]

        if total_pixels == 0:
            return 0.0

        return (motion_pixels / total_pixels) * 100.0

    def _update_phase(self, motion_pct: float, frame: np.ndarray) -> None:
        """
        Met à jour la phase en fonction du mouvement détecté.

        - Si mouvement significatif → passer en ACTIVE (si pas déjà)
        - Si aucun mouvement depuis 3 secondes → retour en VEILLE
        """
        now = time.monotonic()

        with self._lock:
            threshold = self._motion_threshold_pct

        motion_detected = motion_pct >= threshold

        if motion_detected:
            self._last_motion_time = now

            if self._phase == Phase.VEILLE:
                self._phase = Phase.ACTIVE
                logger.info(
                    "Mouvement détecté (%.1f%%) → phase ACTIVE",
                    motion_pct,
                )
                self._emit_event(motion_pct, frame)

        else:
            # Vérifier le délai de retour en veille
            if self._phase == Phase.ACTIVE:
                elapsed = now - self._last_motion_time
                if elapsed >= self._return_to_sleep_delay:
                    self._phase = Phase.VEILLE
                    logger.info(
                        "Aucun mouvement depuis %.1f s → retour VEILLE",
                        elapsed,
                    )
                    self._emit_event(motion_pct, frame=None)

    def _emit_event(self, motion_pct: float, frame: Optional[np.ndarray]) -> None:
        """Émet un MotionEvent à tous les callbacks enregistrés."""
        event = MotionEvent(
            phase=self._phase,
            motion_percent=round(motion_pct, 2),
            timestamp=time.monotonic(),
            frame=frame,
        )
        for cb in self._phase_callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("Erreur dans un callback de phase : %s", exc)

    def _release_camera(self) -> None:
        """Libère la capture vidéo."""
        if self._cap is not None and self._cap.isOpened():
            self._cap.release()
            self._cap = None
            logger.info("Caméra libérée")


# ---------------------------------------------------------------------------
# Exécution directe (pour tests visuels)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    def _on_phase_change(event: MotionEvent) -> None:
        """Callback de test : affiche les changements de phase."""
        status = "MOUVEMENT DÉTECTÉ" if event.phase == Phase.ACTIVE else "retour veille"
        print(f"  [{event.phase.value}] {status} — mouvement: {event.motion_percent:.1f}%")

    agent = MotionAgent(camera_index=0)
    agent.on_phase_change(_on_phase_change)

    print("Motion Agent — test en direct (Ctrl+C pour quitter)")
    print("=" * 50)
    agent.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt...")
        agent.stop()
        print("Terminé.")
