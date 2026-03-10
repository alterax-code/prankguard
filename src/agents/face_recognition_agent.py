# -*- coding: utf-8 -*-
"""
Face Recognition Agent — PrankGuard v3.0

Agent prioritaire de reconnaissance faciale. Utilise InsightFace buffalo_sc
via ONNX Runtime pour identifier les visages détectés comme :
  - OWNER   : propriétaire reconnu (distance cosine ≤ 0.30)
  - STRANGER : visage détecté mais non reconnu
  - UNKNOWN  : visage trop petit, trop flou ou non détecté

Le face recognition est PRIORITAIRE : s'il reconnaît le propriétaire,
aucun autre agent n'est consulté. C'est la règle absolue (section 3.1 du plan).

RGPD : aucune image n'est stockée. Seuls les encodings (vecteurs numériques)
du propriétaire sont conservés, chiffrés en AES-256.

Thread : actif seulement (déclenché par le motion_agent).

Dépendances : insightface, onnxruntime (+ onnxruntime-directml optionnel),
              numpy, opencv-python
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("prankguard.face_recognition_agent")


# ---------------------------------------------------------------------------
# Constantes (section 3.3 du plan v3)
# ---------------------------------------------------------------------------

class FaceIdentity(str, Enum):
    """Résultat d'identification d'un visage."""
    OWNER = "OWNER"        # Propriétaire reconnu
    STRANGER = "STRANGER"  # Visage détecté, non reconnu
    UNKNOWN = "UNKNOWN"    # Pas de visage exploitable


# Tolérance de reconnaissance (distance cosine)
# Distance minimale testée avec l'owner réel : 0.318
# 0.30 garantit la reconnaissance tout en rejetant les inconnus
DEFAULT_TOLERANCE = 0.30

# Taille minimum du visage : 20% de la hauteur du frame
# Ignore les personnes au fond de la pièce
MIN_FACE_HEIGHT_RATIO = 0.20

# Seuil de centrage : 35% depuis le centre de l'image
# Le visage doit être relativement centré pour être considéré
CENTER_THRESHOLD_RATIO = 0.35

# Répertoire des modèles InsightFace
_DEFAULT_MODEL_DIR = Path(
    os.environ.get("APPDATA", Path.home() / ".config")
) / "PrankGuard" / "models"

# Répertoire des encodings du propriétaire
_DEFAULT_ENCODINGS_DIR = Path(
    os.environ.get("APPDATA", Path.home() / ".config")
) / "PrankGuard" / "encodings"

# Nombre d'encodings à stocker lors de l'enrollment (robustesse multi-angle)
MAX_OWNER_ENCODINGS = 10


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------

@dataclass
class DetectedFace:
    """Un visage détecté dans une frame."""
    identity: FaceIdentity
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    distance: float = 1.0            # Distance cosine (0 = identique, 2 = opposé)
    confidence: float = 0.0          # Score de détection InsightFace
    is_centered: bool = False        # Le visage est-il centré dans l'image ?
    is_large_enough: bool = False    # Le visage dépasse-t-il la taille minimum ?
    embedding: Optional[np.ndarray] = field(default=None, repr=False)


@dataclass
class RecognitionResult:
    """Résultat complet d'une analyse de frame."""
    faces: list[DetectedFace] = field(default_factory=list)
    owner_detected: bool = False
    stranger_detected: bool = False
    owner_and_stranger: bool = False  # Shoulder surfer détecté
    inference_ms: float = 0.0
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Face Recognition Agent
# ---------------------------------------------------------------------------

class FaceRecognitionAgent:
    """
    Agent de reconnaissance faciale basé sur InsightFace buffalo_sc.

    Utilise ONNX Runtime avec DmlExecutionProvider (GPU) en priorité,
    CPUExecutionProvider en fallback.

    Utilisation :
        agent = FaceRecognitionAgent(onnx_provider="DmlExecutionProvider")
        agent.load_model()
        agent.load_owner_encodings()
        result = agent.analyze(frame)
    """

    def __init__(
        self,
        onnx_provider: Optional[str] = None,
        tolerance: float = DEFAULT_TOLERANCE,
        model_name: str = "buffalo_sc",
        model_dir: Optional[Path] = None,
        encodings_dir: Optional[Path] = None,
    ) -> None:
        self._tolerance = tolerance
        self._model_name = model_name
        self._model_dir = model_dir or _DEFAULT_MODEL_DIR
        self._encodings_dir = encodings_dir or _DEFAULT_ENCODINGS_DIR

        # Déterminer le provider ONNX
        self._onnx_provider = onnx_provider or self._detect_best_provider()

        # État interne
        self._model = None  # insightface.app.FaceAnalysis
        self._owner_encodings: list[np.ndarray] = []
        self._is_loaded = False

    # ----- Propriétés publiques -----

    @property
    def is_loaded(self) -> bool:
        """True si le modèle est chargé et prêt."""
        return self._is_loaded

    @property
    def has_owner(self) -> bool:
        """True si au moins un encoding du propriétaire est chargé."""
        return len(self._owner_encodings) > 0

    @property
    def tolerance(self) -> float:
        """Tolérance de reconnaissance (distance cosine)."""
        return self._tolerance

    @tolerance.setter
    def tolerance(self, value: float) -> None:
        self._tolerance = max(0.01, min(value, 1.0))

    @property
    def provider(self) -> str:
        """Provider ONNX utilisé."""
        return self._onnx_provider

    # ----- Détection du meilleur provider -----

    @staticmethod
    def _detect_best_provider() -> str:
        """
        Détecte le meilleur provider ONNX disponible.
        Priorité : DmlExecutionProvider > CPUExecutionProvider
        """
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()
            if "DmlExecutionProvider" in available:
                logger.info("Provider GPU détecté : DmlExecutionProvider")
                return "DmlExecutionProvider"
        except ImportError:
            pass

        logger.info("Fallback sur CPUExecutionProvider")
        return "CPUExecutionProvider"

    # ----- Chargement du modèle -----

    def load_model(self) -> None:
        """
        Charge le modèle InsightFace buffalo_sc.
        Télécharge automatiquement le modèle si nécessaire.
        """
        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise RuntimeError(
                "InsightFace non installé. Exécuter : pip install insightface"
            ) from exc

        self._model_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Chargement du modèle %s (provider: %s)...",
            self._model_name, self._onnx_provider,
        )

        providers = [self._onnx_provider]
        if self._onnx_provider != "CPUExecutionProvider":
            providers.append("CPUExecutionProvider")  # Fallback

        try:
            self._model = FaceAnalysis(
                name=self._model_name,
                root=str(self._model_dir),
                providers=providers,
            )
            # Préparer le modèle (taille d'entrée par défaut 640x640)
            self._model.prepare(ctx_id=0, det_size=(640, 640))
            self._is_loaded = True
            logger.info("Modèle %s chargé avec succès", self._model_name)

        except Exception as exc:
            # Fallback CPU si le provider GPU échoue
            if self._onnx_provider != "CPUExecutionProvider":
                logger.warning(
                    "Échec avec %s, fallback CPU : %s",
                    self._onnx_provider, exc,
                )
                self._onnx_provider = "CPUExecutionProvider"
                self._model = FaceAnalysis(
                    name=self._model_name,
                    root=str(self._model_dir),
                    providers=["CPUExecutionProvider"],
                )
                self._model.prepare(ctx_id=0, det_size=(640, 640))
                self._is_loaded = True
                logger.info("Modèle chargé en mode CPU (fallback)")
            else:
                raise

    # ----- Gestion des encodings du propriétaire -----

    def enroll_owner(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """
        Enregistre un encoding du propriétaire à partir d'une frame.
        Retourne l'embedding si un visage est détecté, None sinon.

        RGPD : seul l'embedding (vecteur 512D) est conservé, PAS l'image.
        """
        if not self._is_loaded:
            raise RuntimeError("Modèle non chargé. Appeler load_model() d'abord.")

        faces = self._model.get(frame)
        if not faces:
            logger.warning("Aucun visage détecté pour l'enrollment")
            return None

        if len(faces) > 1:
            logger.warning(
                "Plusieurs visages détectés (%d). Utilisation du plus grand.",
                len(faces),
            )

        # Prendre le visage le plus grand (le plus proche)
        face = max(faces, key=lambda f: _bbox_area(f.bbox))
        embedding = face.normed_embedding

        if len(self._owner_encodings) < MAX_OWNER_ENCODINGS:
            self._owner_encodings.append(embedding)
            logger.info(
                "Encoding propriétaire ajouté (%d/%d)",
                len(self._owner_encodings), MAX_OWNER_ENCODINGS,
            )
        else:
            logger.warning("Nombre maximum d'encodings atteint (%d)", MAX_OWNER_ENCODINGS)

        return embedding

    def save_owner_encodings(self, path: Optional[Path] = None) -> Path:
        """
        Sauvegarde les encodings du propriétaire dans un fichier .npz.
        Note : le chiffrement AES-256 sera ajouté par le module de chiffrement (priorité 13).
        """
        if not self._owner_encodings:
            raise ValueError("Aucun encoding à sauvegarder")

        save_dir = path or self._encodings_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        filepath = save_dir / "owner_encodings.npz"

        np.savez(
            filepath,
            encodings=np.array(self._owner_encodings),
        )
        logger.info("Encodings sauvegardés dans %s", filepath)
        return filepath

    def load_owner_encodings(self, path: Optional[Path] = None) -> int:
        """
        Charge les encodings du propriétaire depuis un fichier .npz.
        Retourne le nombre d'encodings chargés.
        """
        load_dir = path or self._encodings_dir
        filepath = load_dir / "owner_encodings.npz"

        if not filepath.exists():
            logger.warning("Aucun encoding trouvé dans %s", filepath)
            return 0

        data = np.load(filepath)
        self._owner_encodings = list(data["encodings"])
        count = len(self._owner_encodings)
        logger.info("%d encodings propriétaire chargés", count)
        return count

    def clear_owner_encodings(self) -> None:
        """Supprime tous les encodings du propriétaire (RGPD : droit à l'effacement)."""
        self._owner_encodings.clear()
        filepath = self._encodings_dir / "owner_encodings.npz"
        if filepath.exists():
            filepath.unlink()
            logger.info("Encodings propriétaire supprimés")

    # ----- Analyse d'une frame -----

    def analyze(self, frame: np.ndarray) -> RecognitionResult:
        """
        Analyse une frame et identifie tous les visages détectés.

        Pour chaque visage :
          1. Vérifie la taille minimum (20% hauteur frame)
          2. Vérifie le centrage (35% depuis le centre)
          3. Compare l'embedding aux encodings du propriétaire
          4. Attribue OWNER, STRANGER ou UNKNOWN

        Retourne un RecognitionResult avec la liste des visages et les flags
        owner_detected / stranger_detected / owner_and_stranger.
        """
        if not self._is_loaded:
            raise RuntimeError("Modèle non chargé. Appeler load_model() d'abord.")

        start = time.perf_counter()
        result = RecognitionResult(timestamp=time.monotonic())

        # Détection + extraction des embeddings
        faces = self._model.get(frame)

        if not faces:
            result.inference_ms = (time.perf_counter() - start) * 1000.0
            return result

        frame_h, frame_w = frame.shape[:2]

        for face in faces:
            detected = self._classify_face(face, frame_w, frame_h)
            result.faces.append(detected)

            if detected.identity == FaceIdentity.OWNER:
                result.owner_detected = True
            elif detected.identity == FaceIdentity.STRANGER:
                result.stranger_detected = True

        # Détection shoulder surfer : owner ET stranger dans la même frame
        result.owner_and_stranger = result.owner_detected and result.stranger_detected

        result.inference_ms = (time.perf_counter() - start) * 1000.0

        logger.debug(
            "Analyse : %d visage(s) — owner=%s stranger=%s (%.1f ms)",
            len(result.faces),
            result.owner_detected,
            result.stranger_detected,
            result.inference_ms,
        )

        return result

    def _classify_face(
        self, face, frame_w: int, frame_h: int
    ) -> DetectedFace:
        """Classifie un visage détecté par InsightFace."""
        bbox = tuple(int(v) for v in face.bbox)
        x1, y1, x2, y2 = bbox
        confidence = float(face.det_score) if hasattr(face, "det_score") else 0.0

        # Vérifier la taille minimum (20% hauteur frame)
        face_height = y2 - y1
        is_large_enough = (face_height / frame_h) >= MIN_FACE_HEIGHT_RATIO

        # Vérifier le centrage (35% depuis le centre)
        face_cx = (x1 + x2) / 2.0
        face_cy = (y1 + y2) / 2.0
        center_x = frame_w / 2.0
        center_y = frame_h / 2.0
        offset_x = abs(face_cx - center_x) / frame_w
        offset_y = abs(face_cy - center_y) / frame_h
        is_centered = offset_x <= CENTER_THRESHOLD_RATIO and offset_y <= CENTER_THRESHOLD_RATIO

        # Si le visage est trop petit → UNKNOWN (personne au fond de la pièce)
        if not is_large_enough:
            return DetectedFace(
                identity=FaceIdentity.UNKNOWN,
                bbox=bbox,
                confidence=confidence,
                is_centered=is_centered,
                is_large_enough=False,
            )

        # Comparer aux encodings du propriétaire
        embedding = face.normed_embedding
        distance = self._compute_min_distance(embedding)

        if distance <= self._tolerance:
            identity = FaceIdentity.OWNER
        else:
            identity = FaceIdentity.STRANGER

        return DetectedFace(
            identity=identity,
            bbox=bbox,
            distance=round(distance, 4),
            confidence=confidence,
            is_centered=is_centered,
            is_large_enough=True,
            embedding=embedding,
        )

    def _compute_min_distance(self, embedding: np.ndarray) -> float:
        """
        Calcule la distance cosine minimale entre l'embedding donné
        et tous les encodings du propriétaire.

        Distance cosine : 0 = identique, 2 = opposé.
        Retourne 1.0 si aucun encoding de référence.
        """
        if not self._owner_encodings:
            return 1.0

        distances = []
        for ref in self._owner_encodings:
            # Distance cosine = 1 - similarité cosine
            similarity = np.dot(embedding, ref)
            distance = 1.0 - similarity
            distances.append(distance)

        return min(distances)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _bbox_area(bbox) -> float:
    """Calcule l'aire d'une bounding box [x1, y1, x2, y2]."""
    x1, y1, x2, y2 = bbox
    return max(0, x2 - x1) * max(0, y2 - y1)


# ---------------------------------------------------------------------------
# Exécution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    print("Face Recognition Agent — test")
    print("=" * 50)

    agent = FaceRecognitionAgent()

    print(f"  Provider ONNX : {agent.provider}")
    print(f"  Tolérance     : {agent.tolerance}")
    print(f"  Modèle chargé : {agent.is_loaded}")

    try:
        agent.load_model()
        print(f"  Modèle chargé : {agent.is_loaded}")
    except Exception as exc:
        print(f"  Erreur chargement modèle : {exc}")
        raise SystemExit(1)

    # Charger les encodings existants
    count = agent.load_owner_encodings()
    print(f"  Encodings propriétaire : {count}")

    # Test sur une frame de la webcam
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            result = agent.analyze(frame)
            print(f"\n  Résultat d'analyse :")
            print(f"    Visages détectés  : {len(result.faces)}")
            print(f"    Owner détecté     : {result.owner_detected}")
            print(f"    Stranger détecté  : {result.stranger_detected}")
            print(f"    Shoulder surfer   : {result.owner_and_stranger}")
            print(f"    Temps d'inférence : {result.inference_ms:.1f} ms")
            for i, face in enumerate(result.faces):
                print(f"    Visage {i+1}: {face.identity.value} "
                      f"(dist={face.distance:.3f}, conf={face.confidence:.2f}, "
                      f"centré={face.is_centered}, taille_ok={face.is_large_enough})")
        cap.release()
    else:
        print("  Caméra non disponible pour le test")

    print(f"\n{'=' * 50}")
    print("Test terminé.")
