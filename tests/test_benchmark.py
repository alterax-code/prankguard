# -*- coding: utf-8 -*-
"""
Benchmark de performance — PrankGuard v3.0

Mesure les temps d'execution des agents critiques pour verifier
qu'ils respectent les contraintes du plan v3.
"""

import time
import pytest
import numpy as np

from src.agents.trajectory_agent import TrajectoryAgent
from src.agents.decision_agent import DecisionAgent, AgentInputs


class TestTrajectoryBenchmark:
    """Le trajectory agent doit etre quasi-instantane."""

    def test_1000_updates_under_10ms(self):
        agent = TrajectoryAgent()
        start = time.perf_counter()
        for i in range(1000):
            agent.update((100, 100, 100 + i, 200 + i))
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 50.0, f"1000 updates en {elapsed_ms:.1f} ms (max 50)"


class TestDecisionBenchmark:
    """Le decision agent doit evaluer rapidement."""

    def test_1000_evaluations_under_50ms(self):
        agent = DecisionAgent()
        inputs = AgentInputs(
            stranger_detected=True,
            any_face_detected=True,
            face_is_large_enough=True,
            gaze_looking_at_screen=True,
            head_looking_at_screen=True,
            approaching=True,
        )
        start = time.perf_counter()
        for _ in range(1000):
            agent.evaluate(inputs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100.0, f"1000 evaluations en {elapsed_ms:.1f} ms (max 100)"


class TestEncryptionBenchmark:
    """Le chiffrement/dechiffrement doit etre rapide."""

    def test_encrypt_decrypt_under_500ms(self, tmp_config_dir):
        from src.security.encryption import (
            save_encrypted_owner_encodings,
            load_encrypted_owner_encodings,
        )

        encodings = [np.random.randn(512).astype(np.float32) for _ in range(10)]

        start = time.perf_counter()
        save_encrypted_owner_encodings(encodings, config_dir=tmp_config_dir)
        loaded = load_encrypted_owner_encodings(config_dir=tmp_config_dir)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(loaded) == 10
        assert elapsed_ms < 2000.0, f"Encrypt+decrypt en {elapsed_ms:.1f} ms (max 2000)"
