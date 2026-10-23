"""Orchestrateur temps réel : caméra -> détection -> suivi -> reconnaissance -> décision -> actions."""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
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
from .vision.faces import EDGEFACE, FaceEngine, ProfileMatcher, Track, Tracker

log = get("engine")

ACTIVE_INTERVAL = 1 / 15     # cadence d'analyse : visage inconnu / en cours d'identification / alerte
OWNER_INTERVAL = 1 / 10      # seul(s) des profils reconnus devant l'écran
IDLE_INTERVAL = 1 / 8        # cadence quand la scène est vide (économie CPU)
REVERIFY_KNOWN = 0.7         # ré-identification périodique d'un visage reconnu
REVERIFY_OTHER = 0.25        # identification rapide des visages inconnus / en attente
MAX_EMBEDS_PER_FRAME = 3
DARK_LEVEL = 10              # luminosité moyenne en dessous de laquelle la caméra est considérée masquée
DEVICE_QUIET_AFTER_WAKE = 10.0   # après une sortie de veille uniquement (jamais après un déverrouillage)
# Catégories qui se ré-énumèrent d'elles-mêmes au réveil : seules celles-ci sont tolérées pendant
# la fenêtre de réveil. Stockage, clavier, souris, HID, téléphone… déclenchent toujours.
WAKE_TOLERATED = {"display", "audio", "network", "bluetooth", "camera", "other"}
ENROLL_TIMEOUT = 120.0       # un enrôlement abandonné ne doit jamais laisser la protection coupée
LOCK_CONFIRM_DELAY = 2.5     # après LockWorkStation, délai avant de vérifier l'état réel de la session
# Apprentissage continu : n'apprend que des vues sûres, variées et ancrées sur l'enrôlement d'origine
LEARN_INTERVAL = 45.0        # au plus un échantillon appris toutes les 45 s
LEARN_ANCHOR_MARGIN = 0.04   # similarité à l'ENRÔLEMENT ≥ seuil + marge (pas aux échantillons appris)
LEARN_MIN_LIVE = 0.85        # anti-spoofing nettement « réel »
LEARN_NOVELTY = 0.90         # inutile d'apprendre une vue quasi identique à une vue déjà connue
LEARN_IF_BELOW = 0.70        # vue déjà très bien reconnue : rien à apprendre (on garde la place)

PROMPT_SHOW_GRACE = 3.0      # temps laissé à la fenêtre de décision pour s'afficher avant le décompte

_prompt_ids = itertools.count(1)


@dataclass
class DevicePrompt:
    """Branchement en attente de la décision du propriétaire (clavier bloqué pendant ce temps)."""
    devices: list
    deadline: float
    timeout: float
    id: int = field(default_factory=lambda: next(_prompt_ids))


class Engine:
    def __init__(self, config: ConfigStore, gallery: Gallery, events: EventStore, dry_run: bool = False):
        self.config = config
        self.gallery = gallery
        self.events = events
        s = config.s
        self.camera = Camera(s.camera_index, s.camera_width, s.camera_height)
        self.faces = FaceEngine()
        self.tracker = Tracker()
        self.matcher = ProfileMatcher()
        self.anchor = ProfileMatcher()
        self._set_matchers()
        self._last_learn = 0.0
        self.guard = G.Guard(s)
        self.guard.enrolled = bool(self._usable_models())
        self.dry_run = dry_run
        if dry_run:
            log.warning("MODE TEST (--dry-run) : LockWorkStation ne sera jamais appelé")
        self.alarm = Alarm()
        self.watcher = winsys.SystemWatcher(on_device=self._on_device, on_session=self._on_session,
                                            on_power=self._on_power)
        self.watcher.on_device_early = self._on_device_early
        self.keyboard = winsys.KeyboardBlocker()
        self.notify: Callable[[str, str], None] = lambda title, msg: None
        # l'application affiche / ferme la fenêtre de décision (dict de la demande, ou None)
        self.on_device_prompt: Callable[[dict | None], None] = lambda prompt: None
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
        self._lock_check_after = 0.0
        self._next_session_poll = 0.0
        self._reset_tracks = False    # demandé par d'autres threads, appliqué dans le thread moteur
        self._dry_timer: threading.Timer | None = None
        self._prompt: DevicePrompt | None = None
        self._dev_lock = threading.RLock()
        self._prompt_timer: threading.Timer | None = None
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
        self.camera.start()
        if winsys.is_session_locked():
            self.guard.on_session_locked()
            self.camera.pause()
        self.watcher.start()
        self._thread = threading.Thread(target=self._loop, name="Engine", daemon=True)
        self._thread.start()
        if not self.faces.available(EDGEFACE):
            threading.Thread(target=self._fetch_better_model, name="ModelFetch", daemon=True).start()
        missing = [m for m in self.matcher.models if not self.faces.available(m)]
        if missing:
            log.warning("Profil(s) enregistré(s) avec un modèle absent (%s) : téléchargement en cours", missing)
        self.events.add("system", "info", "Surveillance démarrée",
                        "Armée" if self.guard.armed else "En pause")

    def stop(self) -> None:
        self._stop.set()
        if self._prompt_timer:
            self._prompt_timer.cancel()
        self.keyboard.stop()
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
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            minutes = 15.0
        minutes = 15.0 if minutes != minutes else max(1.0, min(1440.0, minutes))  # NaN / inf
        with self._lock:
            self.guard.disarm()
            self._snooze_until = time.time() + minutes * 60
            self.events.add("system", "warning", f"Surveillance en pause {int(minutes)} min")
        self._publish(force=True)

    def lock_now(self, reason: str = "Verrouillage manuel") -> None:
        if self.guard.locked:
            return
        self._do_lock(reason, None, manual=True)

    def _set_matchers(self) -> None:
        self.matcher.set_galleries(self.gallery.by_model())
        self.anchor.set_galleries(self.gallery.by_model(enrolled_only=True))

    def _usable_models(self) -> list[str]:
        return [m for m in self.matcher.models if self.faces.available(m)]

    def _fetch_better_model(self) -> None:
        """Arrière-plan : télécharge EdgeFace une seule fois ; les prochains enrôlements l'utiliseront."""
        if self.faces.enable(EDGEFACE, download=True):
            self.reload_gallery()       # un profil EdgeFace devenu utilisable
            self._publish(force=True)

    def reload_gallery(self) -> None:
        self._set_matchers()
        with self._lock:
            was = self.guard.enrolled
            self.guard.enrolled = bool(self._usable_models())
            if self.guard.enrolled and not was and self.guard.armed:
                self.guard.arm(time.time())   # premier profil : on repart d'un état propre
            self._reset_tracks = True

    def start_enroll(self, name: str) -> None:
        with self._lock:
            self._enroll = EnrollSession(name=name, model=self.faces.preferred)
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
        first = not any(n != sess.name for n in self.gallery.people)
        self.gallery.upsert(sess.name, emb, sess.best_thumb, model=sess.model or self.faces.preferred)
        if sess.base_yaw is not None and first:
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
                if self._enroll and now - self._enroll.started > ENROLL_TIMEOUT:
                    log.warning("Enrôlement abandonné — annulation automatique")
                    self.cancel_enroll()
                if self._reset_tracks:
                    self._reset_tracks = False
                    self.tracker.reset()
                    self._tracks = []
                # Filet de sécurité : si l'événement de déverrouillage est perdu (ou si
                # LockWorkStation a échoué), on resynchronise sur l'état réel de la session.
                if (self.guard.locked and not self.dry_run and now >= self._lock_check_after
                        and now >= self._next_session_poll):
                    self._next_session_poll = now + 1.0
                    if not winsys.is_session_locked():
                        log.warning("Session déverrouillée sans notification — resynchronisation")
                        self._on_session("unlock")

                if self.guard.locked and not self._enroll:
                    self._set_verdict(G.Verdict(G.LOCKED))
                    time.sleep(0.2)
                    continue

                if (self._enroll or self._verdict.state in (G.ALERT, G.SURFER)
                        or any(t.status != "known" for t in self._tracks)):
                    interval = ACTIVE_INTERVAL
                elif self._tracks:
                    interval = OWNER_INTERVAL
                else:
                    interval = IDLE_INTERVAL
                wait = last_analysis + interval - time.time()
                if wait > 0:
                    time.sleep(wait)
                seq2, ts, frame = self.camera.wait_newer(seq, timeout=0.5)
                now = time.time()
                if frame is None or now - ts > 1.5:
                    self._step_guard(G.Observation(now, False, camera_opening=self.camera.opening))
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
        # Reconnaissance : priorité aux visages non identifiés, budget limité par image.
        # Un visage qui regarde l'écran est toujours analysé, même petit ou de biais,
        # sinon il resterait "en analyse" indéfiniment sans jamais déclencher.
        embeds = 0
        due = []
        for t in tracks:
            if not (self.faces.embeddable(t.face) or self._is_looking(t)):
                continue
            period = REVERIFY_KNOWN if t.status in ("known", "spoof") else REVERIFY_OTHER
            if now - t.last_embed >= period:
                due.append(t)
        due.sort(key=lambda t: (t.status in ("known", "spoof"), t.last_embed))
        for t in due[:MAX_EMBEDS_PER_FRAME]:
            was_spoof = t.status == "spoof"
            try:
                if s.anti_spoof:
                    t.observe_cues(self.faces.liveness(frame, t.face), self.faces.screen_pattern(frame, t.face))
                if self.matcher.empty:
                    # aucun profil enrôlé : tout visage réel est inconnu
                    t.last_embed = now
                    t.status = "spoof" if t.is_spoof else ("unknown" if t.hits >= 2 else "pending")
                else:
                    embs = {m: self.faces.embed(frame, t.face, m) for m in self._usable_models()}
                    name, sim, model = self.matcher.best(embs)
                    t.observe_similarity(name, sim, s.match_threshold, now)
                    if s.adaptive_learning and len(tracks) == 1 and model:
                        self._maybe_learn(t, embs[model], now)
                embeds += 1
                if t.status == "spoof" and not was_spoof:
                    log.info("Visage ignoré (photo / écran) : score réel %.2f, trame d'écran %.2f, relief 3D %s",
                             t.live, t.moire, "oui" if t.depth else "non")
            except Exception:
                # une erreur d'un modèle ne doit jamais empêcher la décision de tourner
                t.last_embed = now
                log.exception("Analyse d'un visage en erreur")
        self._tracks = tracks

        obs = G.Observation(now, not self._dark, [
            G.FaceObs(status=t.status, name=t.name, looking=self._is_looking(t)) for t in tracks])
        self._step_guard(obs, frame)
        return embeds

    def _maybe_learn(self, t: Track, emb: np.ndarray, now: float) -> bool:
        """Ajoute une vue du propriétaire au profil si elle est sûre ET apporte du nouveau."""
        s = self.config.s
        if t.status != "known" or not t.name or now - self._last_learn < LEARN_INTERVAL:
            return False
        if s.anti_spoof and (t.live is None or t.live_n < 3 or t.live < LEARN_MIN_LIVE or t.moire > 0):
            return False
        if not self.faces.embeddable(t.face):
            return False
        model = self.gallery.model_of(t.name)
        if model is None:
            return False
        anchor_name, anchor_sim, _ = self.anchor.best({model: emb})
        if anchor_name != t.name or anchor_sim < s.match_threshold + LEARN_ANCHOR_MARGIN:
            return False
        if self.matcher.best({model: emb})[1] >= LEARN_IF_BELOW:
            return False
        known = self.gallery.embeddings(model).get(t.name)
        if known is None or (len(known) and float((known @ emb).max()) >= LEARN_NOVELTY):
            return False
        self._last_learn = now
        if self.gallery.add_adaptive(t.name, emb):
            self._set_matchers()
            log.info("Profil %s enrichi (vue nouvelle, similarité d'ancrage %.2f)", t.name, anchor_sim)
            return True
        return False

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
        # 1) verrouiller d'abord : rien (disque plein, base verrouillée…) ne doit pouvoir l'empêcher
        if self.dry_run:
            # simulation : verrouillage puis déverrouillage automatique, la session réelle n'est pas touchée
            log.warning("DRY-RUN : LockWorkStation non appelé")
            self._on_session("lock")
            if self._dry_timer:
                self._dry_timer.cancel()
            self._dry_timer = threading.Timer(4.0, self._on_session, args=("unlock",))
            self._dry_timer.daemon = True
            self._dry_timer.start()
        else:
            with self._lock:
                self.guard.on_session_locked()
                self._reset_tracks = True
            self._lock_check_after = time.time() + LOCK_CONFIRM_DELAY
            if winsys.lock_workstation():
                log.info("LockWorkStation appelé (équivalent Win+L)")
            else:
                # session NON verrouillée : on ne reste pas « aveugle » en croyant l'être
                log.error("LockWorkStation a échoué")
                with self._lock:
                    self.guard.locked = False
        # 2) puis journaliser / alerter, au mieux
        try:
            self.events.add("lock", "info" if manual else "critical", reason,
                            "Manuel" if manual else "Automatique", snap)
        except Exception:
            log.exception("Journalisation du verrouillage impossible")
        if s.alarm_enabled and not manual:
            try:
                self.alarm.play(s.alarm_volume)
            except Exception:
                log.exception("Alarme impossible")
        self._publish(force=True)

    # ── Événements système ──
    def _on_session(self, what: str) -> None:
        now = time.time()
        if what == "lock":
            with self._lock:
                self.guard.on_session_locked()
                self._reset_tracks = True
            self._resolve_prompt(None, "deny", "Session verrouillée avant votre réponse", lock=False)
            if not self._enroll:
                self.camera.pause()
            log.info("Session verrouillée — caméra libérée")
        else:
            with self._lock:
                if not self.guard.locked:
                    return      # doublon (resynchronisation puis notification WTS) : déjà réarmé
                self.guard.on_session_unlocked(now)
            self.alarm.stop()
            self.camera.resume()
            try:
                self.events.add("session", "info", "Session déverrouillée")
            except Exception:
                log.exception("Journal indisponible")
            log.info("Session déverrouillée — réarmement")
        self._publish(force=True)

    def _on_power(self, what: str) -> None:
        if what == "suspend":
            self.camera.pause()
        else:
            now = time.time()
            self._quiet_devices_until = now + DEVICE_QUIET_AFTER_WAKE
            with self._lock:
                self.guard.on_camera_restart(now)   # la caméra peut mettre du temps à revenir
            if not winsys.is_session_locked():
                self.camera.resume()

    def _device_filter(self, dev: winsys.DeviceArrival, now: float, early: bool = False) -> str | None:
        """Raison d'ignorer un branchement (approuvé, réveil, famille non surveillée), sinon None.

        En précoce, la famille n'est pas encore sûre (la 1re interface d'une rafale n'est pas
        forcément la plus parlante) : on bloque le clavier quand même, la fusion tranchera.
        """
        s = self.config.s
        if dev.key in s.trusted_devices:
            return "approuvé"
        if now < self._quiet_devices_until and dev.category in WAKE_TOLERATED:
            return "réveil, ignoré"
        if not early and dev.category not in s.device_categories:
            return "famille non surveillée"
        return None

    def _on_device_early(self, dev: winsys.DeviceArrival) -> None:
        """Première interface d'un branchement : bloque le clavier tout de suite si une décision peut suivre."""
        now = time.time()
        if self._device_filter(dev, now, early=True):
            return
        with self._lock:
            policy = self.guard.device_policy(now)
        if policy in ("ask", "lock"):
            self.keyboard.start()

    def _journal(self, *args) -> None:
        try:
            self.events.add(*args)
        except Exception:
            log.exception("Journal indisponible")

    def _on_device(self, dev: winsys.DeviceArrival) -> None:
        # Tout le traitement des branchements est sérialisé : une seule question à la fois, et une
        # réponse ne peut pas croiser un nouveau branchement ni l'expiration du délai.
        with self._dev_lock:
            try:
                self._handle_device(dev)
            except Exception:
                log.exception("Traitement du branchement en erreur")
            finally:
                if self._prompt is None:
                    self.keyboard.stop()
        self._publish(force=True)

    def _handle_device(self, dev: winsys.DeviceArrival) -> None:
        now = time.time()
        s = self.config.s
        label = f"{dev.name}" if dev.name else dev.category
        detail = f"{dev.category} · {dev.key}"
        skip = self._device_filter(dev, now)
        if skip:
            if skip == "approuvé":
                log.info("Périphérique approuvé : %s (%s)", label, dev.key)
            else:
                self._journal("device", "info", f"Branché : {label}", f"{detail} · ({skip})")
            return
        with self._lock:
            policy = self.guard.device_policy(now)
        pending = self._prompt
        if policy == "lock":
            if pending is not None:
                pending.devices.append(dev)
                self._resolve_prompt(pending.id, "deny", f"Périphérique branché : {label}")
            else:
                self._deny([dev], f"Périphérique branché : {label}", f"Branché : {label}", detail)
        elif policy == "ask":
            self._journal("device", "warning", f"Branché : {label}", f"{detail} · en attente de votre décision")
            if pending is not None:
                pending.devices.append(dev)      # même décision pour tout ce qui est branché pendant la question
            else:
                self._open_prompt(dev)
        else:
            self._journal("device", "warning" if policy == "notify" else "info", f"Branché : {label}", detail)
            if policy == "notify" and s.notifications:
                self.notify("Nouveau périphérique", label)

    def _deny(self, devices: list, lock_reason: str | None, title: str, detail: str) -> None:
        """Refus : verrouiller D'ABORD (rien ne doit pouvoir le retarder), puis éjecter et journaliser."""
        if lock_reason:
            try:
                self._do_lock(lock_reason, None)
            except Exception:
                log.exception("Verrouillage impossible")
        self._eject(devices)
        self._journal("device", "critical", title, detail)

    # ── Demande d'approbation ──
    def _arm_prompt_timer(self, prompt: DevicePrompt, delay: float) -> None:
        if self._prompt_timer:
            self._prompt_timer.cancel()
        self._prompt_timer = threading.Timer(delay, self._resolve_prompt,
                                             args=(prompt.id, "deny", f"Pas de réponse en {prompt.timeout:g} s"))
        self._prompt_timer.daemon = True
        self._prompt_timer.start()

    def _open_prompt(self, dev: winsys.DeviceArrival) -> None:
        """Appelé sous _dev_lock. Le compte à rebours part une fois la fenêtre affichée."""
        timeout = self.config.s.device_prompt_timeout
        self.keyboard.start()
        prompt = DevicePrompt(devices=[dev], deadline=time.time() + timeout + PROMPT_SHOW_GRACE, timeout=timeout)
        self._prompt = prompt
        # filet : même si la fenêtre ne s'affiche jamais, le délai expire et verrouille
        self._arm_prompt_timer(prompt, timeout + PROMPT_SHOW_GRACE)
        log.warning("Branchement non approuvé : %s — décision demandée (%g s)", dev.name or dev.category, timeout)
        chime()
        try:
            self.on_device_prompt(self.prompt_info())
        except Exception:
            log.exception("Affichage de la demande impossible")
        if self._prompt is prompt:
            prompt.deadline = time.time() + timeout
            self._arm_prompt_timer(prompt, timeout)

    def prompt_info(self) -> dict | None:
        p = self._prompt
        if p is None:
            return None
        return {"id": p.id, "timeout": p.timeout, "left": round(max(0.0, p.deadline - time.time()), 1),
                "keyboard_blocked": self.keyboard.active,
                "devices": [{"name": d.name or d.category, "category": d.category, "key": d.key} for d in p.devices]}

    def decide_device(self, prompt_id: int, decision: str) -> bool:
        """Réponse du propriétaire : once (cette fois) | always (mémoriser) | deny (refuser)."""
        if decision not in ("once", "always", "deny"):
            return False
        return self._resolve_prompt(prompt_id, decision, "Refusé par l'utilisateur")

    def _resolve_prompt(self, prompt_id: int | None, decision: str, reason: str, lock: bool = True) -> bool:
        with self._dev_lock:
            p = self._prompt
            if p is None or (prompt_id is not None and p.id != prompt_id):
                return False
            self._prompt = None
            if self._prompt_timer:
                self._prompt_timer.cancel()
                self._prompt_timer = None
            names = ", ".join(dict.fromkeys(d.name or d.category for d in p.devices))
            try:
                if decision == "deny":
                    self._deny(p.devices, f"Périphérique non approuvé : {names}" if lock else None,
                               f"Refusé : {names}", reason)
                else:
                    if decision == "always":
                        s = self.config.s
                        try:
                            self.config.update({
                                "trusted_devices": list(dict.fromkeys([*s.trusted_devices, *(d.key for d in p.devices)])),
                                "device_names": {**s.device_names, **{d.key: d.name or d.category for d in p.devices}},
                            })
                        except Exception:
                            log.exception("Mémorisation du périphérique impossible")
                    self._journal("device", "info",
                                  f"Autorisé {'pour toujours' if decision == 'always' else 'cette fois'} : {names}",
                                  " · ".join(d.key for d in p.devices))
            finally:
                self.keyboard.stop()
                try:
                    self.on_device_prompt(None)
                except Exception:
                    log.exception("Fermeture de la demande impossible")
        self._publish(force=True)
        return True

    def _eject(self, devices: list) -> None:
        """Retire (comme « Retirer en toute sécurité ») les périphériques USB refusés : rebrancher pour réessayer."""
        if self.dry_run:
            log.warning("DRY-RUN : éjection simulée de %s", ", ".join(d.instance for d in devices))
            return
        for d in devices:
            try:
                ok, _detail = winsys.eject_device(d.instance)
            except Exception:
                log.exception("Éjection impossible : %s", d.instance)
                continue
            if ok:
                self._journal("device", "info", f"Éjecté : {d.name or d.category}", "Rebranchez-le pour le réautoriser")

    def _on_config(self, changed: dict) -> None:
        s = self.config.s
        if {"camera_index", "camera_width", "camera_height"} & changed.keys():
            with self._lock:
                self.guard.on_camera_restart(time.time())
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
                "screen": round(t.moire, 2),
                "screen_raw": round(t.moire_raw, 1) if t.moire_raw is not None else None,
                "depth": t.depth,
                "looking": self._is_looking(t), "yaw": round(t.face.yaw, 2), "pitch": round(t.face.pitch, 2),
            })
        now = time.time()
        return {
            "v": self._version,
            "state": v.state, "reason": v.reason,
            "countdown": round(v.countdown, 1) if v.countdown is not None else None,
            "owner": v.owner,
            "armed": self.guard.armed, "locked": self.guard.locked,
            "camera_waived": self.guard._cam_waived,
            "snooze_left": max(0, int(self._snooze_until - now)) if self._snooze_until else 0,
            "faces": faces,
            "camera": {"open": self.camera.is_open, "error": self.camera.last_error,
                       "backend": self.camera.backend, "dark": self._dark, "width": w, "height": h},
            "metrics": {k: round(v2, 1) for k, v2 in self.metrics.items()},
            "enroll": self._enroll.progress() if self._enroll else None,
            "profiles": len(self.gallery.people),
            "dry_run": self.dry_run,
            "recognizer": self.faces.preferred,
            "device_prompt": self.prompt_info(),
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
