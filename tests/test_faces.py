"""vision/faces.py — parties pures : pose de tête, IoU, suivi, hystérésis, matching."""
import numpy as np
import pytest

from prankguard.vision.faces import Face, Matcher, Track, Tracker, head_pose, iou

THR = 0.42


def landmarks(nose=(50.0, 60.0), re=(30.0, 40.0), le=(70.0, 40.0), rm=(35.0, 80.0), lm=(65.0, 80.0)):
    return np.array([re, le, nose, rm, lm], dtype=np.float32)


def make_face(x, y, w=100.0, h=120.0):
    box = np.array([x, y, w, h], dtype=np.float32)
    return Face(box=box, landmarks=landmarks(), score=0.9, raw=np.zeros(15, np.float32), rel_size=0.3)


def unit(i, dim=128):
    v = np.zeros(dim, np.float32)
    v[i] = 1.0
    return v


# ── head_pose ────────────────────────────────────────────────────────────────

def test_head_pose_frontal_is_near_zero():
    yaw, pitch = head_pose(landmarks())
    assert yaw == pytest.approx(0.0, abs=1e-6)
    assert abs(pitch) < 0.2


def test_head_pose_yaw_sign_follows_nose_shift():
    right = head_pose(landmarks(nose=(60.0, 60.0)))[0]
    left = head_pose(landmarks(nose=(40.0, 60.0)))[0]
    assert right == pytest.approx(0.5) and left == pytest.approx(-0.5)
    assert right > 0 > left


def test_head_pose_pitch_sign_follows_nose_height():
    down = head_pose(landmarks(nose=(50.0, 70.0)))[1]
    up = head_pose(landmarks(nose=(50.0, 50.0)))[1]
    assert down > 0 > up


def test_head_pose_degenerate_eyes():
    yaw, pitch = head_pose(landmarks(re=(50.0, 40.0), le=(50.0, 40.0)))
    assert (yaw, pitch) == (1.0, 0.0)


def test_head_pose_degenerate_mouth_span_gives_zero_pitch():
    lm = landmarks(rm=(35.0, 40.0), lm=(65.0, 40.0))
    assert head_pose(lm)[1] == 0.0


# ── iou ──────────────────────────────────────────────────────────────────────

def test_iou_values():
    a = np.array([0, 0, 10, 10], float)
    assert iou(a, a) == pytest.approx(1.0)
    assert iou(a, np.array([20, 20, 5, 5], float)) == 0.0
    assert iou(a, np.array([5, 0, 10, 10], float)) == pytest.approx(1 / 3)
    assert iou(a, np.array([2, 2, 3, 3], float)) == pytest.approx(9 / 100)   # inclusion
    assert iou(np.array([0, 0, 0, 0], float), np.array([0, 0, 0, 0], float)) == 0.0


def test_iou_is_symmetric():
    a, b = np.array([0, 0, 10, 8], float), np.array([3, 2, 9, 9], float)
    assert iou(a, b) == pytest.approx(iou(b, a))


# ── Tracker ──────────────────────────────────────────────────────────────────

def test_tracker_keeps_id_for_slowly_moving_box():
    tr = Tracker()
    t1 = tr.update([make_face(100, 100)], now=1.0)
    t2 = tr.update([make_face(104, 102)], now=1.1)
    t3 = tr.update([make_face(108, 104)], now=1.2)
    assert len(t1) == len(t2) == len(t3) == 1
    assert t1[0].id == t2[0].id == t3[0].id
    assert t3[0].hits == 3 and t3[0].last_seen == 1.2


def test_tracker_new_box_far_away_creates_new_track():
    tr = Tracker()
    a = tr.update([make_face(100, 100)], now=1.0)[0]
    out = tr.update([make_face(100, 100), make_face(400, 300)], now=1.1)
    assert len(out) == 2
    assert a.id in {t.id for t in out}
    assert len({t.id for t in out}) == 2


def test_tracker_two_faces_keep_their_own_identities():
    tr = Tracker()
    first = tr.update([make_face(50, 50), make_face(400, 50)], now=1.0)
    ids = {t.face.box[0] < 200: t.id for t in first}
    second = tr.update([make_face(402, 52), make_face(52, 52)], now=1.1)   # ordre inversé
    for t in second:
        assert t.id == ids[t.face.box[0] < 200]


def test_tracker_replaced_face_gets_new_id():
    tr = Tracker()
    a = tr.update([make_face(100, 100)], now=1.0)[0]
    b = tr.update([make_face(500, 300)], now=1.1)[0]
    assert b.id != a.id


def test_tracker_track_expires_after_max_age():
    tr = Tracker(max_age=0.8)
    a = tr.update([make_face(100, 100)], now=1.0)[0]
    assert tr.update([], now=1.5) == []                  # absent mais pas encore expiré
    assert len(tr.tracks) == 1
    back = tr.update([make_face(100, 100)], now=1.7)     # revient dans le délai : même identité
    assert back[0].id == a.id
    tr.update([], now=2.0)
    assert len(tr.tracks) == 1
    assert tr.update([], now=2.6) == []                  # > 0.8 s depuis 1.7
    assert tr.tracks == []
    again = tr.update([make_face(100, 100)], now=3.0)
    assert again[0].id != a.id


def test_tracker_reset():
    tr = Tracker()
    tr.update([make_face(1, 1)], now=1.0)
    tr.reset()
    assert tr.tracks == []


# ── Track.observe_similarity (hystérésis) ────────────────────────────────────

def new_track():
    return Track(face=make_face(0, 0))


def test_known_after_two_good_observations():
    t = new_track()
    t.observe_similarity("alice", 0.46, THR, 1.0)       # bon mais pas "très net"
    assert t.status == "pending"
    t.observe_similarity("alice", 0.46, THR, 1.1)
    assert (t.status, t.name) == ("known", "alice")


def test_known_after_one_very_good_observation():
    t = new_track()
    t.observe_similarity("alice", THR + 0.2, THR, 1.0)
    assert (t.status, t.name) == ("known", "alice")


def test_good_similarity_without_a_name_stays_pending():
    t = new_track()
    t.observe_similarity(None, 0.9, THR, 1.0)
    t.observe_similarity(None, 0.9, THR, 1.1)
    assert t.status == "pending"


def test_unknown_only_when_clearly_below_threshold_after_two_observations():
    t = new_track()
    t.observe_similarity(None, 0.05, THR, 1.0)
    assert t.status == "pending"                        # une seule observation ne suffit pas
    t.observe_similarity(None, 0.05, THR, 1.1)
    assert (t.status, t.name) == ("unknown", None)


def test_gray_zone_below_threshold_is_not_unknown():
    t = new_track()
    for i in range(5):
        t.observe_similarity(None, THR - 0.05, THR, 1.0 + i)   # sous le seuil mais > seuil - GAP
    assert t.status == "pending"


def test_known_track_does_not_flip_after_single_bad_observation():
    t = new_track()
    t.observe_similarity("alice", 0.6, THR, 1.0)
    assert t.status == "known"
    t.observe_similarity(None, 0.0, THR, 1.1)           # ema = 0.27 < seuil - GAP, mais 1 raté isolé
    assert (t.status, t.name) == ("known", "alice")
    t.observe_similarity(None, 0.0, THR, 1.2)
    assert t.status == "known"                           # n = 3 < 4
    t.observe_similarity(None, 0.0, THR, 1.3)
    assert t.status == "unknown"                         # raté durable -> bascule


def test_unknown_returns_to_pending_when_similarity_recovers():
    t = new_track()
    for i in range(2):
        t.observe_similarity(None, 0.05, THR, i)
    assert t.status == "unknown"
    for i in range(4):
        t.observe_similarity("alice", 0.40, THR, 10 + i)     # remonte dans la zone d'incertitude
    assert t.status == "pending"


def test_spoof_when_liveness_low():
    t = new_track()
    t.observe_liveness(0.05)
    t.observe_liveness(0.05)
    assert not t.is_spoof                                # deux échantillons ne suffisent pas
    t.observe_liveness(0.05)
    assert t.is_spoof
    t.observe_similarity("alice", 0.95, THR, 1.0)
    assert (t.status, t.name) == ("spoof", None)


def test_liveness_high_is_not_spoof():
    t = new_track()
    for _ in range(4):
        t.observe_liveness(0.95)
    assert not t.is_spoof
    t.observe_similarity("alice", 0.9, THR, 1.0)
    assert t.status == "known"


def test_liveness_ema():
    t = new_track()
    t.observe_liveness(1.0)
    t.observe_liveness(0.0)
    assert t.live == pytest.approx(0.5) and t.live_n == 2


# ── Matcher ──────────────────────────────────────────────────────────────────

def test_matcher_empty_gallery():
    m = Matcher()
    assert m.empty
    assert m.best(unit(0)) == (None, 0.0)
    m.set_gallery({"ghost": np.zeros((0, 128), np.float32)})
    assert m.empty and m.best(unit(0)) == (None, 0.0)


def test_matcher_picks_best_person():
    m = Matcher({"a": np.stack([unit(0)]), "b": np.stack([unit(1)])})
    name, sim = m.best(unit(1))
    assert name == "b" and sim == pytest.approx(1.0)
    name, sim = m.best(unit(0))
    assert name == "a" and sim == pytest.approx(1.0)
    _, sim = m.best(unit(2))
    assert sim == pytest.approx(0.0)


def test_matcher_averages_top3_for_large_galleries():
    # 3 échantillons : 1 identique + 2 orthogonaux -> moyenne (1+0+0)/3, pas le max isolé
    m = Matcher({"a": np.stack([unit(0), unit(1), unit(2)])})
    assert m.best(unit(0))[1] == pytest.approx(1 / 3)
    # moins de 3 échantillons -> max
    m2 = Matcher({"a": np.stack([unit(0), unit(1)])})
    assert m2.best(unit(0))[1] == pytest.approx(1.0)
    # 5 échantillons dont 3 bons : top-3 -> 1.0
    m3 = Matcher({"a": np.stack([unit(0)] * 3 + [unit(1), unit(2)])})
    assert m3.best(unit(0))[1] == pytest.approx(1.0)


def test_matcher_set_gallery_replaces_previous():
    m = Matcher({"a": np.stack([unit(0)])})
    m.set_gallery({"b": np.stack([unit(5)])})
    assert m.best(unit(5))[0] == "b"
    m.set_gallery({})
    assert m.empty


def test_strict_threshold_never_turns_owner_into_stranger():
    """Terrain : exigence à 60 % -> le propriétaire à 0,45 devenait « inconnu » (faux regard indiscret)."""
    t = new_track()
    for i in range(8):
        t.observe_similarity("alice", 0.45, 0.60, 1.0 + i * 0.3)
    assert t.status == "pending"


def test_known_owner_needs_three_consecutive_misses():
    t = new_track()
    t.observe_similarity("alice", 0.80, 0.42, 1.0)
    for i, s in enumerate([0.10, 0.10, 0.75, 0.10, 0.10]):
        t.observe_similarity("alice", s, 0.42, 2.0 + i)
    assert t.status == "known"
    t.observe_similarity("alice", 0.10, 0.42, 9.0)
    t.observe_similarity("alice", 0.10, 0.42, 10.0)
    assert t.status == "unknown"


def test_never_recognised_face_becomes_unknown_after_pending_max():
    """Revue : un inconnu dont le score reste entre 0,30 et le seuil n'était jamais verrouillé."""
    t = new_track()
    t.first_seen = 0.0
    for i in range(6):
        t.observe_similarity("alice", 0.36, 0.42, 0.5 + i * 0.3)
    assert t.status == "pending"                        # < PENDING_MAX
    t.observe_similarity("alice", 0.36, 0.42, Track.PENDING_MAX + 0.1)
    assert t.status == "unknown"


def test_recognised_owner_dipping_below_threshold_stays_pending():
    t = new_track()
    t.first_seen = 0.0
    t.observe_similarity("alice", 0.80, 0.42, 0.1)
    for i in range(10):
        t.observe_similarity("alice", 0.36, 0.42, 1.0 + i)
    assert t.status in ("known", "pending")
