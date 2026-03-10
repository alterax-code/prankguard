# -*- coding: utf-8 -*-
"""Tests unitaires — Trajectory Agent."""

import pytest
from src.agents.trajectory_agent import TrajectoryAgent, Trajectory, TrajectoryResult


class TestTrajectoryAgent:
    """Tests du calcul de trajectoire."""

    def test_approaching(self):
        """Bbox grandissante -> APPROACHING."""
        agent = TrajectoryAgent()
        bboxes = [
            (100, 100, 150, 160),  # 3000
            (95, 95, 155, 165),    # 4200
            (90, 90, 160, 170),    # 5600
            (85, 85, 165, 175),    # 7200
            (80, 80, 170, 180),    # 9000
        ]
        for bbox in bboxes:
            result = agent.update(bbox)
        assert result.trajectory == Trajectory.APPROACHING
        assert result.approaching is True
        assert result.bbox_area_delta_pct > 0

    def test_receding(self):
        """Bbox retrecissante -> RECEDING."""
        agent = TrajectoryAgent()
        bboxes = [
            (80, 80, 170, 180),
            (85, 85, 165, 175),
            (90, 90, 160, 170),
            (95, 95, 155, 165),
            (100, 100, 150, 160),
        ]
        for bbox in bboxes:
            result = agent.update(bbox)
        assert result.trajectory == Trajectory.RECEDING
        assert result.approaching is False

    def test_stable(self):
        """Bbox constante -> STABLE."""
        agent = TrajectoryAgent()
        bbox = (100, 100, 200, 200)
        for _ in range(5):
            result = agent.update(bbox)
        assert result.trajectory == Trajectory.STABLE

    def test_unknown_insufficient_data(self):
        """Moins de 3 frames -> UNKNOWN."""
        agent = TrajectoryAgent()
        result = agent.update((0, 0, 100, 100))
        assert result.trajectory == Trajectory.UNKNOWN
        result = agent.update((0, 0, 110, 110))
        assert result.trajectory == Trajectory.UNKNOWN

    def test_reset(self):
        """Reset vide l'historique."""
        agent = TrajectoryAgent()
        for _ in range(5):
            agent.update((0, 0, 100, 100))
        assert agent.history_size == 5
        agent.reset()
        assert agent.history_size == 0

    def test_update_from_faces_empty(self):
        """Liste vide -> None."""
        agent = TrajectoryAgent()
        result = agent.update_from_faces([])
        assert result is None

    def test_update_from_faces_picks_largest(self):
        """Plusieurs bboxes -> analyse la plus grande."""
        agent = TrajectoryAgent()
        faces = [
            (0, 0, 50, 50),    # 2500
            (0, 0, 200, 200),  # 40000
            (0, 0, 100, 100),  # 10000
        ]
        result = agent.update_from_faces(faces)
        assert result is not None
        assert result.bbox_area_current == 40000.0
