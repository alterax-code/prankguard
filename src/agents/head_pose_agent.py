# -*- coding: utf-8 -*-
"""
Head Pose Agent — PrankGuard v3.0

Calcule l'orientation 3D de la tête (Yaw, Pitch, Roll) via MediaPipe FaceMesh
et l'algorithme solvePnP d'OpenCV. Détermine si la personne regarde vers l'écran.

Seuils (section 4.3 du plan v3) :
  - Yaw < 25° ET Pitch < 20° → tête orientée vers l'écran → suspect
  - Yaw > 25° OU Pitch > 20° → personne regarde ailleurs → passage innocent

Thread : actif seulement (déclenché par le motion_agent en phase ACTIVE).
Profil requis : tous (très léger).

Dépendances : mediapipe, opencv-python, numpy
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("prankguard.head_pose_agent")


# ---------------------------------------------------------------------------
# Constantes (section 4.3 du plan v3)
# ---------------------------------------------------------------------------

# Seuils de détection : en dessous → la tête est orientée vers l'écran
YAW_THRESHOLD_DEG = 25.0    # Rotation gauche/droite
PITCH_THRESHOLD_DEG = 20.0  # Rotation haut/bas

# Indices des landmarks MediaPipe FaceMesh utilisés pour solvePnP
# Pointe du nez, menton, coin gauche œil gauche, coin droit œil droit,
# coin gauche bouche, coin droit bouche
_LANDMARK_INDICES = [1, 152, 33, 263, 61, 291]

# Points 3D de référence du modèle de visage générique (en mm)
# Correspondant aux landmarks ci-dessus, dans le même ordre
_MODEL_POINTS_3D = np.array([
    (0.0, 0.0, 0.0),          # Pointe du nez
    (0.0, -330.0, -65.0),     # Menton
    (-225.0, 170.0, -135.0),  # Coin gauche œil gauche
    (225.0, 170.0, -135.0),   # Coin droit œil droit
    (-150.0, -150.0, -125.0), # Coin gauche bouche
    (150.0, -150.0, -125.0),  # Coin droit bouche
], dtype=np.float64)


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------

@dataclass
class HeadPoseResult:
    """Résultat de l'estimation de la pose de la tête."""
    yaw: float = 0.0          # Rotation gauche/droite en degrés
    pitch: float = 0.0         # Rotation haut/bas en degrés
    roll: float = 0.0          # Inclinaison latérale en degrés
    looking_at_screen: bool = False  # True si Yaw < 25° ET Pitch < 20°
    face_detected: bool = False
    inference_ms: float = 0.0
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Head Pose Agent
# ---------------------------------------------------------------------------

class HeadPoseAgent:
    """
    Agent d'estimation de la pose de la tête via MediaPipe FaceMesh + solvePnP.

    Utilise 6 landmarks clés du visage pour résoudre le problème PnP (Perspective-n-Point)
    et obtenir les angles Yaw/Pitch/Roll en degrés.

    Utilisation :
        agent = HeadPoseAgent()
        result = agent.analyze(frame)
        if result.looking_at_screen:
            print("La personne regarde l'écran")
    """

    def __init__(
        self,
        yaw_threshold: float = YAW_THRESHOLD_DEG,
        pitch_threshold: float = PITCH_THRESHOLD_DEG,
        max_num_faces: int = 2,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        self._yaw_threshold = yaw_threshold
        self._pitch_threshold = pitch_threshold

        # Paramètres MediaPipe
        self._max_num_faces = max_num_faces
        self._min_detection_confidence = min_detection_confidence
        self._min_tracking_confidence = min_tracking_confidence

        # Initialisé au premier appel (lazy loading)
        self._face_mesh = None
        self._is_initialized = False

        # Matrice caméra et coefficients de distorsion (calculés par frame)
        self._camera_matrix: Optional[np.ndarray] = None
        self._dist_coeffs = np.zeros((4, 1), dtype=np.float64)

    # ----- Propriétés publiques -----

    @property
    def is_initialized(self) -> bool:
        """True si MediaPipe FaceMesh est initialisé."""
        return self._is_initialized

    @property
    def yaw_threshold(self) -> float:
        return self._yaw_threshold

    @yaw_threshold.setter
    def yaw_threshold(self, value: float) -> None:
        self._yaw_threshold = max(1.0, value)

    @property
    def pitch_threshold(self) -> float:
        return self._pitch_threshold

    @pitch_threshold.setter
    def pitch_threshold(self, value: float) -> None:
        self._pitch_threshold = max(1.0, value)

    # ----- Initialisation -----

    def initialize(self) -> None:
        """Initialise MediaPipe FaceMesh (peut être appelé manuellement ou au premier analyze)."""
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
            refine_landmarks=False,  # Pas besoin des landmarks iris ici
            min_detection_confidence=self._min_detection_confidence,
            min_tracking_confidence=self._min_tracking_confidence,
        )
        self._is_initialized = True
        logger.info("Head Pose Agent initialisé (MediaPipe FaceMesh)")

    def release(self) -> None:
        """Libère les ressources MediaPipe."""
        if self._face_mesh is not None:
            self._face_mesh.close()
            self._face_mesh = None
            self._is_initialized = False
            logger.info("Head Pose Agent libéré")

    # ----- Analyse -----

    def analyze(self, frame: np.ndarray) -> HeadPoseResult:
        """
        Analyse une frame et calcule la pose de la tête du visage principal.

        Retourne un HeadPoseResult avec Yaw, Pitch, Roll et le flag looking_at_screen.
        Si plusieurs visages sont détectés, analyse le plus grand (le plus proche).
        """
        if not self._is_initialized:
            self.initialize()

        start = time.perf_counter()
        result = HeadPoseResult(timestamp=time.monotonic())

        h, w = frame.shape[:2]

        # Mettre à jour la matrice caméra si la résolution change
        self._update_camera_matrix(w, h)

        # Convertir BGR → RGB pour MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_result = self._face_mesh.process(rgb)

        if not mp_result.multi_face_landmarks:
            result.inference_ms = (time.perf_counter() - start) * 1000.0
            return result

        # Prendre le premier visage détecté (MediaPipe les trie par proximité)
        landmarks = mp_result.multi_face_landmarks[0]

        # Extraire les 6 points 2D correspondant aux landmarks de référence
        image_points = self._extract_image_points(landmarks, w, h)

        # Résoudre PnP pour obtenir les vecteurs de rotation et translation
        yaw, pitch, roll = self._solve_pose(image_points)

        result.yaw = round(yaw, 1)
        result.pitch = round(pitch, 1)
        result.roll = round(roll, 1)
        result.face_detected = True
        result.looking_at_screen = (
            abs(yaw) < self._yaw_threshold and abs(pitch) < self._pitch_threshold
        )
        result.inference_ms = (time.perf_counter() - start) * 1000.0

        logger.debug(
            "Head pose : Yaw=%.1f° Pitch=%.1f° Roll=%.1f° → %s (%.1f ms)",
            result.yaw, result.pitch, result.roll,
            "ÉCRAN" if result.looking_at_screen else "ailleurs",
            result.inference_ms,
        )

        return result

    def analyze_multiple(self, frame: np.ndarray) -> list[HeadPoseResult]:
        """
        Analyse une frame et retourne la pose de TOUS les visages détectés.
        Utile pour le decision_agent qui a besoin de croiser avec les résultats
        du face_recognition_agent.
        """
        if not self._is_initialized:
            self.initialize()

        start = time.perf_counter()
        h, w = frame.shape[:2]
        self._update_camera_matrix(w, h)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_result = self._face_mesh.process(rgb)

        results = []

        if not mp_result.multi_face_landmarks:
            return results

        elapsed_per_face = 0.0
        for landmarks in mp_result.multi_face_landmarks:
            image_points = self._extract_image_points(landmarks, w, h)
            yaw, pitch, roll = self._solve_pose(image_points)

            r = HeadPoseResult(
                yaw=round(yaw, 1),
                pitch=round(pitch, 1),
                roll=round(roll, 1),
                looking_at_screen=(
                    abs(yaw) < self._yaw_threshold and abs(pitch) < self._pitch_threshold
                ),
                face_detected=True,
                timestamp=time.monotonic(),
            )
            results.append(r)

        total_ms = (time.perf_counter() - start) * 1000.0
        for r in results:
            r.inference_ms = round(total_ms / len(results), 2)

        return results

    # ----- Analyse depuis résultat MediaPipe partagé -----

    def analyze_from_mp_result(self, mp_result, w: int, h: int) -> HeadPoseResult:
        """
        Analyse a partir d'un resultat MediaPipe pre-calcule (FaceMesh partage).
        Evite de creer une seconde instance FaceMesh.
        """
        start = time.perf_counter()
        result = HeadPoseResult(timestamp=time.monotonic())

        self._update_camera_matrix(w, h)

        if not mp_result or not mp_result.multi_face_landmarks:
            result.inference_ms = (time.perf_counter() - start) * 1000.0
            return result

        landmarks = mp_result.multi_face_landmarks[0]
        image_points = self._extract_image_points(landmarks, w, h)
        yaw, pitch, roll = self._solve_pose(image_points)

        result.yaw = round(yaw, 1)
        result.pitch = round(pitch, 1)
        result.roll = round(roll, 1)
        result.face_detected = True
        result.looking_at_screen = (
            abs(yaw) < self._yaw_threshold and abs(pitch) < self._pitch_threshold
        )
        result.inference_ms = (time.perf_counter() - start) * 1000.0
        return result

    # ----- Méthodes internes -----

    def _update_camera_matrix(self, w: int, h: int) -> None:
        """
        Calcule la matrice caméra approximative à partir de la résolution.
        Utilise la longueur focale estimée = largeur de l'image.
        """
        if self._camera_matrix is not None:
            if self._camera_matrix[0, 2] == w / 2.0:
                return  # Même résolution, pas de recalcul

        focal_length = float(w)
        center = (w / 2.0, h / 2.0)

        self._camera_matrix = np.array([
            [focal_length, 0.0, center[0]],
            [0.0, focal_length, center[1]],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)

    def _extract_image_points(
        self, landmarks, w: int, h: int
    ) -> np.ndarray:
        """
        Extrait les coordonnées 2D (en pixels) des 6 landmarks de référence
        à partir des landmarks MediaPipe normalisés [0, 1].
        """
        points = []
        for idx in _LANDMARK_INDICES:
            lm = landmarks.landmark[idx]
            x = lm.x * w
            y = lm.y * h
            points.append((x, y))

        return np.array(points, dtype=np.float64)

    def _solve_pose(self, image_points: np.ndarray) -> tuple[float, float, float]:
        """
        Résout le problème PnP pour obtenir Yaw, Pitch, Roll en degrés.

        Utilise cv2.solvePnP avec le modèle de visage 3D générique
        et les 6 points 2D extraits de MediaPipe.
        """
        success, rotation_vec, translation_vec = cv2.solvePnP(
            _MODEL_POINTS_3D,
            image_points,
            self._camera_matrix,
            self._dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )

        if not success:
            logger.warning("solvePnP a échoué")
            return 0.0, 0.0, 0.0

        # Convertir le vecteur de rotation en matrice de rotation
        rotation_mat, _ = cv2.Rodrigues(rotation_vec)

        # Extraire les angles d'Euler depuis la matrice de rotation
        # Décomposition : projectPoints n'est pas nécessaire, on utilise
        # directement les éléments de la matrice
        yaw, pitch, roll = self._rotation_matrix_to_euler(rotation_mat)

        return yaw, pitch, roll

    @staticmethod
    def _rotation_matrix_to_euler(R: np.ndarray) -> tuple[float, float, float]:
        """
        Convertit une matrice de rotation 3×3 en angles d'Euler (Yaw, Pitch, Roll)
        en degrés. Convention : rotation intrinsèque ZYX.
        """
        sy = np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)

        singular = sy < 1e-6

        if not singular:
            pitch = np.degrees(np.arctan2(R[2, 1], R[2, 2]))   # Rotation X
            yaw = np.degrees(np.arctan2(-R[2, 0], sy))          # Rotation Y
            roll = np.degrees(np.arctan2(R[1, 0], R[0, 0]))     # Rotation Z
        else:
            pitch = np.degrees(np.arctan2(-R[1, 2], R[1, 1]))
            yaw = np.degrees(np.arctan2(-R[2, 0], sy))
            roll = 0.0

        return yaw, pitch, roll


# ---------------------------------------------------------------------------
# Exécution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    print("Head Pose Agent — test en direct (Ctrl+C pour quitter)")
    print("=" * 50)

    agent = HeadPoseAgent()
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
                    f"  Yaw={result.yaw:+6.1f}°  "
                    f"Pitch={result.pitch:+6.1f}°  "
                    f"Roll={result.roll:+6.1f}°  "
                    f"[{status}]  ({result.inference_ms:.1f} ms)"
                )
            else:
                print("  Aucun visage détecté")

            # ~10 FPS pour le test
            if cv2.waitKey(100) & 0xFF == ord("q"):
                break

    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        agent.release()
        print("\nTest terminé.")
