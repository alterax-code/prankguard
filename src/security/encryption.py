# -*- coding: utf-8 -*-
"""
Chiffrement des Encodings — PrankGuard v3.0

Chiffre et dechiffre les fichiers d'encodings faciaux en AES-256-GCM
via la bibliotheque Python `cryptography`.

La cle de chiffrement est derivee d'un mot de passe machine-specifique
(combinaison de l'identifiant machine + sel fixe) via PBKDF2.
L'utilisateur n'a pas besoin de saisir de mot de passe.

Section 6.3 du plan v3 :
  - Les encodings sont chiffres localement en AES-256
  - L'application ne stocke JAMAIS d'images

Dependances : cryptography, numpy
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import secrets
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("prankguard.encryption")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_DIR = Path(
    os.environ.get("APPDATA", Path.home() / ".config")
) / "PrankGuard"

_ENCRYPTED_FILENAME = "owner_encodings.enc"
_SALT_FILENAME = "enc_salt.bin"

# Parametres PBKDF2
_PBKDF2_ITERATIONS = 480_000  # Recommandation OWASP 2024
_PBKDF2_KEY_LENGTH = 32       # AES-256 = 32 octets
_PBKDF2_ALGORITHM = "sha256"


# ---------------------------------------------------------------------------
# Derivation de cle
# ---------------------------------------------------------------------------

def _get_machine_id() -> str:
    """
    Genere un identifiant stable et unique pour la machine.
    Combine le nom de la machine, le processeur et l'OS.
    Cet identifiant ne quitte JAMAIS la machine.
    """
    parts = [
        platform.node(),          # Nom de la machine
        platform.processor(),     # Processeur
        platform.platform(),      # OS complet
        os.environ.get("USERNAME", ""),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_or_create_salt(config_dir: Optional[Path] = None) -> bytes:
    """
    Charge ou genere le sel cryptographique.
    Le sel est stocke dans un fichier separe (enc_salt.bin).
    """
    directory = config_dir or _DEFAULT_CONFIG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    salt_path = directory / _SALT_FILENAME

    if salt_path.exists():
        with open(salt_path, "rb") as f:
            salt = f.read()
        if len(salt) == 16:
            return salt
        # Sel corrompu, on en regenere un
        logger.warning("Sel corrompu, regeneration")

    salt = secrets.token_bytes(16)
    with open(salt_path, "wb") as f:
        f.write(salt)

    logger.info("Sel cryptographique genere")
    return salt


def derive_key(config_dir: Optional[Path] = None) -> bytes:
    """
    Derive la cle AES-256 via PBKDF2 a partir de l'identifiant machine + sel.
    L'utilisateur n'a pas besoin de saisir de mot de passe.
    """
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    machine_id = _get_machine_id()
    salt = _get_or_create_salt(config_dir)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_PBKDF2_KEY_LENGTH,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )

    key = kdf.derive(machine_id.encode("utf-8"))
    return key


# ---------------------------------------------------------------------------
# Chiffrement / Dechiffrement AES-256-GCM
# ---------------------------------------------------------------------------

def encrypt_encodings(
    encodings: np.ndarray,
    config_dir: Optional[Path] = None,
) -> Path:
    """
    Chiffre les encodings faciaux en AES-256-GCM et sauvegarde le resultat.

    Le fichier chiffre contient : nonce (12 octets) + tag (16 octets) + ciphertext.

    Args:
        encodings: Array numpy des encodings (N x 512).
        config_dir: Repertoire de configuration (defaut: %APPDATA%/PrankGuard).

    Returns:
        Chemin du fichier chiffre.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    directory = config_dir or _DEFAULT_CONFIG_DIR
    enc_dir = directory / "encodings"
    enc_dir.mkdir(parents=True, exist_ok=True)
    filepath = enc_dir / _ENCRYPTED_FILENAME

    # Serialiser les encodings en bytes
    plaintext = encodings.tobytes()
    metadata = {
        "shape": list(encodings.shape),
        "dtype": str(encodings.dtype),
    }
    # Prepend metadata length + metadata JSON
    import json
    meta_bytes = json.dumps(metadata).encode("utf-8")
    meta_len = len(meta_bytes).to_bytes(4, "big")
    full_plaintext = meta_len + meta_bytes + plaintext

    # Chiffrer
    key = derive_key(config_dir)
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)  # 96 bits pour GCM
    ciphertext = aesgcm.encrypt(nonce, full_plaintext, None)

    # Sauvegarder : nonce + ciphertext (le tag est inclus par AESGCM)
    with open(filepath, "wb") as f:
        f.write(nonce)
        f.write(ciphertext)

    logger.info("Encodings chiffres dans %s (%d octets)", filepath, filepath.stat().st_size)
    return filepath


def decrypt_encodings(
    config_dir: Optional[Path] = None,
) -> Optional[np.ndarray]:
    """
    Dechiffre les encodings faciaux depuis le fichier chiffre.

    Returns:
        Array numpy des encodings, ou None si le fichier n'existe pas
        ou si le dechiffrement echoue.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    directory = config_dir or _DEFAULT_CONFIG_DIR
    filepath = directory / "encodings" / _ENCRYPTED_FILENAME

    if not filepath.exists():
        logger.info("Aucun fichier d'encodings chiffre trouve")
        return None

    try:
        with open(filepath, "rb") as f:
            data = f.read()

        # Extraire nonce (12 octets) + ciphertext
        nonce = data[:12]
        ciphertext = data[12:]

        # Dechiffrer
        key = derive_key(config_dir)
        aesgcm = AESGCM(key)
        full_plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        # Extraire metadata
        import json
        meta_len = int.from_bytes(full_plaintext[:4], "big")
        meta_bytes = full_plaintext[4:4 + meta_len]
        metadata = json.loads(meta_bytes.decode("utf-8"))

        # Reconstruire le ndarray
        plaintext = full_plaintext[4 + meta_len:]
        encodings = np.frombuffer(plaintext, dtype=np.dtype(metadata["dtype"]))
        encodings = encodings.reshape(metadata["shape"])

        logger.info("Encodings dechiffres : shape=%s", encodings.shape)
        return encodings

    except Exception as exc:
        logger.error("Echec du dechiffrement : %s", exc)
        return None


# ---------------------------------------------------------------------------
# API de haut niveau (pour face_recognition_agent)
# ---------------------------------------------------------------------------

def save_encrypted_owner_encodings(
    encodings: list[np.ndarray],
    config_dir: Optional[Path] = None,
) -> Path:
    """
    Sauvegarde les encodings du proprietaire de maniere chiffree.
    Remplace le .npz non chiffre par un .enc chiffre AES-256-GCM.
    """
    if not encodings:
        raise ValueError("Aucun encoding a sauvegarder")

    arr = np.array(encodings)
    return encrypt_encodings(arr, config_dir)


def load_encrypted_owner_encodings(
    config_dir: Optional[Path] = None,
) -> list[np.ndarray]:
    """
    Charge les encodings du proprietaire depuis le fichier chiffre.
    Retourne une liste vide si rien n'est trouve.
    """
    arr = decrypt_encodings(config_dir)
    if arr is None:
        return []
    return list(arr)


def delete_encrypted_encodings(config_dir: Optional[Path] = None) -> bool:
    """Supprime le fichier d'encodings chiffre (RGPD droit a l'effacement)."""
    directory = config_dir or _DEFAULT_CONFIG_DIR
    filepath = directory / "encodings" / _ENCRYPTED_FILENAME

    if filepath.exists():
        filepath.unlink()
        logger.info("Encodings chiffres supprimes")
        return True

    return False


# ---------------------------------------------------------------------------
# Execution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s -- %(message)s",
    )

    print("Encryption Module -- test")
    print("=" * 50)

    # Creer des encodings factices (5 vecteurs de 512 dimensions)
    fake_encodings = [np.random.randn(512).astype(np.float32) for _ in range(5)]
    print(f"  Encodings crees : {len(fake_encodings)} x 512")

    # Chiffrer
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "PrankGuard"

        path = save_encrypted_owner_encodings(fake_encodings, config_dir=test_dir)
        print(f"  Fichier chiffre : {path}")
        print(f"  Taille          : {path.stat().st_size} octets")

        # Dechiffrer
        loaded = load_encrypted_owner_encodings(config_dir=test_dir)
        print(f"  Encodings charges : {len(loaded)} x {loaded[0].shape[0]}")

        # Verifier l'integrite
        for i, (orig, dec) in enumerate(zip(fake_encodings, loaded)):
            if not np.allclose(orig, dec):
                print(f"  ERREUR : encoding {i} ne correspond pas !")
                break
        else:
            print("  Integrite verifiee : tous les encodings correspondent")

        # Supprimer
        deleted = delete_encrypted_encodings(config_dir=test_dir)
        print(f"  Suppression       : {'OK' if deleted else 'rien a supprimer'}")

    print(f"\n{'=' * 50}")
    print("Test termine.")
