import cv2
import mediapipe as mp
import numpy as np
from dataclasses import dataclass
from typing import Optional, List, Tuple


@dataclass
class FaceData:
    """Detected face data."""
    bbox: Tuple[int, int, int, int]  # x, y, w, h
    landmarks: np.ndarray  # 468 landmarks
    yaw: float  # Head rotation left/right
    pitch: float  # Head rotation up/down
    roll: float  # Head tilt
    center_offset: Tuple[float, float]  # Normalized offset from frame center
    size_ratio: float  # Face height / frame height


class FaceDetector:
    """MediaPipe-based face detection with head pose estimation."""

    # Indices for head pose estimation
    NOSE_TIP = 1
    CHIN = 199
    LEFT_EYE_OUTER = 33
    RIGHT_EYE_OUTER = 263
    LEFT_MOUTH = 61
    RIGHT_MOUTH = 291

    def __init__(self, min_detection_confidence: float = 0.7):
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=4,
            refine_landmarks=True,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=0.5
        )

        # 3D model points for head pose estimation
        self.model_points = np.array([
            [0.0, 0.0, 0.0],          # Nose tip
            [0.0, -63.6, -12.5],      # Chin
            [-43.3, 32.7, -26.0],     # Left eye outer
            [43.3, 32.7, -26.0],      # Right eye outer
            [-28.9, -28.9, -24.1],    # Left mouth
            [28.9, -28.9, -24.1]      # Right mouth
        ], dtype=np.float64)

    def detect(self, frame: np.ndarray) -> List[FaceData]:
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb)

        faces = []
        if not results.multi_face_landmarks:
            return faces

        # Camera matrix approximation
        focal_length = w
        center = (w / 2, h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1]
        ], dtype=np.float64)
        dist_coeffs = np.zeros((4, 1))

        for face_landmarks in results.multi_face_landmarks:
            # Extract all landmarks
            landmarks = np.array([
                [lm.x * w, lm.y * h, lm.z * w]
                for lm in face_landmarks.landmark
            ])

            # Bounding box
            x_coords = landmarks[:, 0]
            y_coords = landmarks[:, 1]
            x_min, x_max = int(x_coords.min()), int(x_coords.max())
            y_min, y_max = int(y_coords.min()), int(y_coords.max())
            bbox = (x_min, y_min, x_max - x_min, y_max - y_min)

            # Head pose estimation
            image_points = np.array([
                landmarks[self.NOSE_TIP][:2],
                landmarks[self.CHIN][:2],
                landmarks[self.LEFT_EYE_OUTER][:2],
                landmarks[self.RIGHT_EYE_OUTER][:2],
                landmarks[self.LEFT_MOUTH][:2],
                landmarks[self.RIGHT_MOUTH][:2]
            ], dtype=np.float64)

            success, rotation_vec, _ = cv2.solvePnP(
                self.model_points, image_points, camera_matrix, dist_coeffs
            )

            yaw, pitch, roll = 0.0, 0.0, 0.0
            if success:
                rotation_mat, _ = cv2.Rodrigues(rotation_vec)
                angles = cv2.RQDecomp3x3(rotation_mat)[0]
                pitch, yaw, roll = angles[0], angles[1], angles[2]

            # Center offset (normalized -1 to 1)
            face_center_x = (x_min + x_max) / 2
            face_center_y = (y_min + y_max) / 2
            center_offset = (
                (face_center_x - w / 2) / (w / 2),
                (face_center_y - h / 2) / (h / 2)
            )

            # Size ratio
            size_ratio = (y_max - y_min) / h

            faces.append(FaceData(
                bbox=bbox,
                landmarks=landmarks,
                yaw=yaw,
                pitch=pitch,
                roll=roll,
                center_offset=center_offset,
                size_ratio=size_ratio
            ))

        return faces

    def close(self):
        self.face_mesh.close()
