"""Machine à états Guard : logique pure, horodatages explicites (aucun sleep)."""
import pytest

from prankguard import guard as G
from prankguard.config import Settings
from prankguard.guard import FaceObs, Guard, Observation

OWNER = FaceObs("known", True, "alice")
STRANGER = FaceObs("unknown", True)


def obs(ts, *faces, cam=True):
    return Observation(ts, cam, list(faces))


def mk(**settings):
    """Guard déjà armé (sans délai de réarmement) à t=0."""
    g = Guard(Settings(**settings))
    g.arm(0.0, delay=0.0)
    # Guard initialise owner_last_seen à 0.0 ; avec des ts synthétiques proches de 0, le propriétaire
    # semblerait "vu à l'instant". En production ts = time.time() (≈ 1.7e9) : on reproduit ce cas.
    g.owner_last_seen = -1e6
    return g


def started(**settings):
    """Guard armé dont la caméra a déjà fourni une première image (t=0)."""
    g = mk(**settings)
    assert g.step(obs(0.0)).state == G.IDLE
    return g


# ── Démarrage / caméra ───────────────────────────────────────────────────────

def test_starting_then_camera_lost_after_grace():
    g = Guard(Settings())
    g.arm(100.0)
    assert g.step(obs(101.0, cam=False)).state == G.STARTING
    assert g.step(obs(100.0 + G.STARTUP_GRACE - 0.1, cam=False)).state == G.STARTING
    v = g.step(obs(100.0 + G.STARTUP_GRACE + 0.5, cam=False))
    assert v.state == G.CAMERA_LOST and v.action is None
    assert v.countdown == pytest.approx(g.s.camera_lost_delay)


def test_camera_lost_locks_after_delay():
    g = started()
    v = g.step(obs(10.0, cam=False))
    assert (v.state, v.action) == (G.CAMERA_LOST, None)
    assert v.countdown == pytest.approx(6.0)
    v = g.step(obs(13.0, cam=False))
    assert v.countdown == pytest.approx(3.0) and v.action is None
    v = g.step(obs(16.0, cam=False))
    assert v.state == G.CAMERA_LOST and v.action == "lock"


def test_camera_lost_countdown_restarts_after_recovery():
    g = started()
    g.step(obs(10.0, cam=False))
    assert g.step(obs(12.0)).state == G.IDLE
    v = g.step(obs(14.0, cam=False))
    assert v.countdown == pytest.approx(6.0)


def test_camera_lost_disabled_never_locks():
    g = started(lock_on_camera_lost=False)
    for t in (10.0, 100.0, 1000.0):
        v = g.step(obs(t, cam=False))
        assert v.state == G.CAMERA_LOST and v.action is None and v.countdown is None


def test_camera_lost_during_arming_does_not_lock():
    g = started()
    g.arm(50.0)                                   # réarmement : 3 s de répit
    v = g.step(obs(51.0, cam=False))
    assert v.state == G.CAMERA_LOST and v.action is None and v.countdown is None


# ── Mode SETUP (aucun profil) ────────────────────────────────────────────────

def test_setup_state_never_locks():
    g = started()
    g.enrolled = False
    for t in (1.0, 50.0, 500.0):
        v = g.step(obs(t, STRANGER))
        assert v.state == G.SETUP and v.action is None
    for t in (600.0, 700.0, 9000.0):               # caméra perdue : toujours aucune action
        v = g.step(obs(t, cam=False))
        assert v.state == G.CAMERA_LOST and v.action is None and v.countdown is None


def test_setup_with_lock_on_absence_never_locks():
    g = started(lock_on_absence=True)
    g.enrolled = False
    assert g.step(obs(1000.0)).action is None


# ── Inconnu / propriétaire ───────────────────────────────────────────────────

def test_stranger_alert_countdown_then_lock():
    g = started()
    v = g.step(obs(10.0, STRANGER))
    assert v.state == G.ALERT and v.action is None and v.countdown == pytest.approx(1.5)
    v = g.step(obs(10.5, STRANGER))
    assert v.countdown == pytest.approx(1.0)
    v = g.step(obs(11.5, STRANGER))
    assert v.state == G.ALERT and v.action == "lock" and v.reason
    # après un verrouillage, le compte à rebours repart de zéro
    v = g.step(obs(11.6, STRANGER))
    assert v.action is None and v.countdown == pytest.approx(1.5)


def test_stranger_countdown_resets_when_stranger_stops_looking():
    g = started()
    g.step(obs(10.0, STRANGER))
    assert g.step(obs(10.5, FaceObs("unknown", False))).state == G.ALERT   # trou < LOOK_GAP toléré
    assert g.step(obs(11.0, FaceObs("unknown", False))).state == G.WATCH   # au-delà : remise à zéro
    v = g.step(obs(11.5, STRANGER))
    assert v.countdown == pytest.approx(1.5)


def test_flickering_stranger_detection_still_locks():
    """Revue #5 : une détection qui clignote (1 image sur 2 manquée) ne doit pas empêcher le verrouillage."""
    g = started()
    t, locked = 10.0, False
    for i in range(40):
        faces = (STRANGER,) if i % 2 == 0 else ()
        v = g.step(obs(t, *faces))
        if v.action == "lock":
            locked = True
            break
        t += 1 / 15
    assert locked and t - 10.0 <= 1.5 + 0.2


def test_camera_restart_gives_startup_grace():
    """Revue #8 : après une reprise de veille, la caméra a de nouveau STARTUP_GRACE pour revenir."""
    g = started(lock_on_camera_lost=True, camera_lost_delay=2.0)
    g.on_camera_restart(100.0)
    for t in (100.5, 103.0, 107.5):
        v = g.step(obs(t, cam=False))
        assert v.state == G.STARTING and v.action is None


def test_stranger_with_lock_on_stranger_disabled_never_locks():
    g = started(lock_on_stranger=False)
    for t in (10.0, 20.0, 200.0):
        v = g.step(obs(t, STRANGER))
        assert v.state == G.ALERT and v.action is None and v.countdown is None


def test_pending_or_not_looking_faces_are_only_watched():
    g = started()
    for t, f in ((10.0, FaceObs("pending", True)), (50.0, FaceObs("unknown", False)),
                 (90.0, FaceObs("spoof", True))):
        v = g.step(obs(t, f))
        assert v.state == G.WATCH and v.action is None


def test_owner_present_is_secure():
    g = started()
    v = g.step(obs(5.0, OWNER))
    assert v.state == G.SECURE and v.owner == "alice" and v.action is None


def test_owner_memory_grace():
    g = started()
    g.step(obs(10.0, OWNER))
    v = g.step(obs(10.5))                       # propriétaire vu il y a 0.5 s : toujours présent
    assert v.state == G.SECURE and v.owner == "alice"
    v = g.step(obs(10.0 + G.OWNER_MEMORY + 0.1))
    assert v.state == G.IDLE and v.owner is None


def test_owner_memory_turns_stranger_into_surfer_then_alert():
    g = started()
    g.step(obs(10.0, OWNER))
    v = g.step(obs(10.5, STRANGER))             # le propriétaire n'est plus détecté mais vient d'être vu
    assert v.state == G.SURFER and v.owner == "alice"
    v = g.step(obs(11.5, STRANGER))             # mémoire expirée -> inconnu seul
    assert v.state == G.ALERT


# ── Regard par-dessus l'épaule ───────────────────────────────────────────────

def test_surfer_warn_once_per_episode():
    g = started(surfer_action="warn", surfer_delay=3.0)
    v = g.step(obs(10.0, OWNER, STRANGER))
    assert v.state == G.SURFER and v.action is None
    assert g.step(obs(12.9, OWNER, STRANGER)).action is None
    v = g.step(obs(13.0, OWNER, STRANGER))
    assert v.state == G.SURFER and v.action == "warn" and v.owner == "alice"
    for t in (14.0, 20.0, 60.0):                # un seul avertissement par épisode
        assert g.step(obs(t, OWNER, STRANGER)).action is None
    # fin d'épisode (propriétaire seul) puis nouvel épisode -> nouvel avertissement
    assert g.step(obs(61.0, OWNER)).state == G.SECURE
    assert g.step(obs(62.0, OWNER, STRANGER)).action is None
    assert g.step(obs(65.0, OWNER, STRANGER)).action == "warn"


def test_surfer_lock_after_delay():
    g = started(surfer_action="lock", surfer_delay=3.0)
    v = g.step(obs(10.0, OWNER, STRANGER))
    assert v.state == G.SURFER and v.action is None and v.countdown == pytest.approx(3.0)
    v = g.step(obs(12.0, OWNER, STRANGER))
    assert v.countdown == pytest.approx(1.0)
    v = g.step(obs(13.0, OWNER, STRANGER))
    assert v.state == G.SURFER and v.action == "lock"


def test_surfer_off_never_acts():
    g = started(surfer_action="off")
    for t in (10.0, 20.0, 200.0):
        v = g.step(obs(t, OWNER, STRANGER))
        assert v.state == G.SURFER and v.action is None and v.countdown is None


# ── Absence du propriétaire ──────────────────────────────────────────────────

def test_absence_disabled_by_default():
    g = started()
    assert g.step(obs(5000.0)).action is None


def test_lock_on_absence_countdown_then_lock():
    g = started(lock_on_absence=True, absence_delay=20.0)
    v = g.step(obs(1.0))
    assert v.state == G.IDLE and v.countdown == pytest.approx(19.0)
    v = g.step(obs(19.9, FaceObs("unknown", False)))
    assert v.state == G.WATCH and v.action is None
    v = g.step(obs(20.0))
    assert v.state == G.IDLE and v.action == "lock"


def test_absence_countdown_restarts_from_last_owner_sighting():
    g = started(lock_on_absence=True, absence_delay=20.0)
    g.step(obs(15.0, OWNER))
    assert g.step(obs(30.0)).action is None            # 15 s d'absence < 20 s
    v = g.step(obs(36.0))                              # 21 s depuis la dernière vue
    assert v.action == "lock"


# ── Armement / pause / session ───────────────────────────────────────────────

def test_arming_period_blocks_actions():
    g = started()
    g.arm(50.0)                                         # rearm_delay par défaut = 3 s
    v = g.step(obs(51.0, STRANGER))
    assert v.state == G.ARMING and v.action is None and v.countdown == pytest.approx(2.0)
    v = g.step(obs(52.9, STRANGER))
    assert v.state == G.ARMING and v.action is None
    v = g.step(obs(53.0, STRANGER))
    assert v.state == G.ALERT and v.countdown == pytest.approx(1.5)


def test_arm_with_explicit_delay():
    g = started()
    g.arm(10.0, delay=10.0)
    assert g.step(obs(19.0, STRANGER)).state == G.ARMING
    assert g.step(obs(20.0, STRANGER)).state == G.ALERT


def test_disarm_pauses_everything():
    g = started()
    g.disarm()
    v = g.step(obs(10.0, STRANGER))
    assert v.state == G.PAUSED and v.action is None
    v = g.step(obs(11.0, cam=False))
    assert v.state == G.PAUSED and v.action is None
    v = g.step(obs(12.0, OWNER))
    assert v.state == G.PAUSED and v.owner == "alice"


def test_rearm_after_disarm_resumes():
    g = started()
    g.disarm()
    g.step(obs(5.0))
    g.arm(6.0)
    assert g.step(obs(7.0, STRANGER)).state == G.ARMING
    assert g.step(obs(9.0, STRANGER)).state == G.ALERT


def test_session_locked_state():
    g = started()
    g.on_session_locked()
    for f in ([STRANGER], [OWNER], []):
        v = g.step(obs(10.0, *f))
        assert v.state == G.LOCKED and v.action is None
    assert g.step(obs(11.0, cam=False)).state == G.LOCKED


def test_session_unlocked_restarts_camera_grace_and_arming():
    g = started()
    g.on_session_locked()
    g.on_session_unlocked(100.0)
    assert g.locked is False and g.armed is True
    assert g.step(obs(101.0, cam=False)).state == G.STARTING     # caméra en réouverture
    v = g.step(obs(101.5, STRANGER))
    assert v.state == G.ARMING and v.action is None
    v = g.step(obs(103.5, STRANGER))                              # propriétaire pas vu depuis 3.5 s
    assert v.state == G.ALERT


def test_session_unlocked_counts_as_owner_presence():
    g = started()
    g.on_session_locked()
    g.on_session_unlocked(100.0)
    assert g.owner_recent(100.5) is True
    assert g.owner_recent(100.0 + G.OWNER_MEMORY + 0.1) is False


def test_session_unlocked_while_disarmed_stays_paused():
    g = started()
    g.disarm()
    g.on_session_unlocked(50.0)
    assert g.armed is False
    assert g.step(obs(51.0, STRANGER)).state == G.PAUSED


# ── Politique périphériques ──────────────────────────────────────────────────

@pytest.mark.parametrize("action,owner_here,expected", [
    ("off", True, "off"), ("off", False, "off"),
    ("notify", True, "notify"), ("notify", False, "notify"),
    ("lock", True, "lock"), ("lock", False, "lock"),
    ("lock_if_away", True, "notify"), ("lock_if_away", False, "lock"),
])
def test_device_policy_armed_enrolled(action, owner_here, expected):
    g = Guard(Settings(device_action=action))
    now = 100.0
    g.owner_last_seen = now - 0.5 if owner_here else 0.0
    assert g.device_policy(now) == expected


@pytest.mark.parametrize("action", ["off", "notify", "lock_if_away", "lock"])
def test_device_policy_disarmed_or_locked_is_off(action):
    g = Guard(Settings(device_action=action))
    g.disarm()
    assert g.device_policy(10.0) == "off"
    g2 = Guard(Settings(device_action=action))
    g2.on_session_locked()
    assert g2.device_policy(10.0) == "off"


@pytest.mark.parametrize("action,expected", [("off", "off"), ("notify", "notify"),
                                              ("lock_if_away", "notify"), ("lock", "notify")])
def test_device_policy_not_enrolled_only_notifies(action, expected):
    g = Guard(Settings(device_action=action))
    g.enrolled = False
    assert g.device_policy(10.0) == expected
