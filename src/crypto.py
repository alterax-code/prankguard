"""
Chiffrement AES-256-GCM des fichiers d'encodings.
Sprint 2 Feature 5 — PBKDF2+SHA256, 100 000 itérations, clé liée à la machine.
Format binaire : MAGIC(4) + SALT(16) + NONCE(12) + CIPHERTEXT
"""
import getpass
import os
import socket

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"PGRD"
SALT_LEN = 16
NONCE_LEN = 12
KDF_ITERATIONS = 100_000


def _machine_password() -> str:
    """Génère un mot de passe dérivé des infos machine (lié à la machine locale)."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "localhost"
    return f"pg_{user}_{host}_prankguard"


def _derive_key(password: str, salt: bytes) -> bytes:
    """Dérive une clé AES-256 (32 octets) via PBKDF2-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def is_encrypted(data: bytes) -> bool:
    """Détecte si les données contiennent le header PGRD (fichier chiffré)."""
    return len(data) >= 4 and data[:4] == MAGIC


def encrypt_data(data: bytes, password: str) -> bytes:
    """Chiffre data avec AES-256-GCM. Retourne MAGIC+SALT+NONCE+CIPHERTEXT."""
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return MAGIC + salt + nonce + ciphertext


def decrypt_data(data: bytes, password: str) -> bytes:
    """Déchiffre data AES-256-GCM. Lève une exception si invalide ou mauvais mdp."""
    if not is_encrypted(data):
        raise ValueError("Header PGRD manquant — données non chiffrées")
    salt = data[4:4 + SALT_LEN]
    nonce = data[4 + SALT_LEN:4 + SALT_LEN + NONCE_LEN]
    ciphertext = data[4 + SALT_LEN + NONCE_LEN:]
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def encrypt_encodings(data: bytes) -> bytes:
    """Chiffre les encodings avec le mot de passe machine (sans interaction user)."""
    return encrypt_data(data, _machine_password())


def decrypt_encodings(data: bytes) -> bytes:
    """Déchiffre les encodings avec le mot de passe machine."""
    return decrypt_data(data, _machine_password())
