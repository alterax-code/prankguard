import ctypes
import time
from typing import Callable, Optional


class WindowsLocker:
    """Locks Windows workstation."""

    def __init__(self, on_lock_callback: Optional[Callable] = None):
        self.on_lock_callback = on_lock_callback
        self._last_lock_time = 0.0
        self._min_lock_interval = 3.0  # Prevent spam locking

    def lock(self) -> bool:
        """
        Lock the Windows workstation.

        Uses the Windows API directly (most reliable method).

        Returns:
            True if lock was triggered, False if cooldown active
        """
        current_time = time.time()
        if current_time - self._last_lock_time < self._min_lock_interval:
            return False

        try:
            # Direct Windows API call - most reliable
            ctypes.windll.user32.LockWorkStation()
            self._last_lock_time = current_time

            if self.on_lock_callback:
                self.on_lock_callback()

            return True

        except Exception as e:
            print(f"[Locker] Failed to lock: {e}")
            return False

    def set_cooldown(self, seconds: float):
        """Set minimum interval between locks."""
        self._min_lock_interval = seconds
