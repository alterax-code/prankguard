# -*- coding: utf-8 -*-
"""
System Tray — PrankGuard v3.1

Icone dans la zone de notification Windows (obligation RGPD section 6.2).
L'icone est TOUJOURS visible quand l'application tourne.

Couleur selon le niveau d'escalade :
  - Vert  : VEILLE / SOFT
  - Orange : ALERTE
  - Rouge  : ACTIF
  - Gris   : PAUSE

Menu clic droit : Afficher/Masquer, Pause/Reprendre, Quitter.

Thread : propre thread daemon (pystray).
Dependances : pystray, Pillow
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from PIL import Image, ImageDraw

logger = logging.getLogger("prankguard.systray")


# ---------------------------------------------------------------------------
# Couleurs par niveau d'escalade
# ---------------------------------------------------------------------------

_LEVEL_COLORS = {
    "VEILLE": "#22c55e",   # Vert
    "SOFT": "#22c55e",     # Vert
    "ALERTE": "#f97316",   # Orange
    "ACTIF": "#ef4444",    # Rouge
    "PAUSE": "#6b7280",    # Gris
}


# ---------------------------------------------------------------------------
# Generation d'icone dynamique
# ---------------------------------------------------------------------------

def _create_icon_image(color_hex: str, size: int = 64) -> Image.Image:
    """Genere une icone ronde avec la couleur donnee."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Cercle principal
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color_hex,
        outline="#ffffff",
        width=2,
    )
    # Lettre P au centre
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", size // 2)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "P", font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), "P", fill="#ffffff", font=font)
    return img


# ---------------------------------------------------------------------------
# SysTray Manager
# ---------------------------------------------------------------------------

class SysTrayManager:
    """
    Gere l'icone systray de PrankGuard.

    Utilisation :
        tray = SysTrayManager()
        tray.set_show_callback(show_fn)
        tray.set_pause_callback(pause_fn)
        tray.set_quit_callback(quit_fn)
        tray.start()
        ...
        tray.update_level("ALERTE")
        ...
        tray.stop()
    """

    def __init__(self) -> None:
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._current_level = "VEILLE"
        self._is_paused = False

        # Callbacks
        self._show_callback: Optional[Callable[[], None]] = None
        self._pause_callback: Optional[Callable[[], None]] = None
        self._quit_callback: Optional[Callable[[], None]] = None

    # ----- Callbacks -----

    def set_show_callback(self, callback: Callable[[], None]) -> None:
        self._show_callback = callback

    def set_pause_callback(self, callback: Callable[[], None]) -> None:
        self._pause_callback = callback

    def set_quit_callback(self, callback: Callable[[], None]) -> None:
        self._quit_callback = callback

    # ----- Mise a jour -----

    def update_level(self, level: str) -> None:
        """Met a jour le niveau d'escalade (change l'icone et le tooltip)."""
        self._current_level = level
        self._refresh_icon()

    def update_paused(self, paused: bool) -> None:
        """Met a jour l'etat pause."""
        self._is_paused = paused
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        """Rafraichit l'icone et le tooltip."""
        if self._icon is None:
            return

        display_level = "PAUSE" if self._is_paused else self._current_level
        color = _LEVEL_COLORS.get(display_level, "#6b7280")
        tooltip = f"PrankGuard — {display_level}"

        try:
            self._icon.icon = _create_icon_image(color)
            self._icon.title = tooltip
        except Exception as exc:
            logger.debug("Erreur refresh systray : %s", exc)

    # ----- Cycle de vie -----

    def start(self) -> None:
        """Demarre l'icone systray dans un thread daemon."""
        try:
            import pystray
        except ImportError:
            logger.warning(
                "pystray non installe — icone systray desactivee. "
                "Installer avec : pip install pystray"
            )
            return

        display_level = "PAUSE" if self._is_paused else self._current_level
        color = _LEVEL_COLORS.get(display_level, "#6b7280")
        image = _create_icon_image(color)

        menu = pystray.Menu(
            pystray.MenuItem(
                "Afficher / Masquer",
                self._on_show,
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Reprendre" if self._is_paused else "Pause",
                self._on_pause,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", self._on_quit),
        )

        self._icon = pystray.Icon(
            name="PrankGuard",
            icon=image,
            title=f"PrankGuard — {display_level}",
            menu=menu,
        )

        self._thread = threading.Thread(
            target=self._icon.run,
            name="SysTray",
            daemon=True,
        )
        self._thread.start()
        logger.info("Icone systray demarree")

    def stop(self) -> None:
        """Arrete l'icone systray."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        logger.info("Icone systray arretee")

    # ----- Actions du menu -----

    def _on_show(self, icon=None, item=None) -> None:
        if self._show_callback:
            self._show_callback()

    def _on_pause(self, icon=None, item=None) -> None:
        self._is_paused = not self._is_paused
        self._refresh_icon()
        if self._pause_callback:
            self._pause_callback()

    def _on_quit(self, icon=None, item=None) -> None:
        if self._quit_callback:
            self._quit_callback()
        self.stop()
