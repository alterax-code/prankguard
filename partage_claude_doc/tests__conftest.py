# -*- coding: utf-8 -*-
"""
Fixtures partagees pour les tests PrankGuard.
"""

import numpy as np
import pytest
from pathlib import Path
import tempfile


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Repertoire de configuration temporaire pour les tests."""
    config = tmp_path / "PrankGuard"
    config.mkdir()
    return config


@pytest.fixture
def fake_frame() -> np.ndarray:
    """Frame factice 480x640 BGR."""
    return np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)


@pytest.fixture
def small_frame() -> np.ndarray:
    """Frame factice 240x320 BGR."""
    return np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)


@pytest.fixture
def fake_encodings() -> list[np.ndarray]:
    """Liste de 5 encodings factices 512D normalises."""
    encs = []
    for _ in range(5):
        v = np.random.randn(512).astype(np.float32)
        v /= np.linalg.norm(v)  # Normaliser (comme InsightFace)
        encs.append(v)
    return encs
