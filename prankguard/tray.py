"""Icône de zone de notification : bouclier coloré selon l'état + menu rapide."""
from __future__ import annotations

import threading

import pystray
from PIL import Image, ImageDraw

from . import guard as G

COLORS = {
    G.SECURE: (34, 197, 94), G.IDLE: (56, 189, 248), G.WATCH: (245, 158, 11), G.ALERT: (239, 68, 68),
    G.SURFER: (168, 85, 247), G.CAMERA_LOST: (239, 68, 68), G.PAUSED: (113, 113, 122),
    G.LOCKED: (99, 102, 241), G.ARMING: (56, 189, 248), G.STARTING: (113, 113, 122),
    G.SETUP: (245, 158, 11),
}
LABELS = {
    G.SECURE: "Protégé", G.IDLE: "Poste libre", G.WATCH: "Visage détecté", G.ALERT: "Intrus !",
    G.SURFER: "Regard indiscret", G.CAMERA_LOST: "Caméra indisponible", G.PAUSED: "En pause",
    G.LOCKED: "Verrouillé", G.ARMING: "Armement…", G.STARTING: "Démarrage…",
    G.SETUP: "Configuration requise",
}


def shield_icon(rgb: tuple[int, int, int], size: int = 64) -> Image.Image:
    s = size * 4  # supersampling pour un rendu lisse
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(s * .5, s * .06), (s * .88, s * .2), (s * .84, s * .58), (s * .5, s * .95), (s * .16, s * .58), (s * .12, s * .2)]
    d.polygon(pts, fill=rgb + (255,))
    inner = [(x * .72 + s * .14, y * .72 + s * .12) for x, y in pts]
    d.polygon(inner, fill=(255, 255, 255, 60))
    # oeil stylisé
    d.ellipse((s * .33, s * .38, s * .67, s * .6), fill=(255, 255, 255, 235))
    d.ellipse((s * .44, s * .42, s * .56, s * .56), fill=rgb + (255,))
    return img.resize((size, size), Image.LANCZOS)


class Tray:
    def __init__(self, app):
        self.app = app
        self._state = None
        self._icons = {k: shield_icon(v) for k, v in COLORS.items()}
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir PrankGuard", lambda: app.show_window(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Surveillance active", lambda: app.engine.set_armed(not app.engine.guard.armed),
                             checked=lambda _: app.engine.guard.armed),
            pystray.MenuItem("Pause", pystray.Menu(
                pystray.MenuItem("15 minutes", lambda: app.engine.snooze(15)),
                pystray.MenuItem("1 heure", lambda: app.engine.snooze(60)),
                pystray.MenuItem("4 heures", lambda: app.engine.snooze(240)),
            )),
            pystray.MenuItem("Verrouiller maintenant", lambda: app.engine.lock_now()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", lambda: app.quit()),
        )
        self.icon = pystray.Icon("PrankGuard", self._icons[G.STARTING], "PrankGuard", menu)

    def start(self) -> None:
        threading.Thread(target=self.icon.run, name="Tray", daemon=True).start()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

    def set_state(self, state: str) -> None:
        if state == self._state or state not in self._icons:
            return
        self._state = state
        try:
            self.icon.icon = self._icons[state]
            self.icon.title = f"PrankGuard — {LABELS.get(state, state)}"
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, title: str, msg: str) -> None:
        try:
            self.icon.notify(msg, title)
        except Exception:
            pass
