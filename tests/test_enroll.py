"""enroll.py — buckets de pose, session d'enrôlement, nettoyage des aberrants."""
import numpy as np
import pytest

from prankguard import enroll
from prankguard.enroll import MIN_SAMPLES, TARGETS, EnrollSession, pose_bucket
from prankguard.vision.faces import Face


@pytest.mark.parametrize("yaw,pitch,expected", [
    (0.0, 0.0, "center"), (0.1, -0.1, "center"), (-0.15, 0.2, "center"),
    (0.5, 0.0, "left"), (0.28, 0.4, "left"), (0.85, 0.0, "left"),
    (-0.5, 0.0, "right"), (-0.28, -0.4, "right"), (-0.85, 0.0, "right"),
    (0.0, -0.3, "up"), (0.1, -0.6, "up"),
    (0.0, 0.3, "down"), (-0.1, 0.8, "down"),
    (0.95, 0.0, None),          # profil trop marqué
    (0.2, 0.0, None),           # entre centre et gauche
    (0.0, -0.23, None),         # entre centre et haut
    (0.5, 0.6, None),           # tourné ET penché
])
def test_pose_bucket(yaw, pitch, expected):
    assert pose_bucket(yaw, pitch) == expected


def rand_unit(rng, dim=128):
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_result_drops_outlier_embedding():
    rng = np.random.default_rng(0)
    base = rand_unit(rng)
    good = []
    for _ in range(8):
        v = base + 0.05 * rand_unit(rng)
        good.append(v / np.linalg.norm(v))
    outlier = rand_unit(rng)                      # autre "visage"
    s = EnrollSession("alice")
    s.embs = good[:4] + [outlier] + good[4:]
    res = s.result()
    assert res.shape == (8, 128) and res.dtype == np.float32
    assert not any(np.allclose(r, outlier) for r in res)


def test_result_keeps_all_consistent_embeddings():
    rng = np.random.default_rng(1)
    base = rand_unit(rng)
    s = EnrollSession("bob")
    for _ in range(10):
        v = base + 0.05 * rand_unit(rng)
        s.embs.append(v / np.linalg.norm(v))
    assert len(s.result()) == 10


def test_result_with_fewer_than_three_returns_everything():
    rng = np.random.default_rng(2)
    s = EnrollSession("c")
    s.embs = [rand_unit(rng), rand_unit(rng)]
    assert len(s.result()) == 2
    assert len(EnrollSession("empty").result()) == 0


def test_progress_fields_initial():
    s = EnrollSession("alice")
    p = s.progress()
    assert p["name"] == "alice"
    assert p["counts"] == {k: 0 for k in TARGETS} and p["targets"] == TARGETS
    assert p["total"] == 0 and p["target_total"] == sum(TARGETS.values()) == 18
    assert p["next"] == "center" and p["pose"] is None
    assert p["can_finish"] is False and p["complete"] is False
    assert isinstance(p["hint"], str) and p["hint"]


def test_progress_tracks_counts_and_completion():
    s = EnrollSession("alice")
    s.embs = [np.zeros(128, np.float32)] * MIN_SAMPLES
    assert s.progress()["can_finish"] is True
    s.counts.update(TARGETS)
    p = s.progress()
    assert p["complete"] is True and p["next"] is None


def test_counts_are_not_shared_between_sessions():
    a, b = EnrollSession("a"), EnrollSession("b")
    a.counts["center"] = 3
    assert b.counts["center"] == 0




def test_progress_reports_calibration_flag():
    s = EnrollSession("a")
    assert s.progress()["calibrated"] is False
    s.base_yaw = 0.1
    assert s.progress()["calibrated"] is True


# ── feed() avec un faux moteur ───────────────────────────────────────────────

class FakeEngine:
    def __init__(self):
        rng = np.random.default_rng(3)
        self.crop = rng.integers(0, 255, (112, 112, 3), dtype=np.uint8)   # très "net" (bruit)
        self.n = 0

    def aligned_crop(self, frame, face):
        return self.crop

    def embed(self, frame, face):
        self.n += 1
        v = np.zeros(128, np.float32)
        v[self.n % 128] = 1.0
        return v


FRAME = np.zeros((480, 640, 3), np.uint8)


def face(yaw=0.0, pitch=0.0, rel=0.3):
    return Face(box=np.array([10, 10, 50, 60], np.float32), landmarks=np.zeros((5, 2), np.float32),
                score=0.9, raw=np.zeros(15, np.float32), yaw=yaw, pitch=pitch, rel_size=rel)


def calibrated(yaw=0.0, pitch=0.0):
    """Session dont la calibration (pose naturelle devant l'écran) est terminée."""
    s, eng = EnrollSession("a"), FakeEngine()
    for i in range(enroll.CALIB_FRAMES):
        assert s.feed(eng, FRAME, [face(yaw, pitch)], 0.1 * i) is False   # la calibration ne capture rien
    assert s.base_yaw is not None
    return s, eng


def test_feed_hints_without_capture():
    s, eng = EnrollSession("a"), FakeEngine()
    assert s.feed(eng, FRAME, [], 1.0) is False and "visage" in s.hint.lower()
    assert s.feed(eng, FRAME, [face(), face()], 1.0) is False
    assert s.feed(eng, FRAME, [face(rel=0.05)], 1.0) is False
    assert s.feed(eng, FRAME, [face(rel=0.9)], 1.0) is False
    assert s.total == 0 and s._calib == []             # aucun de ces cas ne compte pour la calibration


def test_calibration_phase_then_capture_starts():
    s, eng = EnrollSession("a"), FakeEngine()
    for i in range(enroll.CALIB_FRAMES - 1):
        assert s.feed(eng, FRAME, [face()], float(i)) is False
        assert s.base_yaw is None and s.total == 0
    assert s.feed(eng, FRAME, [face()], 10.0) is False       # dernière image de calibration
    assert s.base_yaw == pytest.approx(0.0) and s.base_pitch == pytest.approx(0.0)
    assert s.feed(eng, FRAME, [face()], 11.0) is True
    assert s.counts["center"] == 1


def test_calibration_makes_poses_relative_to_the_natural_one():
    s, eng = calibrated(yaw=0.30, pitch=0.20)                 # webcam décentrée
    assert s.base_yaw == pytest.approx(0.30) and s.base_pitch == pytest.approx(0.20)
    assert s.feed(eng, FRAME, [face(yaw=0.30, pitch=0.20)], 5.0) is True
    assert s.current_pose == "center"
    # tourner de +0.3 par rapport à la pose naturelle est "left" (il faut d'abord remplir le centre)
    s.counts["center"] = enroll.TARGETS["center"]
    assert s.feed(eng, FRAME, [face(yaw=0.60, pitch=0.20)], 6.0) is True
    assert s.counts["left"] == 1


def test_unstable_calibration_is_retried():
    s, eng = EnrollSession("a"), FakeEngine()
    for i in range(enroll.CALIB_FRAMES):
        s.feed(eng, FRAME, [face(yaw=0.0 if i % 2 else 0.6)], float(i))   # tête qui bouge
    assert s.base_yaw is None and "immobile" in s.hint.lower()
    assert len(s._calib) < enroll.CALIB_FRAMES
    for i in range(enroll.CALIB_FRAMES):
        s.feed(eng, FRAME, [face()], 10.0 + i)
    assert s.base_yaw == pytest.approx(0.0)


def test_feed_captures_center_respects_interval_and_quota():
    s, eng = calibrated()
    assert s.feed(eng, FRAME, [face()], 1.0) is True
    assert s.counts["center"] == 1 and s.current_pose == "center" and s.best_thumb
    assert s.feed(eng, FRAME, [face()], 1.0 + enroll.CAPTURE_INTERVAL / 2) is False   # trop tôt
    t = 1.0
    for _ in range(10):
        t += 1.0
        s.feed(eng, FRAME, [face()], t)
    assert s.counts["center"] == enroll.TARGETS["center"]       # quota plafonné
    assert s.next_target() == "left"
    assert s.feed(eng, FRAME, [face()], t + 1) is False         # le centre n'est plus voulu
    assert s.feed(eng, FRAME, [face(yaw=0.5)], t + 2) is True
    assert s.counts["left"] == 1


def test_feed_profile_has_no_bucket():
    s, eng = calibrated()
    assert s.feed(eng, FRAME, [face(yaw=0.95)], 5.0) is False
    assert s.current_pose is None and s.total == 0


def test_feed_rejects_blurry_crop():
    s, eng = calibrated()
    eng.crop = np.full((112, 112, 3), 100, np.uint8)            # uniforme -> netteté nulle
    assert s.feed(eng, FRAME, [face()], 5.0) is False
    assert s.total == 0 and "flou" in s.hint.lower()
