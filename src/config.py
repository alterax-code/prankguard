"""
Configuration persistante JSON.
FIX 4 — Modes Pedago/Secure sauvegardés.
FIX 5 — Modes Desktop/Laptop sauvegardés.
Vague 2 — password_needs_change + hash argon2id par défaut.
"""
import json
import os
from dataclasses import dataclass, field, asdict

from src import paths as _paths


@dataclass
class Config:
    """Configuration globale sauvegardée en JSON."""

    # Modes (FIX 4, 5)
    usb_mode: str = "DESKTOP"      # DESKTOP | LAPTOP
    sec_mode: str = "PEDAGO"       # PEDAGO | SECURE

    # Toggles de détection (7 types)
    watch_usb: bool = True
    watch_usb_hid: bool = True
    watch_monitors: bool = False
    watch_network: bool = False
    watch_printers: bool = False
    watch_bluetooth: bool = False
    watch_audio: bool = False

    # Seuils
    face_tolerance: float = 0.45
    min_face_size: float = 0.20
    center_threshold: float = 0.35
    threat_lock_delay: float = 2.0
    no_owner_lock_delay: float = 10.0
    shoulder_grace_period: float = 5.0
    camera_lost_lock_delay: float = 3.0
    lock_cooldown: float = 8.0

    # Analyse vidéo (FIX 6)
    analyze_every_n_frames: int = 3

    # Profil hardware (auto-détecté au démarrage si non configuré explicitement)
    detection_scale: float = 0.33   # Facteur de downscale pour la détection (0.25–0.5)

    # Benchmark hardware (Vague 3 — calibration au premier lancement)
    hardware_benchmarked: bool = False

    # Fonctionnalités avancées
    anti_spoof_enabled: bool = False
    sound_alarm_enabled: bool = False
    close_protection_enabled: bool = False
    close_protection_password_hash: str = ""
    password_needs_change: bool = True   # Vague 2 — forcer changement au premier lancement
    intrusion_log_path: str = field(default_factory=lambda: str(_paths.INTRUSION_LOG))

    # Mode stealth (Sprint 2 — Feature 2)
    stealth_mode: bool = False

    # Chiffrement AES-256 des encodings (Sprint 2 — Feature 5)
    encryption_enabled: bool = False

    # Alertes email SMTP (Sprint 2 — Feature 4)
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password_b64: str = ""
    smtp_recipient: str = ""

    @property
    def encodings_path(self) -> str:
        """Chemin canonique des encodings — toujours %APPDATA%\\PrankGuard\\users\\authorized_users.npz."""
        return str(_paths.USERS_FILE)

    @classmethod
    def load(cls) -> "Config":
        """Charge la config depuis le fichier JSON, ou crée les défauts."""
        explicit_keys: set = set()
        config_file = str(_paths.CONFIG_FILE)
        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Clés présentes explicitement dans config.json
                explicit_keys = set(data.keys())
                # Merge avec les défauts — filtre les clés inconnues (dont l'ancien encodings_path)
                defaults = asdict(cls())
                defaults.update({k: v for k, v in data.items() if k in cls.__dataclass_fields__})
                obj = cls(**{k: v for k, v in defaults.items() if k in cls.__dataclass_fields__})
            except Exception:
                obj = cls()
        else:
            obj = cls()

        # Validation usb_mode / sec_mode (whitelist)
        if obj.usb_mode not in {"DESKTOP", "LAPTOP"}:
            obj.usb_mode = "DESKTOP"
        if obj.sec_mode not in {"PEDAGO", "SECURE"}:
            obj.sec_mode = "PEDAGO"

        # Clamp face_tolerance entre 0.1 et 0.9
        obj.face_tolerance = max(0.1, min(0.9, float(obj.face_tolerance)))

        # Initialiser le hash du mot de passe par défaut si vide ("0000" → argon2id)
        if not obj.close_protection_password_hash:
            from src.security.hardening import hash_password
            obj.close_protection_password_hash = hash_password("0000")
            obj.password_needs_change = True
        # Hash corrompu (non-argon2) — réinitialiser proprement
        elif not obj.close_protection_password_hash.startswith("$argon2"):
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Config: close_protection_password_hash corrompu (format non-argon2) — "
                "réinitialisation sur '0000'."
            )
            from src.security.hardening import hash_password
            obj.close_protection_password_hash = hash_password("0000")
            obj.password_needs_change = True

        # Auto-profil hardware (si non configuré explicitement dans config.json)
        every_n, scale = cls._hw_profile()
        if "analyze_every_n_frames" not in explicit_keys:
            obj.analyze_every_n_frames = every_n
        if "detection_scale" not in explicit_keys:
            obj.detection_scale = scale

        # Persister immédiatement si le fichier n'existe pas (première exécution)
        # ou si des valeurs par défaut ont été injectées (password hash, etc.)
        try:
            if not os.path.exists(str(_paths.CONFIG_FILE)):
                obj.save()
        except Exception:
            pass

        return obj

    def save(self):
        """Sauvegarde la config en JSON."""
        config_file = str(_paths.CONFIG_FILE)
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    def update(self, **kwargs):
        """Met à jour des champs (dataclass uniquement) et sauvegarde."""
        for k, v in kwargs.items():
            if k in self.__dataclass_fields__:
                setattr(self, k, v)
        self.save()

    @staticmethod
    def _hw_profile() -> tuple:
        """Retourne (analyze_every_n, detection_scale) selon le nombre de CPU logiques."""
        cores = os.cpu_count() or 4
        if cores >= 8:
            return 2, 0.5     # HIGH — 8+ coeurs
        elif cores >= 4:
            return 3, 0.33    # MEDIUM — 4-7 coeurs
        else:
            return 5, 0.25    # LOW — moins de 4 coeurs
