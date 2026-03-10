# -*- coding: utf-8 -*-
"""
PrankGuard — Orchestrateur principal v3.0

Point d'entree de l'application. Lance tous les agents, gere leur cycle
de vie, et orchestre la communication entre eux.

Architecture d'escalade progressive :
  Niveau 0 — VEILLE : MOG2 seul, quasi 0% CPU
  Niveau 1 — SOFT : owner actif (clavier/souris), check facial toutes les 15s
  Niveau 2 — ALERTE : marqueur suspect, face recognition seule, 3s confirmation
  Niveau 3 — ACTIF : menace confirmee, tous les agents actifs

Thread : principal (GUI mainloop).
Dependances : tous les modules PrankGuard
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# Agents
from src.core.hardware_profiler import (
    PerformanceProfile,
    ProfileResult,
    run_profiler,
)
from src.agents.motion_agent import MotionAgent, MotionEvent, Phase
from src.agents.face_recognition_agent import (
    FaceRecognitionAgent,
    FaceIdentity,
    RecognitionResult,
)
from src.agents.head_pose_agent import HeadPoseAgent, HeadPoseResult
from src.agents.trajectory_agent import TrajectoryAgent, Trajectory
from src.agents.gaze_estimation_agent import GazeEstimationAgent, GazeResult
from src.agents.decision_agent import (
    DecisionAgent,
    AgentInputs,
    DecisionResult,
    Action,
    SecurityMode,
    Situation,
)
from src.agents.auto_throttle import AutoThrottle, ThrottleLevel, ThrottleState
from src.agents.device_monitor import DeviceMonitor, DeviceEvent, DeviceCategory
from src.gui.gui import PrankGuardGUI

logger = logging.getLogger("prankguard")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_WATCHDOG_INTERVAL_S = 5.0  # Verification des agents toutes les 5 secondes

# Escalade progressive
_USER_ACTIVE_THRESHOLD_MS = 5000   # Idle < 5s = utilisateur actif
_SOFT_CHECK_INTERVAL_S = 15.0      # Check facial en mode SOFT
_ALERTE_CONFIRM_DELAY_S = 3.0      # Duree minimale en ALERTE avant escalade
_ALERTE_TO_SOFT_COOLDOWN_S = 10.0  # Cooldown apres retour ALERTE/ACTIF → SOFT
_POST_LOCK_COOLDOWN_S = 5.0        # Cooldown apres lock+unlock


# ---------------------------------------------------------------------------
# Niveau d'escalade
# ---------------------------------------------------------------------------

class EscalationLevel(str, Enum):
    """Niveaux d'escalade progressive."""
    VEILLE = "VEILLE"   # Niveau 0 — MOG2 seul
    SOFT = "SOFT"       # Niveau 1 — owner actif, check facial periodique
    ALERTE = "ALERTE"   # Niveau 2 — suspect, face recognition seule
    ACTIF = "ACTIF"     # Niveau 3 — menace confirmee, tous agents


# ---------------------------------------------------------------------------
# GetLastInputInfo (Windows user32.dll)
# ---------------------------------------------------------------------------

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.c_uint),
        ('dwTime', ctypes.c_uint),
    ]


def _get_idle_time_ms() -> int:
    """Retourne le temps d'inactivite clavier/souris en millisecondes."""
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return max(0, millis)
    except Exception:
        return 0  # Fallback : considere l'utilisateur actif


# ---------------------------------------------------------------------------
# Orchestrateur
# ---------------------------------------------------------------------------

class PrankGuard:
    """
    Orchestrateur principal de PrankGuard avec escalade progressive.

    4 niveaux : VEILLE -> SOFT -> ALERTE -> ACTIF
    Chaque niveau a ses propres cooldowns et conditions de transition.
    """

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self._config_dir = config_dir

        # Profil materiel
        self._profile: Optional[ProfileResult] = None

        # Agents
        self._motion_agent: Optional[MotionAgent] = None
        self._face_agent: Optional[FaceRecognitionAgent] = None
        self._head_pose_agent: Optional[HeadPoseAgent] = None
        self._trajectory_agent: Optional[TrajectoryAgent] = None
        self._gaze_agent: Optional[GazeEstimationAgent] = None
        self._decision_agent: Optional[DecisionAgent] = None
        self._auto_throttle: Optional[AutoThrottle] = None
        self._device_monitor: Optional[DeviceMonitor] = None

        # Shared MediaPipe FaceMesh (head_pose + gaze partagent une instance)
        self._shared_face_mesh = None

        # GUI
        self._gui: Optional[PrankGuardGUI] = None

        # Etat general
        self._phase = Phase.VEILLE
        self._is_paused = False
        self._running = False
        self._frame_counter = 0
        self._device_alert_pending = False
        self._motion_detected = False

        # Escalade progressive
        self._escalation_level = EscalationLevel.VEILLE
        self._escalation_cooldown_until: float = 0.0
        self._alerte_start: float = 0.0
        self._next_soft_check: float = 0.0

        # Threads
        self._active_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._escalation_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Parametres effectifs (profil + throttle)
        self._effective_frame_skip = 5
        self._effective_gaze_enabled = True
        self._effective_width = 320
        self._effective_height = 240

        # Mode degrade
        self._gaze_available = True
        self._head_pose_available = True

    # =====================================================================
    # Demarrage
    # =====================================================================

    def start(self) -> None:
        """Demarre PrankGuard : profiler, agents, GUI."""
        self._running = True

        logger.info("=== PrankGuard v3.0 ===")

        # 1. Profilage materiel
        self._profile = run_profiler(config_dir=self._config_dir)
        logger.info("Profil : %s", self._profile.profile)
        self._apply_profile_params()

        # 2. Initialiser les agents
        self._init_agents()

        # 3. Initialiser le FaceMesh partage
        self._init_shared_face_mesh()

        # 4. Demarrer les agents permanents
        self._start_permanent_agents()

        # 5. Demarrer le watchdog
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="Watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

        # 6. Demarrer la boucle d'escalade
        self._escalation_thread = threading.Thread(
            target=self._escalation_loop,
            name="Escalation",
            daemon=True,
        )
        self._escalation_thread.start()

        # 7. Creer et configurer la GUI
        self._gui = PrankGuardGUI()
        self._configure_gui()

        # 8. Log initial
        self._gui.logs_tab.add_log("PrankGuard demarre", "INFO")
        self._gui.logs_tab.add_log(f"Profil : {self._profile.profile}", "INFO")
        self._gui.logs_tab.add_log(
            f"Provider ONNX : {self._face_agent.provider}", "INFO"
        )
        self._gui.update_profile(self._profile.profile)
        self._gui.update_level(self._escalation_level.value)

        # 9. Lancer la GUI (bloquant)
        logger.info("Lancement de la GUI")
        self._gui.run()

        # 10. Arret propre apres fermeture de la GUI
        self.stop()

    def stop(self) -> None:
        """Arrete proprement tous les agents."""
        logger.info("Arret de PrankGuard...")
        self._running = False

        if self._motion_agent:
            self._motion_agent.stop()
        if self._auto_throttle:
            self._auto_throttle.stop()
        if self._device_monitor:
            self._device_monitor.stop()
        if self._gaze_agent:
            self._gaze_agent.release()
        if self._head_pose_agent:
            self._head_pose_agent.release()
        if self._shared_face_mesh:
            self._shared_face_mesh.close()
            self._shared_face_mesh = None

        logger.info("PrankGuard arrete")

    # =====================================================================
    # Initialisation des agents
    # =====================================================================

    def _init_agents(self) -> None:
        """Initialise tous les agents."""
        # Motion agent (permanent)
        self._motion_agent = MotionAgent(camera_index=0)

        # Face recognition (charge le modele)
        provider = None
        if self._profile and self._profile.benchmark:
            provider = self._profile.benchmark.get("best_provider")
        self._face_agent = FaceRecognitionAgent(onnx_provider=provider)
        try:
            self._face_agent.load_model()
            self._face_agent.load_owner_encodings()
        except Exception as exc:
            logger.error("Erreur chargement face recognition : %s", exc)

        # Head pose (n'initialise PAS son propre FaceMesh — on utilise le partage)
        self._head_pose_agent = HeadPoseAgent()
        self._head_pose_available = True

        # Trajectory
        self._trajectory_agent = TrajectoryAgent()

        # Gaze estimation (desactive en LITE, n'initialise PAS son propre FaceMesh)
        if self._effective_gaze_enabled:
            self._gaze_agent = GazeEstimationAgent()
            self._gaze_available = True
        else:
            self._gaze_agent = None
            self._gaze_available = False
            logger.info("Gaze estimation desactive (profil LITE)")

        # Decision agent
        self._decision_agent = DecisionAgent(mode=SecurityMode.PEDAGO)

        # Auto-throttle (permanent)
        self._auto_throttle = AutoThrottle()

        # Device monitor (permanent)
        self._device_monitor = DeviceMonitor()
        self._device_monitor.load_whitelist()

    def _init_shared_face_mesh(self) -> None:
        """Initialise une instance FaceMesh partagee entre head_pose et gaze."""
        try:
            import mediapipe as mp
            self._shared_face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=2,
                refine_landmarks=True,  # Superset : inclut iris pour gaze
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            logger.info("FaceMesh partage initialise (head_pose + gaze)")
        except ImportError:
            logger.warning("MediaPipe non disponible — head_pose et gaze desactives")
            self._head_pose_available = False
            self._gaze_available = False
        except Exception as exc:
            logger.warning("Erreur init FaceMesh partage : %s", exc)
            self._head_pose_available = False
            self._gaze_available = False

    def _start_permanent_agents(self) -> None:
        """Demarre les agents qui tournent en permanence."""
        # Motion agent + abonnement
        self._motion_agent.on_phase_change(self._on_phase_change)
        self._motion_agent.on_frame(self._on_frame)
        self._motion_agent.start()

        # Auto-throttle + abonnement
        self._auto_throttle.on_level_change(self._on_throttle_change)
        self._auto_throttle.start()

        # Device monitor + abonnement
        self._device_monitor.on_device_change(self._on_device_change)
        self._device_monitor.start()

    # =====================================================================
    # Configuration GUI
    # =====================================================================

    def _configure_gui(self) -> None:
        """Branche les callbacks GUI <-> orchestrateur."""
        self._gui.set_lock_callback(self._manual_lock)
        self._gui.set_pause_callback(self._toggle_pause)

        # Enrollment callbacks
        self._gui.enrollment_tab.set_capture_callback(self._enrollment_capture)
        self._gui.enrollment_tab.set_save_callback(self._enrollment_save)
        self._gui.enrollment_tab.set_clear_callback(self._enrollment_clear)

    # =====================================================================
    # Escalade progressive
    # =====================================================================

    def _escalation_loop(self) -> None:
        """
        Thread d'escalade progressive.
        Gere les transitions entre les 4 niveaux en fonction de :
          - l'activite clavier/souris (GetLastInputInfo)
          - la detection de mouvement (MOG2)
          - les checks faciaux periodiques
        """
        logger.info("Boucle d'escalade demarree")

        while self._running:
            if self._is_paused:
                time.sleep(0.5)
                continue

            now = time.monotonic()
            idle_ms = _get_idle_time_ms()
            user_active = idle_ms < _USER_ACTIVE_THRESHOLD_MS

            with self._lock:
                motion = self._motion_detected

            level = self._escalation_level

            # Cooldown global d'escalade
            if now < self._escalation_cooldown_until:
                time.sleep(0.5)
                continue

            # --- VEILLE (Niveau 0) ---
            if level == EscalationLevel.VEILLE:
                if user_active:
                    self._escalate_to(EscalationLevel.SOFT, now)
                elif motion:
                    # Mouvement camera SANS activite clavier/souris
                    self._escalate_to(EscalationLevel.ALERTE, now)

            # --- SOFT (Niveau 1) ---
            elif level == EscalationLevel.SOFT:
                if not user_active and not motion:
                    self._escalate_to(EscalationLevel.VEILLE, now)
                elif motion and not user_active:
                    # Mouvement sans activite → suspect
                    self._escalate_to(EscalationLevel.ALERTE, now)
                elif now >= self._next_soft_check:
                    # Check facial periodique
                    self._do_soft_face_check(now)

            # --- ALERTE (Niveau 2) ---
            elif level == EscalationLevel.ALERTE:
                self._do_alerte_check(now)

            # --- ACTIF (Niveau 3) ---
            elif level == EscalationLevel.ACTIF:
                # L'analyse active tourne dans son propre thread
                # On surveille le retour au calme
                if not motion and not user_active:
                    last_motion = self._last_motion_time()
                    if last_motion > 0 and (now - last_motion) >= 3.0:
                        self._escalate_to(EscalationLevel.VEILLE, now)

            # Sleep adapte au niveau
            sleep_map = {
                EscalationLevel.VEILLE: 1.0,
                EscalationLevel.SOFT: 1.0,
                EscalationLevel.ALERTE: 0.3,
                EscalationLevel.ACTIF: 0.5,
            }
            time.sleep(sleep_map.get(level, 0.5))

        logger.info("Boucle d'escalade terminee")

    def _escalate_to(self, level: EscalationLevel, now: float) -> None:
        """Change le niveau d'escalade avec les initialisations appropriees."""
        old = self._escalation_level
        if old == level:
            return

        self._escalation_level = level
        logger.info("Escalade : %s -> %s", old.value, level.value)

        if level == EscalationLevel.VEILLE:
            if self._decision_agent:
                self._decision_agent.reset()

        elif level == EscalationLevel.SOFT:
            self._next_soft_check = now + _SOFT_CHECK_INTERVAL_S

        elif level == EscalationLevel.ALERTE:
            self._alerte_start = now

        elif level == EscalationLevel.ACTIF:
            self._frame_counter = 0
            if self._trajectory_agent:
                self._trajectory_agent.reset()
            # Demarrer le thread d'analyse active
            if self._active_thread is None or not self._active_thread.is_alive():
                self._active_thread = threading.Thread(
                    target=self._active_analysis_loop,
                    name="ActiveAnalysis",
                    daemon=True,
                )
                self._active_thread.start()

        # Mettre a jour la GUI
        if self._gui:
            self._gui.update_level(level.value)
            self._gui.update_phase(level.value)
            self._gui.logs_tab.add_log(
                f"Niveau : {old.value} -> {level.value}", "INFO"
            )

    def _do_soft_face_check(self, now: float) -> None:
        """Check facial rapide en mode SOFT (toutes les 15 secondes)."""
        frame = self._grab_frame()
        if frame is None:
            self._next_soft_check = now + _SOFT_CHECK_INTERVAL_S
            return

        if self._face_agent and self._face_agent.is_loaded:
            try:
                recog = self._face_agent.analyze(frame)
                if recog.owner_detected:
                    # Owner confirme → rester en SOFT
                    logger.debug("SOFT check : owner confirme")
                elif recog.stranger_detected:
                    # Inconnu detecte → escalade ALERTE
                    logger.info("SOFT check : inconnu detecte -> ALERTE")
                    self._escalate_to(EscalationLevel.ALERTE, now)
                    return
                # Aucun visage = normal (owner peut etre hors champ)
            except Exception as exc:
                logger.error("Erreur SOFT face check : %s", exc)

        self._next_soft_check = now + _SOFT_CHECK_INTERVAL_S

    def _do_alerte_check(self, now: float) -> None:
        """Check facial en mode ALERTE (face recognition seule)."""
        frame = self._grab_frame()
        if frame is None:
            return

        if self._face_agent and self._face_agent.is_loaded:
            try:
                recog = self._face_agent.analyze(frame)
                if recog.owner_detected:
                    # Owner reconnu → retour SOFT + cooldown 10s
                    logger.info("ALERTE : owner reconnu -> SOFT + cooldown 10s")
                    self._escalate_to(EscalationLevel.SOFT, now)
                    self._escalation_cooldown_until = now + _ALERTE_TO_SOFT_COOLDOWN_S
                    return
            except Exception as exc:
                logger.error("Erreur ALERTE face check : %s", exc)

        # Verifier si 3 secondes ecoulees sans reconnaissance owner
        if now - self._alerte_start >= _ALERTE_CONFIRM_DELAY_S:
            logger.info("ALERTE : 3s ecoulees, inconnu confirme -> ACTIF")
            self._escalate_to(EscalationLevel.ACTIF, now)

    def _grab_frame(self) -> Optional[np.ndarray]:
        """Capture une frame depuis la camera du motion agent."""
        if (
            self._motion_agent
            and self._motion_agent._cap
            and self._motion_agent._cap.isOpened()
        ):
            ret, frame = self._motion_agent._cap.read()
            if ret:
                return frame
        return None

    def _last_motion_time(self) -> float:
        """Retourne le timestamp du dernier mouvement detecte."""
        if self._motion_agent:
            return self._motion_agent._last_motion_time
        return 0.0

    # =====================================================================
    # Callbacks Motion Agent
    # =====================================================================

    def _on_phase_change(self, event: MotionEvent) -> None:
        """Callback du motion agent : alimente l'escalade."""
        if self._is_paused:
            return
        with self._lock:
            self._phase = event.phase
            self._motion_detected = event.phase == Phase.ACTIVE

    def _on_frame(self, frame: np.ndarray) -> None:
        """Callback du motion agent : chaque frame brute (pour GUI + enrollment)."""
        if self._gui:
            try:
                level = self._escalation_level
                if level == EscalationLevel.VEILLE:
                    situation = "IDLE"
                elif level == EscalationLevel.SOFT:
                    situation = "SAFE"
                elif level == EscalationLevel.ALERTE:
                    situation = "PASSING"
                else:
                    # ACTIF : la situation est determinee par le decision agent
                    situation = "SAFE"
                self._gui.camera_tab.update_frame(frame, situation)
                self._gui.enrollment_tab.update_preview(frame)
            except Exception:
                pass

    # =====================================================================
    # Boucle d'analyse active (Niveau 3 — ACTIF)
    # =====================================================================

    def _active_analysis_loop(self) -> None:
        """
        Boucle d'analyse en niveau ACTIF.
        Execute les agents sur chaque N-ieme frame et transmet au decision agent.
        Optimise : sleep proportionnel au frame_skip, FaceMesh partage.
        """
        logger.info("Boucle d'analyse active demarree")

        while self._running and self._escalation_level == EscalationLevel.ACTIF:
            if self._is_paused:
                time.sleep(0.1)
                continue

            # Capturer une frame depuis le motion agent
            frame = self._grab_frame()
            if frame is None:
                time.sleep(0.1)
                continue

            # Redimensionner pour l'analyse
            analysis_frame = cv2.resize(
                frame, (self._effective_width, self._effective_height)
            )

            # Executer les agents et construire les inputs
            inputs = self._run_analysis_agents(analysis_frame)

            # Owner detecte en ACTIF → retour immediat en SOFT
            if inputs.owner_detected and not inputs.owner_and_stranger:
                now = time.monotonic()
                self._escalate_to(EscalationLevel.SOFT, now)
                self._escalation_cooldown_until = now + _ALERTE_TO_SOFT_COOLDOWN_S
                continue

            # Verifier alerte peripherique
            with self._lock:
                if self._device_alert_pending:
                    inputs.device_alert = True
                    self._device_alert_pending = False

            # Decision
            if self._decision_agent:
                result = self._decision_agent.evaluate(inputs)
                self._handle_decision(result, frame)

            # Sleep adapte au frame skip (evite la boucle serree)
            analysis_interval = max(0.15, self._effective_frame_skip * 0.033)
            time.sleep(analysis_interval)

        logger.info("Boucle d'analyse active terminee")

    def _run_analysis_agents(self, frame: np.ndarray) -> AgentInputs:
        """Execute tous les agents d'analyse sur une frame et retourne les inputs."""
        inputs = AgentInputs(timestamp=time.monotonic())

        # 1. Face recognition (PRIORITAIRE)
        if self._face_agent and self._face_agent.is_loaded:
            try:
                recog = self._face_agent.analyze(frame)
                inputs.owner_detected = recog.owner_detected
                inputs.stranger_detected = recog.stranger_detected
                inputs.owner_and_stranger = recog.owner_and_stranger
                inputs.any_face_detected = len(recog.faces) > 0

                # Details du visage principal
                if recog.faces:
                    main_face = recog.faces[0]
                    inputs.face_is_large_enough = main_face.is_large_enough
                    inputs.face_is_centered = main_face.is_centered

                    # Trajectory
                    if self._trajectory_agent and main_face.is_large_enough:
                        traj = self._trajectory_agent.update(main_face.bbox)
                        inputs.approaching = traj.approaching

            except Exception as exc:
                logger.error("Erreur face recognition : %s", exc)

        # 2. FaceMesh partage (une seule inference pour head_pose + gaze)
        mp_result = None
        if self._shared_face_mesh and (self._head_pose_available or self._gaze_available):
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_result = self._shared_face_mesh.process(rgb)
            except Exception as exc:
                logger.error("Erreur FaceMesh partage : %s", exc)

        h, w = frame.shape[:2]

        # 3. Head pose (via FaceMesh partage)
        if self._head_pose_available and self._head_pose_agent and mp_result:
            try:
                pose = self._head_pose_agent.analyze_from_mp_result(mp_result, w, h)
                if pose.face_detected:
                    inputs.head_looking_at_screen = pose.looking_at_screen
            except Exception as exc:
                logger.error("Erreur head pose : %s", exc)

        # 4. Gaze estimation (via FaceMesh partage)
        if (
            self._gaze_available
            and self._gaze_agent
            and self._effective_gaze_enabled
            and mp_result
        ):
            try:
                gaze = self._gaze_agent.analyze_from_mp_result(mp_result, w, h)
                if gaze.face_detected:
                    inputs.gaze_looking_at_screen = gaze.looking_at_screen
            except Exception as exc:
                logger.error("Erreur gaze estimation : %s", exc)

        return inputs

    # =====================================================================
    # Traitement des decisions
    # =====================================================================

    def _handle_decision(self, result: DecisionResult, frame: np.ndarray) -> None:
        """Traite la decision du decision agent."""
        # Mettre a jour la GUI
        if self._gui:
            try:
                situation = result.situation.value
                self._gui.camera_tab.update_frame(frame, situation)

                if result.action == Action.LOCK:
                    self._gui.logs_tab.add_log(
                        f"VERROUILLAGE : {result.reason}", "CRITICAL"
                    )
                elif result.action == Action.ALERT:
                    self._gui.logs_tab.add_log(
                        f"ALERTE : {result.reason}", "WARNING"
                    )
                elif result.situation == Situation.THREAT:
                    self._gui.logs_tab.add_log(
                        f"THREAT : {result.reason}", "WARNING"
                    )
            except Exception:
                pass

        # Executer l'action
        if result.action == Action.LOCK:
            self._execute_lock(result.reason)
        elif result.action == Action.ALERT:
            self._execute_alert(result.reason)

    def _execute_lock(self, reason: str) -> None:
        """Verrouille le PC avec cooldown post-lock."""
        logger.warning("VERROUILLAGE : %s", reason)
        DecisionAgent.lock_workstation()

        # Activer le cooldown
        if self._decision_agent:
            self._decision_agent.notify_unlock()

        # Cooldown 5s apres lock pour eviter les boucles de lock
        now = time.monotonic()
        self._escalation_cooldown_until = now + _POST_LOCK_COOLDOWN_S
        self._escalate_to(EscalationLevel.SOFT, now)

    def _execute_alert(self, reason: str) -> None:
        """Emet une alerte sonore (shoulder surfer)."""
        logger.warning("ALERTE : %s", reason)
        try:
            import winsound
            # Beep court d'alerte
            winsound.Beep(1000, 300)
        except Exception:
            pass  # Pas de son disponible

    # =====================================================================
    # Callbacks evenementiels
    # =====================================================================

    def _on_throttle_change(self, state: ThrottleState) -> None:
        """Callback auto-throttle : ajuste les parametres effectifs."""
        if self._profile and self._auto_throttle:
            params = self._auto_throttle.get_effective_params(
                base_frame_skip=self._profile.frame_skip,
                base_gaze_enabled=self._profile.gaze_enabled,
                base_width=self._profile.analysis_width,
                base_height=self._profile.analysis_height,
            )
            # Minimum frame_skip de 5 pour optimiser le CPU
            self._effective_frame_skip = max(5, params["frame_skip"])
            self._effective_gaze_enabled = params["gaze_enabled"]
            self._effective_width = params["analysis_width"]
            self._effective_height = params["analysis_height"]

            logger.info(
                "Throttle %s : skip=%d, gaze=%s, res=%dx%d",
                state.level.value,
                self._effective_frame_skip,
                self._effective_gaze_enabled,
                self._effective_width,
                self._effective_height,
            )

            if self._gui:
                self._gui.update_throttle(state.level.value)
                self._gui.logs_tab.add_log(
                    f"Throttle : {state.level.value} (CPU {state.cpu_percent:.0f}%)",
                    "INFO",
                )

    def _on_device_change(self, event: DeviceEvent) -> None:
        """Callback device monitor : nouveau peripherique non whiteliste."""
        if event.is_new and not event.whitelisted:
            with self._lock:
                self._device_alert_pending = True

            logger.warning(
                "Peripherique non whiteliste : [%s] %s",
                event.category.value, event.device_name,
            )

            if self._gui:
                self._gui.logs_tab.add_log(
                    f"Peripherique : {event.category.value} - {event.device_name}",
                    "CRITICAL",
                )

    # =====================================================================
    # Actions manuelles (raccourcis GUI)
    # =====================================================================

    def _manual_lock(self) -> None:
        """Verrouillage manuel (raccourci L)."""
        self._execute_lock("Verrouillage manuel")

    def _toggle_pause(self) -> None:
        """Pause/reprise (raccourci P)."""
        self._is_paused = not self._is_paused
        state = "PAUSE" if self._is_paused else "ACTIF"
        logger.info("Surveillance : %s", state)
        if self._gui:
            self._gui.logs_tab.add_log(f"Surveillance : {state}", "INFO")

    # =====================================================================
    # Enrollment
    # =====================================================================

    def _enrollment_capture(self) -> None:
        """Capture un encoding du proprietaire."""
        if not self._face_agent or not self._face_agent.is_loaded:
            if self._gui:
                self._gui.enrollment_tab.show_message(
                    "Modele non charge. Veuillez patienter."
                )
            return

        frame = self._grab_frame()
        if frame is not None:
            embedding = self._face_agent.enroll_owner(frame)
            if embedding is not None:
                count = len(self._face_agent._owner_encodings)
                if self._gui:
                    self._gui.enrollment_tab.update_progress(count)
                    self._gui.logs_tab.add_log(
                        f"Capture enrollment {count}/10", "INFO"
                    )
            else:
                if self._gui:
                    self._gui.enrollment_tab.show_message(
                        "Aucun visage detecte. Placez-vous devant la camera."
                    )

    def _enrollment_save(self) -> None:
        """Sauvegarde les encodings du proprietaire."""
        if self._face_agent and self._face_agent.has_owner:
            self._face_agent.save_owner_encodings()
            if self._gui:
                self._gui.enrollment_tab.show_message(
                    "Encodings sauvegardes avec succes !"
                )
                self._gui.logs_tab.add_log("Encodings proprietaire sauvegardes", "INFO")
        else:
            if self._gui:
                self._gui.enrollment_tab.show_message(
                    "Aucun encoding a sauvegarder. Capturez d'abord."
                )

    def _enrollment_clear(self) -> None:
        """Supprime tous les encodings (RGPD droit a l'effacement)."""
        if self._face_agent:
            self._face_agent.clear_owner_encodings()
            if self._gui:
                self._gui.enrollment_tab.update_progress(0)
                self._gui.enrollment_tab.show_message(
                    "Tous les encodings ont ete supprimes."
                )
                self._gui.logs_tab.add_log(
                    "Encodings proprietaire supprimes (RGPD)", "INFO"
                )

    # =====================================================================
    # Watchdog
    # =====================================================================

    def _watchdog_loop(self) -> None:
        """Verifie periodiquement l'etat des agents et relance ceux qui ont plante."""
        while self._running:
            time.sleep(_WATCHDOG_INTERVAL_S)

            if not self._running:
                break

            # Verifier le motion agent
            if self._motion_agent and not self._motion_agent.is_running:
                logger.warning("Motion agent plante, relance...")
                try:
                    self._motion_agent.start()
                    if self._gui:
                        self._gui.logs_tab.add_log(
                            "Motion agent relance (watchdog)", "WARNING"
                        )
                except Exception as exc:
                    logger.error("Relance motion agent echouee : %s", exc)

            # Verifier l'auto-throttle
            if self._auto_throttle and not self._auto_throttle.is_running:
                logger.warning("Auto-throttle plante, relance...")
                try:
                    self._auto_throttle.start()
                except Exception as exc:
                    logger.error("Relance auto-throttle echouee : %s", exc)

    # =====================================================================
    # Utilitaires
    # =====================================================================

    def _apply_profile_params(self) -> None:
        """Applique les parametres du profil materiel."""
        if self._profile:
            # Minimum frame_skip de 5 pour optimiser le CPU
            self._effective_frame_skip = max(5, self._profile.frame_skip)
            self._effective_gaze_enabled = self._profile.gaze_enabled
            self._effective_width = self._profile.analysis_width
            self._effective_height = self._profile.analysis_height


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def main() -> int:
    """Point d'entree principal de PrankGuard."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s -- %(message)s",
    )

    app = PrankGuard()
    try:
        app.start()
    except KeyboardInterrupt:
        app.stop()
    except Exception as exc:
        logger.critical("Erreur fatale : %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
