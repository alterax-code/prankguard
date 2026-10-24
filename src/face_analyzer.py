"""
Analyse faciale avec thread dedie pour la fluidite video.
L'analyse tourne en arriere-plan, le rendu video ne bloque jamais.
"""
import cv2
import face_recognition
import numpy as np
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class FaceResult:
    """Resultat d'analyse pour un visage detecte."""
    location: Tuple[int, int, int, int]  # (top, right, bottom, left)
    is_owner: bool
    distance: float
    face_size: float
    center_offset: float
    is_looking: bool


class FaceAnalyzer:
    """
    Wrapper autour de face_recognition avec analyse threadee.
    La capture tourne a 30fps, l'analyse tourne en parallele sans bloquer.
    """

    def __init__(
        self,
        owner_encodings: list,
        tolerance: float = 0.45,
        min_face_size: float = 0.20,
        center_threshold: float = 0.35,
    ):
        self.owner_encodings = owner_encodings
        self.tolerance = tolerance
        self.min_face_size = min_face_size
        self.center_threshold = center_threshold

        # Resultats partages entre threads
        self._last_results: List[FaceResult] = []
        self._results_lock = threading.Lock()

        # Frame a analyser (file d'attente de taille 1)
        self._pending_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()

        # Thread d'analyse
        self._running = True
        self._analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
        self._analysis_thread.start()

        # Flag pour savoir si une nouvelle analyse est dispo
        self._new_analysis = False

    @property
    def last_results(self) -> List[FaceResult]:
        """Dernier resultat d'analyse (thread-safe)."""
        with self._results_lock:
            return list(self._last_results)

    @property
    def has_new_analysis(self) -> bool:
        """True si une nouvelle analyse est disponible depuis le dernier check."""
        if self._new_analysis:
            self._new_analysis = False
            return True
        return False

    def submit_frame(self, frame: np.ndarray):
        """Soumet un frame pour analyse (non-bloquant, ecrase le precedent)."""
        with self._frame_lock:
            self._pending_frame = frame.copy()

    def _analysis_loop(self):
        """Boucle d'analyse dans un thread dedie."""
        while self._running:
            # Recuperer le frame en attente
            with self._frame_lock:
                frame = self._pending_frame
                self._pending_frame = None

            if frame is None:
                time.sleep(0.01)
                continue

            # Analyser (prend ~150-200ms)
            results = self._analyze(frame)

            # Stocker les resultats
            with self._results_lock:
                self._last_results = results
                self._new_analysis = True

    def _analyze(self, frame: np.ndarray) -> List[FaceResult]:
        """Analyse complete d'un frame (appele dans le thread dedie)."""
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Reduire la resolution pour accelerer la detection
        small = cv2.resize(rgb, (0, 0), fx=0.5, fy=0.5)
        locs = face_recognition.face_locations(small)

        results = []
        if locs:
            # Remettre a l'echelle pour les encodings
            locs_full = [(t*2, r*2, b*2, l*2) for (t, r, b, l) in locs]
            encs = face_recognition.face_encodings(rgb, locs_full)

            for loc, enc in zip(locs_full, encs):
                t, r, b, l = loc
                dist = min(face_recognition.face_distance(self.owner_encodings, enc))
                is_own = dist <= self.tolerance
                sz = (b - t) / h
                off = abs((l + r) / 2 - w / 2) / (w / 2)
                looking = sz >= self.min_face_size and off <= self.center_threshold

                results.append(FaceResult(
                    location=loc,
                    is_owner=is_own,
                    distance=dist,
                    face_size=sz,
                    center_offset=off,
                    is_looking=looking,
                ))

        return results

    def draw_on_frame(self, frame: np.ndarray) -> np.ndarray:
        """Dessine les rectangles sur le frame (resultats en cache)."""
        with self._results_lock:
            results = list(self._last_results)

        for r in results:
            t, right, b, l = r.location

            if r.is_owner:
                col = (0, 255, 0)
                lbl = "OWNER"
            elif r.is_looking:
                col = (0, 0, 255)
                lbl = "THREAT"
            else:
                col = (0, 165, 255)
                lbl = "PASSING"

            cv2.rectangle(frame, (l, t), (right, b), col, 2)
            cv2.putText(
                frame, f"{lbl} {r.distance:.2f}",
                (l, t - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2
            )

        return frame

    def get_situation(self) -> dict:
        """Resume de la situation courante."""
        with self._results_lock:
            results = list(self._last_results)

        owner = any(r.is_owner for r in results)
        threat = any(not r.is_owner and r.is_looking for r in results)
        passing = any(not r.is_owner and not r.is_looking for r in results)

        if results:
            r = results[0]
            info = f"Sz:{r.face_size:.0%} D:{r.distance:.2f}"
        else:
            info = "--"

        return {
            "owner": owner,
            "threat": threat,
            "passing": passing,
            "info": info,
            "face_count": len(results),
        }

    def stop(self):
        """Arrete le thread d'analyse."""
        self._running = False
