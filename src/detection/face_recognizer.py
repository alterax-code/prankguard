import cv2
import face_recognition
import numpy as np
from pathlib import Path
from typing import List, Optional, Tuple
import pickle


class FaceRecognizer:
    def __init__(self, owner_faces_dir: Path, tolerance: float = 0.6):
        self.owner_faces_dir = owner_faces_dir
        self.tolerance = tolerance
        self.owner_encodings: List[np.ndarray] = []
        self.encodings_file = owner_faces_dir / "encodings.pkl"
        self._load_encodings()

    def _load_encodings(self):
        if self.encodings_file.exists():
            with open(self.encodings_file, "rb") as f:
                self.owner_encodings = pickle.load(f)
            print(f"[FaceRecognizer] Loaded {len(self.owner_encodings)} owner encodings")
            return
        self._compute_encodings()

    def _compute_encodings(self):
        self.owner_encodings = []
        image_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
        image_files = [f for f in self.owner_faces_dir.iterdir() if f.suffix.lower() in image_extensions]
        for img_path in image_files:
            img = face_recognition.load_image_file(str(img_path))
            encodings = face_recognition.face_encodings(img)
            if encodings:
                self.owner_encodings.append(encodings[0])
                print(f"[FaceRecognizer] Encoded: {img_path.name}")
        if self.owner_encodings:
            self._save_encodings()
            print(f"[FaceRecognizer] Total: {len(self.owner_encodings)} encodings")
        else:
            print("[FaceRecognizer] No owner faces found! Run enrollment first.")

    def _save_encodings(self):
        with open(self.encodings_file, "wb") as f:
            pickle.dump(self.owner_encodings, f)

    def is_owner(self, frame: np.ndarray, face_location: Tuple[int, int, int, int]) -> Optional[bool]:
        if not self.owner_encodings:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb)
        if not locations:
            return None
        encodings = face_recognition.face_encodings(rgb, locations)
        if not encodings:
            return None
        encoding = encodings[0]
        distances = face_recognition.face_distance(self.owner_encodings, encoding)
        if len(distances) == 0:
            return None
        min_distance = np.min(distances)
        return min_distance <= self.tolerance

    def add_owner_encoding(self, frame: np.ndarray, face_location: Tuple[int, int, int, int]) -> bool:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb)
        if not locations:
            return False
        encodings = face_recognition.face_encodings(rgb, locations)
        if not encodings:
            return False
        self.owner_encodings.append(encodings[0])
        self._save_encodings()
        return True

    def clear_encodings(self):
        self.owner_encodings = []
        if self.encodings_file.exists():
            self.encodings_file.unlink()

    @property
    def is_enrolled(self) -> bool:
        return len(self.owner_encodings) > 0
