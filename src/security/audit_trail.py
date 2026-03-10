# -*- coding: utf-8 -*-
"""
Audit Trail — PrankGuard v3.1

Sauvegarde les logs de securite sur disque dans %APPDATA%/PrankGuard/logs/.
Un fichier par jour, rotation automatique apres 30 jours.

Format : [HH:MM:SS] [LEVEL] [ESCALADE] message

Thread-safe : peut etre appele depuis n'importe quel thread.
Dependances : aucune externe
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("prankguard.audit_trail")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_DEFAULT_LOG_DIR = Path(
    os.environ.get("APPDATA", Path.home() / ".config")
) / "PrankGuard" / "logs"

_MAX_LOG_AGE_DAYS = 30
_LOG_FILE_PREFIX = "audit_"
_LOG_FILE_SUFFIX = ".log"


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------

class AuditTrail:
    """
    Journal d'audit persistant.

    Utilisation :
        trail = AuditTrail()
        trail.start()
        trail.log("VEILLE", "INFO", "PrankGuard demarre")
        ...
        trail.stop()
    """

    def __init__(self, log_dir: Optional[Path] = None) -> None:
        self._log_dir = log_dir or _DEFAULT_LOG_DIR
        self._lock = threading.Lock()
        self._current_file = None
        self._current_date: Optional[str] = None

    def start(self) -> None:
        """Initialise le repertoire de logs et effectue la rotation."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._rotate_old_logs()
        logger.info("Audit trail demarre (%s)", self._log_dir)

    def stop(self) -> None:
        """Ferme le fichier de log courant."""
        with self._lock:
            if self._current_file is not None:
                try:
                    self._current_file.close()
                except Exception:
                    pass
                self._current_file = None
        logger.info("Audit trail arrete")

    def log(
        self,
        escalation_level: str,
        level: str,
        message: str,
        details: str = "",
    ) -> None:
        """
        Ecrit une entree dans le journal d'audit.

        Args:
            escalation_level: Niveau d'escalade (VEILLE, SOFT, ALERTE, ACTIF).
            level: Niveau de log (INFO, WARNING, CRITICAL, etc.).
            message: Message principal.
            details: Details supplementaires (optionnel).
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        line = f"[{time_str}] [{level}] [{escalation_level}] {message}"
        if details:
            line += f" | {details}"
        line += "\n"

        with self._lock:
            self._ensure_file(date_str)
            if self._current_file is not None:
                try:
                    self._current_file.write(line)
                    self._current_file.flush()
                except Exception as exc:
                    logger.error("Erreur ecriture audit : %s", exc)

    def _ensure_file(self, date_str: str) -> None:
        """Ouvre le fichier du jour (cree si necessaire)."""
        if self._current_date == date_str and self._current_file is not None:
            return

        # Fermer le fichier precedent
        if self._current_file is not None:
            try:
                self._current_file.close()
            except Exception:
                pass

        filepath = self._log_dir / f"{_LOG_FILE_PREFIX}{date_str}{_LOG_FILE_SUFFIX}"
        try:
            self._current_file = open(filepath, "a", encoding="utf-8")
            self._current_date = date_str
        except Exception as exc:
            logger.error("Impossible d'ouvrir %s : %s", filepath, exc)
            self._current_file = None

    def _rotate_old_logs(self) -> None:
        """Supprime les fichiers de log de plus de 30 jours."""
        cutoff = datetime.now() - timedelta(days=_MAX_LOG_AGE_DAYS)
        count = 0

        for path in self._log_dir.glob(f"{_LOG_FILE_PREFIX}*{_LOG_FILE_SUFFIX}"):
            try:
                # Extraire la date du nom de fichier
                date_part = path.stem.replace(_LOG_FILE_PREFIX, "")
                file_date = datetime.strptime(date_part, "%Y-%m-%d")
                if file_date < cutoff:
                    path.unlink()
                    count += 1
            except (ValueError, OSError):
                continue

        if count > 0:
            logger.info("Rotation : %d fichier(s) de log supprimes (> %d jours)",
                        count, _MAX_LOG_AGE_DAYS)

    def get_today_logs(self) -> list[str]:
        """Retourne les lignes du fichier de log du jour."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        filepath = self._log_dir / f"{_LOG_FILE_PREFIX}{date_str}{_LOG_FILE_SUFFIX}"
        if not filepath.exists():
            return []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.readlines()
        except Exception:
            return []

    def get_log_files(self) -> list[Path]:
        """Retourne la liste des fichiers de log disponibles, tries par date."""
        files = list(self._log_dir.glob(f"{_LOG_FILE_PREFIX}*{_LOG_FILE_SUFFIX}"))
        files.sort(reverse=True)
        return files
