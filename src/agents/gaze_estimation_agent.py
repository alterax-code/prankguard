# -*- coding: utf-8 -*-
"""
Gaze Estimation Agent — PrankGuard v3.0

Utilise MediaPipe FaceMesh (468 + 10 landmarks iris) pour calculer le vecteur
de regard 2D et déterminer si la personne regarde vers l'écran.

Le calcul se base sur la position relative des iris par rapport aux contours
des yeux : si l'iris est centré → la personne regarde droit devant (l'écran).

Désactivé automatiquement en profil LITE (section 5 du plan v3).
Thread : actif seulement (déclenché par le motion_agent en phase ACTIVE).
Profil requis : BALANCED et PERFORMANCE uniquement.

Dépendances : mediapipe, opencv-python, numpy
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("prankguard.gaze_estimation_agent")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Seuil de déviation du regard par rapport au centre de l'œil
# En ratio normalisé [0, 1] : 0 = centre parfait, 1 = extrême
# En dessous de ce seuil → la personne regarde l'écran
_GAZE_CENTER_THRESHOLD = 0.25

# Indices des landmarks MediaPipe FaceMesh pour les contours des yeux
# Œil gauche (du point de vue du sujet)
_LEFT_EYE_CONTOUR = [33, 160, 158, 133, 153, 144]
# Œil droit (du point de vue du sujet)
_RIGHT_EYE_CONTOUR = [362, 385, 387, 263, 373, 380]

# Indices des landmarks iris (nécessite refine_landmarks=True)
# Iris gauche : centre = 468, contour = 469-472
_LEFT_IRIS_CENTER = 468
_LEFT_IRIS_INDICES = [468, 469, 470, 471, 472]
# Iris droit : centre = 473, contour = 474-477
_RIGHT_IRIS_CENTER = 473
_RIGHT_IRIS_INDICES = [473, 474, 475, 476, 477]


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------

@dataclass
class GazeResult:
    """Résultat de l'estimation du regard."""
    looking_at_screen: bool = False
    left_gaze_ratio: float = 0.0    # Déviation iris gauche (0 = centre)
    right_gaze_ratio: float = 0.0   # Déviation iris droit (0 = centre)
    average_gaze_ratio: float = 0.0 # Moyenne des deux yeux
    face_detected: bool = False
    inference_ms: float = 0.0
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Gaze Estimation Agent
# ---------------------------------------------------------------------------

class GazeEstimationAgent:
    """
    Agent d'estimation du regard via MediaPipe FaceMesh (landmarks iris).

    Calcule la position relative des iris par rapport aux contours des yeux
    pour déterminer la direction du regard.

    Utilisation :
        agent = GazeEstimationAgent()
        result = agent.analyze(frame)
        if result.looking_at_screen:
            print("La personne regarde l'écran")
    """

    def __init__(
        self,
        gaze_threshold: float = _GAZE_CENTER_THRESHOLD,
        max_num_faces: int = 2,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        self._gaze_threshold = gaze_threshold
        self._max_num_faces = max_num_faces
        self._min_detection_confidence = min_detection_confidence
        self._min_tracking_confidence = min_tracking_confidence

        # Initialisé au premier appel (lazy loading)
        self._face_mesh = None
        self._is_initialized = False

    # ----- Propriétés publiques -----

    @property
    def is_initialized(self) -> bool:
        """True si MediaPipe FaceMesh est initialisé."""
        return self._is_initialized

    @property
    def gaze_threshold(self) -> float:
        return self._gaze_threshold

    @gaze_threshold.setter
    def gaze_threshold(self, value: float) -> None:
        self._gaze_threshold = max(0.01, min(value, 1.0))

    # ----- Initialisation -----

    def initialize(self) -> None:
        """Initialise MediaPipe FaceMesh avec les landmarks iris activés."""
        if self._is_initialized:
            return

        try:
            import mediapipe as mp
        except ImportError as exc:
            raise RuntimeError(
                "MediaPipe non installé. Exécuter : pip install mediapipe"
            ) from exc

        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=self._max_num_faces,
            refine_landmarks=True,  # IMPORTANT : active les landmarks iris (468-477)
            min_detection_confidence=self._min_detection_confidence,
            min_tracking_confidence=self._min_tracking_confidence,
        )
        self._is_initialized = True
        logger.info("Gaze Estimation Agent initialisé (MediaPipe FaceMesh + iris)")

    def release(self) -> None:
        """Libère les ressources MediaPipe."""
        if self._face_mesh is not None:
            self._face_mesh.close()
            self._face_mesh = None
            self._is_initialized = False
            logger.info("Gaze Estimation Agent libéré")

    # ----- Analyse -----

    def analyze(self, frame: np.ndarray) -> GazeResult:
        """
        Analyse une frame et détermine si la personne regarde l'écran.

        Calcule la position de chaque iris par rapport au contour de l'œil
        correspondant. Si les deux iris sont proches du centre → regarde l'écran.
        """
        if not self._is_initialized:
            self.initialize()

        start = time.perf_counter()
        result = GazeResult(timestamp=time.monotonic())

        h, w = frame.shape[:2]

        # Convertir BGR → RGB pour MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_result = self._face_mesh.process(rgb)

        if not mp_result.multi_face_landmarks:
            result.inference_ms = (time.perf_counter() - start) * 1000.0
            return result

        # Prendre le premier visage
        landmarks = mp_result.multi_face_landmarks[0]
        result.face_detected = True

        # Calculer le ratio de déviation pour chaque œil
        result.left_gaze_ratio = self._compute_eye_gaze_ratio(
            landmarks, w, h, _LEFT_EYE_CONTOUR, _LEFT_IRIS_CENTER
        )
        result.right_gaze_ratio = self._compute_eye_gaze_ratio(
            landmarks, w, h, _RIGHT_EYE_CONTOUR, _RIGHT_IRIS_CENTER
        )

        # Moyenne des deux yeux
        result.average_gaze_ratio = round(
            (result.left_gaze_ratio + result.right_gaze_ratio) / 2.0, 4
        )

        # Verdict : regarde l'écran si la déviation moyenne est faible
        result.looking_at_screen = result.average_gaze_ratio <= self._gaze_threshold

        result.inference_ms = (time.perf_counter() - start) * 1000.0

        logger.debug(
            "Gaze : L=%.3f R=%.3f avg=%.3f → %s (%.1f ms)",
            result.left_gaze_ratio,
            result.right_gaze_ratio,
            result.average_gaze_ratio,
            "ÉCRAN" if result.looking_at_screen else "ailleurs",
            result.inference_ms,
        )

        return result

    def analyze_multiple(self, frame: np.ndarray) -> list[GazeResult]:
        """
        Analyse une frame et retourne le gaze de TOUS les visages détectés.
        Utile pour le decision_agent.
        """
        if not self._is_initialized:
            self.initialize()

        start = time.perf_counter()
        h, w = frame.shape[:2]

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_result = self._face_mesh.process(rgb)

        results = []

        if not mp_result.multi_face_landmarks:
            return results

        for landmarks in mp_result.multi_face_landmarks:
            left = self._compute_eye_gaze_ratio(
                landmarks, w, h, _LEFT_EYE_CONTOUR, _LEFT_IRIS_CENTER
            )
            right = self._compute_eye_gaze_ratio(
                landmarks, w, h, _RIGHT_EYE_CONTOUR, _RIGHT_IRIS_CENTER
            )
            avg = round((left + right) / 2.0, 4)

            r = GazeResult(
                looking_at_screen=avg <= self._gaze_threshold,
                left_gaze_ratio=left,
                right_gaze_ratio=right,
                average_gaze_ratio=avg,
                face_detected=True,
                timestamp=time.monotonic(),
            )
            results.append(r)

        total_ms = (time.perf_counter() - start) * 1000.0
        for r in results:
            r.inference_ms = round(total_ms / len(results), 2)

        return results

    # ----- Analyse depuis résultat MediaPipe partagé -----

    def analyze_from_mp_result(self, mp_result, w: int, h: int) -> GazeResult:
        """
        Analyse a partir d'un resultat MediaPipe pre-calcule (FaceMesh partage).
        Evite de creer une seconde instance FaceMesh.
        """
        start = time.perf_counter()
        result = GazeResult(timestamp=time.monotonic())

        if not mp_result or not mp_result.multi_face_landmarks:
            result.inference_ms = (time.perf_counter() - start) * 1000.0
            return result

        landmarks = mp_result.multi_face_landmarks[0]
        result.face_detected = True

        result.left_gaze_ratio = self._compute_eye_gaze_ratio(
            landmarks, w, h, _LEFT_EYE_CONTOUR, _LEFT_IRIS_CENTER
        )
        result.right_gaze_ratio = self._compute_eye_gaze_ratio(
            landmarks, w, h, _RIGHT_EYE_CONTOUR, _RIGHT_IRIS_CENTER
        )
        result.average_gaze_ratio = round(
            (result.left_gaze_ratio + result.right_gaze_ratio) / 2.0, 4
        )
        result.looking_at_screen = result.average_gaze_ratio <= self._gaze_threshold
        result.inference_ms = (time.perf_counter() - start) * 1000.0
        return result

    # ----- Calcul interne -----

    def _compute_eye_gaze_ratio(
        self,
        landmarks,
        w: int,
        h: int,
        eye_contour_indices: list[int],
        iris_center_index: int,
    ) -> float:
        """
        Calcule le ratio de déviation de l'iris par rapport au centre de l'œil.

        Retourne un float entre 0.0 (iris parfaitement centré = regarde droit)
        et ~1.0 (iris au bord de l'œil = regarde sur le côté).
        """
        # Extraire les points du contour de l'œil en pixels
        eye_points = []
        for idx in eye_contour_indices:
            lm = landmarks.landmark[idx]
            eye_points.append((lm.x * w, lm.y * h))

        eye_points = np.array(eye_points, dtype=np.float64)

        # Centre géométrique de l'œil
        eye_center_x = np.mean(eye_points[:, 0])
        eye_center_y = np.mean(eye_points[:, 1])

        # Position de l'iris
        iris_lm = landmarks.landmark[iris_center_index]
        iris_x = iris_lm.x * w
        iris_y = iris_lm.y * h

        # Dimensions de l'œil (pour normaliser)
        eye_width = np.max(eye_points[:, 0]) - np.min(eye_points[:, 0])
        eye_height = np.max(eye_points[:, 1]) - np.min(eye_points[:, 1])

        if eye_width < 1.0 or eye_height < 1.0:
            return 0.5  # Œil trop petit, valeur neutre

        # Déviation normalisée de l'iris par rapport au centre
        dx = abs(iris_x - eye_center_x) / (eye_width / 2.0)
        dy = abs(iris_y - eye_center_y) / (eye_height / 2.0)

        # Distance euclidienne normalisée
        ratio = float(np.sqrt(dx ** 2 + dy ** 2))

        return round(min(ratio, 2.0), 4)


# ---------------------------------------------------------------------------
# Exécution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    print("Gaze Estimation Agent — test en direct (Ctrl+C pour quitter)")
    print("=" * 50)

    agent = GazeEstimationAgent()
    agent.initialize()

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("Caméra non disponible")
        raise SystemExit(1)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            result = agent.analyze(frame)

            if result.face_detected:
                status = ">>> ÉCRAN <<<" if result.looking_at_screen else "    ailleurs"
                print(
                    f"  L={result.left_gaze_ratio:.3f}  "
                    f"R={result.right_gaze_ratio:.3f}  "
                    f"avg={result.average_gaze_ratio:.3f}  "
                    f"[{status}]  ({result.inference_ms:.1f} ms)"
                )
            else:
                print("  Aucun visage détecté")

            if cv2.waitKey(100) & 0xFF == ord("q"):
                break

    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        agent.release()
        print("\nTest terminé.")
