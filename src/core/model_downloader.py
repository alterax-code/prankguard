# -*- coding: utf-8 -*-
"""
Model Downloader — PrankGuard v3.1

Telecharge et verifie les modeles InsightFace au premier lancement.
Stocke dans %APPDATA%/PrankGuard/models/.

Verification d'integrite via SHA256 apres telechargement.

Thread : peut etre appele depuis un thread worker.
Dependances : urllib, hashlib (stdlib)
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from zipfile import ZipFile

logger = logging.getLogger("prankguard.model_downloader")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_DEFAULT_MODEL_DIR = Path(
    os.environ.get("APPDATA", Path.home() / ".config")
) / "PrankGuard" / "models"

# InsightFace buffalo_sc — modele leger pour la reconnaissance faciale
_MODEL_NAME = "buffalo_sc"

# URL du CDN InsightFace (modele officiel)
_MODEL_URL = (
    "https://github.com/deepinsight/insightface/releases/download/"
    "v0.7/buffalo_sc.zip"
)

# SHA256 du fichier zip (a mettre a jour si le modele change)
# Note : si le hash n'est pas connu, la verification est ignoree
_MODEL_SHA256: Optional[str] = None

# Taille approximative pour l'affichage de la progress bar
_MODEL_SIZE_APPROX_MB = 300


# ---------------------------------------------------------------------------
# Callbacks de progression
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int], None]  # (downloaded_bytes, total_bytes)


# ---------------------------------------------------------------------------
# Model Downloader
# ---------------------------------------------------------------------------

class ModelDownloader:
    """
    Telecharge les modeles IA necessaires au premier lancement.

    Utilisation :
        dl = ModelDownloader()
        if not dl.is_model_available():
            dl.download(progress_callback=my_callback)
    """

    def __init__(self, model_dir: Optional[Path] = None) -> None:
        self._model_dir = model_dir or _DEFAULT_MODEL_DIR

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    def is_model_available(self) -> bool:
        """Verifie si le modele buffalo_sc est deja telecharge."""
        model_path = self._model_dir / _MODEL_NAME
        if not model_path.exists():
            return False
        # Verifier qu'il contient au moins les fichiers ONNX
        onnx_files = list(model_path.glob("*.onnx"))
        return len(onnx_files) > 0

    def download(
        self,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Path:
        """
        Telecharge le modele depuis le CDN InsightFace.

        Args:
            progress_callback: Fonction appelee avec (bytes_dl, bytes_total).

        Returns:
            Chemin du repertoire du modele.

        Raises:
            RuntimeError: Si le telechargement ou la verification echoue.
        """
        self._model_dir.mkdir(parents=True, exist_ok=True)
        zip_path = self._model_dir / f"{_MODEL_NAME}.zip"

        logger.info("Telechargement du modele %s...", _MODEL_NAME)

        try:
            self._download_file(_MODEL_URL, zip_path, progress_callback)
        except Exception as exc:
            raise RuntimeError(
                f"Echec du telechargement du modele.\n"
                f"URL : {_MODEL_URL}\n"
                f"Erreur : {exc}\n\n"
                f"Telechargement manuel :\n"
                f"1. Telecharger {_MODEL_URL}\n"
                f"2. Extraire dans {self._model_dir / _MODEL_NAME}\n"
            ) from exc

        # Verification SHA256
        if _MODEL_SHA256:
            actual_hash = self._compute_sha256(zip_path)
            if actual_hash != _MODEL_SHA256:
                zip_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Hash SHA256 invalide.\n"
                    f"Attendu : {_MODEL_SHA256}\n"
                    f"Obtenu  : {actual_hash}\n"
                    f"Le fichier a ete supprime. Reessayez le telechargement."
                )
            logger.info("Hash SHA256 verifie")

        # Extraction
        logger.info("Extraction du modele...")
        model_path = self._model_dir / _MODEL_NAME
        try:
            with ZipFile(zip_path, "r") as zf:
                zf.extractall(self._model_dir)
        except Exception as exc:
            raise RuntimeError(f"Erreur extraction : {exc}") from exc
        finally:
            zip_path.unlink(missing_ok=True)

        if not model_path.exists():
            # Le zip peut extraire dans un sous-repertoire different
            extracted = [d for d in self._model_dir.iterdir() if d.is_dir()]
            for d in extracted:
                if d.name != _MODEL_NAME and any(d.glob("*.onnx")):
                    d.rename(model_path)
                    break

        logger.info("Modele %s installe dans %s", _MODEL_NAME, model_path)
        return model_path

    def _download_file(
        self,
        url: str,
        dest: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        """Telecharge un fichier avec suivi de progression."""
        req = urllib.request.Request(url, headers={"User-Agent": "PrankGuard/3.1"})
        with urllib.request.urlopen(req, timeout=60) as response:
            total = int(response.headers.get("Content-Length", 0))
            if total == 0:
                total = _MODEL_SIZE_APPROX_MB * 1024 * 1024

            downloaded = 0
            chunk_size = 64 * 1024  # 64 KB

            with open(dest, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)

    @staticmethod
    def _compute_sha256(filepath: Path) -> str:
        """Calcule le hash SHA256 d'un fichier."""
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
