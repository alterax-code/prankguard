"""vision/gaze.py — règle tête + yeux, extraction des caractéristiques, et vrai modèle MediaPipe."""
import numpy as np
import pytest

from prankguard.vision import gaze as GZ
from prankguard.vision.faces import Face, Track


# ── Règle de décision ────────────────────────────────────────────────────────

@pytest.mark.parametrize("head, iris, looking", [
    (0.0, 0.0, True),         # face à l'écran
    (0.44, -0.14, True),      # tête tournée, yeux ramenés sur l'écran (cas qui échouait)
    (-0.55, 0.06, True),      # idem de l'autre côté
    (0.0, 0.12, False),       # tête droite, yeux sur le côté
    (0.0, -0.12, False),
    (0.44, 0.05, False),      # tête ET yeux tournés du même côté
    (0.6, 0.0, False),        # tête très tournée, yeux dans l'axe de la tête
])
def test_eyes_on_screen(head, iris, looking):
    assert GZ.eyes_on_screen(head, iris) is looking


def test_eyes_on_screen_respects_calibrated_screen_axis():
    assert not GZ.eyes_on_screen(0.3, 0.0)
    assert GZ.eyes_on_screen(0.3, 0.0, yaw_center=0.3)


def _mesh(iris_shift=0.0, nose_x=128.0):
    lm = np.zeros((478, 3), np.float32)
    for (outer, inner, top, bot, iris), cx, side, bias in zip(GZ._EYES, (100.0, 156.0), (-1, 1), GZ.EYE_BIAS):
        lm[outer, :2] = (cx + side * 12, 120)          # coins externes vers l'extérieur du visage
        lm[inner, :2] = (cx - side * 12, 120)
        lm[top, :2], lm[bot, :2] = (cx, 116), (cx, 124)
        lm[iris, :2] = (cx + 24 * (bias + iris_shift), 120)
    lm[GZ._NOSE_TIP, :2] = (nose_x, 140)
    return lm


def test_eye_features_neutral_and_shifted():
    yaw, iris = GZ.eye_features(_mesh())
    assert yaw == pytest.approx(0.0, abs=1e-3) and iris == pytest.approx(0.0, abs=1e-4)
    yaw, iris = GZ.eye_features(_mesh(iris_shift=0.1, nose_x=140.0))
    assert iris == pytest.approx(0.1, abs=1e-4) and yaw > 0


def test_roi_matrix_maps_face_center_to_crop_center():
    box = np.array([200, 100, 120, 140], np.float32)
    lm5 = np.array([[230, 150], [290, 150], [260, 180], [235, 210], [285, 210]], np.float32)
    m = GZ.roi_matrix(box, lm5)
    c = m @ np.array([260.0, 170.0, 1.0])
    assert c == pytest.approx([GZ.INPUT / 2, GZ.INPUT / 2], abs=1e-3)


# ── Historique par visage ────────────────────────────────────────────────────

def _track():
    f = Face(box=np.array([0, 0, 100, 120], np.float32), landmarks=np.zeros((5, 2), np.float32),
             score=0.9, raw=np.zeros(15, np.float32))
    t = Track(face=f)
    t.last_seen = 10.0
    return t


def test_track_eyes_need_two_recent_samples_and_use_median():
    t = _track()
    assert t.eyes_looking() is None
    t.eyes.append((9.9, 0.44, -0.14))
    assert t.eyes_looking() is None                 # une mesure isolée ne suffit pas
    t.eyes.append((9.95, 0.44, -0.15))
    t.eyes.append((9.97, 0.0, 0.5))                 # mesure aberrante : la médiane l'écarte
    assert t.eyes_looking() is True


def test_track_eyes_expire():
    t = _track()
    t.eyes.extend([(5.0, 0.0, 0.2), (5.1, 0.0, 0.2)])
    assert t.eyes_looking() is None                 # trop ancien : retour à la pose de tête


# ── Vrai modèle ──────────────────────────────────────────────────────────────

def test_real_landmarker_on_a_frontal_face():
    import cv2
    from pathlib import Path
    from prankguard.vision.faces import FaceEngine
    img = cv2.resize(cv2.imread(str(Path(__file__).resolve().parent / "assets" / "lena.jpg")), (640, 480))
    face = FaceEngine().detect(img)[0]
    g = GZ.GazeEstimator().estimate(img, face.box, face.landmarks)
    assert g is not None and g.score > 0.9
    assert abs(g.head_yaw) < 0.5 and abs(g.iris) < 0.2


def test_small_faces_are_left_to_head_pose():
    est = GZ.GazeEstimator()
    box = np.array([0, 0, GZ.MIN_FACE_PX - 1, 70], np.float32)
    assert est.estimate(np.zeros((480, 640, 3), np.uint8), box, np.zeros((5, 2), np.float32)) is None
