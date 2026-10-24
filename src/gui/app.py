"""
Application principale PrankGuard v2.0 — GUI CustomTkinter.
Tous les parametres sont branches sur la logique metier.
"""
import cv2
import pickle
import time
import threading
import winsound

import customtkinter as ctk
from PIL import Image, ImageTk
from datetime import datetime

from src.config import Config
from src.logger import logger
from src.face_analyzer import FaceAnalyzer
from src.state_machine import StateMachine, State, STATE_COLORS
from src.security.locker import Locker
from src.devices.watcher import DeviceWatcher
from src.devices.poller import PollingWatcher
from src.devices.notification import DeviceNotification
from src.devices.blocker import (
    block_bluetooth, unblock_bluetooth,
    block_network, unblock_network,
    block_usb,
)


class PasswordDialog(ctk.CTkToplevel):
    """Popup de mot de passe pour fermer l'app."""

    def __init__(self, parent, config: Config, on_success):
        super().__init__(parent)
        self.config = config
        self.on_success = on_success
        self.title("PrankGuard - Mot de passe")
        self.geometry("350x200")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()

        ctk.CTkLabel(
            self, text="Mot de passe requis",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 10))

        self.entry = ctk.CTkEntry(self, show="*", width=200, placeholder_text="Mot de passe")
        self.entry.pack(pady=5)
        self.entry.focus()
        self.entry.bind("<Return>", lambda e: self._check())

        self.error_label = ctk.CTkLabel(self, text="", text_color="#e74c3c")
        self.error_label.pack(pady=3)

        ctk.CTkButton(
            self, text="Valider", width=120, command=self._check
        ).pack(pady=10)

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _check(self):
        pwd = self.entry.get()
        if self.config.check_password(pwd):
            self.grab_release()
            self.destroy()
            self.on_success()
        else:
            self.error_label.configure(text="Mot de passe incorrect")
            self.entry.delete(0, "end")

    def _cancel(self):
        self.grab_release()
        self.destroy()


class ChangePasswordDialog(ctk.CTkToplevel):
    """Popup pour changer le mot de passe."""

    def __init__(self, parent, config: Config):
        super().__init__(parent)
        self.config = config
        self.title("Changer le mot de passe")
        self.geometry("350x280")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()

        ctk.CTkLabel(
            self, text="Changer le mot de passe",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(15, 10))

        ctk.CTkLabel(self, text="Ancien mot de passe :").pack(pady=(5, 0))
        self.old_entry = ctk.CTkEntry(self, show="*", width=200)
        self.old_entry.pack(pady=3)

        ctk.CTkLabel(self, text="Nouveau mot de passe :").pack(pady=(5, 0))
        self.new_entry = ctk.CTkEntry(self, show="*", width=200)
        self.new_entry.pack(pady=3)

        self.error_label = ctk.CTkLabel(self, text="", text_color="#e74c3c")
        self.error_label.pack(pady=3)

        ctk.CTkButton(
            self, text="Valider", width=120, command=self._change
        ).pack(pady=10)

        self.protocol("WM_DELETE_WINDOW", lambda: (self.grab_release(), self.destroy()))

    def _change(self):
        old = self.old_entry.get()
        new = self.new_entry.get()

        if not self.config.check_password(old):
            self.error_label.configure(text="Ancien mot de passe incorrect")
            return

        if len(new) < 4:
            self.error_label.configure(text="Minimum 4 caracteres")
            return

        self.config.set_password(new)
        logger.info("Mot de passe modifie")
        self.grab_release()
        self.destroy()


class PrankGuardApp(ctk.CTk):
    """Application principale PrankGuard."""

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.title("PrankGuard v2.0")
        self.geometry("1100x800")
        self.minsize(900, 700)

        # Charger les encodings
        with open(config.encodings_path, "rb") as f:
            self.owner_encodings = pickle.load(f)

        # Etat global
        self.running = True
        self.paused = False
        self.cap = None
        self._owner_present = False  # Suivi presence owner pour logique device

        # Modules
        self.face_analyzer = FaceAnalyzer(
            owner_encodings=self.owner_encodings,
            tolerance=config.face_tolerance,
            min_face_size=config.min_face_size,
            center_threshold=config.center_threshold,
        )
        self.state_machine = StateMachine(
            sec_mode=config.sec_mode,
            threat_lock_delay=config.threat_lock_delay,
            no_owner_lock_delay=config.no_owner_lock_delay,
            shoulder_grace_period=config.shoulder_grace_period,
            camera_lost_lock_delay=config.camera_lost_lock_delay,
        )
        self.locker = Locker(
            usb_mode=config.usb_mode,
            lock_cooldown=config.lock_cooldown,
        )

        # Variables de toggles
        self.watch_usb = ctk.BooleanVar(value=config.watch_usb)
        self.watch_usb_hid = ctk.BooleanVar(value=config.watch_usb_hid)
        self.watch_monitors = ctk.BooleanVar(value=config.watch_monitors)
        self.watch_network = ctk.BooleanVar(value=config.watch_network)
        self.watch_printers = ctk.BooleanVar(value=config.watch_printers)
        self.watch_bluetooth = ctk.BooleanVar(value=config.watch_bluetooth)
        self.watch_audio = ctk.BooleanVar(value=config.watch_audio)

        # Construire l'interface
        self._build_ui()

        # Brancher le logger sur la GUI
        logger.set_gui_callback(self._log_to_gui)

        # Demarrer les watchers
        self.usb_watcher = DeviceWatcher(
            callback=lambda dt: self._on_device("USB", "Peripherique USB")
        )
        self.usb_watcher.start()

        self.poll_watcher = PollingWatcher(callback=self._on_device)
        self._sync_toggles()
        self.poll_watcher.start()

        # Cooldown initial (5s pour stabiliser les baselines)
        self.locker.set_device_cooldown(5.0)
        logger.start(f"PrankGuard v2.0 demarre - {len(self.owner_encodings)} visages charges")
        logger.info(f"Mode: {config.usb_mode} | {config.sec_mode}")
        logger.info(f"Tolerance faciale: {config.face_tolerance}")

        # Log des toggles actifs
        actifs = []
        if config.watch_usb: actifs.append("USB")
        if config.watch_usb_hid: actifs.append("HID")
        if config.watch_monitors: actifs.append("Moniteurs")
        if config.watch_network: actifs.append("Reseau")
        if config.watch_printers: actifs.append("Imprimantes")
        if config.watch_bluetooth: actifs.append("Bluetooth")
        if config.watch_audio: actifs.append("Audio")
        logger.info(f"Detection active: {', '.join(actifs) if actifs else 'AUCUNE'}")

        # Threads
        threading.Thread(target=self._camera_loop, daemon=True).start()
        threading.Thread(target=self._keyboard_listener, daemon=True).start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Demander de changer le mot de passe si c'est le defaut
        if not config.password_changed:
            self.after(1000, self._prompt_change_password)

    # -- UI --

    def _build_ui(self):
        """Construit l'interface responsive."""
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, height=60, corner_radius=10)
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            hdr, text="PrankGuard v2.0",
            font=ctk.CTkFont(size=24, weight="bold")
        ).grid(row=0, column=0, padx=20, pady=10)
        self.status_label = ctk.CTkLabel(
            hdr, text="DEMARRAGE",
            font=ctk.CTkFont(size=18, weight="bold"), text_color="#888"
        )
        self.status_label.grid(row=0, column=2, padx=20, pady=10)

        # Tabs
        self.tabs = ctk.CTkTabview(self, corner_radius=10)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        tab_cam = self.tabs.add("Camera")
        tab_log = self.tabs.add("Logs")
        tab_set = self.tabs.add("Parametres")

        # -- Onglet Camera --
        tab_cam.grid_rowconfigure(0, weight=1)
        tab_cam.grid_columnconfigure(0, weight=1)

        cam_container = ctk.CTkFrame(tab_cam, corner_radius=10)
        cam_container.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        cam_container.grid_rowconfigure(0, weight=1)
        cam_container.grid_columnconfigure(0, weight=1)

        self.cam_label = ctk.CTkLabel(cam_container, text="Initialisation camera...")
        self.cam_label.grid(row=0, column=0, sticky="nsew")

        info_bar = ctk.CTkFrame(tab_cam, height=70, corner_radius=10)
        info_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.face_info = ctk.CTkLabel(info_bar, text="--", font=ctk.CTkFont(size=14))
        self.face_info.pack(side="left", padx=20, pady=10)
        self.countdown_label = ctk.CTkLabel(
            info_bar, text="",
            font=ctk.CTkFont(size=18, weight="bold"), text_color="#e74c3c"
        )
        self.countdown_label.pack(side="right", padx=20, pady=10)

        # -- Onglet Logs --
        log_frame = ctk.CTkFrame(tab_log, corner_radius=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Consolas", size=12)
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkButton(
            tab_log, text="Effacer", width=100,
            command=lambda: self.log_box.delete("1.0", "end")
        ).pack(pady=(0, 10))

        # -- Onglet Parametres --
        scroll = ctk.CTkScrollableFrame(tab_set, corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=10, pady=10)

        # Mode USB
        ctk.CTkLabel(scroll, text="Mode de blocage USB",
                      font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
        self.usb_mode_var = ctk.StringVar(value=self.config.usb_mode)
        ctk.CTkSegmentedButton(
            scroll, values=["DESKTOP", "LAPTOP"],
            variable=self.usb_mode_var, command=self._on_usb_mode
        ).pack(pady=5)
        ctk.CTkLabel(scroll,
                      text="DESKTOP: Stockage USB seul | LAPTOP: Tous les ports USB",
                      font=ctk.CTkFont(size=11), text_color="#888").pack()

        # Mode Securite
        ctk.CTkLabel(scroll, text="Mode de securite",
                      font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
        self.sec_mode_var = ctk.StringVar(value=self.config.sec_mode)
        ctk.CTkSegmentedButton(
            scroll, values=["PEDAGO", "SECURE"],
            variable=self.sec_mode_var, command=self._on_sec_mode
        ).pack(pady=5)
        ctk.CTkLabel(scroll,
                      text="PEDAGO: Demo (pas de lock auto) | SECURE: Lock si pas d'owner >10s",
                      font=ctk.CTkFont(size=11), text_color="#888").pack()

        # Detection de peripheriques
        ctk.CTkLabel(scroll, text="Detection de peripheriques",
                      font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))

        btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="Tout activer", width=120,
                       fg_color="#27ae60", command=self._enable_all).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Tout desactiver", width=120,
                       fg_color="#e74c3c", command=self._disable_all).pack(side="left", padx=5)

        det_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        det_frame.pack(fill="x", padx=20, pady=10)

        left_col = ctk.CTkFrame(det_frame, fg_color="transparent")
        left_col.pack(side="left", fill="both", expand=True, padx=10)
        ctk.CTkLabel(left_col, text="USB & Stockage",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      text_color="#3498db").pack(anchor="w", pady=(5, 5))
        ctk.CTkSwitch(left_col, text="USB Devices", variable=self.watch_usb,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(left_col, text="HID (souris, clavier)", variable=self.watch_usb_hid,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(left_col, text="Imprimantes", variable=self.watch_printers,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)

        right_col = ctk.CTkFrame(det_frame, fg_color="transparent")
        right_col.pack(side="right", fill="both", expand=True, padx=10)
        ctk.CTkLabel(right_col, text="Affichage & Reseau",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      text_color="#9b59b6").pack(anchor="w", pady=(5, 5))
        ctk.CTkSwitch(right_col, text="Moniteurs", variable=self.watch_monitors,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text="Reseau", variable=self.watch_network,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text="Bluetooth", variable=self.watch_bluetooth,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text="Audio", variable=self.watch_audio,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)

        # Seuils
        ctk.CTkLabel(scroll, text="Seuils de detection",
                      font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

        # Tolerance faciale (NOUVEAU)
        tol_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        tol_frame.pack(fill="x", padx=40, pady=5)
        self.tol_label = ctk.CTkLabel(
            tol_frame,
            text=f"Tolerance faciale: {self.config.face_tolerance:.2f}",
            width=200, anchor="w"
        )
        self.tol_label.pack(side="left")
        self.tol_slider = ctk.CTkSlider(
            tol_frame, from_=0.25, to=0.65, number_of_steps=40,
            command=self._on_tolerance
        )
        self.tol_slider.set(self.config.face_tolerance)
        self.tol_slider.pack(side="right", expand=True, fill="x", padx=(20, 0))

        # Taille min visage
        face_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        face_frame.pack(fill="x", padx=40, pady=5)
        self.face_size_label = ctk.CTkLabel(
            face_frame,
            text=f"Taille min. visage: {int(self.config.min_face_size * 100)}%",
            width=200, anchor="w"
        )
        self.face_size_label.pack(side="left")
        self.face_slider = ctk.CTkSlider(
            face_frame, from_=10, to=40, number_of_steps=30,
            command=self._on_face_size
        )
        self.face_slider.set(self.config.min_face_size * 100)
        self.face_slider.pack(side="right", expand=True, fill="x", padx=(20, 0))

        # Delai de lock
        delay_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        delay_frame.pack(fill="x", padx=40, pady=5)
        self.delay_label = ctk.CTkLabel(
            delay_frame,
            text=f"Delai de lock: {self.config.threat_lock_delay:.1f}s",
            width=200, anchor="w"
        )
        self.delay_label.pack(side="left")
        self.delay_slider = ctk.CTkSlider(
            delay_frame, from_=1, to=5, number_of_steps=8,
            command=self._on_delay
        )
        self.delay_slider.set(self.config.threat_lock_delay)
        self.delay_slider.pack(side="right", expand=True, fill="x", padx=(20, 0))

        # Boutons
        btn_frame2 = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_frame2.pack(pady=20)
        ctk.CTkButton(
            btn_frame2, text="Re-enregistrer le visage",
            fg_color="#e74c3c", command=self._reenroll
        ).pack(side="left", padx=10)
        ctk.CTkButton(
            btn_frame2, text="Changer le mot de passe",
            fg_color="#3498db", command=self._prompt_change_password
        ).pack(side="left", padx=10)

        # -- Footer --
        ftr = ctk.CTkFrame(self, height=50, corner_radius=10)
        ftr.grid(row=2, column=0, sticky="ew", padx=10, pady=(5, 10))

        self.usb_lbl = ctk.CTkLabel(
            ftr, text="USB: OK", font=ctk.CTkFont(size=14), text_color="#2ecc71"
        )
        self.usb_lbl.pack(side="left", padx=20, pady=10)
        self.cam_lbl = ctk.CTkLabel(
            ftr, text="CAM: OK", font=ctk.CTkFont(size=14), text_color="#2ecc71"
        )
        self.cam_lbl.pack(side="left", padx=10, pady=10)
        self.mode_lbl = ctk.CTkLabel(
            ftr, text=f"{self.config.usb_mode} | {self.config.sec_mode}",
            font=ctk.CTkFont(size=14), text_color="#888"
        )
        self.mode_lbl.pack(side="left", padx=20, pady=10)
        self.cd_lbl = ctk.CTkLabel(
            ftr, text="", font=ctk.CTkFont(size=12), text_color="#f39c12"
        )
        self.cd_lbl.pack(side="left", padx=10, pady=10)

        ctk.CTkButton(
            ftr, text="LOCK", width=70, fg_color="#e74c3c",
            command=lambda: self.locker.do_lock("Manuel")
        ).pack(side="right", padx=5, pady=10)
        self.pause_btn = ctk.CTkButton(
            ftr, text="PAUSE", width=70, fg_color="#f39c12",
            command=self._toggle_pause
        )
        self.pause_btn.pack(side="right", padx=5, pady=10)
        ctk.CTkButton(
            ftr, text="UNBLOCK", width=80, fg_color="#3498db",
            command=self._unblock_action
        ).pack(side="right", padx=5, pady=10)

    # -- Callbacks Settings (tous branches sur la logique) --

    def _on_usb_mode(self, v):
        self.config.update(usb_mode=v)
        self.locker.usb_mode = v
        self.mode_lbl.configure(text=f"{v} | {self.config.sec_mode}")
        logger.mode(f"Mode USB -> {v}")

    def _on_sec_mode(self, v):
        self.config.update(sec_mode=v)
        self.state_machine.sec_mode = v
        self.mode_lbl.configure(text=f"{self.config.usb_mode} | {v}")
        logger.mode(f"Mode securite -> {v}")

    def _on_tolerance(self, v):
        v = round(v, 2)
        self.face_analyzer.tolerance = v
        self.config.update(face_tolerance=v)
        self.tol_label.configure(text=f"Tolerance faciale: {v:.2f}")
        logger.toggle(f"Tolerance faciale -> {v:.2f}")

    def _on_face_size(self, v):
        self.face_analyzer.min_face_size = v / 100
        self.config.update(min_face_size=v / 100)
        self.face_size_label.configure(text=f"Taille min. visage: {int(v)}%")

    def _on_delay(self, v):
        self.state_machine.threat_lock_delay = v
        self.config.update(threat_lock_delay=v)
        self.delay_label.configure(text=f"Delai de lock: {v:.1f}s")

    def _on_toggle_change(self):
        self.locker.set_device_cooldown(5.0)
        self._sync_toggles()
        self.poll_watcher.reset_baselines()
        self.config.update(
            watch_usb=self.watch_usb.get(),
            watch_usb_hid=self.watch_usb_hid.get(),
            watch_monitors=self.watch_monitors.get(),
            watch_network=self.watch_network.get(),
            watch_printers=self.watch_printers.get(),
            watch_bluetooth=self.watch_bluetooth.get(),
            watch_audio=self.watch_audio.get(),
        )
        # Log les toggles actifs
        actifs = []
        if self.watch_usb.get(): actifs.append("USB")
        if self.watch_usb_hid.get(): actifs.append("HID")
        if self.watch_monitors.get(): actifs.append("Moniteurs")
        if self.watch_network.get(): actifs.append("Reseau")
        if self.watch_printers.get(): actifs.append("Imprimantes")
        if self.watch_bluetooth.get(): actifs.append("Bluetooth")
        if self.watch_audio.get(): actifs.append("Audio")
        logger.toggle(f"Detection: {', '.join(actifs) if actifs else 'AUCUNE'} (cooldown 5s)")

    def _sync_toggles(self):
        self.poll_watcher.watch_usb_hid = self.watch_usb_hid.get()
        self.poll_watcher.watch_monitors = self.watch_monitors.get()
        self.poll_watcher.watch_network = self.watch_network.get()
        self.poll_watcher.watch_printers = self.watch_printers.get()
        self.poll_watcher.watch_bluetooth = self.watch_bluetooth.get()
        self.poll_watcher.watch_audio = self.watch_audio.get()
        self.usb_watcher.enabled = self.watch_usb.get()

    def _enable_all(self):
        self.locker.set_device_cooldown(5.0)
        for var in [self.watch_usb, self.watch_usb_hid, self.watch_monitors,
                    self.watch_network, self.watch_printers, self.watch_bluetooth,
                    self.watch_audio]:
            var.set(True)
        self._sync_toggles()
        self.poll_watcher.reset_baselines()
        logger.toggle("TOUTE la detection ACTIVEE (cooldown 5s)")

    def _disable_all(self):
        for var in [self.watch_usb, self.watch_usb_hid, self.watch_monitors,
                    self.watch_network, self.watch_printers, self.watch_bluetooth,
                    self.watch_audio]:
            var.set(False)
        self._sync_toggles()
        logger.toggle("TOUTE la detection DESACTIVEE")

    # -- Device arrival --

    def _on_device(self, device_type: str, device_info: str = ""):
        """
        Quand un device est detecte :
        - Si owner present : notification seulement (pas de lock)
        - Si owner absent : lock + bloquer
        """
        if self.locker.device_cooldown_active:
            return
        if self.paused:
            return

        toggle_map = {
            "USB": self.watch_usb, "USB HID": self.watch_usb_hid,
            "Monitor": self.watch_monitors, "Network": self.watch_network,
            "Printer": self.watch_printers, "Bluetooth": self.watch_bluetooth,
            "Audio": self.watch_audio,
        }
        toggle = toggle_map.get(device_type)
        if toggle and not toggle.get():
            return

        if not self.locker.can_lock():
            return

        # Bloquer immediatement selon le type
        if device_type == "Bluetooth":
            block_bluetooth()
        elif device_type == "Network":
            block_network()
        elif device_type in ("USB", "USB HID"):
            block_usb(self.config.usb_mode)

        logger.device(f"Connexion {device_type} detectee : {device_info}")

        if self._owner_present:
            # Owner present -> notification, PAS de lock
            logger.device(f"Owner present -> notification")
            self.after(0, lambda: self._show_device_notification(
                device_type, device_info, can_lock=False
            ))
        else:
            # Owner absent -> lock immediat
            logger.device(f"Owner ABSENT -> verrouillage")
            self.after(0, lambda: self._device_blocked(device_type, force_lock=True))

    def _show_device_notification(self, device_type: str, device_info: str,
                                   can_lock: bool = False):
        DeviceNotification(
            parent=self,
            device_type=device_type,
            device_info=device_info or device_type,
            on_allow=lambda: self._device_allowed(device_type),
            on_block=lambda: self._device_blocked(device_type, force_lock=can_lock),
        )

    def _device_allowed(self, device_type: str):
        if device_type == "Bluetooth":
            unblock_bluetooth()
        elif device_type == "Network":
            unblock_network()
        elif device_type in ("USB", "USB HID"):
            from src.devices.blocker import unblock_usb
            unblock_usb()
            self.locker.usb_blocked = False

        self.locker.set_device_cooldown(10.0)
        self.poll_watcher.reset_baselines()
        self._update_usb_label()
        logger.device(f"{device_type} AUTORISE par l'utilisateur")

    def _device_blocked(self, device_type: str, force_lock: bool = False):
        """Bloquer le device. Lock le PC seulement si force_lock=True."""
        self.locker.set_device_cooldown(10.0)

        if force_lock:
            logger.device(f"{device_type} BLOQUE + verrouillage du poste")
            self.locker.do_lock(f"Nouveau {device_type} (owner absent)")
        else:
            logger.device(f"{device_type} BLOQUE (connexion coupee, pas de lock)")

        self.poll_watcher.reset_baselines()
        self._update_usb_label()

    def _update_usb_label(self):
        if self.locker.usb_blocked:
            self.usb_lbl.configure(text="USB: BLOQUE", text_color="#e74c3c")
        else:
            self.usb_lbl.configure(text="USB: OK", text_color="#2ecc71")

    # -- Camera loop (video fluide) --

    def _camera_loop(self):
        """Boucle camera : capture a 30fps, analyse en arriere-plan."""
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while self.running:
            # Cooldowns footer
            self._update_cooldowns()

            if self.paused:
                time.sleep(0.1)
                continue

            ret, frame = self.cap.read()

            if not ret or frame is None:
                self.after(0, lambda: self.cam_lbl.configure(
                    text="CAM: PERDUE", text_color="#e74c3c"
                ))
                result = self.state_machine.on_camera_lost(self.locker.can_lock())
                if result["should_lock"]:
                    self.locker.do_lock(result["lock_reason"])
                    self._update_usb_label()
                    self.cap.release()
                    time.sleep(2)
                    self.cap = cv2.VideoCapture(0)
                time.sleep(0.1)
                continue

            self.state_machine.on_camera_ok()
            self.after(0, lambda: self.cam_lbl.configure(
                text="CAM: OK", text_color="#2ecc71"
            ))

            # Soumettre le frame pour analyse (non-bloquant)
            self.face_analyzer.submit_frame(frame)

            # Verifier si une nouvelle analyse est dispo
            if self.face_analyzer.has_new_analysis:
                situation = self.face_analyzer.get_situation()
                self._owner_present = situation["owner"]

                result = self.state_machine.update(situation, self.locker.can_lock())

                # Afficher info
                info = situation["info"]
                state = result["state"]
                color = STATE_COLORS.get(state, "#888")
                countdown = result["countdown"]

                self.after(0, lambda i=info: self.face_info.configure(text=i))
                self.after(0, lambda s=state, c=color: self.status_label.configure(
                    text=s, text_color=c
                ))
                self.after(0, lambda t=countdown: self.countdown_label.configure(text=t))

                if result["should_lock"]:
                    self.locker.do_lock(result["lock_reason"])
                    self._update_usb_label()
                    time.sleep(2)
                    continue

                if situation["owner"] and self.locker.usb_blocked:
                    self.locker.do_unlock()
                    self.locker.set_device_cooldown(5.0)
                    self.poll_watcher.reset_baselines()
                    self._update_usb_label()

            # Dessiner les rectangles (resultats en cache) + afficher
            frame = self.face_analyzer.draw_on_frame(frame)
            self._display_frame(frame)

            time.sleep(0.033)  # ~30fps

        if self.cap:
            self.cap.release()
        self.face_analyzer.stop()

    def _display_frame(self, frame):
        """Affiche un frame dans la GUI."""
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        try:
            cw = self.cam_label.winfo_width()
            ch = self.cam_label.winfo_height()
            if cw > 1 and ch > 1:
                ratio = min(cw / 640, ch / 480)
                nw = int(640 * ratio)
                nh = int(480 * ratio)
                img = img.resize((nw, nh))
        except Exception:
            img = img.resize((640, 480))

        imgtk = ImageTk.PhotoImage(image=img)
        self.after(0, lambda i=imgtk: (
            self.cam_label.configure(image=i, text=""),
            setattr(self.cam_label, "image", i)
        ))

    def _update_cooldowns(self):
        lock_cd = self.locker.get_cooldown_remaining()
        dev_cd = self.locker.get_device_cooldown_remaining()

        if lock_cd > 0:
            text = f"Lock CD: {lock_cd:.1f}s"
        elif dev_cd > 0:
            text = f"Dev CD: {dev_cd:.1f}s"
        else:
            text = ""

        self.after(0, lambda t=text: self.cd_lbl.configure(text=t))

    # -- Keyboard --

    def _keyboard_listener(self):
        import keyboard
        while self.running:
            if keyboard.is_pressed("p"):
                self.after(0, self._toggle_pause)
                time.sleep(0.3)
            if not self.paused:
                if keyboard.is_pressed("l"):
                    self.after(0, lambda: self.locker.do_lock("Manuel (clavier)"))
                    time.sleep(0.5)
                if keyboard.is_pressed("u"):
                    self.after(0, self._unblock_action)
                    time.sleep(0.3)
            time.sleep(0.05)

    # -- Actions --

    def _toggle_pause(self):
        self.paused = not self.paused
        self.poll_watcher.paused = self.paused
        self.usb_watcher.paused = self.paused
        self.pause_btn.configure(
            text="RESUME" if self.paused else "PAUSE",
            fg_color="#27ae60" if self.paused else "#f39c12"
        )
        logger.info("En pause" if self.paused else "Reprise")

    def _unblock_action(self):
        self.locker.do_unlock()
        self.locker.set_device_cooldown(5.0)
        self.poll_watcher.reset_baselines()
        self._update_usb_label()
        logger.unlock("USB debloque manuellement (cooldown 5s)")

    def _reenroll(self):
        self.running = False
        self.usb_watcher.stop()
        self.poll_watcher.stop()
        self.face_analyzer.stop()
        time.sleep(0.3)
        self.destroy()

        from src.enrollment import EnrollmentWindow
        EnrollmentWindow(
            encodings_path=self.config.encodings_path,
            on_complete=lambda: PrankGuardApp(self.config).mainloop()
        ).mainloop()

    def _prompt_change_password(self):
        ChangePasswordDialog(self, self.config)

    def _log_to_gui(self, formatted_msg: str):
        try:
            self.log_box.insert("end", formatted_msg + "\n")
            self.log_box.see("end")
        except Exception:
            pass

    def _on_close(self):
        """Fermeture protegee par mot de passe."""
        PasswordDialog(self, self.config, on_success=self._do_close)

    def _do_close(self):
        """Fermeture effective apres validation du mot de passe."""
        self.running = False
        self.usb_watcher.stop()
        self.poll_watcher.stop()
        self.face_analyzer.stop()
        self.locker.do_unlock()
        time.sleep(0.2)
        self.destroy()
