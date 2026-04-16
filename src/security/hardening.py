"""
Sécurité renforcée — argon2id, DPAPI, anti-capture d'écran.
Vague 2 : remplace SHA-256 naïf, chiffrement lié au compte Windows.
"""
import ctypes
import hashlib

import win32crypt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from argon2.low_level import Type

# Paramètres argon2id — OWASP 2023 (time=3, mem=64MB, parallelism=4)
_PH = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    type=Type.ID,
)


def hash_password(pwd: str) -> str:
    """Hash pwd via argon2id. Retourne le hash encodé (salt + params inclus)."""
    return _PH.hash(pwd)


def verify_password(pwd: str, stored_hash: str) -> bool:
    """
    Vérifie pwd contre stored_hash.
    Migration transparente : si stored_hash est un SHA-256 legacy (64 hex chars),
    vérifie via SHA-256. Le caller doit appeler needs_rehash() et re-hasher si True.
    """
    # Détection SHA-256 legacy (64 chars hex, pas de préfixe $argon2)
    if len(stored_hash) == 64 and not stored_hash.startswith("$argon2"):
        return hashlib.sha256(pwd.encode()).hexdigest() == stored_hash
    # Argon2id
    try:
        return _PH.verify(stored_hash, pwd)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True si le hash est SHA-256 legacy ou si les paramètres argon2 sont obsolètes."""
    if len(stored_hash) == 64 and not stored_hash.startswith("$argon2"):
        return True
    try:
        return _PH.check_needs_rehash(stored_hash)
    except Exception:
        return True


# ── DPAPI (Windows CryptProtectData) ─────────────────────────────────────────

def dpapi_protect(data: bytes, description: str = "PrankGuard") -> bytes:
    """Chiffre data via DPAPI (scope utilisateur Windows). Retourne le blob chiffré."""
    return win32crypt.CryptProtectData(data, description, None, None, None, 0)


def dpapi_unprotect(data: bytes) -> bytes:
    """Déchiffre un blob DPAPI. Retourne les bytes en clair."""
    _, decrypted = win32crypt.CryptUnprotectData(data, None, None, None, 0)
    return decrypted


# ── Anti-capture d'écran ─────────────────────────────────────────────────────

WDA_NONE               = 0x00
WDA_MONITOR            = 0x01
WDA_EXCLUDEFROMCAPTURE = 0x11   # Windows 10 build 19041+


def set_window_capture_protection(hwnd: int) -> bool:
    """
    Applique WDA_EXCLUDEFROMCAPTURE (0x11) à la fenêtre hwnd.
    Fallback WDA_MONITOR (0x01) si build Windows antérieur à 19041.
    Retourne True si une protection a été appliquée.
    """
    user32 = ctypes.windll.user32
    # Tentative WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+)
    if user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        return True
    # Fallback WDA_MONITOR
    if user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR):
        return True
    # Aucune protection disponible
    try:
        from src.logger import logger
        logger.info("Anti-capture: SetWindowDisplayAffinity non supporté (build Windows trop ancien)")
    except Exception:
        pass
    return False
