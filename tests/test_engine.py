"""Moteur : défauts « fail-open » relevés en revue (sans caméra réelle, sans verrouillage réel)."""
import time

import numpy as np
import pytest

from prankguard import engine as E
from prankguard import guard as G
from prankguard import winsys
from prankguard.config import ConfigStore
from prankguard.events import EventStore
from prankguard.gallery import Gallery


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(winsys, "is_session_locked", lambda: False)
    cfg = ConfigStore(tmp_path / "config.json")
    gal = Gallery(tmp_path / "faces.bin", encrypt=False)
    gal.upsert("alice", np.eye(128, dtype=np.float32)[:3], b"")
    ev = EventStore(tmp_path / "ev.sqlite3", tmp_path / "snaps")
    e = E.Engine(cfg, gal, ev)
    yield e
    ev.close()


def test_lock_happens_even_if_journal_fails(eng, monkeypatch):
    """Revue #2 : une erreur du journal (disque plein…) ne doit jamais empêcher LockWorkStation."""
    calls = []
    monkeypatch.setattr(winsys, "lock_workstation", lambda: calls.append("lock") or True)

    def broken_add(*a, **k):
        raise OSError("disque plein")
    monkeypatch.setattr(eng.events, "add", broken_add)
    eng._do_lock("Inconnu devant l'écran", np.zeros((480, 640, 3), np.uint8))
    assert calls == ["lock"]
    assert eng.guard.locked


@pytest.mark.parametrize("minutes", [float("inf"), float("nan"), -5, "abc", 1e9])
def test_snooze_is_clamped_and_snapshot_stays_valid(eng, minutes):
    """Revue #11 : snooze(inf/nan) ne doit ni désarmer à vie ni casser la publication d'état."""
    eng.snooze(minutes)
    left = eng.snapshot()["snooze_left"]
    assert 0 < left <= 1440 * 60


def test_abandoned_enrollment_is_cancelled(eng, monkeypatch):
    """Revue #1 : un enrôlement abandonné est annulé automatiquement (protection jamais suspendue)."""
    eng.start_enroll("bob")
    eng._enroll.started = time.time() - E.ENROLL_TIMEOUT - 1
    # une itération de boucle suffit à appliquer le délai d'expiration
    eng._stop.set()   # on n'exécute que le préambule via un appel direct
    monkeypatch.setattr(eng.camera, "wait_newer", lambda *a, **k: (0, 0.0, None))
    eng._stop.clear()

    def one_iteration():
        orig = eng._step_guard

        def stop_after(*a, **k):
            eng._stop.set()
            return orig(*a, **k)
        monkeypatch.setattr(eng, "_step_guard", stop_after)
        eng._loop()
    one_iteration()
    assert eng._enroll is None


def test_session_unlock_is_resynchronised_when_event_is_lost(eng, monkeypatch):
    """Revue #7 : si la notification de déverrouillage est perdue, la boucle se resynchronise."""
    eng.guard.on_session_locked()
    eng._lock_check_after = 0.0
    monkeypatch.setattr(winsys, "is_session_locked", lambda: False)
    monkeypatch.setattr(eng.camera, "resume", lambda: None)

    def one_frame_then_stop(*a, **k):
        eng._stop.set()          # une seule itération de boucle
        return 0, 0.0, None
    monkeypatch.setattr(eng.camera, "wait_newer", one_frame_then_stop)
    eng._loop()
    assert not eng.guard.locked


def test_looking_face_is_always_analysed(eng, monkeypatch):
    """Revue #6 : un visage qui regarde l'écran mais est petit / de biais est quand même identifié."""
    from prankguard.vision.faces import Face
    face = Face(box=np.array([300, 200, 30, 36], np.float32), landmarks=np.zeros((5, 2), np.float32),
                score=0.9, raw=np.zeros(15, np.float32), yaw=0.0, pitch=0.0, rel_size=0.075)
    eng.config.update({"min_face_size": 0.05, "anti_spoof": False})
    monkeypatch.setattr(eng.faces, "detect", lambda frame: [face])
    monkeypatch.setattr(eng.faces, "embed", lambda frame, f: -np.eye(128, dtype=np.float32)[0])
    assert not eng.faces.embeddable(face)
    frame = np.full((480, 640, 3), 120, np.uint8)
    t = time.time()
    for i in range(4):
        eng._analyze(frame, t + i * 0.3)
    assert eng._tracks and eng._tracks[0].status == "unknown"
    assert eng._verdict.state in (G.ALERT, G.ARMING)


# ── Branchements de périphériques ────────────────────────────────────────────

def _dev(category, key="1532:00B4", name="Razer"):
    return winsys.DeviceArrival(category=category, name=name, key=key, instance=r"USB\X")


@pytest.fixture
def locks(eng, monkeypatch):
    calls = []
    monkeypatch.setattr(winsys, "lock_workstation", lambda: calls.append(1) or True)
    monkeypatch.setattr(eng.camera, "pause", lambda: None)
    monkeypatch.setattr(eng.camera, "resume", lambda: None)
    eng.config.update({"device_action": "lock"})
    eng._quiet_devices_until = 0.0
    return calls


def test_device_locks_right_after_session_unlock(eng, locks):
    """Bug terrain : un branchement juste après un déverrouillage était ignoré (« réveil »)."""
    eng._on_session("lock")
    eng._on_session("unlock")
    eng._on_device(_dev("hid"))
    assert locks == [1]


@pytest.mark.parametrize("category", ["storage", "keyboard", "mouse", "hid", "usb", "phone"])
def test_wake_window_never_ignores_risky_devices(eng, locks, category):
    eng._on_power("resume")
    eng._on_device(_dev(category))
    assert locks == [1]


@pytest.mark.parametrize("category", ["display", "audio", "network", "bluetooth"])
def test_wake_window_tolerates_self_reenumerating_devices(eng, locks, category):
    eng._on_power("resume")
    eng._on_device(_dev(category))
    assert locks == []


def test_trusted_device_never_locks(eng, locks):
    eng.config.update({"trusted_devices": ["1532:00B4"]})
    eng._on_device(_dev("mouse"))
    assert locks == []


def test_manual_lock_is_ignored_when_already_locked(eng, locks):
    eng.lock_now()
    eng.lock_now()
    assert locks == [1]


def test_device_locks_without_any_profile_when_rule_is_always(eng, locks):
    """Bug terrain : profils supprimés -> les branchements ne faisaient plus que notifier."""
    eng.gallery.remove("alice")
    eng.reload_gallery()
    assert not eng.guard.enrolled
    eng._on_device(_dev("hid"))
    assert locks == [1]


def test_duplicate_unlock_is_ignored(eng, locks, monkeypatch):
    """Terrain : resynchronisation puis notification WTS -> un seul réarmement journalisé."""
    added = []
    monkeypatch.setattr(eng.events, "add", lambda *a, **k: added.append(a[0]))
    eng._on_session("lock")
    eng._on_session("unlock")
    eng._on_session("unlock")
    assert added.count("session") == 1
    assert not eng.guard.locked
