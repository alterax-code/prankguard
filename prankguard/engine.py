"""Orchestrateur temps réel : caméra -> détection -> suivi -> reconnaissance -> décision -> actions."""
from __future__ import annotations

import threading
import time
from typing import Callable

import cv2
import numpy as np

from . import guard as G
from . import winsys
from .alerts import Alarm, chime
from .config import ConfigStore
from .enroll import EnrollSession
from .events import EventStore
from .gallery import Gallery
from .log import get
from .vision.camera import Camera
from .vision.faces import FaceEngine, Matcher, Track, Tracker

log = get("engine")

ACTIVE_INTERVAL = 1 / 15     # cadence d'analyse quand il y a des visages
IDLE_INTERVAL = 1 / 8        # cadence quand la scène est vide (économie CPU)
REVERIFY_KNOWN = 0.7         # ré-identification périodique d'un visage reconnu
REVERIFY_OTHER = 0.25        # identification rapide des visages inconnus / en attente
MAX_EMBEDS_PER_FRAME = 3
DARK_LEVEL = 10              # luminosité moyenne en dessous de laquelle la caméra est considérée masquée
DEVICE_QUIET_AFTER_WAKE = 12.0


class Engine:
    def __init__(self, config: ConfigStore, gallery: Gallery, events: EventStore, dry_run: bool = False):
        self.config = config
        self.gallery = gallery
        self.events = events
        s = config.s
        self.camera = Camera(s.camera_index, s.camera_width, s.camera_height)
        self.faces = FaceEngine()
        self.tracker = Tracker()
        self.matcher = Matcher(gallery.embeddings())
        self.guard = G.Guard(s)
        self.guard.enrolled = not self.matcher.empty
        self.dry_run = dry_run
        self.alarm = Alarm()
        self.watcher = winsys.SystemWatcher(on_device=self._on_device, on_session=self._on_session,
                                            on_power=self._on_power)
        self.notify: Callable[[str, str], None] = lambda title, msg: None
        self.on_change: Callable[[dict], None] = lambda snap: None

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._verdict = G.Verdict(G.STARTING)
        self._tracks: list[Track] = []
        self._frame_shape = (s.camera_height, s.camera_width)
        self._dark = False
        self._enroll: EnrollSession | None = None
        self._snooze_until = 0.0
        self._quiet_devices_until = time.time() + 3.0
        self._lock_pending_check = 0.0
        self.metrics = {"analysis_ms": 0.0, "analysis_fps": 0.0, "camera_fps": 0.0, "embeds_per_s": 0.0}
        self._version = 0

        config.subscribe(self._on_config)

    # ── Cycle de vie ──
    def start(self) -> None:
        s = self.config.s
        if not s.armed_on_start:
            self.guard.disarm()
        else:
            self.guard.arm(time.time(), delay=2.0)
        if winsys.is_session_locked():
            self.guard.on_session_locked()
        else:
            self.camera.start()
        self.watcher.start()
        self._thread = threading.Thread(target=self._loop, name="Engine", daemon=True)
        self._thread.start()
        self.events.add("system", "info", "Surveillance démarrée",
                        "Armée" if self.guard.armed else "En pause")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(3)
        self.watcher.stop()
        self.camera.stop()
        self.alarm.stop()

    # ── Commandes (appelées depuis l'UI / le systray) ──
    def set_armed(self, armed: bool) -> None:
        with self._lock:
            self._snooze_until = 0.0
            if armed:
                self.guard.arm(time.time())
                self.events.add("system", "info", "Surveillance activée")
            else:
                self.guard.disarm()
                self.events.add("system", "warning", "Surveillance désactivée")
        self._publish(force=True)

    def snooze(self, minutes: float) -> None:
        with self._lock:
            self.guard.disarm()
            self._snooze_until = time.time() + minutes * 60
            self.events.add("system", "warning", f"Surveillance en pause {int(minutes)} min")
        self._publish(force=True)

    def lock_now(self, reason: str = "Verrouillage manuel") -> None:
        self._do_lock(reason, None, manual=True)

    def reload_gallery(self) -> None:
        self.matcher.set_gallery(self.gallery.embeddings())
        with self._lock:
            self.guard.enrolled = not self.matcher.empty
            self.tracker.reset()

    def start_enroll(self, name: str) -> None:
        with self._lock:
            self._enroll = EnrollSession(name=name)
            if not self.camera.is_open:
                self.camera.resume()
        self._publish(force=True)

    def finish_enroll(self) -> dict:
        with self._lock:
            sess, self._enroll = self._enroll, None
        if not sess or sess.total < 3:
            self._publish(force=True)
            return {"ok": False, "error": "Pas assez d'échantillons"}
        emb = sess.result()
        self.gallery.upsert(sess.name, emb, sess.best_thumb)
        if sess.base_yaw is not None:
            # la position de la caméra par rapport à l'écran vient d'être mesurée
            self.config.update({"gaze_yaw": round(sess.base_yaw, 3), "gaze_pitch": round(sess.base_pitch, 3)})
        self.reload_gallery()
        self.events.add("enroll", "info", f"Profil « {sess.name} » enregistré", f"{len(emb)} échantillons")
        self._publish(force=True)
        return {"ok": True, "samples": int(len(emb))}

    def cancel_enroll(self) -> None:
        with self._lock:
            self._enroll = None
        self._publish(force=True)

    # ── Boucle principale ──
    def _loop(self) -> None:
        seq = 0
        last_analysis = 0.0
        fps_n, fps_t, emb_n = 0, time.time(), 0
        while not self._stop.is_set():
            try:
                now = time.time()
                if self._snooze_until and now >= self._snooze_until:
                    self._snooze_until = 0.0
                    self.guard.arm(now)
                    self.events.add("system", "info", "Fin de la pause — surveillance réactivée")
                if self._lock_pending_check and now >= self._lock_pending_check:
                    self._lock_pending_check = 0.0
                    if not winsys.is_session_locked():
                        log.warning("La session ne s'est pas verrouillée — réarmement")
                        self.guard.on_session_unlocked(now)
                        self.camera.resume()

                if self.guard.locked and not self._enroll:
                    self._set_verdict(G.Verdict(G.LOCKED))
                    time.sleep(0.2)
                    continue

                busy = bool(self._tracks) or self._verdict.state in (G.ALERT, G.SURFER) or self._enroll
                interval = ACTIVE_INTERVAL if busy else IDLE_INTERVAL
                wait = last_analysis + interval - time.time()
                if wait > 0:
                    time.sleep(wait)
                seq2, ts, frame = self.camera.wait_newer(seq, timeout=0.5)
                now = time.time()
                if frame is None or now - ts > 1.5:
                    self._step_guard(G.Observation(now, False))
                    continue
                if seq2 == seq:
                    continue
                seq = seq2
                last_analysis = now
                t0 = time.perf_counter()
                emb_n += self._analyze(frame, now)
                dt = (time.perf_counter() - t0) * 1000
                self.metrics["analysis_ms"] = 0.8 * self.metrics["analysis_ms"] + 0.2 * dt
                fps_n += 1
                if now - fps_t >= 1.0:
                    self.metrics["analysis_fps"] = fps_n / (now - fps_t)
                    self.metrics["embeds_per_s"] = emb_n / (now - fps_t)
                    self.metrics["camera_fps"] = self.camera.measured_fps
                    fps_n, emb_n, fps_t = 0, 0, now
            except Exception:
                log.exception("Erreur dans la boucle d'analyse")
                time.sleep(0.5)

    def _analyze(self, frame: np.ndarray, now: float) -> int:
        s = self.config.s
        h, w = frame.shape[:2]
        self._frame_shape = (h, w)
        # Caméra masquée : image quasi noire (sur une vignette, coût négligeable)
        self._dark = float(cv2.resize(frame, (32, 24), interpolation=cv2.INTER_AREA).mean()) < DARK_LEVEL
        faces = [] if self._dark else self.faces.detect(frame)

        with self._lock:
            enroll = self._enroll
        if enroll:
            if enroll.feed(self.faces, frame, faces, now):
                pass
            self._tracks = self.tracker.update(faces, now)
            self._publish()
            return 1

        tracks = self.tracker.update(faces, now)
        # Reconnaissance : priorité aux visages non identifiés, budget limité par image
        embeds = 0
        due = []
        for t in tracks:
            if not self.faces.embeddable(t.face):
                continue
            period = REVERIFY_KNOWN if t.status in ("known", "spoof") else REVERIFY_OTHER
            if now - t.last_embed >= period:
                due.append(t)
        due.sort(key=lambda t: (t.status in ("known", "spoof"), t.last_embed))
        for t in due[:MAX_EMBEDS_PER_FRAME]:
            if s.anti_spoof:
                t.observe_liveness(self.faces.liveness(frame, t.face))
            if self.matcher.empty:
                # aucun profil enrôlé : tout visage réel est inconnu
                t.last_embed = now
                t.status = "spoof" if t.is_spoof else ("unknown" if t.hits >= 2 else "pending")
            else:
                name, sim = self.matcher.best(self.faces.embed(frame, t.face))
                t.observe_similarity(name, sim, s.match_threshold, now)
            embeds += 1
        self._tracks = tracks

        obs = G.Observation(now, not self._dark, [
            G.FaceObs(status=t.status, name=t.name, looking=self._is_looking(t)) for t in tracks])
        self._step_guard(obs, frame)
        return embeds

    def _is_looking(self, t: Track) -> bool:
        s = self.config.s
        f = t.face
        return (f.rel_size >= s.min_face_size and abs(f.yaw - s.gaze_yaw) <= s.max_yaw
                and -0.9 <= f.pitch - s.gaze_pitch <= 1.0)

    def _step_guard(self, obs: G.Observation, frame: np.ndarray | None = None) -> None:
        if not obs.camera_ok:
            self._tracks = []
        with self._lock:
            v = self.guard.step(obs)
        if v.action == "lock":
            self._do_lock(v.reason, frame)
        elif v.action == "warn":
            self.events.add("warn", "warning", v.reason, "Propriétaire présent, regard tiers détecté",
                            frame if self.config.s.save_snapshots else None)
            chime()
            if self.config.s.notifications:
                self.notify("Regard indiscret", v.reason)
        self._set_verdict(v)

    def _set_verdict(self, v: G.Verdict) -> None:
        old = self._verdict
        self._verdict = v
        self._publish(force=old.state != v.state)

    # ── Actions ──
    def _do_lock(self, reason: str, frame: np.ndarray | None, manual: bool = False) -> None:
        s = self.config.s
        if frame is None and not manual:
            _, _, frame = self.camera.latest()
        snap = frame if (s.save_snapshots and frame is not None and not manual) else None
        log.warning("VERROUILLAGE : %s", reason)
        self.events.add("lock", "info" if manual else "critical", reason,
                        "Manuel" if manual else "Automatique", snap)
        if s.alarm_enabled and not manual:
            self.alarm.play(s.alarm_volume)
        if self.dry_run:
            # simulation : verrouillage puis déverrouillage automatique, la session réelle n'est pas touchée
            log.warning("DRY-RUN : LockWorkStation non appelé")
            self._on_session("lock")
            t = threading.Timer(4.0, self._on_session, args=("unlock",))
            t.daemon = True
            t.start()
            return
        with self._lock:
            self.guard.on_session_locked()
            self.tracker.reset()
        self._lock_pending_check = time.time() + 2.5
        if not winsys.lock_workstation():
            log.error("LockWorkStation a échoué")
        self._publish(force=True)

    # ── Événements système ──
    def _on_session(self, what: str) -> None:
        now = time.time()
        if what == "lock":
            with self._lock:
                self.guard.on_session_locked()
                self.tracker.reset()
            self._lock_pending_check = 0.0
            if not self._enroll:
                self.camera.pause()
            log.info("Session verrouillée — caméra libérée")
        else:
            self._quiet_devices_until = now + DEVICE_QUIET_AFTER_WAKE
            self.alarm.stop()
            with self._lock:
                self.guard.on_session_unlocked(now)
            self.camera.resume()
            self.events.add("session", "info", "Session déverrouillée")
            log.info("Session déverrouillée — réarmement")
        self._publish(force=True)

    def _on_power(self, what: str) -> None:
        if what == "suspend":
            self.camera.pause()
        else:
            self._quiet_devices_until = time.time() + DEVICE_QUIET_AFTER_WAKE
            if not winsys.is_session_locked():
                self.camera.resume()

    def _on_device(self, dev: winsys.DeviceArrival) -> None:
        now = time.time()
        s = self.config.s
        label = f"{dev.name}" if dev.name else dev.category
        detail = f"{dev.category} · {dev.key}"
        if dev.key in s.trusted_devices:
            log.info("Périphérique approuvé : %s (%s)", label, dev.key)
            return
        if now < self._quiet_devices_until:
            self.events.add("device", "info", f"Branché : {label}", detail + " · (réveil, ignoré)")
            return
        with self._lock:
            policy = self.guard.device_policy(now)
        if policy == "lock":
            self.events.add("device", "critical", f"Branché : {label}", detail)
            self._do_lock(f"Périphérique branché : {label}", None)
        else:
            self.events.add("device", "warning" if policy == "notify" else "info", f"Branché : {label}", detail)
            if policy == "notify" and s.notifications:
                self.notify("Nouveau périphérique", label)
        self._publish(force=True)

    def _on_config(self, changed: dict) -> None:
        s = self.config.s
        if {"camera_index", "camera_width", "camera_height"} & changed.keys():
            self.camera.reconfigure(s.camera_index, s.camera_width, s.camera_height)
        if "start_with_windows" in changed:
            try:
                winsys.set_autostart(s.start_with_windows)
            except OSError:
                log.exception("Autostart impossible")
        self._publish(force=True)

    # ── État publié vers l'UI ──
    def snapshot(self) -> dict:
        v = self._verdict
        h, w = self._frame_shape
        faces = []
        for t in self._tracks:
            x, y, bw, bh = (float(c) for c in t.face.box)
            faces.append({
                "id": t.id, "x": x / w, "y": y / h, "w": bw / w, "h": bh / h,
                "status": t.status, "name": t.name,
                "sim": round(float(t.ema), 3) if t.ema is not None else None,
                "live": round(float(t.live), 2) if t.live is not None else None,
                "looking": self._is_looking(t), "yaw": round(t.face.yaw, 2), "pitch": round(t.face.pitch, 2),
            })
        now = time.time()
        return {
            "v": self._version,
            "state": v.state, "reason": v.reason,
            "countdown": round(v.countdown, 1) if v.countdown is not None else None,
            "owner": v.owner,
            "armed": self.guard.armed, "locked": self.guard.locked,
            "snooze_left": max(0, int(self._snooze_until - now)) if self._snooze_until else 0,
            "faces": faces,
            "camera": {"open": self.camera.is_open, "error": self.camera.last_error,
                       "backend": self.camera.backend, "dark": self._dark, "width": w, "height": h},
            "metrics": {k: round(v2, 1) for k, v2 in self.metrics.items()},
            "enroll": self._enroll.progress() if self._enroll else None,
            "profiles": len(self.gallery.people),
            "dry_run": self.dry_run,
        }

    _last_pub = 0.0

    def _publish(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_pub < 1 / 15:
            return
        self._last_pub = now
        self._version += 1
        try:
            self.on_change(self.snapshot())
        except Exception:
            log.exception("Publication d'état en erreur")
