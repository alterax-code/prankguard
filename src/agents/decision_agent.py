# -*- coding: utf-8 -*-
"""
Decision Agent — PrankGuard v3.0

Cœur de la logique de décision. Reçoit les résultats de tous les agents
et prend la décision finale : ne rien faire, alerter ou verrouiller.

RÈGLE ABSOLUE : les agents ne sont JAMAIS combinés par addition de scores.
Le face recognition est prioritaire et court-circuite tout.

Flux de décision (section 3.1 du plan v3) :
  1. Owner reconnu ? → STOP immédiat, aucun verrouillage possible.
  2. Owner + stranger ? → SHOULDER SURFER : alerte sonore, pas de lock.
  3. Stranger seul → vérifier 3 conditions :
     - Gaze : regarde l'écran ?
     - Head pose : tête orientée vers la caméra ?
     - Trajectory : s'approche de l'écran ?
     Si ≥ 2/3 conditions → THREAT → lock après 4 secondes.
  4. Personne détectée → IDLE (mode SECURE : lock après 10s cumulatif).
  5. Visage au loin → PASSING (mode SECURE : lock après 10s cumulatif).

Thread : actif seulement (phase ACTIVE).

Dépendances : aucune externe (reçoit des dataclasses des autres agents)
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("prankguard.decision_agent")


# ---------------------------------------------------------------------------
# Constantes (section 3.3 du plan v3)
# ---------------------------------------------------------------------------

class SecurityMode(str, Enum):
    """Mode de sécurité configurable par l'utilisateur."""
    PEDAGO = "PEDAGO"  # Verrouille uniquement sur menace directe (par défaut)
    SECURE = "SECURE"  # Verrouille aussi en IDLE/PASSING


class Situation(str, Enum):
    """Situations détectées par le decision agent."""
    SAFE = "SAFE"                      # Owner seul, tout va bien
    THREAT = "THREAT"                  # Stranger fixe l'écran, s'approche
    IDLE = "IDLE"                      # Personne devant le PC
    PASSING = "PASSING"                # Quelqu'un passe au loin
    SHOULDER_SURFER = "SHOULDER_SURFER"  # Owner + stranger
    DEVICE_ALERT = "DEVICE_ALERT"      # Nouveau périphérique branché
    COOLDOWN = "COOLDOWN"              # Cooldown après déverrouillage


class Action(str, Enum):
    """Actions possibles du decision agent."""
    NOTHING = "NOTHING"      # Ne rien faire
    ALERT = "ALERT"          # Alerte sonore (shoulder surfer)
    LOCK = "LOCK"            # Verrouiller le PC


# Délais en secondes
THREAT_LOCK_DELAY_S = 4.0        # Délai avant lock sur THREAT
IDLE_LOCK_DELAY_S = 10.0         # Délai IDLE/PASSING en mode SECURE (cumulatif)
UNLOCK_COOLDOWN_S = 3.0          # Cooldown après déverrouillage

# Nombre minimum de conditions remplies pour THREAT (sur gaze, head_pose, trajectory)
MIN_THREAT_CONDITIONS = 2

# Grace period : duree pendant laquelle l'owner est considere present apres
# sa derniere detection. Evite l'oscillation "owner / no face" lors
# d'une occlusion partielle (main devant le visage, etc.)
OWNER_GRACE_PERIOD_S = 2.0


# ---------------------------------------------------------------------------
# Structures de données d'entrée
# ---------------------------------------------------------------------------

@dataclass
class AgentInputs:
    """
    Résultats agrégés de tous les agents pour une frame donnée.
    Le decision_agent ne connaît pas les agents directement — il reçoit
    cette structure de l'orchestrateur (prankguard.py).
    """
    # Face recognition
    owner_detected: bool = False
    stranger_detected: bool = False
    owner_and_stranger: bool = False  # Shoulder surfer

    # Gaze estimation (peut être None si désactivé en profil LITE)
    gaze_looking_at_screen: Optional[bool] = None

    # Head pose
    head_looking_at_screen: Optional[bool] = None

    # Trajectory
    approaching: Optional[bool] = None

    # Face recognition détails (pour les visages individuels)
    any_face_detected: bool = False
    face_is_large_enough: bool = False  # > 20% hauteur frame
    face_is_centered: bool = False      # < 35% depuis centre

    # Device monitor
    device_alert: bool = False

    # Timestamp de la frame
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Structures de données de sortie
# ---------------------------------------------------------------------------

@dataclass
class DecisionResult:
    """Résultat de la décision."""
    situation: Situation = Situation.SAFE
    action: Action = Action.NOTHING
    threat_timer_active: bool = False
    threat_timer_remaining_s: float = 0.0
    idle_timer_active: bool = False
    idle_timer_remaining_s: float = 0.0
    conditions_met: int = 0          # Nombre de conditions THREAT remplies (0-3)
    reason: str = ""                 # Explication lisible de la décision
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Decision Agent
# ---------------------------------------------------------------------------

# Type des callbacks
DecisionCallback = Callable[[DecisionResult], None]


class DecisionAgent:
    """
    Agent de décision central de PrankGuard.

    Reçoit les résultats de tous les agents via AgentInputs et retourne
    une DecisionResult avec la situation détectée et l'action à entreprendre.

    IMPORTANT : les scores des agents ne sont JAMAIS additionnés.
    Le face recognition est évalué en premier et court-circuite tout.

    Utilisation :
        agent = DecisionAgent(mode=SecurityMode.PEDAGO)
        agent.on_decision(callback)
        result = agent.evaluate(inputs)
    """

    def __init__(
        self,
        mode: SecurityMode = SecurityMode.PEDAGO,
        threat_delay: float = THREAT_LOCK_DELAY_S,
        idle_delay: float = IDLE_LOCK_DELAY_S,
        cooldown_delay: float = UNLOCK_COOLDOWN_S,
    ) -> None:
        self._mode = mode
        self._threat_delay = threat_delay
        self._idle_delay = idle_delay
        self._cooldown_delay = cooldown_delay

        # Timers internes
        self._threat_start: Optional[float] = None  # Début du timer THREAT
        self._idle_accumulated: float = 0.0          # Timer IDLE cumulatif (ne se reset pas)
        self._idle_last_tick: Optional[float] = None
        self._cooldown_until: float = 0.0            # Timestamp fin de cooldown

        # Grace period : dernier instant ou l'owner a ete vu
        self._owner_last_seen: float = 0.0

        # Callbacks
        self._decision_callbacks: list[DecisionCallback] = []

        # Dernier résultat
        self._last_result: Optional[DecisionResult] = None

    # ----- Propriétés publiques -----

    @property
    def mode(self) -> SecurityMode:
        return self._mode

    @mode.setter
    def mode(self, value: SecurityMode) -> None:
        self._mode = value
        logger.info("Mode de sécurité changé : %s", value.value)

    @property
    def last_result(self) -> Optional[DecisionResult]:
        return self._last_result

    # ----- Callbacks -----

    def on_decision(self, callback: DecisionCallback) -> None:
        """Enregistre un callback appelé à chaque décision."""
        self._decision_callbacks.append(callback)

    # ----- Cooldown après déverrouillage -----

    def notify_unlock(self) -> None:
        """
        Notifie le decision agent qu'un déverrouillage vient d'avoir lieu.
        Active le cooldown de 3 secondes pour éviter un re-lock immédiat.
        """
        self._cooldown_until = time.monotonic() + self._cooldown_delay
        self._reset_timers()
        logger.info("Cooldown post-déverrouillage activé (%.0f s)", self._cooldown_delay)

    # ----- Évaluation principale -----

    def evaluate(self, inputs: AgentInputs) -> DecisionResult:
        """
        Évalue les résultats de tous les agents et retourne la décision.

        Suit strictement le flux de décision de la section 3.1 du plan v3.
        Les agents ne sont JAMAIS combinés par addition de scores.
        """
        now = time.monotonic()
        result = DecisionResult(timestamp=now)

        # --- Étape 0 : Cooldown après déverrouillage ---
        if now < self._cooldown_until:
            result.situation = Situation.COOLDOWN
            result.action = Action.NOTHING
            result.reason = "Cooldown post-déverrouillage actif"
            self._emit(result)
            return result

        # --- Étape 0bis : Alerte périphérique (priorité maximale) ---
        if inputs.device_alert:
            result.situation = Situation.DEVICE_ALERT
            result.action = Action.LOCK
            result.reason = "Nouveau peripherique detecte : verrouillage immediat"
            self._reset_timers()
            self._emit(result)
            return result

        # --- Étape 1 : Owner reconnu ? → STOP immédiat ---
        if inputs.owner_detected and not inputs.owner_and_stranger:
            self._owner_last_seen = now
            result.situation = Situation.SAFE
            result.action = Action.NOTHING
            result.reason = "Propriétaire reconnu"
            self._reset_timers()
            self._emit(result)
            return result

        # --- Étape 2 : Owner + stranger ? → SHOULDER SURFER ---
        if inputs.owner_and_stranger:
            self._owner_last_seen = now
            result.situation = Situation.SHOULDER_SURFER
            result.action = Action.ALERT
            result.reason = "Proprietaire + inconnu detectes : alerte shoulder surfer"
            # Pas de lock, mais alerte sonore
            self._threat_start = None  # Pas de timer THREAT
            self._emit(result)
            return result

        # --- Étape 3 : Stranger seul → évaluer les 3 conditions ---
        if inputs.stranger_detected and inputs.face_is_large_enough:
            conditions = self._count_threat_conditions(inputs)
            result.conditions_met = conditions

            if conditions >= MIN_THREAT_CONDITIONS:
                # THREAT détecté — gérer le timer de 4 secondes
                result.situation = Situation.THREAT
                result = self._handle_threat_timer(result, now)
                self._stop_idle_timer()
                self._emit(result)
                return result
            else:
                # Stranger présent mais ne fixe pas l'écran → PASSING
                result.situation = Situation.PASSING
                result.action = Action.NOTHING
                result.reason = (
                    f"Inconnu détecté mais {conditions}/3 conditions "
                    f"(minimum {MIN_THREAT_CONDITIONS})"
                )
                self._threat_start = None
                result = self._handle_idle_passing_timer(result, now)
                self._emit(result)
                return result

        # --- Étape 4 : Visage détecté mais trop petit → PASSING ---
        if inputs.any_face_detected and not inputs.face_is_large_enough:
            # Grace period : si l'owner etait la recemment et pas de stranger
            if (
                not inputs.stranger_detected
                and (now - self._owner_last_seen) < OWNER_GRACE_PERIOD_S
            ):
                result.situation = Situation.SAFE
                result.action = Action.NOTHING
                result.reason = "Owner vu récemment (grace period)"
                self._threat_start = None
                self._emit(result)
                return result
            result.situation = Situation.PASSING
            result.action = Action.NOTHING
            result.reason = "Visage détecté au loin (trop petit)"
            self._threat_start = None
            result = self._handle_idle_passing_timer(result, now)
            self._emit(result)
            return result

        # --- Étape 5 : Aucun visage → IDLE ---
        if not inputs.any_face_detected:
            # Grace period : si l'owner etait la recemment et pas de stranger
            if (
                not inputs.stranger_detected
                and (now - self._owner_last_seen) < OWNER_GRACE_PERIOD_S
            ):
                result.situation = Situation.SAFE
                result.action = Action.NOTHING
                result.reason = "Owner vu récemment (grace period)"
                self._threat_start = None
                self._emit(result)
                return result
            result.situation = Situation.IDLE
            result.action = Action.NOTHING
            result.reason = "Aucun visage détecté"
            self._threat_start = None
            result = self._handle_idle_passing_timer(result, now)
            self._emit(result)
            return result

        # --- Fallback : situation non couverte → SAFE ---
        result.situation = Situation.SAFE
        result.action = Action.NOTHING
        result.reason = "Situation non classifiée"
        self._emit(result)
        return result

    # ----- Conditions THREAT -----

    def _count_threat_conditions(self, inputs: AgentInputs) -> int:
        """
        Compte le nombre de conditions THREAT remplies parmi :
          1. Gaze : regarde l'écran
          2. Head pose : tête orientée vers la caméra
          3. Trajectory : s'approche de l'écran

        Si un agent est désactivé (None), il ne compte ni pour ni contre.
        Le seuil est 2/3 (ou 2/N si un agent est désactivé).
        """
        conditions = 0

        if inputs.gaze_looking_at_screen is True:
            conditions += 1

        if inputs.head_looking_at_screen is True:
            conditions += 1

        if inputs.approaching is True:
            conditions += 1

        return conditions

    # ----- Timer THREAT (4 secondes) -----

    def _handle_threat_timer(self, result: DecisionResult, now: float) -> DecisionResult:
        """
        Gère le timer de confirmation THREAT.
        Le lock ne se produit qu'après 4 secondes continues de THREAT.
        """
        if self._threat_start is None:
            # Démarrer le timer
            self._threat_start = now
            logger.info("THREAT détecté — timer de %.0f s démarré", self._threat_delay)

        elapsed = now - self._threat_start
        remaining = max(0.0, self._threat_delay - elapsed)

        result.threat_timer_active = True
        result.threat_timer_remaining_s = round(remaining, 1)

        if elapsed >= self._threat_delay:
            result.action = Action.LOCK
            result.reason = (
                f"THREAT confirmé après {self._threat_delay:.0f} s "
                f"({result.conditions_met}/3 conditions)"
            )
            logger.warning("THREAT confirmé → VERROUILLAGE")
        else:
            result.action = Action.NOTHING
            result.reason = (
                f"THREAT en cours ({elapsed:.1f}/{self._threat_delay:.0f} s, "
                f"{result.conditions_met}/3 conditions)"
            )

        return result

    # ----- Timer IDLE/PASSING (mode SECURE, 10 secondes cumulatif) -----

    def _handle_idle_passing_timer(
        self, result: DecisionResult, now: float
    ) -> DecisionResult:
        """
        Gère le timer cumulatif IDLE/PASSING en mode SECURE.
        Le timer ne se réinitialise PAS si la personne alterne entre IDLE et PASSING.
        En mode PÉDAGO, ce timer n'est pas actif.
        """
        if self._mode != SecurityMode.SECURE:
            return result

        # Accumuler le temps
        if self._idle_last_tick is not None:
            delta = now - self._idle_last_tick
            self._idle_accumulated += delta
        self._idle_last_tick = now

        remaining = max(0.0, self._idle_delay - self._idle_accumulated)
        result.idle_timer_active = True
        result.idle_timer_remaining_s = round(remaining, 1)

        if self._idle_accumulated >= self._idle_delay:
            result.action = Action.LOCK
            result.reason = (
                f"{result.situation.value} en mode SECURE — "
                f"lock après {self._idle_delay:.0f} s cumulatif"
            )
            logger.warning("IDLE/PASSING cumulatif → VERROUILLAGE (mode SECURE)")

        return result

    def _stop_idle_timer(self) -> None:
        """Arrête le timer IDLE (un THREAT a été détecté, on bascule sur ce timer)."""
        self._idle_last_tick = None

    # ----- Réinitialisation des timers -----

    def _reset_timers(self) -> None:
        """Réinitialise tous les timers (owner reconnu ou cooldown)."""
        self._threat_start = None
        self._idle_accumulated = 0.0
        self._idle_last_tick = None

    def reset(self) -> None:
        """Réinitialisation complète (retour en phase VEILLE)."""
        self._reset_timers()
        self._last_result = None
        logger.debug("Decision agent réinitialisé")

    # ----- Émission du résultat -----

    def _emit(self, result: DecisionResult) -> None:
        """Sauvegarde et notifie le résultat."""
        self._last_result = result

        logger.debug(
            "Décision : %s → %s (%s)",
            result.situation.value, result.action.value, result.reason,
        )

        for cb in self._decision_callbacks:
            try:
                cb(result)
            except Exception as exc:
                logger.error("Erreur callback décision : %s", exc)

    # ----- Action système : verrouiller le PC -----

    @staticmethod
    def lock_workstation() -> bool:
        """
        Verrouille le poste Windows via l'API Win32 LockWorkStation.
        Retourne True si le verrouillage a réussi.
        """
        try:
            result = ctypes.windll.user32.LockWorkStation()
            if result:
                logger.info("Poste verrouillé avec succès")
            else:
                logger.error("Échec du verrouillage (LockWorkStation retourne 0)")
            return bool(result)
        except Exception as exc:
            logger.error("Impossible de verrouiller le poste : %s", exc)
            return False


# ---------------------------------------------------------------------------
# Exécution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    print("Decision Agent — test avec des scénarios simulés")
    print("=" * 60)

    def _on_decision(result: DecisionResult) -> None:
        print(
            f"  [{result.situation.value:17s}] -> {result.action.value:7s} "
            f"| {result.reason}"
        )

    agent = DecisionAgent(mode=SecurityMode.PEDAGO)
    agent.on_decision(_on_decision)

    # Scénario 1 : Owner seul
    print("\n--- Scénario 1 : Owner seul ---")
    agent.evaluate(AgentInputs(
        owner_detected=True,
        any_face_detected=True,
        face_is_large_enough=True,
    ))

    # Scénario 2 : Stranger fixe l'écran (THREAT)
    print("\n--- Scénario 2 : Stranger fixe l'écran ---")
    agent.evaluate(AgentInputs(
        stranger_detected=True,
        any_face_detected=True,
        face_is_large_enough=True,
        gaze_looking_at_screen=True,
        head_looking_at_screen=True,
        approaching=False,
    ))

    # Scénario 3 : Shoulder surfer
    print("\n--- Scénario 3 : Shoulder surfer ---")
    agent.evaluate(AgentInputs(
        owner_detected=True,
        stranger_detected=True,
        owner_and_stranger=True,
        any_face_detected=True,
        face_is_large_enough=True,
    ))

    # Scénario 4 : IDLE en mode PÉDAGO
    print("\n--- Scénario 4 : IDLE en mode PÉDAGO ---")
    agent.evaluate(AgentInputs(
        any_face_detected=False,
    ))

    # Scénario 5 : IDLE en mode SECURE
    print("\n--- Scénario 5 : IDLE en mode SECURE ---")
    agent_secure = DecisionAgent(mode=SecurityMode.SECURE)
    agent_secure.on_decision(_on_decision)
    agent_secure.evaluate(AgentInputs(
        any_face_detected=False,
    ))

    # Scénario 6 : Device alert
    print("\n--- Scénario 6 : Device alert ---")
    agent.evaluate(AgentInputs(
        device_alert=True,
    ))

    # Scénario 7 : THREAT mais owner visible aussi
    print("\n--- Scénario 7 : THREAT annulé par owner ---")
    agent.evaluate(AgentInputs(
        owner_detected=True,
        stranger_detected=True,
        owner_and_stranger=True,
        any_face_detected=True,
        face_is_large_enough=True,
        gaze_looking_at_screen=True,
        head_looking_at_screen=True,
        approaching=True,
    ))

    print(f"\n{'=' * 60}")
    print("Test terminé.")
