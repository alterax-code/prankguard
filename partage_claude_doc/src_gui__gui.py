# -*- coding: utf-8 -*-
"""
GUI — PrankGuard v3.0

Interface graphique CustomTkinter avec theme sombre professionnel.
4 onglets : Camera, Logs, Parametres, Enrollment.
Overlays colores sur la camera live selon l'etat de securite.
Raccourcis clavier : L (lock), P (pause), U (deblocage USB).

Thread : principal (mainloop Tkinter).
Dependances : customtkinter, Pillow, opencv-python, numpy
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger("prankguard.gui")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Couleurs des overlays selon la situation
_OVERLAY_COLORS = {
    "SAFE": "#22c55e",              # Vert
    "THREAT": "#ef4444",            # Rouge
    "PASSING": "#f97316",           # Orange
    "IDLE": "#6b7280",              # Gris
    "SHOULDER_SURFER": "#a855f7",   # Violet
    "DEVICE_ALERT": "#ef4444",      # Rouge
    "COOLDOWN": "#3b82f6",          # Bleu
}

_OVERLAY_LABELS = {
    "SAFE": "Proprietaire reconnu",
    "THREAT": "MENACE DETECTEE",
    "PASSING": "Passage detecte",
    "IDLE": "Aucun visage",
    "SHOULDER_SURFER": "Shoulder surfer !",
    "DEVICE_ALERT": "Peripherique inconnu !",
    "COOLDOWN": "Cooldown actif",
}

# Dimensions par defaut de la fenetre
_WINDOW_WIDTH = 900
_WINDOW_HEIGHT = 650
_CAMERA_DISPLAY_WIDTH = 640
_CAMERA_DISPLAY_HEIGHT = 480


# ---------------------------------------------------------------------------
# Onglet Camera
# ---------------------------------------------------------------------------

class CameraTab(ctk.CTkFrame):
    """Onglet avec vue live de la webcam et overlay d'etat."""

    def __init__(self, master: ctk.CTkFrame, **kwargs) -> None:
        super().__init__(master, **kwargs)

        # Label pour afficher la video
        self._video_label = ctk.CTkLabel(self, text="")
        self._video_label.pack(padx=10, pady=10, expand=True)

        # Barre d'etat sous la video
        self._status_frame = ctk.CTkFrame(self)
        self._status_frame.pack(fill="x", padx=10, pady=(0, 10))

        self._status_label = ctk.CTkLabel(
            self._status_frame,
            text="En attente de la camera...",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._status_label.pack(side="left", padx=10, pady=5)

        self._fps_label = ctk.CTkLabel(
            self._status_frame,
            text="",
            font=ctk.CTkFont(size=12),
        )
        self._fps_label.pack(side="right", padx=10, pady=5)

        # Etat courant
        self._current_situation = "IDLE"
        self._current_image: Optional[ctk.CTkImage] = None

        # Thread-safe : derniere frame en attente (tuple ou None)
        self._pending_data = None

        # Timer de rendu sur le main thread (~30 FPS)
        self._poll_camera()

    def update_frame(self, frame: np.ndarray, situation: str = "SAFE") -> None:
        """Thread-safe : stocke la derniere frame pour rendu sur le main thread."""
        self._pending_data = (frame.copy(), situation)

    def _poll_camera(self) -> None:
        """Rend la derniere frame en attente sur le main thread (~30 FPS)."""
        data = self._pending_data
        if data is not None:
            self._pending_data = None
            self._render_frame(data[0], data[1])
        self.after(33, self._poll_camera)

    def _render_frame(self, frame: np.ndarray, situation: str) -> None:
        """Rendu effectif sur le main thread avec CTkImage."""
        self._current_situation = situation

        # Redimensionner
        frame_resized = cv2.resize(
            frame, (_CAMERA_DISPLAY_WIDTH, _CAMERA_DISPLAY_HEIGHT)
        )

        # Appliquer l'overlay colore (bande en haut)
        color_hex = _OVERLAY_COLORS.get(situation, "#6b7280")
        color_bgr = self._hex_to_bgr(color_hex)
        overlay = frame_resized.copy()
        cv2.rectangle(overlay, (0, 0), (_CAMERA_DISPLAY_WIDTH, 40), color_bgr, -1)
        cv2.addWeighted(overlay, 0.6, frame_resized, 0.4, 0, frame_resized)

        # Texte d'etat
        label = _OVERLAY_LABELS.get(situation, situation)
        cv2.putText(
            frame_resized, label,
            (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
        )

        # Convertir BGR -> RGB -> PIL -> CTkImage
        rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        self._current_image = ctk.CTkImage(
            light_image=img, dark_image=img,
            size=(_CAMERA_DISPLAY_WIDTH, _CAMERA_DISPLAY_HEIGHT),
        )
        self._video_label.configure(image=self._current_image, text="")

    def update_status(self, text: str) -> None:
        """Met a jour le texte de la barre d'etat."""
        self._status_label.configure(text=text)

    def update_fps(self, fps: float) -> None:
        """Met a jour l'affichage FPS."""
        self._fps_label.configure(text=f"{fps:.1f} FPS")

    @staticmethod
    def _hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
        """Convertit #RRGGBB en (B, G, R) pour OpenCV."""
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (b, g, r)


# ---------------------------------------------------------------------------
# Onglet Logs
# ---------------------------------------------------------------------------

class LogsTab(ctk.CTkFrame):
    """Onglet avec historique horodate des evenements."""

    def __init__(self, master: ctk.CTkFrame, **kwargs) -> None:
        super().__init__(master, **kwargs)

        # Zone de texte scrollable
        self._textbox = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled",
            wrap="word",
        )
        self._textbox.pack(fill="both", expand=True, padx=10, pady=10)

        # Bouton pour effacer les logs
        self._clear_btn = ctk.CTkButton(
            self, text="Effacer les logs", command=self._clear_logs, width=150
        )
        self._clear_btn.pack(pady=(0, 10))

        # Queue thread-safe pour les logs
        self._log_queue: queue.Queue[str] = queue.Queue()

    def add_log(self, message: str, level: str = "INFO") -> None:
        """Ajoute une ligne de log (thread-safe via queue)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}\n"
        self._log_queue.put(line)

    def process_queue(self) -> None:
        """Traite la queue de logs (appele depuis le mainloop)."""
        while not self._log_queue.empty():
            try:
                line = self._log_queue.get_nowait()
                self._textbox.configure(state="normal")
                self._textbox.insert("end", line)
                self._textbox.see("end")
                self._textbox.configure(state="disabled")
            except queue.Empty:
                break

    def _clear_logs(self) -> None:
        """Efface tous les logs."""
        self._textbox.configure(state="normal")
        self._textbox.delete("1.0", "end")
        self._textbox.configure(state="disabled")


# ---------------------------------------------------------------------------
# Onglet Parametres
# ---------------------------------------------------------------------------

class SettingsTab(ctk.CTkFrame):
    """Onglet parametres : mode, toggles, seuils."""

    def __init__(self, master: ctk.CTkFrame, **kwargs) -> None:
        super().__init__(master, **kwargs)

        # Conteneur scrollable
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Mode de securite ---
        self._mode_label = ctk.CTkLabel(
            self._scroll, text="Mode de securite",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._mode_label.pack(anchor="w", padx=10, pady=(10, 5))

        self._mode_var = ctk.StringVar(value="PEDAGO")
        self._mode_menu = ctk.CTkSegmentedButton(
            self._scroll,
            values=["PEDAGO", "SECURE"],
            variable=self._mode_var,
        )
        self._mode_menu.pack(fill="x", padx=10, pady=(0, 15))

        # --- Tolerance face recognition ---
        self._tolerance_label = ctk.CTkLabel(
            self._scroll, text="Tolerance reconnaissance (0.20 - 0.50)",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._tolerance_label.pack(anchor="w", padx=10, pady=(10, 5))

        self._tolerance_var = ctk.DoubleVar(value=0.30)
        self._tolerance_slider = ctk.CTkSlider(
            self._scroll, from_=0.20, to=0.50,
            variable=self._tolerance_var, number_of_steps=30,
        )
        self._tolerance_slider.pack(fill="x", padx=10)
        self._tolerance_value_label = ctk.CTkLabel(
            self._scroll, text="0.30",
        )
        self._tolerance_value_label.pack(anchor="e", padx=10, pady=(0, 15))
        self._tolerance_var.trace_add(
            "write", lambda *_: self._tolerance_value_label.configure(
                text=f"{self._tolerance_var.get():.2f}"
            )
        )

        # --- Profil de performance ---
        self._profile_label = ctk.CTkLabel(
            self._scroll, text="Profil de performance",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._profile_label.pack(anchor="w", padx=10, pady=(10, 5))

        self._profile_var = ctk.StringVar(value="AUTO")
        self._profile_menu = ctk.CTkSegmentedButton(
            self._scroll,
            values=["AUTO", "PERFORMANCE", "BALANCED", "LITE"],
            variable=self._profile_var,
        )
        self._profile_menu.pack(fill="x", padx=10, pady=(0, 15))

        # --- Mode USB Desktop / Laptop ---
        self._usb_mode_label = ctk.CTkLabel(
            self._scroll, text="Mode USB",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._usb_mode_label.pack(anchor="w", padx=10, pady=(10, 5))

        self._usb_mode_var = ctk.StringVar(value="DESKTOP")
        self._usb_mode_menu = ctk.CTkSegmentedButton(
            self._scroll,
            values=["DESKTOP", "LAPTOP"],
            variable=self._usb_mode_var,
        )
        self._usb_mode_menu.pack(fill="x", padx=10, pady=(0, 5))

        self._usb_mode_desc = ctk.CTkLabel(
            self._scroll,
            text="DESKTOP : bloque stockage USB uniquement\n"
                 "LAPTOP : bloque tous les USB externes",
            font=ctk.CTkFont(size=11),
            text_color="#9ca3af",
            justify="left",
        )
        self._usb_mode_desc.pack(anchor="w", padx=10, pady=(0, 15))

        # --- Surveillance peripheriques ---
        self._device_label = ctk.CTkLabel(
            self._scroll, text="Surveillance peripheriques",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._device_label.pack(anchor="w", padx=10, pady=(10, 5))

        self._device_toggles: dict[str, ctk.CTkSwitch] = {}
        device_categories = [
            ("USB / HID", "USB_HID"),
            ("Moniteurs", "MONITOR"),
            ("Reseau", "NETWORK"),
            ("Bluetooth", "BLUETOOTH"),
            ("Audio", "AUDIO"),
            ("Imprimantes", "PRINTER"),
        ]
        for label, key in device_categories:
            var = ctk.BooleanVar(value=True)
            switch = ctk.CTkSwitch(self._scroll, text=label, variable=var)
            switch.pack(anchor="w", padx=20, pady=2)
            self._device_toggles[key] = switch

    # ----- Accesseurs -----

    @property
    def security_mode(self) -> str:
        return self._mode_var.get()

    @property
    def tolerance(self) -> float:
        return self._tolerance_var.get()

    @property
    def profile(self) -> str:
        return self._profile_var.get()

    @property
    def enabled_device_categories(self) -> set[str]:
        return {
            key for key, switch in self._device_toggles.items()
            if switch.get()
        }

    @property
    def usb_mode(self) -> str:
        return self._usb_mode_var.get()

    def set_mode(self, mode: str) -> None:
        self._mode_var.set(mode)

    def set_tolerance(self, value: float) -> None:
        self._tolerance_var.set(value)

    def set_profile(self, profile: str) -> None:
        self._profile_var.set(profile)

    def set_usb_mode(self, mode: str) -> None:
        self._usb_mode_var.set(mode)


# ---------------------------------------------------------------------------
# Onglet Enrollment
# ---------------------------------------------------------------------------

class EnrollmentTab(ctk.CTkFrame):
    """Onglet pour (re)enregistrer le visage du proprietaire."""

    def __init__(self, master: ctk.CTkFrame, **kwargs) -> None:
        super().__init__(master, **kwargs)

        # Instructions
        self._instruction_label = ctk.CTkLabel(
            self,
            text=(
                "Enregistrement du visage\n\n"
                "Placez-vous devant la camera et cliquez sur 'Capturer'.\n"
                "Prenez plusieurs photos sous differents angles\n"
                "pour ameliorer la reconnaissance."
            ),
            font=ctk.CTkFont(size=13),
            justify="center",
        )
        self._instruction_label.pack(padx=20, pady=(20, 10))

        # Apercu camera
        self._preview_label = ctk.CTkLabel(self, text="")
        self._preview_label.pack(padx=10, pady=10)

        # Barre de progression
        self._progress_frame = ctk.CTkFrame(self)
        self._progress_frame.pack(fill="x", padx=20, pady=5)

        self._progress_label = ctk.CTkLabel(
            self._progress_frame, text="Captures : 0 / 10"
        )
        self._progress_label.pack(side="left", padx=10)

        self._progress_bar = ctk.CTkProgressBar(self._progress_frame)
        self._progress_bar.pack(side="right", fill="x", expand=True, padx=10)
        self._progress_bar.set(0)

        # Boutons
        self._btn_frame = ctk.CTkFrame(self)
        self._btn_frame.pack(pady=10)

        self._capture_btn = ctk.CTkButton(
            self._btn_frame, text="Capturer", width=150,
        )
        self._capture_btn.pack(side="left", padx=10)

        self._save_btn = ctk.CTkButton(
            self._btn_frame, text="Sauvegarder", width=150,
        )
        self._save_btn.pack(side="left", padx=10)

        self._clear_btn = ctk.CTkButton(
            self._btn_frame, text="Tout supprimer", width=150,
            fg_color="#ef4444", hover_color="#dc2626",
        )
        self._clear_btn.pack(side="left", padx=10)

        # Etat
        self._capture_count = 0
        self._max_captures = 10
        self._current_preview_image: Optional[ctk.CTkImage] = None

        # Thread-safe : derniere frame preview en attente
        self._pending_preview = None

        # Timer de rendu preview sur le main thread
        self._poll_preview()

    def set_capture_callback(self, callback: Callable[[], None]) -> None:
        self._capture_btn.configure(command=callback)

    def set_save_callback(self, callback: Callable[[], None]) -> None:
        self._save_btn.configure(command=callback)

    def set_clear_callback(self, callback: Callable[[], None]) -> None:
        self._clear_btn.configure(command=callback)

    def update_preview(self, frame: np.ndarray) -> None:
        """Thread-safe : stocke la derniere frame pour rendu sur le main thread."""
        self._pending_preview = frame.copy()

    def _poll_preview(self) -> None:
        """Rend le dernier apercu en attente sur le main thread."""
        data = self._pending_preview
        if data is not None:
            self._pending_preview = None
            self._render_preview(data)
        self.after(100, self._poll_preview)

    def _render_preview(self, frame: np.ndarray) -> None:
        """Rendu effectif de l'apercu sur le main thread avec CTkImage."""
        resized = cv2.resize(frame, (320, 240))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        self._current_preview_image = ctk.CTkImage(
            light_image=img, dark_image=img, size=(320, 240),
        )
        self._preview_label.configure(image=self._current_preview_image, text="")

    def update_progress(self, count: int) -> None:
        """Met a jour la barre de progression."""
        self._capture_count = count
        ratio = min(count / self._max_captures, 1.0)
        self._progress_bar.set(ratio)
        self._progress_label.configure(
            text=f"Captures : {count} / {self._max_captures}"
        )

    def show_message(self, text: str) -> None:
        self._instruction_label.configure(text=text)


# ---------------------------------------------------------------------------
# Fenetre principale
# ---------------------------------------------------------------------------

class PrankGuardGUI(ctk.CTk):
    """
    Fenetre principale de PrankGuard.

    4 onglets : Camera, Logs, Parametres, Enrollment.
    Theme sombre CustomTkinter.
    Raccourcis clavier : L (lock), P (pause), U (deblocage USB).
    """

    def __init__(self) -> None:
        super().__init__()

        # Configuration fenetre
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("PrankGuard")
        self.geometry(f"{_WINDOW_WIDTH}x{_WINDOW_HEIGHT}")
        self.minsize(700, 500)

        # Callbacks externes
        self._lock_callback: Optional[Callable[[], None]] = None
        self._pause_callback: Optional[Callable[[], None]] = None
        self._usb_unblock_callback: Optional[Callable[[], None]] = None

        # Etat
        self._is_paused = False

        # Construction de l'interface
        self._build_ui()
        self._bind_shortcuts()

        # Timer pour traiter la queue de logs
        self._poll_logs()

        logger.info("GUI initialisee")

    def _build_ui(self) -> None:
        """Construit l'interface avec les 4 onglets."""
        # Tabview (onglets)
        self._tabview = ctk.CTkTabview(self)
        self._tabview.pack(fill="both", expand=True, padx=10, pady=10)

        # Creer les onglets
        self._tabview.add("Camera")
        self._tabview.add("Logs")
        self._tabview.add("Parametres")
        self._tabview.add("Enrollment")

        # Instancier les contenus
        self.camera_tab = CameraTab(self._tabview.tab("Camera"))
        self.camera_tab.pack(fill="both", expand=True)

        self.logs_tab = LogsTab(self._tabview.tab("Logs"))
        self.logs_tab.pack(fill="both", expand=True)

        self.settings_tab = SettingsTab(self._tabview.tab("Parametres"))
        self.settings_tab.pack(fill="both", expand=True)

        self.enrollment_tab = EnrollmentTab(self._tabview.tab("Enrollment"))
        self.enrollment_tab.pack(fill="both", expand=True)

        # Barre inferieure globale
        self._bottom_bar = ctk.CTkFrame(self, height=35)
        self._bottom_bar.pack(fill="x", padx=10, pady=(0, 10))

        self._phase_label = ctk.CTkLabel(
            self._bottom_bar, text="Phase : VEILLE",
            font=ctk.CTkFont(size=12),
        )
        self._phase_label.pack(side="left", padx=10)

        self._level_label = ctk.CTkLabel(
            self._bottom_bar, text="Niveau : VEILLE",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._level_label.pack(side="left", padx=10)

        self._profile_label = ctk.CTkLabel(
            self._bottom_bar, text="Profil : --",
            font=ctk.CTkFont(size=12),
        )
        self._profile_label.pack(side="left", padx=20)

        self._throttle_label = ctk.CTkLabel(
            self._bottom_bar, text="Throttle : NORMAL",
            font=ctk.CTkFont(size=12),
        )
        self._throttle_label.pack(side="left", padx=20)

        self._shortcuts_label = ctk.CTkLabel(
            self._bottom_bar,
            text="L: Lock | P: Pause | U: USB",
            font=ctk.CTkFont(size=11),
            text_color="#6b7280",
        )
        self._shortcuts_label.pack(side="right", padx=10)

    def _bind_shortcuts(self) -> None:
        """Configure les raccourcis clavier."""
        self.bind("<l>", lambda _: self._on_shortcut_lock())
        self.bind("<L>", lambda _: self._on_shortcut_lock())
        self.bind("<p>", lambda _: self._on_shortcut_pause())
        self.bind("<P>", lambda _: self._on_shortcut_pause())
        self.bind("<u>", lambda _: self._on_shortcut_usb())
        self.bind("<U>", lambda _: self._on_shortcut_usb())

    # ----- Raccourcis clavier -----

    def _on_shortcut_lock(self) -> None:
        """L = verrouillage manuel immediat."""
        logger.info("Raccourci L : verrouillage manuel")
        self.logs_tab.add_log("Verrouillage manuel (raccourci L)", "ACTION")
        if self._lock_callback:
            self._lock_callback()

    def _on_shortcut_pause(self) -> None:
        """P = pause / reprise de la surveillance."""
        self._is_paused = not self._is_paused
        state = "PAUSE" if self._is_paused else "ACTIF"
        logger.info("Raccourci P : %s", state)
        self.logs_tab.add_log(f"Surveillance : {state} (raccourci P)", "ACTION")
        if self._pause_callback:
            self._pause_callback()

    def _on_shortcut_usb(self) -> None:
        """U = deblocage manuel des ports USB."""
        logger.info("Raccourci U : deblocage USB")
        self.logs_tab.add_log("Deblocage USB (raccourci U)", "ACTION")
        if self._usb_unblock_callback:
            self._usb_unblock_callback()

    # ----- Enregistrement des callbacks -----

    def set_lock_callback(self, callback: Callable[[], None]) -> None:
        self._lock_callback = callback

    def set_pause_callback(self, callback: Callable[[], None]) -> None:
        self._pause_callback = callback

    def set_usb_unblock_callback(self, callback: Callable[[], None]) -> None:
        self._usb_unblock_callback = callback

    # ----- Mise a jour de l'etat -----

    def update_phase(self, phase: str) -> None:
        """Met a jour l'affichage de la phase (VEILLE/ACTIVE)."""
        self._phase_label.configure(text=f"Phase : {phase}")

    def update_level(self, level: str) -> None:
        """Met a jour l'affichage du niveau d'escalade."""
        colors = {
            "VEILLE": "#6b7280",
            "SOFT": "#22c55e",
            "ALERTE": "#f97316",
            "ACTIF": "#ef4444",
        }
        color = colors.get(level, "#6b7280")
        self._level_label.configure(text=f"Niveau : {level}", text_color=color)

    def update_profile(self, profile: str) -> None:
        """Met a jour l'affichage du profil."""
        self._profile_label.configure(text=f"Profil : {profile}")

    def update_throttle(self, level: str) -> None:
        """Met a jour l'affichage du throttle."""
        self._throttle_label.configure(text=f"Throttle : {level}")

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    # ----- Traitement des logs -----

    def _poll_logs(self) -> None:
        """Traite la queue de logs toutes les 100ms."""
        self.logs_tab.process_queue()
        self.after(100, self._poll_logs)

    # ----- Lancement -----

    def run(self) -> None:
        """Lance le mainloop Tkinter (bloquant)."""
        logger.info("Demarrage du mainloop GUI")
        self.mainloop()


# ---------------------------------------------------------------------------
# Execution directe (pour tests visuels)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s -- %(message)s",
    )

    print("PrankGuard GUI -- test visuel")
    print("=" * 40)

    gui = PrankGuardGUI()

    # Simuler quelques logs
    gui.logs_tab.add_log("PrankGuard demarre", "INFO")
    gui.logs_tab.add_log("Profil : BALANCED", "INFO")
    gui.logs_tab.add_log("Camera ouverte", "INFO")
    gui.logs_tab.add_log("Phase VEILLE active", "INFO")

    # Simuler la mise a jour de la barre d'etat
    gui.update_phase("VEILLE")
    gui.update_profile("BALANCED")
    gui.update_throttle("NORMAL")
    gui.update_level("VEILLE")

    # Simuler un flux camera dans un thread
    def _fake_camera():
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        situations = ["SAFE", "SAFE", "SAFE", "PASSING", "THREAT", "SHOULDER_SURFER"]
        idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            situation = situations[idx % len(situations)]
            try:
                gui.camera_tab.update_frame(frame, situation)
            except Exception:
                break
            idx += 1
            time.sleep(0.1)
        cap.release()

    cam_thread = threading.Thread(target=_fake_camera, daemon=True)
    cam_thread.start()

    gui.set_lock_callback(lambda: print("  [CALLBACK] Lock!"))
    gui.set_pause_callback(lambda: print(f"  [CALLBACK] Pause: {gui.is_paused}"))

    gui.run()
    print("GUI fermee.")
