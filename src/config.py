"""
Configuration persistante JSON.
Modes, toggles, seuils, mot de passe.
"""
import json
import os
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".prankguard")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_ENCODINGS = os.path.join("data", "owner_faces", "encodings.pkl")


def _hash_password(password: str) -> str:
    """Hash un mot de passe avec SHA-256."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# Hash du mot de passe par defaut "0000"
DEFAULT_PASSWORD_HASH = _hash_password("0000")


@dataclass
class Config:
    """Configuration globale sauvegardee en JSON."""

    # Chemins
    encodings_path: str = DEFAULT_ENCODINGS

    # Modes
    usb_mode: str = "DESKTOP"
    sec_mode: str = "PEDAGO"

    # Toggles de detection (7 types)
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

    # Mot de passe (hash SHA-256)
    password_hash: str = DEFAULT_PASSWORD_HASH
    password_changed: bool = False  # True si l'utilisateur a change le mdp

    @classmethod
    def load(cls) -> "Config":
        """Charge la config depuis le fichier JSON, ou cree les defauts."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                defaults = asdict(cls())
                defaults.update(data)
                return cls(**{k: v for k, v in defaults.items()
                             if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()

    def save(self):
        """Sauvegarde la config en JSON."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    def update(self, **kwargs):
        """Met a jour des champs et sauvegarde."""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.save()

    def check_password(self, password: str) -> bool:
        """Verifie un mot de passe."""
        return _hash_password(password) == self.password_hash

    def set_password(self, new_password: str):
        """Change le mot de passe."""
        self.password_hash = _hash_password(new_password)
        self.password_changed = True
        self.save()
