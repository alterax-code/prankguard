"""Surveillance SSID WiFi — Vague 5."""
import logging
import re
import subprocess
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SSIDMonitor:
    """Détecte les changements de SSID WiFi via netsh, planifié par tk.after()."""

    def __init__(self):
        self._current_ssid: Optional[str] = None
        self._callback: Optional[Callable] = None
        self._tk_widget = None
        self._running = False

    def get_current_ssid(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.splitlines():
                # Matcher "   SSID : NomDuReseau" — exclure BSSID
                m = re.match(r"^\s+SSID\s*:\s*(.+)$", line)
                if m:
                    ssid = m.group(1).strip()
                    if ssid:
                        return ssid
        except Exception as exc:
            logger.debug("SSIDMonitor: netsh error: %s", exc)
        return None

    def start(self, tk_widget, callback: Callable):
        """Lance le monitoring. callback(old_ssid, new_ssid) appelé en cas de changement."""
        self._tk_widget = tk_widget
        self._callback = callback
        self._running = True
        self._current_ssid = self.get_current_ssid()
        logger.debug("SSIDMonitor: démarré (SSID initial: %s)", self._current_ssid)
        self._schedule_check()

    def stop(self):
        self._running = False

    def _schedule_check(self):
        if not self._running or self._tk_widget is None:
            return
        try:
            self._tk_widget.after(15000, self._check)
        except Exception:
            pass

    def _check(self):
        if not self._running:
            return
        try:
            new_ssid = self.get_current_ssid()
            if new_ssid != self._current_ssid:
                old = self._current_ssid
                self._current_ssid = new_ssid
                if self._callback:
                    self._callback(old, new_ssid)
        except Exception as exc:
            logger.debug("SSIDMonitor: check error: %s", exc)
        self._schedule_check()
