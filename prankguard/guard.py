"""Décision de sécurité — logique pure (aucune I/O), entièrement testable.

Entrée : une Observation par image analysée. Sortie : un Verdict (état + action éventuelle).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings

# États exposés à l'UI
PAUSED = "paused"          # surveillance désactivée / en pause
LOCKED = "locked"          # session Windows verrouillée — caméra libérée
ARMING = "arming"          # vient d'être (ré)armé, pas encore d'action
STARTING = "starting"      # caméra en cours d'ouverture
IDLE = "idle"              # personne devant l'écran
SECURE = "secure"          # propriétaire présent
WATCH = "watch"            # visage inconnu / en cours d'identification, ne regarde pas l'écran
ALERT = "alert"            # inconnu regarde l'écran, propriétaire absent -> compte à rebours
SURFER = "surfer"          # inconnu regarde par-dessus l'épaule du propriétaire
CAMERA_LOST = "camera_lost"
SETUP = "setup"            # aucun profil enrôlé : aucune action automatique

OWNER_MEMORY = 1.2         # le propriétaire reste "présent" ce temps après sa dernière détection
LOOK_GAP = 0.6             # une détection manquée isolée ne remet pas le compte à rebours à zéro
STARTUP_GRACE = 8.0        # temps laissé à la caméra pour s'ouvrir avant de la déclarer perdue
OPENING_GRACE = 30.0       # si Windows est encore en train de l'ouvrir (lent après un déverrouillage)


@dataclass
class FaceObs:
    status: str              # known | unknown | pending
    looking: bool
    name: str | None = None


@dataclass
class Observation:
    ts: float
    camera_ok: bool
    faces: list[FaceObs] = field(default_factory=list)
    camera_opening: bool = False


@dataclass
class Verdict:
    state: str
    action: str | None = None      # None | "lock" | "warn"
    reason: str = ""
    countdown: float | None = None  # secondes restantes avant action
    owner: str | None = None


class Guard:
    def __init__(self, settings: Settings):
        self.s = settings
        self.armed = True
        self.locked = False
        self.enrolled = True
        self._arming_until = 0.0
        self._started = False
        self._start_ref = 0.0
        self._camera_ever_ok = False   # au lancement, une caméra qui n'a jamais répondu ne verrouille pas
        self._last_lock_camera = False
        self._cam_waived = False
        self._reset_timers()
        self.owner_last_seen = float("-inf")
        self.owner_name: str | None = None
        self._absence_ref = 0.0

    def _reset_timers(self) -> None:
        self._stranger_since: float | None = None
        self._surfer_since: float | None = None
        self._surfer_warned = False
        self._cam_lost_since: float | None = None
        self._stranger_last: float | None = None

    # ── Événements externes ──
    def arm(self, now: float, delay: float | None = None) -> None:
        self.armed = True
        if not self._started:
            self._start_ref = now
        self._arming_until = now + (self.s.rearm_delay if delay is None else delay)
        self._absence_ref = now
        self._reset_timers()

    def disarm(self) -> None:
        self.armed = False
        self._reset_timers()

    def on_session_locked(self) -> None:
        self.locked = True
        self._reset_timers()

    def on_session_unlocked(self, now: float) -> None:
        self.locked = False
        # Verrouillé pour caméra masquée puis déverrouillé par le propriétaire (mot de passe) :
        # l'objectif fermé est volontaire, on ne reverrouille pas en boucle tant que l'image n'est pas revenue.
        self._cam_waived = self._last_lock_camera
        self._last_lock_camera = False
        self._started = False
        self._start_ref = now
        # celui qui vient de déverrouiller a tapé le mot de passe : on considère le propriétaire présent
        self.owner_last_seen = now
        if self.armed:
            self.arm(now)

    def on_camera_restart(self, now: float) -> None:
        """Caméra rouverte (reprise de veille, changement de caméra) : nouveau délai de démarrage."""
        self._started = False
        self._start_ref = now
        self._cam_lost_since = None

    def owner_recent(self, now: float) -> bool:
        return now - self.owner_last_seen <= OWNER_MEMORY

    # ── Pas de décision ──
    def step(self, obs: Observation) -> Verdict:
        now, s = obs.ts, self.s
        if self.locked:
            return Verdict(LOCKED)
        if not self.armed:
            self._track_owner(obs)
            return Verdict(PAUSED, owner=self.owner_name if self.owner_recent(now) else None)

        # Caméra
        if not obs.camera_ok:
            grace = OPENING_GRACE if obs.camera_opening else STARTUP_GRACE
            if not self._started and now - self._start_ref < grace:
                return Verdict(STARTING)
            if self._cam_lost_since is None:
                self._cam_lost_since = now
            # Terrain : caméra encore bloquée par le processus précédent -> ouverture > 36 s au lancement,
            # et l'utilisateur se faisait verrouiller par l'app qu'il venait de démarrer.
            if (s.lock_on_camera_lost and self.enrolled and self._camera_ever_ok and not self._cam_waived
                    and now >= self._arming_until):
                left = s.camera_lost_delay - (now - self._cam_lost_since)
                if left <= 0:
                    self._last_lock_camera = True
                    return self._lock(CAMERA_LOST, "Caméra indisponible ou masquée")
                return Verdict(CAMERA_LOST, countdown=left)
            return Verdict(CAMERA_LOST)
        self._started = True
        self._camera_ever_ok = True
        self._cam_lost_since = None
        self._cam_waived = False

        self._track_owner(obs)
        if not self.enrolled:
            self._stranger_since = self._surfer_since = None
            return Verdict(SETUP)
        owner_here = self.owner_recent(now)
        owner = self.owner_name if owner_here else None
        if any(f.status == "unknown" and f.looking for f in obs.faces):
            self._stranger_last = now
        stranger_looking = self._stranger_last is not None and now - self._stranger_last <= LOOK_GAP
        arming = now < self._arming_until

        if arming:
            self._stranger_since = self._surfer_since = None
            return Verdict(ARMING, owner=owner, countdown=self._arming_until - now)

        if stranger_looking and owner_here:
            self._stranger_since = None
            if self._surfer_since is None:
                self._surfer_since = now
            left = s.surfer_delay - (now - self._surfer_since)
            if s.surfer_action == "lock":
                if left <= 0:
                    return self._lock(SURFER, "Regard indiscret par-dessus l'épaule")
                return Verdict(SURFER, countdown=left, owner=owner)
            if s.surfer_action == "warn" and left <= 0 and not self._surfer_warned:
                self._surfer_warned = True
                return Verdict(SURFER, action="warn", reason="Quelqu'un regarde votre écran", owner=owner)
            return Verdict(SURFER, owner=owner)

        if stranger_looking:
            self._surfer_since = None
            if self._stranger_since is None:
                self._stranger_since = now
            if s.lock_on_stranger:
                left = s.stranger_delay - (now - self._stranger_since)
                if left <= 0:
                    return self._lock(ALERT, "Inconnu devant l'écran")
                return Verdict(ALERT, countdown=left)
            return Verdict(ALERT)

        self._stranger_since = None
        self._surfer_since = None
        self._surfer_warned = False

        if owner_here:
            return Verdict(SECURE, owner=owner)

        state = WATCH if obs.faces else IDLE
        if s.lock_on_absence:
            ref = max(self.owner_last_seen, self._absence_ref)
            left = s.absence_delay - (now - ref)
            if left <= 0:
                return self._lock(state, "Propriétaire absent")
            return Verdict(state, countdown=left)
        return Verdict(state)

    def _track_owner(self, obs: Observation) -> None:
        for f in obs.faces:
            if f.status == "known":
                self.owner_last_seen = obs.ts
                self.owner_name = f.name
                break

    def _lock(self, state: str, reason: str) -> Verdict:
        self._reset_timers()
        return Verdict(state, action="lock", reason=reason)

    # ── Périphériques ──
    def device_policy(self, now: float) -> str:
        """Action à appliquer à un branchement non approuvé : off | notify | ask | lock.

        « ask » ne demande que si le propriétaire est reconnu devant l'écran : sinon la question
        pourrait être validée par l'intrus lui-même, on verrouille directement.
        Sans profil, impossible de savoir qui répond : on demande quand même (le délai verrouille).
        """
        a = self.s.device_action
        if not self.armed or self.locked or a == "off":
            return "off"
        if a == "ask" and self.enrolled and not self.owner_recent(now):
            return "lock"
        return a
