"""
Icône systray temps réel — Sprint 2 Feature 1.
Cercle coloré PIL 64×64 mis à jour à chaque changement d'état.
Menu contextuel : Afficher/Masquer + Quitter.
"""
import threading
from PIL import Image, ImageDraw
import pystray


# Mapping état (strings State.*) → couleur hex
STATE_ICON_COLORS = {
    "SECURE":      "#2ecc71",  # Vert
    "IDLE":        "#888888",  # Gris
    "PASSING":     "#f39c12",  # Orange
    "GRACE":       "#e67e22",  # Orange foncé
    "THREAT":      "#e74c3c",  # Rouge
    "SURFER":      "#9b59b6",  # Violet
    "CAMERA_LOST": "#555555",  # Gris foncé
    "DÉMARRAGE":   "#888888",  # Gris (état initial GUI)
    "PAUSE":       "#3498db",  # Bleu
}

ICON_SIZE = 64


def _hex_to_rgb(hex_color: str) -> tuple:
    """Convertit une couleur hex (#rrggbb) en tuple (R, G, B)."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _make_icon(hex_color: str, size: int = ICON_SIZE) -> Image.Image:
    """Génère une image RGBA avec un cercle plein de la couleur donnée."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r, g, b = _hex_to_rgb(hex_color)
    draw.ellipse([4, 4, size - 4, size - 4], fill=(r, g, b, 255))
    return img


class SystrayIcon:
    """
    Icône dans la barre de notification Windows.
    Thread daemon — ne bloque pas l'application principale.
    Couleur = état de détection en temps réel.
    """

    def __init__(self, on_show_hide, on_quit):
        """
        on_show_hide : callable() → bascule visibilité fenêtre principale
        on_quit      : callable() → déclenche la fermeture propre
        """
        self._on_show_hide = on_show_hide
        self._on_quit = on_quit
        self._icon: pystray.Icon = None
        self._current_color = STATE_ICON_COLORS["IDLE"]

    def start(self) -> None:
        """Crée et démarre l'icône systray dans un thread daemon."""
        icon_img = _make_icon(self._current_color)
        menu = pystray.Menu(
            pystray.MenuItem(
                "Afficher / Masquer", self._cb_show_hide, default=True
            ),
            pystray.MenuItem("Quitter PrankGuard", self._cb_quit),
        )
        self._icon = pystray.Icon("prankguard", icon_img, "PrankGuard", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def update_state(self, state: str) -> None:
        """Met à jour couleur + tooltip de l'icône selon l'état détection."""
        color = STATE_ICON_COLORS.get(state, STATE_ICON_COLORS["IDLE"])
        if color == self._current_color or self._icon is None:
            return
        self._current_color = color
        self._icon.icon = _make_icon(color)
        self._icon.title = f"PrankGuard — {state}"

    def stop(self) -> None:
        """Arrête proprement l'icône systray."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def _cb_show_hide(self, icon, item) -> None:
        self._on_show_hide()

    def _cb_quit(self, icon, item) -> None:
        self._on_quit()
