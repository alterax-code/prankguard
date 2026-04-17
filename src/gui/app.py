"""
Application principale PrankGuard — GUI CustomTkinter.
FIX 3 — Pause complète (camera + devices + keyboard sauf P).
FIX 4 — Modes Pedago/Secure effectifs.
FIX 5 — Modes Desktop/Laptop effectifs + sauvegarde.
FIX 6 — Vidéo fluide (frame skip + affichage continu).
FIX 7 — Notifications device (popup au lieu de lock direct).
FIX 9 — Layout responsive (grid + redimensionnement caméra).
"""
import cv2
import numpy as np
import os
import time
import threading
import winsound

import customtkinter as ctk
from PIL import Image
from datetime import datetime

from src import paths
from src.config import Config
from src.logger import logger
from src.face_analyzer import FaceAnalyzer
from src.systray import SystrayIcon
from src.enrollment import load_authorized_users, load_authorized_users_from_bytes, save_authorized_users
from src.crypto import is_encrypted, encrypt_encodings, decrypt_encodings
from src.anti_spoof import AntiSpoof
from src.intrusion_report import IntrusionReporter, IntrusionType, Criticality
from src.email_alert import EmailAlerter
from src.state_machine import StateMachine, State, STATE_COLORS
from src.security.locker import Locker
from src.devices.watcher import DeviceWatcher
from src.devices.poller import PollingWatcher
from src.devices.notification import DeviceNotification
from src.devices.blocker import (
    block_bluetooth, unblock_bluetooth,
    block_network, unblock_network,
)
from src.security.hardening import (
    hash_password, verify_password, needs_rehash, set_window_capture_protection,
)
from src import audit as _audit
from src.motion_detector import MotionDetector
from src import events_db as _edb
from src import device_inventory as _inventory
import face_recognition
from src.challenge_response import ChallengeResponse
from src.network_monitor import SSIDMonitor
from src.watchdog import WatchdogThread, start_external_watchdog


class _ForcePasswordChangeDialog(ctk.CTkToplevel):
    """Dialog modal bloquant — force le changement du mot de passe par défaut."""

    def __init__(self, parent):
        super().__init__(parent)
        self._app = parent
        self.title("PrankGuard — Changement de mot de passe requis")
        self.geometry("440x300")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close_dialog)
        self._build_ui()
        self.lift()
        self.focus_force()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Changement de mot de passe requis",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(pady=(20, 4))
        ctk.CTkLabel(
            self,
            text="Le mot de passe par défaut doit être modifié\navant d'utiliser PrankGuard.",
            font=ctk.CTkFont(size=12), justify="center",
        ).pack(pady=(0, 10))

        self._pwd_entry = ctk.CTkEntry(
            self, placeholder_text="Nouveau mot de passe", show="*", width=290
        )
        self._pwd_entry.pack(pady=4)

        self._confirm_entry = ctk.CTkEntry(
            self, placeholder_text="Confirmer le mot de passe", show="*", width=290
        )
        self._confirm_entry.pack(pady=4)

        self._error_lbl = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11), text_color="#e74c3c"
        )
        self._error_lbl.pack(pady=2)

        ctk.CTkLabel(
            self, text="Min. 8 caractères · 1 majuscule · 1 chiffre",
            font=ctk.CTkFont(size=11), text_color="#888",
        ).pack(pady=2)

        ctk.CTkButton(self, text="Valider", width=200, command=self._submit).pack(pady=10)
        self.bind("<Return>", lambda _: self._submit())

    @staticmethod
    def _validate(pwd: str) -> str:
        if len(pwd) < 8:
            return "Minimum 8 caractères requis"
        if not any(c.isupper() for c in pwd):
            return "Au moins 1 majuscule requise"
        if not any(c.isdigit() for c in pwd):
            return "Au moins 1 chiffre requis"
        return ""

    def _submit(self):
        pwd     = self._pwd_entry.get()
        confirm = self._confirm_entry.get()
        if pwd != confirm:
            self._error_lbl.configure(text="Les mots de passe ne correspondent pas")
            return
        err = self._validate(pwd)
        if err:
            self._error_lbl.configure(text=err)
            return
        self._app.config.update(
            close_protection_password_hash=hash_password(pwd),
            password_needs_change=False,
        )
        _audit.log_event("PASSWORD_CHANGED", {"forced": True})
        self.destroy()

    def _on_close_dialog(self):
        """Fermer le dialog = fermer l'application."""
        self._app._on_close()


class _PauseDurationDialog(ctk.CTkToplevel):
    """Dialog de sélection de durée de pause (Vague 4)."""

    def __init__(self, parent, callback):
        super().__init__(parent)
        self._callback = callback
        self.title("Durée de pause")
        self.geometry("300x180")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)
        self._build_ui()
        self.lift()
        self.focus_force()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Choisir la durée de pause",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(pady=(20, 12))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=5)

        ctk.CTkButton(
            btn_frame, text="15 min", width=80, fg_color="#e67e22",
            command=lambda: self._select(900)
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_frame, text="1 heure", width=80, fg_color="#e67e22",
            command=lambda: self._select(3600)
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_frame, text="Manuel", width=80, fg_color="#7f8c8d",
            command=lambda: self._select(0)
        ).pack(side="left", padx=6)

    def _select(self, seconds: float):
        self.grab_release()
        self.destroy()
        self._callback(seconds)


class PrankGuardApp(ctk.CTk):
    """Application principale PrankGuard."""

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.title("PrankGuard v2.0")
        self.geometry("1100x800")
        self.minsize(900, 700)  # FIX 9 — taille minimale

        # Charger les utilisateurs autorisés (déchiffrement AES-256 si nécessaire)
        try:
            raw = open(config.encodings_path, "rb").read()
            if is_encrypted(raw):
                raw = decrypt_encodings(raw)
            self.authorized_users = load_authorized_users_from_bytes(raw)
        except Exception:
            self.authorized_users = {}

        # État global
        self.running = True
        self.paused = False
        self.cap = None
        self._closing = False          # FIX shutdown — guard .after() post-destroy
        self._cap_lock = threading.Lock()  # FIX OpenCV — protège release/reopen

        # Modules
        self.face_analyzer = FaceAnalyzer(
            authorized_users=self.authorized_users,
            tolerance=config.face_tolerance,
            min_face_size=config.min_face_size,
            center_threshold=config.center_threshold,
            analyze_every_n=config.analyze_every_n_frames,
            detection_scale=config.detection_scale,
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

        # Anti-spoofing
        self._anti_spoof = AntiSpoof()
        self._anti_spoof_var = ctk.BooleanVar(value=config.anti_spoof_enabled)

        # Alarme sonore
        self._alarm_active: bool = False
        self._alarm_thread: threading.Thread = None
        self._alarm_var = ctk.BooleanVar(value=config.sound_alarm_enabled)
        self._threat_start: float = None  # Timestamp début état THREAT

        # Protection anti-fermeture
        self._close_protection_var = ctk.BooleanVar(value=config.close_protection_enabled)

        # Rapport d'intrusion
        self._reporter = IntrusionReporter(config.intrusion_log_path)
        self._intrusion_active: bool = False

        # Vague 3 — Motion pre-filter + suivi transitions d'état
        self.motion_detector = MotionDetector()
        self._last_face_state: str = "NONE"

        # Vague 4 — pause temporisée
        self._pause_until: float = 0.0

        # Vague 4 — dashboard stats
        self._fps_counter: int = 0
        self._fps_last_time: float = time.time()
        self._fps_current: float = 0.0
        self._motion_skip_total: int = 0
        self._motion_skip_skipped: int = 0

        # Vague 5 — Challenge-response anti-spoof
        self._challenge_response = ChallengeResponse()
        self._challenge_response_var = ctk.BooleanVar(value=config.challenge_response_enabled)

        # Vague 5 — SSID monitoring
        self._ssid_monitor = SSIDMonitor()
        self._watch_ssid_var = ctk.BooleanVar(value=config.watch_ssid)

        # Vague 5 — Watchdog
        self._watchdog = WatchdogThread()

        # Alertes email SMTP (Sprint 2 — Feature 4)
        self._email_alerter = EmailAlerter(
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            smtp_user=config.smtp_user,
            smtp_password_b64=config.smtp_password_b64,
            recipient=config.smtp_recipient,
        )
        self._email_var = ctk.BooleanVar(value=config.email_enabled)

        # Chiffrement AES-256 des encodings (Sprint 2 — Feature 5)
        self._encrypt_var = ctk.BooleanVar(value=config.encryption_enabled)

        # Variables de toggles
        self.watch_usb = ctk.BooleanVar(value=config.watch_usb)
        self.watch_usb_hid = ctk.BooleanVar(value=config.watch_usb_hid)
        self.watch_monitors = ctk.BooleanVar(value=config.watch_monitors)
        self.watch_network = ctk.BooleanVar(value=config.watch_network)
        self.watch_printers = ctk.BooleanVar(value=config.watch_printers)
        self.watch_bluetooth = ctk.BooleanVar(value=config.watch_bluetooth)
        self.watch_audio = ctk.BooleanVar(value=config.watch_audio)

        # Mode stealth (Sprint 2 — Feature 2)
        self._stealth_var = ctk.BooleanVar(value=config.stealth_mode)

        # Construire l'interface
        self._build_ui()

        # Systray — icône couleur temps réel dans la barre de notification
        self._systray = SystrayIcon(
            on_show_hide=lambda: self.after(0, self._toggle_window),
            on_quit=lambda: self.after(0, self._on_close_request),
        )
        self._systray.start()
        # Mode stealth : afficher brièvement puis masquer (flash de confirmation)
        if config.stealth_mode:
            logger.info("Mode stealth actif — fenêtre masquée dans 1.5s (accès via systray)")
            self.after(1500, self.withdraw)

        # Brancher le logger sur la GUI
        logger.set_gui_callback(self._log_to_gui)

        # Démarrer les watchers
        self.usb_watcher = DeviceWatcher(callback=lambda dt: self._on_device("USB", "Périphérique USB"))
        self.usb_watcher.start()

        self.poll_watcher = PollingWatcher(callback=self._on_device)
        self._sync_toggles()
        self.poll_watcher.start()

        # Vague 4 — scan initial (auto-autoriser les périphériques présents au démarrage)
        threading.Thread(target=_inventory.scan_current, daemon=True).start()

        # Cooldown initial (5s pour stabiliser les baselines)
        self.locker.set_device_cooldown(5.0)
        total_enc = sum(len(v) for v in self.authorized_users.values())
        logger.start(
            f"PrankGuard v2.0 démarré — {total_enc} visages, "
            f"{len(self.authorized_users)} utilisateur(s): {list(self.authorized_users.keys())}"
        )
        try:
            _audit.log_event("APP_START", {"users": list(self.authorized_users.keys())})
        except Exception as exc:
            logger.error(f"Audit APP_START: erreur — {exc}")

        # Threads
        threading.Thread(target=self._camera_loop, daemon=True).start()
        # Raccourcis clavier désactivés — interfèrent avec la saisie normale
        # threading.Thread(target=self._keyboard_listener, daemon=True).start()

        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        # Anti-capture d'écran désactivé — bloque le partage d'écran
        # self.after(100, self._apply_capture_protection)
        # Forcer changement de mot de passe si nécessaire (Vague 2)
        self.after(200, self._check_force_password_change)
        # Vague 4 — démarrer le dashboard
        self.after(3000, self._update_dashboard)

        # Vague 5 — Watchdog heartbeat + externe
        self._watchdog.start()
        try:
            start_external_watchdog()
        except Exception as exc:
            logger.error(f"Watchdog externe: erreur démarrage — {exc}")

        # Vague 5 — SSID monitoring
        if config.watch_ssid:
            self._ssid_monitor.start(self, self._on_ssid_change)

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        """Construit l'interface avec grid responsive (FIX 9)."""
        # Conteneur principal
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
            hdr, text="DÉMARRAGE",
            font=ctk.CTkFont(size=18, weight="bold"), text_color="#888"
        )
        self.status_label.grid(row=0, column=2, padx=20, pady=10)

        # Tabs
        self.tabs = ctk.CTkTabview(self, corner_radius=10)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        tab_cam = self.tabs.add("Caméra")
        tab_log = self.tabs.add("Logs")
        tab_inv = self.tabs.add("Inventaire")
        tab_set = self.tabs.add("Paramètres")

        # ── Onglet Caméra ────────────────────────────────────────────
        tab_cam.grid_rowconfigure(0, weight=1)
        tab_cam.grid_columnconfigure(0, weight=1)

        cam_container = ctk.CTkFrame(tab_cam, corner_radius=10)
        cam_container.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        cam_container.grid_rowconfigure(0, weight=1)
        cam_container.grid_columnconfigure(0, weight=1)

        self.cam_label = ctk.CTkLabel(cam_container, text="Initialisation caméra...")
        self.cam_label.grid(row=0, column=0, sticky="nsew")

        # Barre d'info sous la caméra
        info_bar = ctk.CTkFrame(tab_cam, height=70, corner_radius=10)
        info_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

        self.face_info = ctk.CTkLabel(
            info_bar, text="--", font=ctk.CTkFont(size=14)
        )
        self.face_info.pack(side="left", padx=20, pady=10)
        # Indicateur anti-spoofing (masqué si désactivé)
        self.spoof_lbl = ctk.CTkLabel(
            info_bar, text="",
            font=ctk.CTkFont(size=13), text_color="#f39c12"
        )
        self.spoof_lbl.pack(side="left", padx=10, pady=10)
        self.countdown_label = ctk.CTkLabel(
            info_bar, text="",
            font=ctk.CTkFont(size=18, weight="bold"), text_color="#e74c3c"
        )
        self.countdown_label.pack(side="right", padx=20, pady=10)
        # Vague 4 — dashboard stats (FPS / skip% / events today)
        self.dash_events_lbl = ctk.CTkLabel(
            info_bar, text="Evt: --", font=ctk.CTkFont(size=12), text_color="#888"
        )
        self.dash_events_lbl.pack(side="right", padx=8, pady=10)
        self.dash_skip_lbl = ctk.CTkLabel(
            info_bar, text="Skip: --", font=ctk.CTkFont(size=12), text_color="#888"
        )
        self.dash_skip_lbl.pack(side="right", padx=8, pady=10)
        self.dash_fps_lbl = ctk.CTkLabel(
            info_bar, text="FPS: --", font=ctk.CTkFont(size=12), text_color="#888"
        )
        self.dash_fps_lbl.pack(side="right", padx=8, pady=10)

        # ── Onglet Logs ──────────────────────────────────────────────
        # Vague 5 — analytics section
        stats_frame = ctk.CTkFrame(tab_log, corner_radius=10, height=90)
        stats_frame.pack(fill="x", padx=10, pady=(10, 4))
        stats_frame.pack_propagate(False)
        self._stats_labels: dict = {}
        for key in ["Locks", "Face_Owner", "Face_Unknown", "Alarms", "USB"]:
            f = ctk.CTkFrame(stats_frame, fg_color="transparent")
            f.pack(side="left", expand=True, padx=8, pady=8)
            ctk.CTkLabel(f, text=key, font=ctk.CTkFont(size=11), text_color="#888").pack()
            lbl = ctk.CTkLabel(f, text="--", font=ctk.CTkFont(size=22, weight="bold"))
            lbl.pack()
            self._stats_labels[key] = lbl
        self.after(500, self._update_stats_section)

        log_frame = ctk.CTkFrame(tab_log, corner_radius=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=4)
        self.log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Consolas", size=12)
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=10)

        btn_row = ctk.CTkFrame(tab_log, fg_color="transparent")
        btn_row.pack(pady=(0, 10))
        ctk.CTkButton(
            btn_row, text="Effacer", width=100,
            command=lambda: self.log_box.delete("1.0", "end")
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_row, text="Exporter CSV", width=120, fg_color="#2980b9",
            command=self._export_csv
        ).pack(side="left", padx=6)

        # ── Onglet Inventaire ────────────────────────────────────────
        inv_frame = ctk.CTkFrame(tab_inv, corner_radius=10)
        inv_frame.pack(fill="both", expand=True, padx=10, pady=10)

        inv_hdr = ctk.CTkFrame(inv_frame, fg_color="transparent")
        inv_hdr.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(
            inv_hdr, text="Inventaire des périphériques",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(side="left")
        ctk.CTkButton(
            inv_hdr, text="Actualiser", width=100,
            command=self._refresh_inventory_ui
        ).pack(side="right")

        # En-tête colonnes
        cols_frame = ctk.CTkFrame(inv_frame, fg_color="#1a1a2e", corner_radius=6)
        cols_frame.pack(fill="x", padx=10, pady=(2, 0))
        for txt, w in [("Catégorie", 120), ("Nom", 280), ("Statut", 90), ("Action", 90)]:
            ctk.CTkLabel(
                cols_frame, text=txt, width=w,
                font=ctk.CTkFont(size=12, weight="bold"), anchor="w"
            ).pack(side="left", padx=6, pady=4)

        self._inv_scroll = ctk.CTkScrollableFrame(inv_frame, corner_radius=6)
        self._inv_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._refresh_inventory_ui()

        # ── Onglet Paramètres ────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(tab_set, corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=10, pady=10)

        # Mode USB (FIX 5)
        ctk.CTkLabel(
            scroll, text="Mode de blocage USB",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 5))
        self.usb_mode_var = ctk.StringVar(value=self.config.usb_mode)
        ctk.CTkSegmentedButton(
            scroll, values=["DESKTOP", "LAPTOP"],
            variable=self.usb_mode_var, command=self._on_usb_mode
        ).pack(pady=5)
        ctk.CTkLabel(
            scroll, text="DESKTOP: Stockage USB seul | LAPTOP: Tous les ports USB",
            font=ctk.CTkFont(size=11), text_color="#888"
        ).pack()

        # Mode Sécurité (FIX 4)
        ctk.CTkLabel(
            scroll, text="Mode de sécurité",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 5))
        self.sec_mode_var = ctk.StringVar(value=self.config.sec_mode)
        ctk.CTkSegmentedButton(
            scroll, values=["PEDAGO", "SECURE"],
            variable=self.sec_mode_var, command=self._on_sec_mode
        ).pack(pady=5)
        ctk.CTkLabel(
            scroll, text="PEDAGO: Démo (pas de lock auto) | SECURE: Lock si pas d'owner >10s",
            font=ctk.CTkFont(size=11), text_color="#888"
        ).pack()

        # Détection de périphériques
        ctk.CTkLabel(
            scroll, text="Détection de périphériques",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 5))

        btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(
            btn_frame, text="Tout activer", width=120,
            fg_color="#27ae60", command=self._enable_all
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            btn_frame, text="Tout désactiver", width=120,
            fg_color="#e74c3c", command=self._disable_all
        ).pack(side="left", padx=5)

        det_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        det_frame.pack(fill="x", padx=20, pady=10)

        left_col = ctk.CTkFrame(det_frame, fg_color="transparent")
        left_col.pack(side="left", fill="both", expand=True, padx=10)

        ctk.CTkLabel(
            left_col, text="USB & Stockage",
            font=ctk.CTkFont(size=13, weight="bold"), text_color="#3498db"
        ).pack(anchor="w", pady=(5, 5))
        ctk.CTkSwitch(left_col, text="USB Devices", variable=self.watch_usb,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(left_col, text="HID (souris, clavier)", variable=self.watch_usb_hid,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(left_col, text="Imprimantes", variable=self.watch_printers,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)

        right_col = ctk.CTkFrame(det_frame, fg_color="transparent")
        right_col.pack(side="right", fill="both", expand=True, padx=10)

        ctk.CTkLabel(
            right_col, text="Affichage & Réseau",
            font=ctk.CTkFont(size=13, weight="bold"), text_color="#9b59b6"
        ).pack(anchor="w", pady=(5, 5))
        ctk.CTkSwitch(right_col, text="Moniteurs", variable=self.watch_monitors,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text="Réseau", variable=self.watch_network,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text="Bluetooth", variable=self.watch_bluetooth,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text="Audio", variable=self.watch_audio,
                       command=self._on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text="SSID WiFi (changement réseau)", variable=self._watch_ssid_var,
                       command=self._on_watch_ssid_toggle).pack(anchor="w", pady=3)

        # Seuils
        ctk.CTkLabel(
            scroll, text="Seuils de détection",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 10))

        face_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        face_frame.pack(fill="x", padx=40, pady=5)
        self.face_size_label = ctk.CTkLabel(
            face_frame,
            text=f"Taille min. visage: {int(self.config.min_face_size * 100)}%",
            width=180, anchor="w"
        )
        self.face_size_label.pack(side="left")
        self.face_slider = ctk.CTkSlider(
            face_frame, from_=10, to=40, number_of_steps=30,
            command=self._on_face_size
        )
        self.face_slider.set(self.config.min_face_size * 100)
        self.face_slider.pack(side="right", expand=True, fill="x", padx=(20, 0))

        delay_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        delay_frame.pack(fill="x", padx=40, pady=5)
        self.delay_label = ctk.CTkLabel(
            delay_frame,
            text=f"Délai de lock: {self.config.threat_lock_delay}s",
            width=180, anchor="w"
        )
        self.delay_label.pack(side="left")
        self.delay_slider = ctk.CTkSlider(
            delay_frame, from_=1, to=5, number_of_steps=8,
            command=self._on_delay
        )
        self.delay_slider.set(self.config.threat_lock_delay)
        self.delay_slider.pack(side="right", expand=True, fill="x", padx=(20, 0))

        # Fonctionnalités avancées
        ctk.CTkLabel(
            scroll, text="Fonctionnalités avancées",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 5))
        ctk.CTkSwitch(
            scroll, text="Anti-spoofing (détection de blink)",
            variable=self._anti_spoof_var, command=self._on_anti_spoof_toggle
        ).pack(anchor="w", padx=40, pady=3)
        ctk.CTkSwitch(
            scroll, text="Challenge-response anti-spoof (pose vérification)",
            variable=self._challenge_response_var, command=self._on_challenge_response_toggle
        ).pack(anchor="w", padx=40, pady=3)
        ctk.CTkSwitch(
            scroll, text="Alarme sonore (mode SECURE, intrusion >3s)",
            variable=self._alarm_var, command=self._on_alarm_toggle
        ).pack(anchor="w", padx=40, pady=3)

        # Vague 4 — son personnalisé + volume
        alarm_sound_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        alarm_sound_frame.pack(fill="x", padx=40, pady=(2, 0))
        ctk.CTkLabel(alarm_sound_frame, text="Son (.wav):", width=90, anchor="w").pack(side="left")
        self._alarm_path_var = ctk.StringVar(value=self.config.alarm_sound_path)
        ctk.CTkEntry(
            alarm_sound_frame, textvariable=self._alarm_path_var, width=280,
            placeholder_text="C:\\chemin\\vers\\alarme.wav (vide = bip par défaut)"
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            alarm_sound_frame, text="OK", width=40,
            command=self._save_alarm_sound
        ).pack(side="left", padx=4)

        alarm_vol_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        alarm_vol_frame.pack(fill="x", padx=40, pady=(4, 8))
        self._alarm_vol_label = ctk.CTkLabel(
            alarm_vol_frame, text=f"Volume: {self.config.alarm_volume}%", width=120, anchor="w"
        )
        self._alarm_vol_label.pack(side="left")
        self._alarm_vol_slider = ctk.CTkSlider(
            alarm_vol_frame, from_=0, to=100, number_of_steps=20,
            command=self._on_alarm_volume
        )
        self._alarm_vol_slider.set(self.config.alarm_volume)
        self._alarm_vol_slider.pack(side="left", expand=True, fill="x", padx=(10, 0))
        ctk.CTkSwitch(
            scroll, text="Protection anti-fermeture (mot de passe + watchdog)",
            variable=self._close_protection_var, command=self._on_close_protection_toggle
        ).pack(anchor="w", padx=40, pady=3)
        ctk.CTkButton(
            scroll, text="Changer le mot de passe", width=200, fg_color="#8e44ad",
            command=self._change_password
        ).pack(anchor="w", padx=40, pady=(2, 8))
        ctk.CTkSwitch(
            scroll, text="Mode stealth (démarrer fenêtre masquée — accès via systray)",
            variable=self._stealth_var, command=self._on_stealth_toggle
        ).pack(anchor="w", padx=40, pady=3)

        # Alertes email SMTP (Sprint 2 — Feature 4)
        ctk.CTkLabel(
            scroll, text="Alertes email (CRITICAL)",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 5))
        ctk.CTkSwitch(
            scroll, text="Activer les alertes email pour événements CRITICAL",
            variable=self._email_var, command=self._on_email_toggle
        ).pack(anchor="w", padx=40, pady=3)

        smtp_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        smtp_frame.pack(fill="x", padx=40, pady=5)

        # Ligne 1 : host + port
        row1 = ctk.CTkFrame(smtp_frame, fg_color="transparent")
        row1.pack(fill="x", pady=2)
        ctk.CTkLabel(row1, text="Serveur SMTP:", width=120, anchor="w").pack(side="left")
        self._smtp_host_var = ctk.StringVar(value=self.config.smtp_host)
        ctk.CTkEntry(row1, textvariable=self._smtp_host_var, width=220,
                     placeholder_text="smtp.gmail.com").pack(side="left", padx=5)
        ctk.CTkLabel(row1, text="Port:", width=40, anchor="w").pack(side="left", padx=(10, 0))
        self._smtp_port_var = ctk.StringVar(value=str(self.config.smtp_port))
        ctk.CTkEntry(row1, textvariable=self._smtp_port_var, width=60).pack(side="left", padx=5)

        # Ligne 2 : user + mot de passe
        row2 = ctk.CTkFrame(smtp_frame, fg_color="transparent")
        row2.pack(fill="x", pady=2)
        ctk.CTkLabel(row2, text="Utilisateur:", width=120, anchor="w").pack(side="left")
        self._smtp_user_var = ctk.StringVar(value=self.config.smtp_user)
        ctk.CTkEntry(row2, textvariable=self._smtp_user_var, width=290,
                     placeholder_text="user@gmail.com").pack(side="left", padx=5)

        row3 = ctk.CTkFrame(smtp_frame, fg_color="transparent")
        row3.pack(fill="x", pady=2)
        ctk.CTkLabel(row3, text="Mot de passe:", width=120, anchor="w").pack(side="left")
        self._smtp_pwd_var = ctk.StringVar(value="")
        ctk.CTkEntry(row3, textvariable=self._smtp_pwd_var, width=290, show="*",
                     placeholder_text="(inchangé si vide)").pack(side="left", padx=5)

        # Ligne 3 : destinataire
        row4 = ctk.CTkFrame(smtp_frame, fg_color="transparent")
        row4.pack(fill="x", pady=2)
        ctk.CTkLabel(row4, text="Destinataire:", width=120, anchor="w").pack(side="left")
        self._smtp_rcpt_var = ctk.StringVar(value=self.config.smtp_recipient)
        ctk.CTkEntry(row4, textvariable=self._smtp_rcpt_var, width=290,
                     placeholder_text="alert@example.com").pack(side="left", padx=5)

        ctk.CTkButton(
            scroll, text="Enregistrer config SMTP", width=200, fg_color="#2980b9",
            command=self._save_smtp_config
        ).pack(anchor="w", padx=40, pady=(4, 8))

        # Gestion des utilisateurs (Sprint 2 — multi-utilisateurs)
        ctk.CTkLabel(
            scroll, text="Gestion des utilisateurs",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 5))
        self._users_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._users_frame.pack(fill="x", padx=40, pady=5)
        self._refresh_users_ui()
        ctk.CTkButton(
            scroll, text="Ajouter un utilisateur", width=180, fg_color="#27ae60",
            command=self._add_user
        ).pack(anchor="w", padx=40, pady=(2, 10))

        # Chiffrement AES-256 des encodings (Sprint 2 — Feature 5)
        ctk.CTkLabel(
            scroll, text="Chiffrement des encodings",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(20, 5))
        ctk.CTkSwitch(
            scroll,
            text="Chiffrer les encodings (AES-256-GCM, clé liée à cette machine)",
            variable=self._encrypt_var, command=self._on_encrypt_toggle
        ).pack(anchor="w", padx=40, pady=3)

        # Bouton re-enrollment (ré-enregistre l'owner)
        ctk.CTkButton(
            scroll, text="Ré-enregistrer le visage (owner)",
            fg_color="#e74c3c", command=self._reenroll
        ).pack(pady=20)

        # ── Footer ───────────────────────────────────────────────────
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
        self.stop_alarm_btn = ctk.CTkButton(
            ftr, text="Stop alarme", width=90, fg_color="#8e44ad",
            command=self._stop_alarm
        )
        self.stop_alarm_btn.pack(side="right", padx=5, pady=10)
        self.stop_alarm_btn.pack_forget()  # Masqué par défaut
        self.pause_btn = ctk.CTkButton(
            ftr, text="PAUSE", width=70, fg_color="#f39c12",
            command=self._toggle_pause
        )
        self.pause_btn.pack(side="right", padx=5, pady=10)
        ctk.CTkButton(
            ftr, text="UNBLOCK", width=80, fg_color="#3498db",
            command=self._unblock_action
        ).pack(side="right", padx=5, pady=10)

    # ── Callbacks Settings ────────────────────────────────────────────

    def _on_usb_mode(self, v):
        """FIX 5 — Change et sauvegarde le mode USB."""
        self.config.update(usb_mode=v)
        self.locker.usb_mode = v
        self.mode_lbl.configure(text=f"{v} | {self.config.sec_mode}")
        logger.mode(f"Mode USB → {v}")
        _edb.log_event("MODE_CHANGED", {"mode": "USB", "value": v})

    def _on_sec_mode(self, v):
        """FIX 4 — Change et sauvegarde le mode sécurité."""
        self.config.update(sec_mode=v)
        self.state_machine.sec_mode = v
        self.mode_lbl.configure(text=f"{self.config.usb_mode} | {v}")
        logger.mode(f"Mode sécurité → {v}")
        _edb.log_event("MODE_CHANGED", {"mode": "SECURITY", "value": v})
        # Réappliquer la protection anti-capture après changement de mode (désactivé)
        # self.after(50, self._apply_capture_protection)

    def _apply_capture_protection(self):
        """Applique la protection anti-capture d'écran (WDA_EXCLUDEFROMCAPTURE)."""
        try:
            set_window_capture_protection(self.winfo_id())
        except Exception as exc:
            logger.error(f"Anti-capture: erreur SetWindowDisplayAffinity — {exc}")

    def _check_force_password_change(self):
        """Ouvre le dialog de changement forcé si le mot de passe est encore par défaut."""
        try:
            stored = self.config.close_protection_password_hash
            is_default = verify_password("0000", stored)
            if not self.config.password_needs_change and not is_default:
                return
            _ForcePasswordChangeDialog(self)
        except Exception as exc:
            logger.error(f"Force password change check: erreur — {exc}")

    def _on_face_size(self, v):
        self.face_analyzer.min_face_size = v / 100
        self.config.update(min_face_size=v / 100)
        self.face_size_label.configure(text=f"Taille min. visage: {int(v)}%")

    def _on_delay(self, v):
        self.state_machine.threat_lock_delay = v
        self.config.update(threat_lock_delay=v)
        self.delay_label.configure(text=f"Délai de lock: {v:.1f}s")

    def _on_anti_spoof_toggle(self):
        """Active/désactive l'anti-spoofing et réinitialise l'état."""
        enabled = self._anti_spoof_var.get()
        self.config.update(anti_spoof_enabled=enabled)
        self._anti_spoof.reset()
        if not enabled:
            self.after(0, lambda: self.spoof_lbl.configure(text=""))
        logger.toggle(f"Anti-spoofing {'ACTIVÉ' if enabled else 'DÉSACTIVÉ'}")

    def _on_alarm_toggle(self):
        """Active/désactive l'alarme sonore."""
        enabled = self._alarm_var.get()
        self.config.update(sound_alarm_enabled=enabled)
        if not enabled:
            self._stop_alarm()
        logger.toggle(f"Alarme sonore {'ACTIVÉE' if enabled else 'DÉSACTIVÉE'}")

    def _on_close_protection_toggle(self):
        """Active/désactive la protection anti-fermeture."""
        enabled = self._close_protection_var.get()
        self.config.update(close_protection_enabled=enabled)
        logger.toggle(f"Protection anti-fermeture {'ACTIVÉE' if enabled else 'DÉSACTIVÉE'}")

    def _on_stealth_toggle(self):
        """Active/désactive le mode stealth (fenêtre masquée au démarrage)."""
        enabled = self._stealth_var.get()
        self.config.update(stealth_mode=enabled)
        logger.toggle(f"Mode stealth {'ACTIVÉ' if enabled else 'DÉSACTIVÉ'}")

    def _on_email_toggle(self):
        """Active/désactive les alertes email CRITICAL."""
        enabled = self._email_var.get()
        self.config.update(email_enabled=enabled)
        logger.toggle(f"Alertes email {'ACTIVÉES' if enabled else 'DÉSACTIVÉES'}")

    def _save_smtp_config(self):
        """Sauvegarde la configuration SMTP dans config.json."""
        host = self._smtp_host_var.get().strip()
        user = self._smtp_user_var.get().strip()
        rcpt = self._smtp_rcpt_var.get().strip()
        plain_pwd = self._smtp_pwd_var.get()

        # Conserver l'ancien mot de passe si le champ est vide
        pwd_b64 = (
            EmailAlerter.encode_password(plain_pwd)
            if plain_pwd
            else self.config.smtp_password_b64
        )

        try:
            port = int(self._smtp_port_var.get())
        except ValueError:
            port = 587

        self.config.update(
            smtp_host=host,
            smtp_port=port,
            smtp_user=user,
            smtp_password_b64=pwd_b64,
            smtp_recipient=rcpt,
        )
        self._email_alerter.reconfigure(host, port, user, pwd_b64, rcpt)
        self._smtp_pwd_var.set("")  # Vider le champ mot de passe après sauvegarde
        logger.toggle("Configuration SMTP sauvegardée")

    def _on_encrypt_toggle(self):
        """Active/désactive le chiffrement AES-256 des encodings à la volée."""
        import io
        enabled = self._encrypt_var.get()
        path = self.config.encodings_path

        if not os.path.exists(path):
            self.config.update(encryption_enabled=enabled)
            return

        try:
            raw = open(path, "rb").read()
            if enabled and not is_encrypted(raw):
                # Chiffrer le fichier existant
                raw = encrypt_encodings(raw)
                with open(path, "wb") as f:
                    f.write(raw)
                logger.toggle("Encodings chiffrés (AES-256-GCM)")
            elif not enabled and is_encrypted(raw):
                # Déchiffrer le fichier existant
                raw = decrypt_encodings(raw)
                with open(path, "wb") as f:
                    f.write(raw)
                logger.toggle("Encodings déchiffrés")
        except Exception as exc:
            logger.error(f"Erreur chiffrement/déchiffrement: {exc}")
            # Remettre le switch à son état précédent
            self._encrypt_var.set(not enabled)
            return

        self.config.update(encryption_enabled=enabled)
        _edb.log_event("ENCRYPTION_TOGGLED", {"enabled": enabled})

    def _change_password(self):
        """Dialogue pour changer le mot de passe de protection."""
        dialog = ctk.CTkInputDialog(
            text="Nouveau mot de passe (min 8 chars, 1 majuscule, 1 chiffre):",
            title="Changer le mot de passe"
        )
        new_pwd = dialog.get_input()
        if new_pwd is None:
            return
        # Validation
        if (len(new_pwd) < 8
                or not any(c.isupper() for c in new_pwd)
                or not any(c.isdigit() for c in new_pwd)):
            logger.info("Mot de passe invalide — min 8 chars, 1 majuscule, 1 chiffre")
            return
        self.config.update(
            close_protection_password_hash=hash_password(new_pwd),
            password_needs_change=False,
        )
        _audit.log_event("PASSWORD_CHANGED", {"forced": False})
        logger.toggle("Mot de passe de protection modifié")

    def _log_intrusion_event(self, event):
        """Affiche un résumé d'intrusion dans le log GUI."""
        crit_colors = {
            Criticality.INFO:     "[INFO]",
            Criticality.WARNING:  "[WARNING]",
            Criticality.CRITICAL: "[CRITICAL]",
        }
        prefix = crit_colors.get(event.criticality, "[?]")
        msg = (f"{prefix} Intrusion {event.intrusion_type.value} "
               f"— {event.duration:.1f}s")
        if event.devices_plugged:
            msg += f" | devices: {', '.join(event.devices_plugged)}"
        if event.spoof_detected:
            msg += " | SPOOF"
        if event.pending_email and self.config.email_enabled:
            sent = self._email_alerter.send_critical_alert(event)
            msg += " | EMAIL" if sent else " | EMAIL (rate-limit)"
        logger.error(msg) if event.criticality != Criticality.INFO else logger.info(msg)

    def _start_alarm(self):
        """Démarre l'alarme sonore dans un thread daemon."""
        if self._alarm_active:
            return
        self._alarm_active = True
        self.after(0, lambda: self.stop_alarm_btn.pack(side="right", padx=5, pady=10))
        logger.lock("Alarme sonore déclenchée — intrusion SECURE >3s")
        _edb.log_event("ALARM_START", {})
        self._alarm_thread = threading.Thread(target=self._alarm_loop, daemon=True)
        self._alarm_thread.start()

    def _stop_alarm(self):
        """Arrête l'alarme sonore et réinitialise le timer de menace."""
        if not self._alarm_active:
            return
        self._alarm_active = False
        self._threat_start = None
        self.after(0, lambda: self.stop_alarm_btn.pack_forget())
        logger.toggle("Alarme sonore arrêtée")
        _edb.log_event("ALARM_STOP", {})

    def _on_toggle_change(self):
        """Met à jour les toggles + cooldown 5s."""
        self.locker.set_device_cooldown(5.0)
        self._sync_toggles()
        self.poll_watcher.reset_baselines()
        # Sauvegarder dans la config
        self.config.update(
            watch_usb=self.watch_usb.get(),
            watch_usb_hid=self.watch_usb_hid.get(),
            watch_monitors=self.watch_monitors.get(),
            watch_network=self.watch_network.get(),
            watch_printers=self.watch_printers.get(),
            watch_bluetooth=self.watch_bluetooth.get(),
            watch_audio=self.watch_audio.get(),
        )
        logger.toggle("Paramètres de détection mis à jour (cooldown 5s)")

    def _sync_toggles(self):
        """Synchronise les toggles GUI → watchers."""
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
        logger.toggle("TOUTE la détection ACTIVÉE (cooldown 5s)")

    def _disable_all(self):
        for var in [self.watch_usb, self.watch_usb_hid, self.watch_monitors,
                    self.watch_network, self.watch_printers, self.watch_bluetooth,
                    self.watch_audio]:
            var.set(False)
        self._sync_toggles()
        logger.toggle("TOUTE la détection DÉSACTIVÉE")

    # ── Device arrival (FIX 7) ────────────────────────────────────────

    def _on_device(self, device_type: str, device_info: str = ""):
        """
        FIX 7 — Quand un device est détecté : bloquer d'abord, puis notification.
        """
        # Vérifier cooldowns et toggles
        if self.locker.device_cooldown_active:
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

        # Vague 4 — vérifier inventaire AVANT alerte (périphérique autorisé = silencieux)
        entry = _inventory.add_or_update(device_type, device_info)
        self.after(0, self._refresh_inventory_ui)
        if entry.authorized:
            logger.info(f"Périphérique autorisé ignoré: {device_type} — {device_info}")
            _edb.log_event("USB_CONNECTED", {"type": device_type, "info": device_info, "authorized": True})
            return
        # Périphérique inconnu — alerte même en pause (sécurité)

        # FIX 7 — Bloquer immédiatement selon le type
        if device_type == "Bluetooth":
            block_bluetooth()
        elif device_type == "Network":
            block_network()

        logger.device(f"Connexion {device_type} détectée : {device_info} — EN ATTENTE")
        _edb.log_event("USB_CONNECTED", {"type": device_type, "info": device_info})
        # Notifier le rapport d'intrusion si une intrusion est en cours
        if self._intrusion_active:
            self._reporter.update_current(device=f"{device_type}:{device_info}")

        # Afficher la notification sur le main thread
        self.after(0, lambda: self._show_device_notification(device_type, device_info))

    def _show_device_notification(self, device_type: str, device_info: str):
        """Affiche la popup d'autorisation."""
        DeviceNotification(
            parent=self,
            device_type=device_type,
            device_info=device_info or device_type,
            on_allow=lambda: self._device_allowed(device_type),
            on_block=lambda: self._device_blocked(device_type),
        )

    def _device_allowed(self, device_type: str):
        """L'utilisateur a autorisé le device."""
        if device_type == "Bluetooth":
            unblock_bluetooth()
        elif device_type == "Network":
            unblock_network()

        self.locker.set_device_cooldown(10.0)
        self.poll_watcher.reset_baselines()
        logger.device(f"{device_type} AUTORISÉ par l'utilisateur")

    def _device_blocked(self, device_type: str):
        """L'utilisateur a bloqué (ou timeout)."""
        logger.device(f"{device_type} BLOQUÉ — verrouillage du poste")
        self.locker.set_device_cooldown(10.0)
        self.locker.do_lock(f"Nouveau {device_type} bloqué")
        _edb.log_event("LOCK_TRIGGERED", {"reason": f"Nouveau {device_type} bloqué"})
        self._update_usb_label()

    def _update_usb_label(self):
        if self._closing:
            return
        if self.locker.usb_blocked:
            self.usb_lbl.configure(text="USB: BLOQUÉ", text_color="#e74c3c")
        else:
            self.usb_lbl.configure(text="USB: OK", text_color="#2ecc71")

    # ── Camera loop (FIX 6) ───────────────────────────────────────────

    def _camera_loop(self):
        """Boucle caméra avec frame skip pour vidéo fluide."""
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while self.running and not self._closing:
            # Afficher les cooldowns dans le footer
            self._update_cooldowns()

            # FIX 3 — Pause complète
            if self.paused:
                # Vague 4 — auto-resume si durée écoulée
                if self._pause_until > 0 and time.time() >= self._pause_until:
                    self.after(0, self._resume_pause)
                time.sleep(0.1)
                continue

            ret, frame = self.cap.read()

            if not ret or frame is None:
                # Caméra perdue
                self.after(0, lambda: self.cam_lbl.configure(
                    text="CAM: PERDUE", text_color="#e74c3c"
                ))
                result = self.state_machine.on_camera_lost(self.locker.can_lock())
                if result["should_lock"]:
                    self.locker.do_lock(result["lock_reason"])
                    self.after(0, self._update_usb_label)
                    with self._cap_lock:
                        self.cap.release()
                        self.cap = None
                    time.sleep(2)
                    with self._cap_lock:
                        if self.running and not self._closing:
                            self.cap = cv2.VideoCapture(0)
                time.sleep(0.1)
                continue

            self.state_machine.on_camera_ok()
            self.after(0, lambda: self.cam_lbl.configure(
                text="CAM: OK", text_color="#2ecc71"
            ))

            # Vague 4 — compteur FPS (fenêtre 3s)
            self._fps_counter += 1
            _fps_now = time.time()
            if _fps_now - self._fps_last_time >= 3.0:
                self._fps_current = self._fps_counter / (_fps_now - self._fps_last_time)
                self._fps_counter = 0
                self._fps_last_time = _fps_now

            # FIX 6 + Vague 3 — Analyse 1 frame sur N avec motion pre-filter
            if self.face_analyzer.is_next_analysis_frame():
                self._motion_skip_total += 1
                if self.motion_detector.should_analyze(frame):
                    analysis = self.face_analyzer.process_frame(frame)
                else:
                    self._motion_skip_skipped += 1
                    self.face_analyzer.tick()
                    analysis = None
            else:
                analysis = self.face_analyzer.process_frame(frame)

            if analysis is not None:
                # Frame d'analyse — mettre à jour la state machine
                situation = self.face_analyzer.get_situation()
                result = self.state_machine.update(situation, self.locker.can_lock())

                # Vague 3 — Transitions d'état faciale (events_db)
                if situation["owner"] and not situation["threat"]:
                    _new_face = "OWNER"
                elif situation["threat"]:
                    _new_face = "UNKNOWN"
                elif not self.face_analyzer.last_results:
                    _new_face = "NONE"
                else:
                    _new_face = "PASSING"
                if _new_face != self._last_face_state:
                    _edb.log_event(f"FACE_{_new_face}", {
                        "prev": self._last_face_state,
                        "face_count": situation["face_count"],
                    })
                    self._last_face_state = _new_face
                self.motion_detector.set_owner_safe(_new_face == "OWNER")

                # Afficher info visage
                self.after(0, lambda i=situation["info"]: self.face_info.configure(text=i))

                # Mettre à jour le statut
                state = result["state"]
                color = STATE_COLORS.get(state, "#888")
                self.after(0, lambda s=state, c=color: self.status_label.configure(
                    text=s, text_color=c
                ))
                self.after(0, lambda t=result["countdown"]: self.countdown_label.configure(text=t))
                # Mettre à jour la couleur du systray
                self._systray.update_state(state)

                # Vérifier si on doit locker
                if result["should_lock"]:
                    self.locker.do_lock(result["lock_reason"])
                    _edb.log_event("LOCK_TRIGGERED", {"reason": result["lock_reason"]})
                    self.after(0, self._update_usb_label)
                    time.sleep(2)
                    continue

                # Owner reconnu → débloquer USB
                if situation["owner"] and self.locker.usb_blocked:
                    self.locker.do_unlock()
                    self.locker.set_device_cooldown(5.0)
                    self.poll_watcher.reset_baselines()
                    self.after(0, self._update_usb_label)

                # Anti-spoofing : analyser uniquement si activé et owner présent
                if self._anti_spoof_var.get():
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    owner_faces = [r for r in self.face_analyzer.last_results if r.is_owner]
                    if owner_faces:
                        spoof_result = self._anti_spoof.update(rgb, owner_faces[0].location)
                        if spoof_result["spoof_suspect"]:
                            spoof_txt = f"SPOOF? EAR:{spoof_result['ear']}"
                            self.after(0, lambda t=spoof_txt: self.spoof_lbl.configure(
                                text=t, text_color="#e74c3c"
                            ))
                            logger.face(f"Anti-spoof: suspect (EAR={spoof_result['ear']}, blinks={spoof_result['blink_count']})")
                            # Vague 5 — challenge-response si activé
                            if (self._challenge_response_var.get()
                                    and not self._challenge_response.is_active()):
                                triggered = self._challenge_response.update_ear(
                                    spoof_result.get("ear_left", 0.3),
                                    spoof_result.get("ear_right", 0.3),
                                )
                                if triggered:
                                    self._challenge_response.start_challenge()
                                    logger.face("Challenge-response démarré")
                        else:
                            spoof_txt = f"LIVE blinks:{spoof_result['blink_count']}"
                            self.after(0, lambda t=spoof_txt: self.spoof_lbl.configure(
                                text=t, text_color="#2ecc71"
                            ))

                        # Vague 5 — valider le challenge si actif
                        if self._challenge_response.is_active():
                            lm_list = face_recognition.face_landmarks(
                                cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                                [owner_faces[0].location]
                            )
                            if lm_list:
                                passed = self._challenge_response.validate_pose(lm_list[0])
                                if passed:
                                    logger.face("Challenge-response validé — owner confirmé LIVE")
                                    self._anti_spoof.reset()
                            if self._challenge_response.is_failed():
                                logger.face("Challenge-response ÉCHOUÉ — spoof probable")
                                _edb.log_event("SPOOF_CHALLENGE_FAILED", {})
                                self._challenge_response.reset()
                    else:
                        # Owner perdu → reset état blink et challenge
                        self._anti_spoof.reset()
                        self._challenge_response.reset()
                        self.after(0, lambda: self.spoof_lbl.configure(text=""))
                else:
                    self.after(0, lambda: self.spoof_lbl.configure(text=""))

                # Alarme sonore : SECURE + activée + THREAT depuis >3s
                if result["state"] == "THREAT":
                    if self._threat_start is None:
                        self._threat_start = time.time()
                    elapsed = time.time() - self._threat_start
                    if (self._alarm_var.get()
                            and self.config.sec_mode == "SECURE"
                            and not self._alarm_active
                            and elapsed >= 3.0):
                        self._start_alarm()
                else:
                    self._threat_start = None
                    if self._alarm_active:
                        self._stop_alarm()

                # Rapport d'intrusion
                intrusion_states = (State.THREAT, State.SURFER)
                if result["state"] in intrusion_states:
                    itype = (IntrusionType.UNKNOWN_FACE if result["state"] == State.THREAT
                             else IntrusionType.SHOULDER_SURF)
                    if not self._intrusion_active:
                        self._reporter.start_intrusion(itype)
                        self._intrusion_active = True
                    else:
                        # Mise à jour distance + spoof
                        threats = [r for r in self.face_analyzer.last_results if not r.is_owner]
                        dist = min((r.distance for r in threats), default=None)
                        spoof = (self._anti_spoof_var.get()
                                 and self._anti_spoof.spoof_suspect)
                        if result["should_lock"]:
                            self._reporter.update_current(
                                face_distance=dist, spoof=spoof, action="LOCK"
                            )
                        else:
                            self._reporter.update_current(face_distance=dist, spoof=spoof)
                else:
                    if self._intrusion_active:
                        self._intrusion_active = False
                        event = self._reporter.end_intrusion()
                        if event:
                            self.after(0, lambda e=event: self._log_intrusion_event(e))

            # Dessiner les rectangles (résultats en cache) et afficher
            frame = self.face_analyzer.draw_on_frame(frame)
            # Vague 5 — overlay challenge-response
            if self._challenge_response.is_active():
                instruction = self._challenge_response.get_instruction()
                cv2.putText(
                    frame, instruction,
                    (10, frame.shape[0] - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2, cv2.LINE_AA,
                )
            self._display_frame(frame)

            time.sleep(0.03)

        with self._cap_lock:
            if self.cap:
                self.cap.release()
                self.cap = None

    def _display_frame(self, frame):
        """Affiche un frame dans la GUI (FIX 6 — tous les frames)."""
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        # FIX 9 — Redimensionner proportionnellement au conteneur
        try:
            container_w = self.cam_label.winfo_width()
            container_h = self.cam_label.winfo_height()
            if container_w > 1 and container_h > 1:
                # Garder le ratio 4:3
                ratio = min(container_w / 640, container_h / 480)
                new_w = int(640 * ratio)
                new_h = int(480 * ratio)
                img = img.resize((new_w, new_h))
        except Exception:
            img = img.resize((640, 480))

        # FIX 3 — CTkImage évite le warning PIL.ImageTk.PhotoImage
        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        self.after(0, lambda i=ctk_img: self.cam_label.configure(image=i, text=""))

    def _update_cooldowns(self):
        """Met à jour l'affichage des cooldowns dans le footer."""
        lock_cd = self.locker.get_cooldown_remaining()
        dev_cd = self.locker.get_device_cooldown_remaining()

        if lock_cd > 0:
            text = f"Lock CD: {lock_cd:.1f}s"
        elif dev_cd > 0:
            text = f"Dev CD: {dev_cd:.1f}s"
        else:
            text = ""

        self.after(0, lambda t=text: self.cd_lbl.configure(text=t))

    # ── Keyboard (FIX 3) — DÉSACTIVÉ ─────────────────────────────────
    # Raccourcis P/L/U/Q désactivés : interfèrent avec la saisie normale.
    # Réactiver en décommentant le thread dans __init__ et cette méthode.

    # def _keyboard_listener(self):
    #     """FIX 3 — En pause, seul P (toggle pause) reste actif."""
    #     import keyboard
    #     while self.running:
    #         if keyboard.is_pressed("p"):
    #             self.after(0, self._toggle_pause)
    #             time.sleep(0.3)
    #         if not self.paused:
    #             if keyboard.is_pressed("l"):
    #                 self.after(0, lambda: self.locker.do_lock("Manuel (clavier)"))
    #                 time.sleep(0.5)
    #             if keyboard.is_pressed("u"):
    #                 self.after(0, self._unblock_action)
    #                 time.sleep(0.3)
    #             if keyboard.is_pressed("q"):
    #                 self.after(0, self._on_close_request)
    #                 time.sleep(0.5)
    #         time.sleep(0.05)

    # ── Actions ───────────────────────────────────────────────────────

    def _toggle_pause(self):
        """Vague 4 — Pause/Resume avec durée temporisée."""
        if self.paused or time.time() < self._pause_until:
            self._resume_pause()
            return
        self._show_pause_dialog()

    def _show_pause_dialog(self):
        _PauseDurationDialog(self, callback=self._activate_pause)

    def _activate_pause(self, duration_seconds: float):
        """Active la pause — les watchers continuent (périphériques inconnus alertent toujours)."""
        self.paused = True
        # poll_watcher + usb_watcher NON pausés — sécurité : périphériques inconnus alertent même en pause
        self._pause_until = time.time() + duration_seconds if duration_seconds > 0 else 0.0
        self.pause_btn.configure(text="RESUME", fg_color="#27ae60")
        dur_str = f"{int(duration_seconds)}s" if duration_seconds > 0 else "manuel"
        logger.info(f"⏸ En pause ({dur_str})")
        _edb.log_event("PAUSE_STARTED", {"duration": duration_seconds})
        _audit.log_event("PAUSE_STARTED", {"duration": duration_seconds})

    def _resume_pause(self):
        """Reprend l'activité normale."""
        self.paused = False
        self._pause_until = 0.0
        self.pause_btn.configure(text="PAUSE", fg_color="#f39c12")
        logger.info("▶ Reprise")
        _edb.log_event("PAUSE_ENDED", {})
        _audit.log_event("PAUSE_ENDED", {})

    def _unblock_action(self):
        """Débloque USB manuellement."""
        self.locker.do_unlock()
        _edb.log_event("UNLOCK", {"source": "manual"})
        self.locker.set_device_cooldown(5.0)
        self.poll_watcher.reset_baselines()
        self._update_usb_label()
        logger.unlock("USB débloqué manuellement (cooldown 5s)")

    def _reenroll(self):
        """Relance l'enrollment pour l'owner."""
        self._do_enrollment("owner")

    def _do_enrollment(self, username: str):
        """Pause caméra → enrollment CTkToplevel → reload utilisateurs."""
        from src.enrollment import EnrollmentWindow

        self.running = False  # Arrêter la boucle caméra

        def _on_success():
            self._reload_users()
            self.running = True
            threading.Thread(target=self._camera_loop, daemon=True).start()

        def _on_cancel():
            self.running = True
            threading.Thread(target=self._camera_loop, daemon=True).start()

        # Ouvrir après 200ms (laisse le thread caméra libérer VideoCapture)
        self.after(200, lambda: EnrollmentWindow(
            parent=self,
            encodings_path=self.config.encodings_path,
            username=username,
            encrypt_enabled=self.config.encryption_enabled,
            on_success=_on_success,
            on_cancel=_on_cancel,
        ))

    def _reload_users(self):
        """Recharge les utilisateurs autorisés depuis le fichier d'encodings."""
        try:
            raw = open(self.config.encodings_path, "rb").read()
            if is_encrypted(raw):
                raw = decrypt_encodings(raw)
            self.authorized_users = load_authorized_users_from_bytes(raw)
        except Exception:
            pass
        self.face_analyzer = FaceAnalyzer(
            authorized_users=self.authorized_users,
            tolerance=self.config.face_tolerance,
            min_face_size=self.config.min_face_size,
            center_threshold=self.config.center_threshold,
            analyze_every_n=self.config.analyze_every_n_frames,
            detection_scale=self.config.detection_scale,
        )
        self.motion_detector.reset()
        self._refresh_users_ui()
        total_enc = sum(len(v) for v in self.authorized_users.values())
        logger.info(
            f"Utilisateurs rechargés — {total_enc} visages, "
            f"{list(self.authorized_users.keys())}"
        )

    def _refresh_users_ui(self):
        """Reconstruit la liste des utilisateurs dans l'onglet Paramètres."""
        for widget in self._users_frame.winfo_children():
            widget.destroy()
        for uname, encs in self.authorized_users.items():
            row = ctk.CTkFrame(self._users_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row, text=f"{uname}  ({len(encs)} photo(s))", anchor="w"
            ).pack(side="left")
            if len(self.authorized_users) > 1:
                ctk.CTkButton(
                    row, text="Supprimer", width=80, fg_color="#e74c3c",
                    command=lambda n=uname: self._remove_user(n)
                ).pack(side="right")

    def _add_user(self):
        """Invite à entrer un nom puis lance l'enrollment pour ce nouvel utilisateur."""
        dialog = ctk.CTkInputDialog(
            text="Nom du nouvel utilisateur:",
            title="Ajouter un utilisateur"
        )
        username = dialog.get_input()
        if not username or not username.strip():
            return
        username = username.strip().lower().replace(" ", "_")
        self._do_enrollment(username)

    def _remove_user(self, name: str):
        """Supprime un utilisateur des encodings sauvegardés."""
        if name not in self.authorized_users:
            return
        del self.authorized_users[name]
        save_authorized_users(self.config.encodings_path, self.authorized_users)
        # Recréer le FaceAnalyzer avec les utilisateurs mis à jour
        self.face_analyzer = FaceAnalyzer(
            authorized_users=self.authorized_users,
            tolerance=self.config.face_tolerance,
            min_face_size=self.config.min_face_size,
            center_threshold=self.config.center_threshold,
            analyze_every_n=self.config.analyze_every_n_frames,
            detection_scale=self.config.detection_scale,
        )
        self._refresh_users_ui()
        logger.info(f"Utilisateur '{name}' supprimé")

    def _log_to_gui(self, formatted_msg: str):
        """Callback du logger → dispatch sur le thread GUI (thread-safe)."""
        if not self._closing:
            self.after(0, lambda m=formatted_msg: self._insert_log(m))

    def _insert_log(self, msg: str):
        try:
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
        except Exception:
            pass

    def _on_close_request(self):
        """Fermeture demandée — vérifie le mot de passe si protection activée."""
        if not self._close_protection_var.get():
            self._on_close()
            return
        dialog = ctk.CTkInputDialog(
            text="Mot de passe requis pour fermer PrankGuard:",
            title="Protection anti-fermeture"
        )
        password = dialog.get_input()
        if password is None:
            return
        stored = self.config.close_protection_password_hash
        if verify_password(password, stored):
            # Migration transparente SHA-256 → argon2id si nécessaire
            if needs_rehash(stored):
                self.config.update(
                    close_protection_password_hash=hash_password(password),
                    password_needs_change=False,
                )
            self._on_close()
        else:
            logger.info("Protection anti-fermeture: mot de passe incorrect")

    def _toggle_window(self):
        """Bascule la visibilité de la fenêtre principale (systray double-clic)."""
        if self.winfo_viewable():
            self.withdraw()
        else:
            self.deiconify()
            self.lift()
            self.focus_force()

    # ── Alarme sonore personnalisée (Vague 4) ────────────────────────

    def _save_alarm_sound(self):
        path = self._alarm_path_var.get().strip()
        self.config.update(alarm_sound_path=path)
        logger.toggle(f"Son alarme → {'bip par défaut' if not path else path}")

    def _on_alarm_volume(self, v):
        vol = int(v)
        self.config.update(alarm_volume=vol)
        self._alarm_vol_label.configure(text=f"Volume: {vol}%")

    def _alarm_loop(self):
        """Boucle alarme — .wav personnalisé ou bip 2500 Hz."""
        path = self.config.alarm_sound_path
        while self._alarm_active:
            try:
                if path and os.path.isfile(path) and path.lower().endswith(".wav"):
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    time.sleep(2.0)
                else:
                    winsound.Beep(2500, 500)
            except Exception:
                pass
            time.sleep(0.1)

    # ── Dashboard (Vague 4) ──────────────────────────────────────────

    def _update_dashboard(self):
        """Met à jour les stats dashboard toutes les 3s."""
        if self._closing:
            return
        try:
            self.dash_fps_lbl.configure(text=f"FPS: {self._fps_current:.1f}")
            skip_pct = (
                int(self._motion_skip_skipped / self._motion_skip_total * 100)
                if self._motion_skip_total > 0 else 0
            )
            self.dash_skip_lbl.configure(text=f"Skip: {skip_pct}%")
            midnight = time.mktime(
                datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timetuple()
            )
            evts = _edb.get_events(since=midnight, limit=500)
            self.dash_events_lbl.configure(text=f"Evt: {len(evts)}")
        except Exception:
            pass
        self.after(3000, self._update_dashboard)

    # ── Inventaire (Vague 4) ─────────────────────────────────────────

    def _refresh_inventory_ui(self):
        """Reconstruit la liste des périphériques dans l'onglet Inventaire."""
        if self._closing:
            return
        try:
            for widget in self._inv_scroll.winfo_children():
                widget.destroy()
            entries = _inventory.get_all()
            if not entries:
                ctk.CTkLabel(
                    self._inv_scroll, text="Aucun périphérique enregistré",
                    text_color="#888"
                ).pack(pady=20)
                return
            for entry in sorted(entries, key=lambda e: e.last_seen, reverse=True):
                row = ctk.CTkFrame(self._inv_scroll, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkLabel(
                    row, text=entry.category, width=120, anchor="w",
                    font=ctk.CTkFont(size=12)
                ).pack(side="left", padx=6)
                ctk.CTkLabel(
                    row, text=entry.name[:50], width=280, anchor="w",
                    font=ctk.CTkFont(size=12)
                ).pack(side="left", padx=6)
                status_color = "#2ecc71" if entry.authorized else "#e74c3c"
                status_text = "Autorisé" if entry.authorized else "Inconnu"
                ctk.CTkLabel(
                    row, text=status_text, width=90, anchor="w",
                    font=ctk.CTkFont(size=12), text_color=status_color
                ).pack(side="left", padx=6)
                if entry.authorized:
                    ctk.CTkButton(
                        row, text="Bloquer", width=80, fg_color="#e74c3c",
                        command=lambda did=entry.device_id: self._inv_block(did)
                    ).pack(side="left", padx=6)
                else:
                    ctk.CTkButton(
                        row, text="Autoriser", width=80, fg_color="#27ae60",
                        command=lambda did=entry.device_id: self._inv_authorize(did)
                    ).pack(side="left", padx=6)
        except Exception:
            pass

    def _inv_authorize(self, device_id: str):
        _inventory.authorize(device_id)
        self._refresh_inventory_ui()

    def _inv_block(self, device_id: str):
        _inventory.block(device_id)
        self._refresh_inventory_ui()

    def _on_close(self):
        """Fermeture propre de l'application."""
        _audit.log_event("APP_STOP", {})
        self._closing = True
        self.running = False
        self._alarm_active = False  # Arrêter l'alarme si active
        self._systray.stop()  # Arrêter l'icône systray
        # Clôturer l'intrusion en cours si applicable
        if self._intrusion_active:
            self._intrusion_active = False
            self._reporter.end_intrusion()
        # Écrire le flag watchdog avant la destruction
        try:
            with open(str(paths.SHUTDOWN_FLAG), "w") as f:
                f.write("ok")
        except Exception:
            pass
        self.usb_watcher.stop()
        self.poll_watcher.stop()
        self._ssid_monitor.stop()
        self._watchdog.stop()
        self.locker.do_unlock()
        self.destroy()

    # ── Vague 5 — méthodes ───────────────────────────────────────────

    def _on_challenge_response_toggle(self):
        """Active/désactive le challenge-response anti-spoof."""
        enabled = self._challenge_response_var.get()
        self.config.update(challenge_response_enabled=enabled)
        if not enabled:
            self._challenge_response.reset()
        logger.toggle(f"Challenge-response {'ACTIVÉ' if enabled else 'DÉSACTIVÉ'}")

    def _on_watch_ssid_toggle(self):
        """Active/désactive la surveillance SSID WiFi."""
        enabled = self._watch_ssid_var.get()
        self.config.update(watch_ssid=enabled)
        if enabled and not self._ssid_monitor._running:
            self._ssid_monitor.start(self, self._on_ssid_change)
        elif not enabled:
            self._ssid_monitor.stop()
        logger.toggle(f"Surveillance SSID {'ACTIVÉE' if enabled else 'DÉSACTIVÉE'}")

    def _on_ssid_change(self, old_ssid, new_ssid):
        """Callback changement de SSID — locker si mode SECURE."""
        logger.info(f"SSID changé : {old_ssid!r} → {new_ssid!r}")
        _edb.log_event("SSID_CHANGED", {"old": old_ssid, "new": new_ssid})
        if self.config.sec_mode == "SECURE" and self.locker.can_lock():
            self.locker.do_lock("Changement de réseau WiFi")
            _edb.log_event("LOCK_TRIGGERED", {"reason": "SSID_CHANGED"})
            self.after(0, self._update_usb_label)

    def _update_stats_section(self):
        """Rafraîchit les compteurs analytics dans l'onglet Logs (toutes les 10s)."""
        if self._closing:
            return
        try:
            stats = _edb.get_stats()
            mapping = {
                "Locks":        stats.get("LOCK_TRIGGERED", 0),
                "Face_Owner":   stats.get("FACE_OWNER", 0),
                "Face_Unknown": stats.get("FACE_UNKNOWN", 0),
                "Alarms":       stats.get("ALARM_START", 0),
                "USB":          stats.get("USB_CONNECTED", 0),
            }
            for key, val in mapping.items():
                lbl = self._stats_labels.get(key)
                if lbl:
                    lbl.configure(text=str(val))
        except Exception:
            pass
        self.after(10000, self._update_stats_section)

    def _export_csv(self):
        """Exporte les 30 derniers jours d'événements en CSV dans APP_DATA/exports/."""
        try:
            ts = datetime.now().strftime("%Y-%m-%d")
            out_path = str(paths.EXPORTS_DIR / f"events_{ts}.csv")
            n = _edb.export_csv(out_path, days=30)
            logger.info(f"Export CSV : {n} événements → {out_path}")
        except Exception as exc:
            logger.error(f"Export CSV: erreur — {exc}")
