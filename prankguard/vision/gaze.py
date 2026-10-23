"""Direction du regard par les yeux (MediaPipe Face Landmarker : 478 points dont les iris).

La pose de la tête seule se trompe dans les deux sens : tête tournée mais yeux sur l'écran
(« ne regarde pas » à tort), tête droite mais yeux sur le côté (« regarde » à tort).
On combine l'orientation de la tête (maillage) et le décalage des iris dans les yeux.

Mesuré sur une capture guidée du propriétaire (visage ~84 px, webcam 640x480) :
- règle tête seule : 32 % de faux « ne regarde pas », 89 % de faux « regarde » ;
- tête + yeux : 9 % de faux « ne regarde pas », yeux franchement sur le côté reconnus.
Limites physiques : le haut / bas (clavier, plafond) n'est PAS mesurable à cette distance (les
paupières suivent l'iris) -> on garde la pose de tête pour le vertical ; sous ~60 px de visage,
l'iris ne fait que quelques pixels -> retour à la pose de tête.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import models

INPUT = 256
ROI_SCALE = 1.5             # carré 1,5x la boîte du visage, comme le modèle a été entraîné
MIN_FACE_PX = 60            # en dessous, les iris ne sont plus mesurables : pose de tête seule
MIN_SCORE = 0.5             # confiance « visage présent » du maillage

# Décalage neutre de l'iris de chaque œil quand on regarde droit (mesuré, regard au centre)
EYE_BIAS = (-0.025, 0.018)
EYE_GAIN = 3.0              # 1 largeur d'œil de décalage d'iris ≈ 3 unités de lacet de tête
LOOK_TOL = 0.22             # |tête + yeux| sous ce seuil : regarde l'écran
TURNED = 0.30               # tête nettement tournée...
COMPENSATE = 0.03           # ... mais yeux ramenés vers l'écran (sens opposé) : regarde l'écran

# Indices MediaPipe : coin externe, coin interne, paupière haute, paupière basse, centre de l'iris
_EYES = ((33, 133, 159, 145, 468), (263, 362, 386, 374, 473))
_NOSE_TIP = 1


@dataclass
class EyeGaze:
    head_yaw: float          # lacet de la tête mesuré sur le maillage (0 = face à la caméra)
    iris: float              # décalage horizontal des iris, en largeurs d'œil (biais neutre retiré)
    score: float


def roi_matrix(box: np.ndarray, landmarks5: np.ndarray) -> np.ndarray:
    """Transformation image -> crop 256x256 centré sur le visage, yeux à l'horizontale."""
    re, le = landmarks5[0], landmarks5[1]
    ang = float(np.degrees(np.arctan2(le[1] - re[1], le[0] - re[0])))
    cx, cy = float(box[0] + box[2] / 2), float(box[1] + box[3] / 2)
    size = float(max(box[2], box[3])) * ROI_SCALE
    m = cv2.getRotationMatrix2D((cx, cy), ang, INPUT / max(size, 1.0))
    m[0, 2] += INPUT / 2 - cx
    m[1, 2] += INPUT / 2 - cy
    return m


def eye_features(lm: np.ndarray) -> tuple[float, float]:
    """(lacet de tête, décalage des iris) depuis 478 points dans le repère du crop (yeux horizontaux)."""
    offs, weights = [], []
    for (outer, inner, _top, _bot, iris), bias in zip(_EYES, EYE_BIAS):
        a, b = lm[outer, :2], lm[inner, :2]
        width = float(np.linalg.norm(b - a))
        if width < 1e-3:
            continue
        offs.append((lm[iris, 0] - (a[0] + b[0]) / 2) / width - bias)
        weights.append(width ** 4)          # l'œil le plus visible (le plus large) domine
    span = float(np.linalg.norm(lm[263, :2] - lm[33, :2]))
    eyemid = (lm[33, :2] + lm[263, :2]) / 2
    head_yaw = float((lm[_NOSE_TIP, 0] - eyemid[0]) / span) if span > 1e-3 else 0.0
    iris = float(np.average(offs, weights=weights)) if offs else 0.0
    return head_yaw, iris


def eyes_on_screen(head_yaw: float, iris: float, yaw_center: float = 0.0) -> bool:
    """Regard horizontal dirigé vers l'écran, en tenant compte des yeux qui compensent la tête."""
    yaw = head_yaw - yaw_center
    if abs(yaw + EYE_GAIN * iris) <= LOOK_TOL:
        return True
    return bool(abs(yaw) >= TURNED and abs(iris) >= COMPENSATE and np.sign(iris) == -np.sign(yaw))


class GazeEstimator:
    def __init__(self):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(str(models.resolve(models.FACE_MESH)), opts,
                                          providers=["CPUExecutionProvider"])
        self._input = self._sess.get_inputs()[0].name

    def estimate(self, frame: np.ndarray, box: np.ndarray, landmarks5: np.ndarray) -> EyeGaze | None:
        if box[2] < MIN_FACE_PX:
            return None
        crop = cv2.warpAffine(frame, roi_matrix(box, landmarks5), (INPUT, INPUT), flags=cv2.INTER_LINEAR)
        blob = (crop[:, :, ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        lm, logit = self._sess.run(None, {self._input: blob})
        score = float(1.0 / (1.0 + np.exp(-float(np.ravel(logit)[0]))))
        if score < MIN_SCORE:
            return None
        head_yaw, iris = eye_features(lm[0])
        return EyeGaze(head_yaw, iris, score)
