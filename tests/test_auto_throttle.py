# -*- coding: utf-8 -*-
"""Tests unitaires — Auto-Throttle."""

import pytest
from src.agents.auto_throttle import AutoThrottle, ThrottleLevel


class TestThrottleLevels:
    """Tests de la determination du niveau de throttle."""

    def test_normal_below_60(self):
        assert AutoThrottle._determine_level(0.0) == ThrottleLevel.NORMAL
        assert AutoThrottle._determine_level(30.0) == ThrottleLevel.NORMAL
        assert AutoThrottle._determine_level(59.9) == ThrottleLevel.NORMAL

    def test_reduced_60_to_75(self):
        assert AutoThrottle._determine_level(60.0) == ThrottleLevel.REDUCED
        assert AutoThrottle._determine_level(70.0) == ThrottleLevel.REDUCED
        assert AutoThrottle._determine_level(74.9) == ThrottleLevel.REDUCED

    def test_lite_75_to_85(self):
        assert AutoThrottle._determine_level(75.0) == ThrottleLevel.LITE
        assert AutoThrottle._determine_level(80.0) == ThrottleLevel.LITE
        assert AutoThrottle._determine_level(84.9) == ThrottleLevel.LITE

    def test_minimal_above_85(self):
        assert AutoThrottle._determine_level(85.0) == ThrottleLevel.MINIMAL
        assert AutoThrottle._determine_level(95.0) == ThrottleLevel.MINIMAL
        assert AutoThrottle._determine_level(100.0) == ThrottleLevel.MINIMAL


class TestEffectiveParams:
    """Tests de la fusion profil + throttle."""

    def test_normal_uses_base(self):
        throttle = AutoThrottle()
        # Niveau NORMAL par defaut
        params = throttle.get_effective_params(
            base_frame_skip=5, base_gaze_enabled=True,
            base_width=320, base_height=240,
        )
        assert params["frame_skip"] == 5
        assert params["gaze_enabled"] is True
        assert params["analysis_width"] == 320

    def test_throttle_never_increases(self):
        """Le throttle ne fait que reduire, jamais augmenter."""
        throttle = AutoThrottle()
        # Meme en NORMAL, frame_skip ne descend pas en dessous du base
        params = throttle.get_effective_params(
            base_frame_skip=10, base_gaze_enabled=False,
            base_width=160, base_height=120,
        )
        assert params["frame_skip"] == 10  # Pas reduit a 5
        assert params["gaze_enabled"] is False  # Pas reactive
