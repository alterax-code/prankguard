# -*- coding: utf-8 -*-
"""
Auto-Throttle — PrankGuard v3.0

Monitore le CPU global du systeme toutes les 2 secondes et ajuste
dynamiquement la charge de PrankGuard pour ne pas gener les autres
programmes.

Seuils (section 4.7 du plan v3) :
  - CPU < 60%  : profil normal, aucun ajustement
  - CPU 60-75% : analyse reduite a 1 frame sur 7
  - CPU 75-85% : bascule en profil LITE (gaze off, resolution reduite)
  - CPU > 85%  : mode minimal (face recog seul, 1 frame sur 15)

Thread : permanent (tourne tant que l'application est active).

Dependances : psutil
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

import psutil

logger = logging.getLogger("prankguard.auto_throttle")


# ---------------------------------------------------------------------------
# Constantes (section 4.7 / table 9 du plan v3)
# ---------------------------------------------------------------------------

class ThrottleLevel(str, Enum):
    """Niveaux de throttle appliques par l'auto-throttle."""
    NORMAL = "NORMAL"    # CPU < 60% : profil normal
    REDUCED = "REDUCED"  # CPU 60-75% : frame skip augmente
    LITE = "LITE"        # CPU 75-85% : bascule profil LITE
    MINIMAL = "MINIMAL"  # CPU > 85% : face recog seul


# Seuils CPU globaux (en pourcentage)
_THRESHOLD_REDUCED = 60.0
_THRESHOLD_LITE = 75.0
_THRESHOLD_MINIMAL = 85.0

# Intervalle de monitoring (en secondes)
_DEFAULT_POLL_INTERVAL_S = 2.0

# Parametres derives par niveau de throttle
_THROTTLE_PARAMS: dict[ThrottleLevel, dict] = {
    ThrottleLevel.NORMAL: {
        "frame_skip": None,    # None = utiliser la valeur du profil hardware
        "gaze_enabled": None,  # None = utiliser la valeur du profil hardware
        "analysis_width": None,
        "analysis_height": None,
    },
    ThrottleLevel.REDUCED: {
        "frame_skip": 7,
        "gaze_enabled": None,
        "analysis_width": None,
        "analysis_height": None,
    },
    ThrottleLevel.LITE: {
        "frame_skip": 10,
        "gaze_enabled": False,
        "analysis_width": 160,
        "analysis_height": 120,
    },
    ThrottleLevel.MINIMAL: {
        "frame_skip": 15,
        "gaze_enabled": False,
        "analysis_width": 160,
        "analysis_height": 120,
    },
}


# ---------------------------------------------------------------------------
# Structures de donnees
# ---------------------------------------------------------------------------

@dataclass
class ThrottleState:
    """Etat courant de l'auto-throttle."""
    level: ThrottleLevel = ThrottleLevel.NORMAL
    cpu_percent: float = 0.0
    frame_skip: Optional[int] = None
    gaze_enabled: Optional[bool] = None
    analysis_width: Optional[int] = None
    analysis_height: Optional[int] = None
    timestamp: float = 0.0


# Type des callbacks
ThrottleCallback = Callable[[ThrottleState], None]


# ---------------------------------------------------------------------------
# Auto-Throttle
# ---------------------------------------------------------------------------

class AutoThrottle:
    """
    Moniteur de charge CPU avec ajustement dynamique des parametres
    de PrankGuard.

    Tourne dans un thread dedie. Mesure le CPU global toutes les 2 secondes
    et emet un evenement quand le niveau de throttle change.

    Utilisation :
        throttle = AutoThrottle()
        throttle.on_level_change(callback)
        throttle.start()
        ...
        throttle.stop()
    """

    def __init__(
        self,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._poll_interval = poll_interval_s

        # Etat interne
        self._current_level = ThrottleLevel.NORMAL
        self._current_cpu: float = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Callbacks
        self._level_callbacks: list[ThrottleCallback] = []

    # ----- Proprietes publiques -----

    @property
    def level(self) -> ThrottleLevel:
        """Niveau de throttle courant."""
        with self._lock:
            return self._current_level

    @property
    def cpu_percent(self) -> float:
        """Dernier pourcentage CPU mesure."""
        with self._lock:
            return self._current_cpu

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def state(self) -> ThrottleState:
        """Etat complet courant."""
        with self._lock:
            params = _THROTTLE_PARAMS[self._current_level]
            return ThrottleState(
                level=self._current_level,
                cpu_percent=self._current_cpu,
                frame_skip=params["frame_skip"],
                gaze_enabled=params["gaze_enabled"],
                analysis_width=params["analysis_width"],
                analysis_height=params["analysis_height"],
                timestamp=time.monotonic(),
            )

    # ----- Callbacks -----

    def on_level_change(self, callback: ThrottleCallback) -> None:
        """Enregistre un callback appele quand le niveau de throttle change."""
        self._level_callbacks.append(callback)

    # ----- Controle du cycle de vie -----

    def start(self) -> None:
        """Demarre le monitoring CPU dans un thread dedie."""
        if self._running:
            logger.warning("Auto-throttle deja en cours")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="AutoThrottle",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Auto-throttle demarre (intervalle: %.1f s)",
            self._poll_interval,
        )

    def stop(self) -> None:
        """Arrete le monitoring."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Auto-throttle arrete")

    # ----- Boucle principale -----

    def _run_loop(self) -> None:
        """Boucle de monitoring : mesure CPU → determine niveau → notifie."""
        # Premier appel pour initialiser psutil (le premier retourne toujours 0)
        psutil.cpu_percent(interval=None)

        while self._running:
            time.sleep(self._poll_interval)

            if not self._running:
                break

            # Mesurer le CPU global
            cpu = psutil.cpu_percent(interval=None)

            with self._lock:
                self._current_cpu = cpu

            # Determiner le niveau
            new_level = self._determine_level(cpu)

            with self._lock:
                old_level = self._current_level

            if new_level != old_level:
                with self._lock:
                    self._current_level = new_level

                logger.info(
                    "CPU a %.1f%% : throttle %s -> %s",
                    cpu, old_level.value, new_level.value,
                )
                self._emit_change()

    @staticmethod
    def _determine_level(cpu_percent: float) -> ThrottleLevel:
        """
        Determine le niveau de throttle en fonction du CPU global.
        Les seuils sont issus de la table 9 du plan v3.
        """
        if cpu_percent >= _THRESHOLD_MINIMAL:
            return ThrottleLevel.MINIMAL
        elif cpu_percent >= _THRESHOLD_LITE:
            return ThrottleLevel.LITE
        elif cpu_percent >= _THRESHOLD_REDUCED:
            return ThrottleLevel.REDUCED
        else:
            return ThrottleLevel.NORMAL

    def _emit_change(self) -> None:
        """Notifie tous les callbacks du changement de niveau."""
        current_state = self.state
        for cb in self._level_callbacks:
            try:
                cb(current_state)
            except Exception as exc:
                logger.error("Erreur callback throttle : %s", exc)

    # ----- API pour l'orchestrateur -----

    def get_effective_params(
        self,
        base_frame_skip: int,
        base_gaze_enabled: bool,
        base_width: int,
        base_height: int,
    ) -> dict:
        """
        Retourne les parametres effectifs en tenant compte du throttle.

        Les valeurs du profil hardware sont utilisees comme base.
        L'auto-throttle ne fait que les reduire (jamais les augmenter).

        Args:
            base_frame_skip: frame_skip du profil hardware
            base_gaze_enabled: gaze_enabled du profil hardware
            base_width: analysis_width du profil hardware
            base_height: analysis_height du profil hardware

        Returns:
            dict avec frame_skip, gaze_enabled, analysis_width, analysis_height
        """
        with self._lock:
            params = _THROTTLE_PARAMS[self._current_level]

        return {
            "frame_skip": params["frame_skip"] or base_frame_skip,
            "gaze_enabled": params["gaze_enabled"] if params["gaze_enabled"] is not None else base_gaze_enabled,
            "analysis_width": params["analysis_width"] or base_width,
            "analysis_height": params["analysis_height"] or base_height,
        }


# ---------------------------------------------------------------------------
# Execution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s -- %(message)s",
    )

    print("Auto-Throttle -- test en direct (Ctrl+C pour quitter)")
    print("=" * 55)

    def _on_change(state: ThrottleState) -> None:
        print(
            f"  [CHANGEMENT] {state.level.value} "
            f"(CPU: {state.cpu_percent:.1f}%, "
            f"frame_skip: {state.frame_skip}, "
            f"gaze: {state.gaze_enabled})"
        )

    throttle = AutoThrottle(poll_interval_s=2.0)
    throttle.on_level_change(_on_change)
    throttle.start()

    try:
        while True:
            state = throttle.state
            print(
                f"  CPU: {state.cpu_percent:5.1f}% "
                f"| Niveau: {state.level.value:8s}"
            )
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nArret...")
        throttle.stop()
        print("Termine.")
