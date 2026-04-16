"""
Chiffrement des fichiers d'encodings.
Vague 2 — migration vers DPAPI (Windows CryptProtectData).
Backward compat : PGRD (AES-256-GCM legacy) déchiffré automatiquement.
Format DPAP : MAGIC(4) + len_blob(4, big-endian) + DPAPI_blob
"""
import getpass
import os
import socket
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC_LEGACY = b"PGRD"   # AES-256-GCM (legacy)
MAGIC_DPAPI  = b"DPAP"   # DPAPI Windows (nouveau)
MAGIC        = MAGIC_LEGACY   # Alias backward compat pour callers existants

SALT_LEN       = 16
NONCE_LEN      = 12
KDF_ITERATIONS = 100_000


def _machine_password() -> str:
    """Génère un mot de passe machine (utilisé par l'AES legacy uniquement)."""
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
    """Dérive une clé AES-256 via PBKDF2-SHA256 (legacy)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def is_encrypted(data: bytes) -> bool:
    """Détecte si les données sont chiffrées (PGRD legacy ou DPAP nouveau)."""
    return len(data) >= 4 and data[:4] in (MAGIC_LEGACY, MAGIC_DPAPI)


# ── AES-256-GCM (legacy — backward compat) ───────────────────────────────────

def encrypt_data(data: bytes, password: str) -> bytes:
    """Chiffre data AES-256-GCM. Format : PGRD+SALT+NONCE+CIPHERTEXT."""
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive_key(password, salt)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return MAGIC_LEGACY + salt + nonce + ciphertext


def decrypt_data(data: bytes, password: str) -> bytes:
    """Déchiffre data AES-256-GCM (legacy)."""
    if data[:4] != MAGIC_LEGACY:
        raise ValueError("Header PGRD manquant")
    salt       = data[4:4 + SALT_LEN]
    nonce      = data[4 + SALT_LEN:4 + SALT_LEN + NONCE_LEN]
    ciphertext = data[4 + SALT_LEN + NONCE_LEN:]
    key = _derive_key(password, salt)
    return AESGCM(key).decrypt(nonce, ciphertext, None)


# ── DPAPI (Windows CryptProtectData) ─────────────────────────────────────────

def _dpapi_wrap(data: bytes) -> bytes:
    """Chiffre data via DPAPI. Format : DPAP + len_blob(4 BE) + blob."""
    from src.security.hardening import dpapi_protect
    blob = dpapi_protect(data, "PrankGuard Encodings")
    return MAGIC_DPAPI + struct.pack(">I", len(blob)) + blob


def _dpapi_unwrap(data: bytes) -> bytes:
    """Déchiffre data DPAPI."""
    if data[:4] != MAGIC_DPAPI:
        raise ValueError("Header DPAP manquant")
    blob_len = struct.unpack(">I", data[4:8])[0]
    blob = data[8:8 + blob_len]
    from src.security.hardening import dpapi_unprotect
    return dpapi_unprotect(blob)


# ── API publique ──────────────────────────────────────────────────────────────

def encrypt_encodings(data: bytes) -> bytes:
    """Chiffre les encodings via DPAPI (scope utilisateur Windows)."""
    return _dpapi_wrap(data)


def decrypt_encodings(data: bytes) -> bytes:
    """
    Déchiffre les encodings.
    PGRD (AES legacy)  : déchiffre avec mot de passe machine.
    DPAP (DPAPI)       : déchiffre via CryptUnprotectData.
    """
    magic = data[:4] if len(data) >= 4 else b""
    if magic == MAGIC_LEGACY:
        return decrypt_data(data, _machine_password())
    if magic == MAGIC_DPAPI:
        return _dpapi_unwrap(data)
    raise ValueError(f"Format d'encodings non reconnu (magic={magic!r})")
