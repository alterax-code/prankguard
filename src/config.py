"""
Configuration persistante JSON.
FIX 4 — Modes Pedago/Secure sauvegardés.
FIX 5 — Modes Desktop/Laptop sauvegardés.
"""
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, Any


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".prankguard")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_ENCODINGS = os.path.join("data", "owner_faces", "encodings.npy")


@dataclass
class Config:
    """Configuration globale sauvegardée en JSON."""

    # Chemins
    encodings_path: str = DEFAULT_ENCODINGS

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

    @classmethod
    def load(cls) -> "Config":
        """Charge la config depuis le fichier JSON, ou crée les défauts."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Merge avec les défauts (gère les nouvelles clés)
                defaults = asdict(cls())
                defaults.update(data)
                obj = cls(**{k: v for k, v in defaults.items() if k in cls.__dataclass_fields__})
            except Exception:
                obj = cls()
        else:
            obj = cls()

        # Migration chemin .pkl → .npy
        if obj.encodings_path.endswith(".pkl"):
            obj.encodings_path = obj.encodings_path[:-4] + ".npy"

        # Validation usb_mode / sec_mode (whitelist)
        if obj.usb_mode not in {"DESKTOP", "LAPTOP"}:
            obj.usb_mode = "DESKTOP"
        if obj.sec_mode not in {"PEDAGO", "SECURE"}:
            obj.sec_mode = "PEDAGO"

        # Clamp face_tolerance entre 0.1 et 0.9
        obj.face_tolerance = max(0.1, min(0.9, float(obj.face_tolerance)))

        return obj

    def save(self):
        """Sauvegarde la config en JSON."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    def update(self, **kwargs):
        """Met à jour des champs et sauvegarde."""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.save()
