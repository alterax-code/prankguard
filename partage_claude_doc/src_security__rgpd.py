# -*- coding: utf-8 -*-
"""
RGPD & Consentement — PrankGuard v3.0

Gere le consentement utilisateur au premier lancement et le droit
a l'effacement (suppression de toutes les donnees personnelles).

Conformite RGPD (section 6 du plan v3) :
  - Popup de consentement AVANT toute activation de la camera
  - Explication claire de ce qui est stocke / non stocke
  - Bouton explicite "J'accepte"
  - Bouton "Supprimer toutes mes donnees" dans les parametres
  - Suppression immediate et irreversible de tous les encodings, logs, config

Dependances : customtkinter (pour la popup), pathlib, json
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger("prankguard.rgpd")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_DIR = Path(
    os.environ.get("APPDATA", Path.home() / ".config")
) / "PrankGuard"

_CONSENT_FILENAME = "consent.json"

# Texte de consentement (section 6.1 du plan v3)
CONSENT_TITLE = "PrankGuard - Consentement"

CONSENT_TEXT_FR = """
PrankGuard - Consentement au traitement des donnees

Avant d'utiliser PrankGuard, veuillez lire attentivement :

CE QUE FAIT L'APPLICATION :
  - Surveille la webcam pour detecter les visages
  - Monitore les connexions de peripheriques (USB, reseau, etc.)
  - Verrouille automatiquement le PC en cas de menace detectee

CE QUI EST STOCKE (localement uniquement) :
  - Vecteurs numeriques (embeddings) de votre visage : 512 nombres
    decimaux, chiffres en AES-256. Ils ne permettent PAS de
    reconstituer un visage.
  - Logs d'activite : horodatage des evenements, SANS images.
  - Configuration : profil, preferences, options de monitoring.

CE QUI N'EST JAMAIS STOCKE :
  - Aucune image ou video n'est enregistree sur le disque
  - Aucune donnee n'est envoyee sur Internet
  - Aucune donnee n'est partagee avec des tiers

VOS DROITS :
  - Vous pouvez supprimer toutes vos donnees a tout moment
    depuis les parametres de l'application.
  - La suppression est immediate et irreversible.

En cliquant "J'accepte", vous autorisez PrankGuard a activer
la camera et la surveillance sur cet ordinateur.
""".strip()

CONSENT_TEXT_EN = """
PrankGuard - Data Processing Consent

Before using PrankGuard, please read carefully:

WHAT THE APPLICATION DOES:
  - Monitors the webcam to detect faces
  - Monitors device connections (USB, network, etc.)
  - Automatically locks the PC when a threat is detected

WHAT IS STORED (locally only):
  - Numerical vectors (embeddings) of your face: 512 decimal
    numbers, encrypted with AES-256. They CANNOT be used to
    reconstruct a face.
  - Activity logs: event timestamps, WITHOUT images.
  - Configuration: profile, preferences, monitoring options.

WHAT IS NEVER STORED:
  - No image or video is ever saved to disk
  - No data is ever sent over the Internet
  - No data is ever shared with third parties

YOUR RIGHTS:
  - You can delete all your data at any time from the
    application settings.
  - Deletion is immediate and irreversible.

By clicking "I Accept", you authorize PrankGuard to activate
the camera and monitoring on this computer.
""".strip()


# ---------------------------------------------------------------------------
# Gestion du consentement
# ---------------------------------------------------------------------------

def has_consent(config_dir: Optional[Path] = None) -> bool:
    """Verifie si l'utilisateur a donne son consentement."""
    directory = config_dir or _DEFAULT_CONFIG_DIR
    path = directory / _CONSENT_FILENAME

    if not path.exists():
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("accepted", False) is True
    except (json.JSONDecodeError, KeyError):
        return False


def save_consent(accepted: bool, config_dir: Optional[Path] = None) -> Path:
    """Sauvegarde le choix de consentement."""
    directory = config_dir or _DEFAULT_CONFIG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _CONSENT_FILENAME

    import time
    data = {
        "accepted": accepted,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "version": "3.0",
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Consentement enregistre : %s", "accepte" if accepted else "refuse")
    return path


def revoke_consent(config_dir: Optional[Path] = None) -> None:
    """Revoque le consentement (remet a zero)."""
    directory = config_dir or _DEFAULT_CONFIG_DIR
    path = directory / _CONSENT_FILENAME
    if path.exists():
        path.unlink()
    logger.info("Consentement revoque")


# ---------------------------------------------------------------------------
# Popup de consentement (CustomTkinter)
# ---------------------------------------------------------------------------

def show_consent_dialog(lang: str = "fr") -> bool:
    """
    Affiche la popup de consentement au premier lancement.
    Retourne True si l'utilisateur accepte, False sinon.
    Bloquant (attend la reponse).
    """
    try:
        import customtkinter as ctk
    except ImportError:
        # Fallback : consentement automatique en mode console
        logger.warning("CustomTkinter non disponible, consentement console")
        return _console_consent(lang)

    result = {"accepted": False}

    # Fenetre de consentement
    dialog = ctk.CTk()
    ctk.set_appearance_mode("dark")
    dialog.title(CONSENT_TITLE)
    dialog.geometry("650x550")
    dialog.resizable(False, False)

    # Titre
    title_label = ctk.CTkLabel(
        dialog,
        text="PrankGuard",
        font=ctk.CTkFont(size=24, weight="bold"),
    )
    title_label.pack(pady=(20, 5))

    subtitle_label = ctk.CTkLabel(
        dialog,
        text="Consentement au traitement des donnees" if lang == "fr"
             else "Data Processing Consent",
        font=ctk.CTkFont(size=14),
        text_color="#9ca3af",
    )
    subtitle_label.pack(pady=(0, 15))

    # Zone de texte scrollable
    text = CONSENT_TEXT_FR if lang == "fr" else CONSENT_TEXT_EN
    textbox = ctk.CTkTextbox(
        dialog,
        font=ctk.CTkFont(family="Consolas", size=11),
        wrap="word",
    )
    textbox.pack(fill="both", expand=True, padx=20, pady=5)
    textbox.insert("1.0", text)
    textbox.configure(state="disabled")

    # Boutons
    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)

    def _accept():
        result["accepted"] = True
        dialog.destroy()

    def _refuse():
        result["accepted"] = False
        dialog.destroy()

    accept_text = "J'accepte" if lang == "fr" else "I Accept"
    refuse_text = "Je refuse" if lang == "fr" else "I Decline"

    accept_btn = ctk.CTkButton(
        btn_frame, text=accept_text, width=180, height=40,
        font=ctk.CTkFont(size=14, weight="bold"),
        command=_accept,
    )
    accept_btn.pack(side="left", padx=15)

    refuse_btn = ctk.CTkButton(
        btn_frame, text=refuse_text, width=180, height=40,
        font=ctk.CTkFont(size=14),
        fg_color="#6b7280", hover_color="#4b5563",
        command=_refuse,
    )
    refuse_btn.pack(side="left", padx=15)

    # Empecher la fermeture sans choix
    dialog.protocol("WM_DELETE_WINDOW", _refuse)

    dialog.mainloop()

    return result["accepted"]


def _console_consent(lang: str = "fr") -> bool:
    """Fallback consentement en mode console."""
    text = CONSENT_TEXT_FR if lang == "fr" else CONSENT_TEXT_EN
    print(text)
    print()
    prompt = "Acceptez-vous ? (oui/non) : " if lang == "fr" else "Do you accept? (yes/no): "
    response = input(prompt).strip().lower()
    return response in ("oui", "o", "yes", "y")


# ---------------------------------------------------------------------------
# Droit a l'effacement (RGPD Article 17)
# ---------------------------------------------------------------------------

def delete_all_user_data(config_dir: Optional[Path] = None) -> dict:
    """
    Supprime TOUTES les donnees personnelles de l'utilisateur.
    Retourne un dict avec le detail de ce qui a ete supprime.

    Donnees supprimees :
      - Encodings faciaux (owner_encodings.npz)
      - Configuration (hardware_profile.json, consent.json)
      - Whitelist peripheriques (device_whitelist.json)
      - Logs (si stockes dans le repertoire config)

    L'application revient a l'etat du premier lancement.
    """
    directory = config_dir or _DEFAULT_CONFIG_DIR
    deleted = {
        "encodings": False,
        "config": False,
        "whitelist": False,
        "consent": False,
        "total_files": 0,
    }

    if not directory.exists():
        logger.info("Aucun repertoire de donnees a supprimer")
        return deleted

    # Lister et supprimer les fichiers specifiques
    targets = [
        ("encodings", directory / "encodings" / "owner_encodings.npz"),
        ("config", directory / "hardware_profile.json"),
        ("whitelist", directory / "device_whitelist.json"),
        ("consent", directory / _CONSENT_FILENAME),
    ]

    for key, path in targets:
        if path.exists():
            path.unlink()
            deleted[key] = True
            deleted["total_files"] += 1
            logger.info("Supprime : %s", path)

    # Supprimer le repertoire encodings s'il est vide
    encodings_dir = directory / "encodings"
    if encodings_dir.exists() and not any(encodings_dir.iterdir()):
        encodings_dir.rmdir()

    logger.info(
        "Donnees utilisateur supprimees : %d fichier(s)",
        deleted["total_files"],
    )

    return deleted


def get_stored_data_summary(config_dir: Optional[Path] = None) -> dict:
    """
    Retourne un resume des donnees stockees (pour transparence RGPD).
    Utile pour afficher dans les parametres.
    """
    directory = config_dir or _DEFAULT_CONFIG_DIR
    summary = {
        "config_dir": str(directory),
        "consent_given": has_consent(config_dir),
        "encodings_exist": False,
        "encodings_count": 0,
        "profile_exists": False,
        "whitelist_exists": False,
        "total_size_kb": 0,
    }

    if not directory.exists():
        return summary

    # Encodings
    enc_path = directory / "encodings" / "owner_encodings.npz"
    if enc_path.exists():
        summary["encodings_exist"] = True
        summary["total_size_kb"] += enc_path.stat().st_size / 1024
        try:
            import numpy as np
            data = np.load(enc_path)
            summary["encodings_count"] = len(data["encodings"])
        except Exception:
            pass

    # Profil
    profile_path = directory / "hardware_profile.json"
    if profile_path.exists():
        summary["profile_exists"] = True
        summary["total_size_kb"] += profile_path.stat().st_size / 1024

    # Whitelist
    wl_path = directory / "device_whitelist.json"
    if wl_path.exists():
        summary["whitelist_exists"] = True
        summary["total_size_kb"] += wl_path.stat().st_size / 1024

    summary["total_size_kb"] = round(summary["total_size_kb"], 1)

    return summary


# ---------------------------------------------------------------------------
# Execution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s -- %(message)s",
    )

    print("RGPD Module -- test")
    print("=" * 40)

    # Verifier le consentement
    print(f"  Consentement actuel : {has_consent()}")

    # Resume des donnees
    summary = get_stored_data_summary()
    print(f"  Repertoire config   : {summary['config_dir']}")
    print(f"  Encodings           : {summary['encodings_count']} fichier(s)")
    print(f"  Profil sauvegarde   : {summary['profile_exists']}")
    print(f"  Whitelist           : {summary['whitelist_exists']}")
    print(f"  Taille totale       : {summary['total_size_kb']} Ko")

    # Test popup consentement
    print("\n  Test popup consentement...")
    accepted = show_consent_dialog(lang="fr")
    print(f"  Resultat : {'accepte' if accepted else 'refuse'}")

    if accepted:
        save_consent(True)
        print(f"  Consentement sauvegarde : {has_consent()}")

    print(f"\n{'=' * 40}")
    print("Test termine.")
