"""
Analyse faciale avec optimisation frame skip.
FIX 6 — Capture à 30fps, analyse 1 frame sur N, affiche tous les frames
         avec les rectangles du dernier résultat.
Pipeline hybride : YuNet (détection rapide) + dlib (encodage), fallback dlib HOG.
"""
import logging
import os
import urllib.request
import cv2
import face_recognition
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

YUNET_MODEL_PATH = os.path.join("data", "models", "yunet.onnx")
YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)


def _ensure_yunet_model() -> Optional[str]:
    """Télécharge yunet.onnx si absent. Retourne le chemin ou None en cas d'erreur."""
    if os.path.exists(YUNET_MODEL_PATH):
        return YUNET_MODEL_PATH
    try:
        os.makedirs(os.path.dirname(YUNET_MODEL_PATH), exist_ok=True)
        logger.info("Telechargement modele YuNet...")
        urllib.request.urlretrieve(YUNET_URL, YUNET_MODEL_PATH)
        return YUNET_MODEL_PATH
    except Exception as exc:
        logger.warning("YuNet indisponible: %s — fallback dlib HOG", exc)
        return None


@dataclass
class FaceResult:
    """Résultat d'analyse pour un visage détecté."""
    location: Tuple[int, int, int, int]  # (top, right, bottom, left)
    is_owner: bool
    distance: float
    face_size: float      # Taille relative du visage (0-1)
    center_offset: float  # Décalage par rapport au centre (0-1)
    is_looking: bool      # Centré + assez grand = regarde l'écran
    name: str = "?"       # Nom de l'utilisateur reconnu (Sprint 2)


class FaceAnalyzer:
    """
    Wrapper autour de face_recognition avec frame skip.
    Analyse 1 frame sur N, garde le dernier résultat pour l'affichage.
    """

    def __init__(
        self,
        owner_encodings: list = None,
        tolerance: float = 0.45,
        min_face_size: float = 0.20,
        center_threshold: float = 0.35,
        analyze_every_n: int = 3,
        detection_scale: float = 0.33,
        authorized_users: dict = None,
    ):
        # Construire les listes plates d'encodings + labels depuis authorized_users
        if authorized_users is not None:
            self._user_encodings: list = []
            self._user_labels: list = []
            for uname, encs in authorized_users.items():
                for enc in encs:
                    self._user_encodings.append(enc)
                    self._user_labels.append(uname)
        else:
            self._user_encodings = list(owner_encodings) if owner_encodings else []
            self._user_labels = ["owner"] * len(self._user_encodings)

        # Alias backward-compat
        self.owner_encodings = self._user_encodings
        self.tolerance = tolerance
        self.min_face_size = min_face_size
        self.center_threshold = center_threshold
        self.analyze_every_n = analyze_every_n
        self.detection_scale = detection_scale

        self._frame_count = 0
        self._last_results: List[FaceResult] = []

        # Détection YuNet (optionnelle — fallback dlib HOG si indisponible)
        self._use_yunet = False
        self._yunet = None
        self._yunet_size = (0, 0)
        model_path = _ensure_yunet_model()
        if model_path:
            try:
                self._yunet = cv2.FaceDetectorYN.create(
                    model_path, "", (320, 240),
                    score_threshold=0.6,
                    nms_threshold=0.3,
                    top_k=5000,
                )
                self._use_yunet = True
                logger.info("YuNet charge: %s", model_path)
            except Exception as exc:
                logger.warning("YuNet init echoue: %s — fallback dlib HOG", exc)

    @property
    def last_results(self) -> List[FaceResult]:
        """Dernier résultat d'analyse (pour l'affichage continu)."""
        return self._last_results

    def process_frame(self, frame: np.ndarray) -> Optional[List[FaceResult]]:
        """
        Traite un frame. Retourne les résultats si c'est un frame d'analyse,
        None sinon (utiliser last_results pour l'affichage).
        """
        self._frame_count += 1

        if self._frame_count % self.analyze_every_n != 0:
            return None  # Pas un frame d'analyse

        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Downscale pour la détection (plus rapide), encode sur frame full-res
        s = self.detection_scale
        if self._use_yunet:
            small_bgr = cv2.resize(frame, (0, 0), fx=s, fy=s)
            locs = self._detect_faces_yunet(small_bgr, s, h, w)
        else:
            small_rgb = cv2.resize(rgb, (0, 0), fx=s, fy=s)
            locs_small = face_recognition.face_locations(small_rgb)
            locs = [(int(t / s), int(r / s), int(b / s), int(l / s))
                    for t, r, b, l in locs_small]
        results = []

        if locs:
            encs = face_recognition.face_encodings(
                rgb, known_face_locations=locs, num_jitters=1, model="large"
            )
            for loc, enc in zip(locs, encs):
                t, r, b, l = loc
                if self._user_encodings:
                    dists = face_recognition.face_distance(self._user_encodings, enc)
                    best_idx = int(np.argmin(dists))
                    best_dist = float(dists[best_idx])
                    is_own = best_dist <= self.tolerance
                    matched_name = self._user_labels[best_idx] if is_own else "?"
                else:
                    best_dist = 1.0
                    is_own = False
                    matched_name = "?"
                sz = (b - t) / h
                off = abs((l + r) / 2 - w / 2) / (w / 2)
                looking = sz >= self.min_face_size and off <= self.center_threshold

                results.append(FaceResult(
                    location=loc,
                    is_owner=is_own,
                    distance=best_dist,
                    face_size=sz,
                    center_offset=off,
                    is_looking=looking,
                    name=matched_name,
                ))

        self._last_results = results
        return results

    def _detect_faces_yunet(
        self, small_bgr: np.ndarray, scale: float, orig_h: int, orig_w: int
    ) -> List[Tuple[int, int, int, int]]:
        """Détecte les visages via YuNet sur frame BGR réduit.
        Retourne des locs (top, right, bottom, left) en coordonnées full-res."""
        sh, sw = small_bgr.shape[:2]
        if (sw, sh) != self._yunet_size:
            self._yunet.setInputSize((sw, sh))
            self._yunet_size = (sw, sh)
        _, faces = self._yunet.detect(small_bgr)
        if faces is None:
            return []
        locs = []
        for face in faces:
            x, y, fw, fh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
            top = max(0, int(y / scale))
            left = max(0, int(x / scale))
            bottom = min(orig_h, int((y + fh) / scale))
            right = min(orig_w, int((x + fw) / scale))
            locs.append((top, right, bottom, left))
        return locs

    def draw_on_frame(self, frame: np.ndarray) -> np.ndarray:
        """Dessine les rectangles et labels sur le frame (résultats en cache)."""
        for r in self._last_results:
            t, right, b, l = r.location

            if r.is_owner:
                col = (0, 255, 0)
                lbl = r.name  # Affiche le nom de l'utilisateur reconnu
            elif r.is_looking:
                col = (0, 0, 255)  # BGR rouge
                lbl = "THREAT"
            else:
                col = (0, 165, 255)  # BGR orange
                lbl = "PASSING"

            cv2.rectangle(frame, (l, t), (right, b), col, 2)
            cv2.putText(
                frame, f"{lbl} {r.distance:.2f}",
                (l, t - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2
            )

        return frame

    def get_situation(self) -> dict:
        """
        Résumé de la situation courante basé sur le dernier résultat.
        Retourne un dict avec : owner, threat, passing, info
        """
        owner = any(r.is_owner for r in self._last_results)
        threat = any(not r.is_owner and r.is_looking for r in self._last_results)
        passing = any(not r.is_owner and not r.is_looking for r in self._last_results)

        # Info texte pour la GUI
        if self._last_results:
            r = self._last_results[0]
            info = f"Sz:{r.face_size:.0%} D:{r.distance:.2f}"
        else:
            info = "--"

        return {
            "owner": owner,
            "threat": threat,
            "passing": passing,
            "info": info,
            "face_count": len(self._last_results),
        }
