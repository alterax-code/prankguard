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
    monkeypatch.setattr(eng.faces, "embed", lambda frame, f, m=None: -np.eye(128, dtype=np.float32)[0])
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


# ── Demande d'approbation d'un branchement ───────────────────────────────────

@pytest.fixture
def ask(eng, monkeypatch):
    """Propriétaire reconnu devant l'écran, règle « Demander »."""
    calls = {"lock": 0, "eject": [], "prompt": []}
    monkeypatch.setattr(winsys, "lock_workstation", lambda: calls.__setitem__("lock", calls["lock"] + 1) or True)
    monkeypatch.setattr(winsys, "eject_device", lambda inst: calls["eject"].append(inst) or (True, inst))
    monkeypatch.setattr(eng.camera, "pause", lambda: None)
    monkeypatch.setattr(eng.camera, "resume", lambda: None)
    monkeypatch.setattr(E, "chime", lambda: None)
    eng.on_device_prompt = calls["prompt"].append
    eng.config.update({"device_action": "ask", "device_prompt_timeout": 3})
    eng._quiet_devices_until = 0.0
    eng.guard.owner_last_seen = time.time() + 3600      # présent pendant tout le test
    yield calls
    if eng._prompt_timer:
        eng._prompt_timer.cancel()


def test_present_owner_is_asked_and_keyboard_blocked(eng, ask):
    eng._on_device_early(_dev("hid"))
    assert eng.keyboard.active                        # bloqué avant même la fusion de la rafale
    eng._on_device(_dev("hid"))
    assert ask["lock"] == 0
    info = eng.snapshot()["device_prompt"]
    assert info and info["devices"][0]["key"] == "1532:00B4"
    assert ask["prompt"] and ask["prompt"][-1]["id"] == info["id"]


def test_approve_once_unblocks_without_remembering(eng, ask):
    eng._on_device(_dev("hid"))
    pid = eng.snapshot()["device_prompt"]["id"]
    assert eng.decide_device(pid, "once")
    assert not eng.keyboard.active and ask["lock"] == 0 and ask["eject"] == []
    assert "1532:00B4" not in eng.config.s.trusted_devices
    assert ask["prompt"][-1] is None and eng.snapshot()["device_prompt"] is None


def test_approve_always_remembers_device_with_its_name(eng, ask):
    eng._on_device(_dev("hid"))
    eng.decide_device(eng.snapshot()["device_prompt"]["id"], "always")
    assert "1532:00B4" in eng.config.s.trusted_devices
    assert eng.config.s.device_names["1532:00B4"] == "Razer"
    eng._on_device(_dev("hid"))                       # rebranché : plus de question
    assert eng.snapshot()["device_prompt"] is None


def test_deny_ejects_and_locks(eng, ask):
    eng._on_device(_dev("storage"))
    eng.decide_device(eng.snapshot()["device_prompt"]["id"], "deny")
    assert ask["lock"] == 1 and ask["eject"] == [r"USB\X"]
    assert not eng.keyboard.active


def test_no_answer_locks_after_timeout(eng, ask):
    eng.config.update({"device_prompt_timeout": 3})
    eng._on_device(_dev("hid"))
    eng._prompt_timer.cancel()
    eng._prompt_timer.function(*eng._prompt_timer.args)   # expiration simulée
    assert ask["lock"] == 1 and ask["eject"] == [r"USB\X"]


def test_stale_or_invalid_decision_is_rejected(eng, ask):
    eng._on_device(_dev("hid"))
    pid = eng.snapshot()["device_prompt"]["id"]
    assert not eng.decide_device(pid + 1, "once")
    assert not eng.decide_device(pid, "maybe")
    assert eng.snapshot()["device_prompt"] is not None


def test_absent_owner_is_not_asked(eng, ask):
    """Personne de reconnu devant l'écran : la question pourrait être validée par l'intrus -> verrouillage."""
    eng.guard.owner_last_seen = 0.0
    eng._on_device(_dev("hid"))
    assert ask["lock"] == 1 and eng.snapshot()["device_prompt"] is None


def test_second_device_joins_pending_question(eng, ask):
    eng._on_device(_dev("hid"))
    eng._on_device(_dev("storage", key="0781:5581", name="SanDisk"))
    info = eng.snapshot()["device_prompt"]
    assert [d["name"] for d in info["devices"]] == ["Razer", "SanDisk"]
    eng.decide_device(info["id"], "always")
    assert {"1532:00B4", "0781:5581"} <= set(eng.config.s.trusted_devices)


def test_unwatched_category_is_ignored(eng, ask):
    eng.config.update({"device_categories": ["storage"]})
    eng._on_device_early(_dev("hid"))
    eng._on_device(_dev("hid"))
    assert not eng.keyboard.active and ask["lock"] == 0 and eng.snapshot()["device_prompt"] is None


def test_session_lock_during_question_refuses_device(eng, ask):
    eng._on_device(_dev("hid"))
    eng._on_session("lock")
    assert eng.snapshot()["device_prompt"] is None and ask["eject"] == [r"USB\X"]
    assert ask["lock"] == 0                            # déjà verrouillée : pas de second verrouillage


# ── Apprentissage continu ─────────────────────────────────────────────────────

def _unit(*pairs):
    v = np.zeros(128, np.float32)
    for i, x in pairs:
        v[i] = x
    return v / np.linalg.norm(v)


@pytest.fixture
def learner(eng, monkeypatch):
    from prankguard.vision.faces import Track
    eng.gallery.upsert("alice", np.stack([_unit((0, 1), (1, .05)), _unit((0, 1), (2, .05)), _unit((0, 1), (3, .05))]), b"")
    eng.reload_gallery()
    monkeypatch.setattr(eng.faces, "embeddable", lambda f: True)
    t = Track(face=None)
    t.status, t.name, t.live, t.live_n = "known", "alice", 0.97, 5
    return t


def test_learns_a_new_safe_view_once_per_interval(eng, learner):
    emb = _unit((0, .6), (5, .8))                  # même personne, autre éclairage : sim d'ancrage 0,6
    assert eng._maybe_learn(learner, emb, 1000.0)
    assert eng.gallery.summary()[0]["learned"] == 1
    assert not eng._maybe_learn(learner, _unit((0, .6), (6, .8)), 1010.0)   # trop tôt
    assert eng._maybe_learn(learner, _unit((0, .6), (6, .8)), 1000.0 + E.LEARN_INTERVAL)


def test_does_not_learn_redundant_view(eng, learner):
    assert not eng._maybe_learn(learner, _unit((0, 1), (1, .05)), 1000.0)


def test_does_not_learn_when_not_anchored_to_enrollment(eng, learner):
    """Vue proche d'échantillons appris mais pas de l'enrôlement : refusée (anti-dérive)."""
    assert not eng._maybe_learn(learner, _unit((0, .35), (7, .94)), 1000.0)


def test_does_not_learn_doubtful_liveness(eng, learner):
    learner.live = 0.6
    assert not eng._maybe_learn(learner, _unit((0, .8), (5, .6)), 1000.0)


def test_does_not_learn_from_a_screen(eng, learner):
    learner.moire = 0.3
    assert not eng._maybe_learn(learner, _unit((0, .6), (5, .8)), 1000.0)


def test_learning_can_be_disabled(eng, learner, monkeypatch):
    eng.config.update({"adaptive_learning": False})
    frame = np.zeros((480, 640, 3), np.uint8)
    called = []
    monkeypatch.setattr(eng, "_maybe_learn", lambda *a: called.append(a))
    from prankguard.vision.faces import Face
    face = Face(box=np.array([200, 100, 200, 240], np.float32), landmarks=np.zeros((5, 2), np.float32),
                score=0.9, raw=np.zeros(15, np.float32), yaw=0.0, pitch=0.0, rel_size=0.5)
    monkeypatch.setattr(eng.faces, "detect", lambda f: [face])
    monkeypatch.setattr(eng.faces, "embed", lambda f, x, m=None: _unit((0, 1)))
    eng._analyze(frame + 100, 1000.0)
    assert called == []


def test_failed_lockworkstation_does_not_leave_engine_blind(eng, monkeypatch):
    monkeypatch.setattr(winsys, "lock_workstation", lambda: False)
    eng._do_lock("Inconnu devant l'écran", np.zeros((480, 640, 3), np.uint8))
    assert not eng.guard.locked


def test_refusal_locks_before_ejecting(eng, ask, monkeypatch):
    order = []
    monkeypatch.setattr(winsys, "lock_workstation", lambda: order.append("lock") or True)
    monkeypatch.setattr(winsys, "eject_device", lambda inst: order.append("eject") or (True, inst))
    eng._on_device(_dev("storage"))
    eng.decide_device(eng.snapshot()["device_prompt"]["id"], "deny")
    assert order == ["lock", "eject"]


def test_journal_failure_never_blocks_refusal(eng, ask, monkeypatch):
    def broken(*a, **k):
        raise OSError("disque plein")
    eng._on_device(_dev("hid"))
    monkeypatch.setattr(eng.events, "add", broken)
    assert eng.decide_device(eng.snapshot()["device_prompt"]["id"], "deny")
    assert ask["lock"] == 1 and not eng.keyboard.active and ask["prompt"][-1] is None


def test_decision_racing_with_absent_owner_device_still_locks(eng, ask):
    """Revue : « autoriser » cliqué pendant qu'un 2e périphérique arrive en l'absence du propriétaire."""
    eng._on_device(_dev("hid"))
    eng.guard.owner_last_seen = 0.0                    # le propriétaire est parti
    eng._on_device(_dev("storage", key="0781:5581", name="SanDisk"))
    assert ask["lock"] == 1 and eng.snapshot()["device_prompt"] is None


def test_prompt_countdown_starts_after_window_is_shown(eng, ask, monkeypatch):
    def slow_window(info):
        if info:
            time.sleep(0.3)
    eng.on_device_prompt = slow_window
    eng._on_device(_dev("hid"))
    assert eng.snapshot()["device_prompt"]["left"] >= eng.config.s.device_prompt_timeout - 0.1


def test_does_not_learn_a_view_already_well_recognised(eng, learner):
    eng.gallery.add_adaptive("alice", _unit((0, .8), (5, .6)))
    eng.reload_gallery()
    assert not eng._maybe_learn(learner, _unit((0, .8), (5, .6), (8, .05)), 1000.0)


def test_enrollment_model_is_frozen_and_stored(eng, monkeypatch):
    from prankguard.vision import faces as F
    monkeypatch.setattr(F.FaceEngine, "preferred", property(lambda self: "edgeface_xs"))
    eng.start_enroll("bob")
    assert eng._enroll.model == "edgeface_xs"
    eng._enroll.embs = list(np.eye(512, dtype=np.float32)[:5])
    assert eng.finish_enroll()["ok"]
    assert eng.gallery.model_of("bob") == "edgeface_xs"


def test_profile_with_unavailable_model_is_not_matched(eng, monkeypatch):
    eng.gallery.upsert("bob", np.eye(512, dtype=np.float32)[:3], b"", model="edgeface_xs")
    eng.reload_gallery()
    monkeypatch.setattr(eng.faces, "available", lambda m: m == "sface")
    assert eng._usable_models() == ["sface"]          # alice (SFace) reste reconnue, bob attend son modèle
