# -*- coding: utf-8 -*-
"""Tests unitaires — Decision Agent."""

import time
import pytest
from src.agents.decision_agent import (
    DecisionAgent,
    AgentInputs,
    DecisionResult,
    Action,
    Situation,
    SecurityMode,
)


class TestDecisionOwnerPriority:
    """Le face recognition est prioritaire et court-circuite tout."""

    def test_owner_alone_is_safe(self):
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(
            owner_detected=True,
            any_face_detected=True,
            face_is_large_enough=True,
        ))
        assert result.situation == Situation.SAFE
        assert result.action == Action.NOTHING

    def test_owner_cancels_threat(self):
        """Owner + stranger = shoulder surfer, PAS lock."""
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(
            owner_detected=True,
            stranger_detected=True,
            owner_and_stranger=True,
            any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True,
            head_looking_at_screen=True,
            approaching=True,
        ))
        assert result.situation == Situation.SHOULDER_SURFER
        assert result.action == Action.ALERT
        # Jamais LOCK quand owner est visible


class TestThreatDetection:
    """Detection THREAT : 2/3 conditions requises."""

    def test_2_of_3_conditions_is_threat(self):
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(
            stranger_detected=True,
            any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True,
            head_looking_at_screen=True,
            approaching=False,
        ))
        assert result.situation == Situation.THREAT
        assert result.conditions_met == 2
        # Timer demarre, pas encore lock
        assert result.action == Action.NOTHING
        assert result.threat_timer_active is True

    def test_1_of_3_conditions_is_passing(self):
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(
            stranger_detected=True,
            any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True,
            head_looking_at_screen=False,
            approaching=False,
        ))
        assert result.situation == Situation.PASSING
        assert result.conditions_met == 1
        assert result.action == Action.NOTHING

    def test_3_of_3_conditions_is_threat(self):
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(
            stranger_detected=True,
            any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True,
            head_looking_at_screen=True,
            approaching=True,
        ))
        assert result.situation == Situation.THREAT
        assert result.conditions_met == 3

    def test_no_score_addition(self):
        """Les scores ne sont JAMAIS additionnes. Conditions comptees independamment."""
        agent = DecisionAgent()
        # Chaque agent est True/False, pas un score numerique
        inputs = AgentInputs(
            stranger_detected=True,
            any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True,
            head_looking_at_screen=True,
            approaching=True,
        )
        result = agent.evaluate(inputs)
        # conditions_met = 3, pas un "score"
        assert result.conditions_met == 3
        assert result.situation == Situation.THREAT


class TestThreatTimer:
    """Timer THREAT de 4 secondes."""

    def test_threat_timer_starts(self):
        agent = DecisionAgent(threat_delay=4.0)
        result = agent.evaluate(AgentInputs(
            stranger_detected=True, any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True, head_looking_at_screen=True,
        ))
        assert result.threat_timer_active is True
        assert result.action == Action.NOTHING  # Pas encore 4s

    def test_threat_timer_locks_after_delay(self):
        agent = DecisionAgent(threat_delay=0.1)  # 100ms pour le test
        inputs = AgentInputs(
            stranger_detected=True, any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True, head_looking_at_screen=True,
        )
        agent.evaluate(inputs)
        time.sleep(0.15)
        result = agent.evaluate(inputs)
        assert result.action == Action.LOCK

    def test_threat_timer_resets_when_owner_returns(self):
        agent = DecisionAgent(threat_delay=4.0)
        # THREAT demarre
        agent.evaluate(AgentInputs(
            stranger_detected=True, any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True, head_looking_at_screen=True,
        ))
        # Owner revient
        result = agent.evaluate(AgentInputs(
            owner_detected=True, any_face_detected=True,
            face_is_large_enough=True,
        ))
        assert result.situation == Situation.SAFE
        # Re-THREAT : timer repart de zero
        result = agent.evaluate(AgentInputs(
            stranger_detected=True, any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True, head_looking_at_screen=True,
        ))
        assert result.threat_timer_active is True
        assert result.action == Action.NOTHING  # Timer a recommence


class TestIdleAndPassing:
    """IDLE et PASSING en mode PEDAGO vs SECURE."""

    def test_idle_pedago_nothing(self):
        agent = DecisionAgent(mode=SecurityMode.PEDAGO)
        result = agent.evaluate(AgentInputs(any_face_detected=False))
        assert result.situation == Situation.IDLE
        assert result.action == Action.NOTHING

    def test_idle_secure_timer(self):
        agent = DecisionAgent(mode=SecurityMode.SECURE, idle_delay=0.1)
        inputs = AgentInputs(any_face_detected=False)
        agent.evaluate(inputs)
        time.sleep(0.15)
        result = agent.evaluate(inputs)
        assert result.action == Action.LOCK

    def test_idle_passing_cumulative(self):
        """Timer IDLE/PASSING cumulatif — ne se reset PAS."""
        agent = DecisionAgent(mode=SecurityMode.SECURE, idle_delay=0.2)
        # IDLE 100ms
        agent.evaluate(AgentInputs(any_face_detected=False))
        time.sleep(0.1)
        # PASSING 100ms
        agent.evaluate(AgentInputs(
            any_face_detected=True, face_is_large_enough=False,
        ))
        time.sleep(0.12)
        # Doit lock : 100 + 120 = 220ms > 200ms
        result = agent.evaluate(AgentInputs(any_face_detected=False))
        assert result.action == Action.LOCK

    def test_small_face_is_passing(self):
        """Visage trop petit -> PASSING."""
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(
            any_face_detected=True,
            face_is_large_enough=False,
        ))
        assert result.situation == Situation.PASSING


class TestDeviceAlert:
    """Alerte peripherique -> lock immediat."""

    def test_device_alert_locks(self):
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(device_alert=True))
        assert result.situation == Situation.DEVICE_ALERT
        assert result.action == Action.LOCK

    def test_device_alert_overrides_owner(self):
        """Device alert a priorite maximale, meme si owner present."""
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(
            owner_detected=True,
            any_face_detected=True,
            device_alert=True,
        ))
        assert result.action == Action.LOCK


class TestCooldown:
    """Cooldown post-deverrouillage de 3 secondes."""

    def test_cooldown_prevents_lock(self):
        agent = DecisionAgent(cooldown_delay=0.2)
        agent.notify_unlock()
        # Pendant le cooldown, meme un THREAT ne lock pas
        result = agent.evaluate(AgentInputs(
            stranger_detected=True, any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True, head_looking_at_screen=True,
        ))
        assert result.situation == Situation.COOLDOWN
        assert result.action == Action.NOTHING

    def test_cooldown_expires(self):
        agent = DecisionAgent(cooldown_delay=0.05)
        agent.notify_unlock()
        time.sleep(0.06)
        result = agent.evaluate(AgentInputs(
            owner_detected=True, any_face_detected=True,
        ))
        assert result.situation == Situation.SAFE


class TestShoulderSurfer:
    """Owner + stranger = alerte sonore."""

    def test_shoulder_surfer_alerts(self):
        agent = DecisionAgent()
        result = agent.evaluate(AgentInputs(
            owner_detected=True,
            stranger_detected=True,
            owner_and_stranger=True,
            any_face_detected=True,
            face_is_large_enough=True,
        ))
        assert result.situation == Situation.SHOULDER_SURFER
        assert result.action == Action.ALERT

    def test_shoulder_surfer_never_locks(self):
        """Shoulder surfer = ALERT, jamais LOCK."""
        agent = DecisionAgent()
        for _ in range(20):
            result = agent.evaluate(AgentInputs(
                owner_detected=True,
                stranger_detected=True,
                owner_and_stranger=True,
                any_face_detected=True,
                face_is_large_enough=True,
            ))
        assert result.action == Action.ALERT  # Toujours ALERT, jamais LOCK
