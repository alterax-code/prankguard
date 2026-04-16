"""
Journal d'audit signé HMAC-SHA256.
Vague 2 — traçabilité des actions sensibles.
Format : JSON Lines, chaque ligne signée individuellement.
"""
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import List, Tuple

from src import paths

_KEY_FILE  = paths.LOGS_DIR / "audit.key"
_AUDIT_LOG = paths.LOGS_DIR / "audit.jsonl"

_hmac_key: bytes = None


def _load_or_create_key() -> bytes:
    """Charge ou crée la clé HMAC via DPAPI (32 bytes aléatoires, persistée)."""
    global _hmac_key
    if _hmac_key is not None:
        return _hmac_key

    key_path = str(_KEY_FILE)
    if os.path.exists(key_path):
        try:
            from src.security.hardening import dpapi_unprotect
            blob = open(key_path, "rb").read()
            _hmac_key = dpapi_unprotect(blob)
            return _hmac_key
        except Exception:
            # Profil Windows différent ou fichier corrompu — créer nouvelle clé
            pass

    # Créer et persister une nouvelle clé
    new_key = os.urandom(32)
    try:
        from src.security.hardening import dpapi_protect
        os.makedirs(str(paths.LOGS_DIR), exist_ok=True)
        blob = dpapi_protect(new_key, "PrankGuard Audit Key")
        with open(key_path, "wb") as f:
            f.write(blob)
    except Exception:
        pass    # Opérer sans persistance si DPAPI indisponible

    _hmac_key = new_key
    return _hmac_key


def _sign(payload: str, key: bytes) -> str:
    """Calcule HMAC-SHA256 sur payload encodé UTF-8."""
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def log_event(action: str, details: dict) -> None:
    """
    Logue un événement d'audit signé.
    action  : constante string — ex. "PASSWORD_CHANGED", "LOCK_TRIGGERED"
    details : dict de contexte libre (sérialisé JSON)
    """
    try:
        key = _load_or_create_key()
        ts = datetime.now(timezone.utc).isoformat()
        payload = {"ts": ts, "action": action, "details": details}
        payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        sig = _sign(payload_str, key)
        line = json.dumps({**payload, "hmac": sig}, ensure_ascii=False)

        os.makedirs(str(paths.LOGS_DIR), exist_ok=True)
        with open(str(_AUDIT_LOG), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass    # Le journal d'audit ne doit JAMAIS crasher l'application


def verify_audit_log() -> List[Tuple[int, str]]:
    """
    Vérifie l'intégrité de chaque ligne du journal d'audit.
    Retourne une liste de (numéro_ligne, raison) pour les lignes altérées.
    Usage futur : Vague 3 — interface de vérification d'intégrité.
    """
    tampered: List[Tuple[int, str]] = []
    log_path = str(_AUDIT_LOG)

    if not os.path.exists(log_path):
        return []

    try:
        key = _load_or_create_key()
    except Exception:
        return [(0, "Impossible de charger la clé HMAC")]

    with open(log_path, "r", encoding="utf-8") as f:
        for i, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                stored_sig = entry.pop("hmac", None)
                if stored_sig is None:
                    tampered.append((i, "HMAC manquant"))
                    continue
                payload_str = json.dumps(entry, ensure_ascii=False, sort_keys=True)
                expected = _sign(payload_str, key)
                if not hmac.compare_digest(expected, stored_sig):
                    tampered.append((i, "HMAC invalide"))
            except Exception as exc:
                tampered.append((i, f"Erreur parsing: {exc}"))

    return tampered
