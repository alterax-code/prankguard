"""
Popup de notification pour les connexions de périphériques.
FIX 7 — Popup CTkToplevel modale avec Autoriser/Bloquer + countdown 30s.
"""
import customtkinter as ctk
import winsound
import threading
from typing import Callable, Optional


# Icônes par type de device
DEVICE_ICONS = {
    "USB":       "🔌",
    "USB HID":   "🖱",
    "Monitor":   "🖥",
    "Network":   "🌐",
    "Printer":   "🖨",
    "Bluetooth": "📶",
    "Audio":     "🔊",
}

COUNTDOWN_SECONDS = 30


class DeviceNotification(ctk.CTkToplevel):
    """
    Popup modale pour autoriser ou bloquer un périphérique détecté.
    Auto-bloque après COUNTDOWN_SECONDS si pas de réponse.
    """

    def __init__(
        self,
        parent,
        device_type: str,
        device_info: str,
        on_allow: Callable,
        on_block: Callable,
    ):
        super().__init__(parent)
        self.on_allow = on_allow
        self.on_block = on_block
        self.remaining = COUNTDOWN_SECONDS
        self._closed = False

        icon = DEVICE_ICONS.get(device_type, "⚠")
        self.title(f"PrankGuard — {device_type} détecté")
        self.geometry("450x280")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()  # Modale

        # Beep d'alerte
        winsound.Beep(2500, 300)

        # Contenu
        ctk.CTkLabel(
            self, text=f"{icon} Nouveau périphérique détecté",
            font=ctk.CTkFont(size=20, weight="bold"), text_color="#e74c3c"
        ).pack(pady=(20, 10))

        ctk.CTkLabel(
            self, text=f"Type : {device_type}",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(pady=2)

        ctk.CTkLabel(
            self, text=device_info,
            font=ctk.CTkFont(size=12), text_color="#aaa",
            wraplength=400
        ).pack(pady=(2, 15))

        # Countdown
        self.countdown_label = ctk.CTkLabel(
            self, text=f"Auto-blocage dans {self.remaining}s",
            font=ctk.CTkFont(size=13), text_color="#f39c12"
        )
        self.countdown_label.pack(pady=5)

        # Boutons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=15)

        ctk.CTkButton(
            btn_frame, text="✓ Autoriser", width=150, height=45,
            fg_color="#27ae60", hover_color="#2ecc71",
            command=self._allow
        ).pack(side="left", padx=15)

        ctk.CTkButton(
            btn_frame, text="✗ Bloquer", width=150, height=45,
            fg_color="#e74c3c", hover_color="#c0392b",
            command=self._block
        ).pack(side="left", padx=15)

        self.protocol("WM_DELETE_WINDOW", self._block)

        # Lancer le countdown
        self._tick()

    def _tick(self):
        """Décrément du countdown chaque seconde."""
        if self._closed:
            return

        self.remaining -= 1
        if self.remaining <= 0:
            self._block()
            return

        self.countdown_label.configure(
            text=f"Auto-blocage dans {self.remaining}s"
        )

        # Beep d'urgence dans les 5 dernières secondes
        if self.remaining <= 5:
            winsound.Beep(1800, 100)

        self.after(1000, self._tick)

    def _allow(self):
        """L'utilisateur autorise le device."""
        if self._closed:
            return
        self._closed = True
        self.grab_release()
        self.destroy()
        self.on_allow()

    def _block(self):
        """L'utilisateur bloque (ou timeout)."""
        if self._closed:
            return
        self._closed = True
        self.grab_release()
        self.destroy()
        self.on_block()
