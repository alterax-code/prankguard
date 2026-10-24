"""
Popup de notification pour les connexions de peripheriques.
Countdown 15s, affichage fluide, option lock separee.
"""
import customtkinter as ctk
import winsound
import threading
from typing import Callable


DEVICE_ICONS = {
    "USB":       "USB",
    "USB HID":   "HID",
    "Monitor":   "MON",
    "Network":   "NET",
    "Printer":   "PRN",
    "Bluetooth": "BT",
    "Audio":     "AUD",
}

COUNTDOWN_SECONDS = 15


class DeviceNotification(ctk.CTkToplevel):
    """
    Popup modale : Autoriser / Bloquer un peripherique.
    Auto-bloque apres 15s si pas de reponse.
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

        icon = DEVICE_ICONS.get(device_type, "?")
        self.title(f"PrankGuard - {device_type} detecte")
        self.geometry("450x260")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()

        # Beep d'alerte
        try:
            winsound.Beep(2500, 200)
        except Exception:
            pass

        # Contenu
        ctk.CTkLabel(
            self, text=f"Nouveau peripherique detecte",
            font=ctk.CTkFont(size=18, weight="bold"), text_color="#e74c3c"
        ).pack(pady=(15, 8))

        ctk.CTkLabel(
            self, text=f"Type : {device_type}",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(pady=2)

        ctk.CTkLabel(
            self, text=device_info if device_info else device_type,
            font=ctk.CTkFont(size=12), text_color="#aaa",
            wraplength=400
        ).pack(pady=(2, 10))

        # Countdown
        self.countdown_label = ctk.CTkLabel(
            self, text=f"Auto-blocage dans {self.remaining}s",
            font=ctk.CTkFont(size=13), text_color="#f39c12"
        )
        self.countdown_label.pack(pady=5)

        # Boutons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=12)

        ctk.CTkButton(
            btn_frame, text="Autoriser", width=150, height=40,
            fg_color="#27ae60", hover_color="#2ecc71",
            command=self._allow
        ).pack(side="left", padx=15)

        ctk.CTkButton(
            btn_frame, text="Bloquer", width=150, height=40,
            fg_color="#e74c3c", hover_color="#c0392b",
            command=self._block
        ).pack(side="left", padx=15)

        self.protocol("WM_DELETE_WINDOW", self._block)
        self._tick()

    def _tick(self):
        """Decrement du countdown chaque seconde."""
        if self._closed:
            return

        self.remaining -= 1
        if self.remaining <= 0:
            self._block()
            return

        self.countdown_label.configure(
            text=f"Auto-blocage dans {self.remaining}s"
        )

        if self.remaining <= 5:
            self.countdown_label.configure(text_color="#e74c3c")
            try:
                winsound.Beep(1800, 80)
            except Exception:
                pass

        self.after(1000, self._tick)

    def _allow(self):
        if self._closed:
            return
        self._closed = True
        self.grab_release()
        self.destroy()
        self.on_allow()

    def _block(self):
        if self._closed:
            return
        self._closed = True
        self.grab_release()
        self.destroy()
        self.on_block()
