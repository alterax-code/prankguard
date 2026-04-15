"""
Orchestration du verrouillage Windows.
Gère le mutex, les cooldowns, et la séquence block USB → LockWorkStation.
"""
import ctypes
import time
import threading

from src.logger import logger
from src.devices.blocker import block_usb, unblock_usb


class Locker:
    """Gestionnaire centralisé du verrouillage."""

    def __init__(self, usb_mode: str = "DESKTOP", lock_cooldown: float = 8.0):
        self.usb_mode = usb_mode
        self.lock_cooldown = lock_cooldown
        self.usb_blocked = False

        self._last_lock_time: float = 0
        self._device_cooldown_end: float = 0.0
        self._mutex = threading.Lock()

    def can_lock(self) -> bool:
        """Vérifie si le cooldown est écoulé."""
        return time.time() - self._last_lock_time > self.lock_cooldown

    def get_cooldown_remaining(self) -> float:
        """Retourne le temps restant du cooldown (0 si prêt)."""
        remaining = self.lock_cooldown - (time.time() - self._last_lock_time)
        return max(0, remaining)

    def do_lock(self, reason: str) -> bool:
        """
        Verrouille le PC : block USB → LockWorkStation.
        Retourne True si le lock a été effectué.
        """
        with self._mutex:
            if not self.can_lock():
                return False

            logger.lock(f"VERROUILLAGE — Raison: {reason}")

            # Bloquer USB selon le mode
            block_usb(self.usb_mode)
            self.usb_blocked = True

            # Enregistrer le timestamp
            self._last_lock_time = time.time()

            # Verrouiller Windows
            ctypes.windll.user32.LockWorkStation()

            return True

    def do_unlock(self):
        """Déverrouille USB quand l'owner revient."""
        if self.usb_blocked:
            unblock_usb()
            self.usb_blocked = False
            logger.unlock("USB débloqué — propriétaire reconnu")

    def set_device_cooldown(self, seconds: float = 5.0):
        """Définit un cooldown device (après changement de config)."""
        self._device_cooldown_end = time.time() + seconds

    @property
    def device_cooldown_active(self) -> bool:
        """Vérifie si le cooldown device est actif."""
        return time.time() < self._device_cooldown_end

    def get_device_cooldown_remaining(self) -> float:
        """Temps restant du cooldown device."""
        return max(0, self._device_cooldown_end - time.time())
