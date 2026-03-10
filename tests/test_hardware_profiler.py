# -*- coding: utf-8 -*-
"""Tests unitaires — Hardware Profiler."""

import json
import pytest
from pathlib import Path

from src.core.hardware_profiler import (
    PerformanceProfile,
    HardwareInfo,
    BenchmarkResult,
    ProfileResult,
    _determine_profile,
    _apply_profile_params,
    save_profile,
    load_profile,
    run_profiler,
)


class TestDetermineProfile:
    """Tests de l'attribution du profil selon le materiel."""

    def test_performance_profile(self):
        """Machine puissante -> PERFORMANCE."""
        hw = HardwareInfo(cpu_cores_physical=8, ram_total_gb=16.0, gpu_backend="DmlExecutionProvider")
        bench = BenchmarkResult(cpu_inference_ms=40.0, gpu_inference_ms=20.0, benchmark_ran=True)
        assert _determine_profile(hw, bench) == PerformanceProfile.PERFORMANCE

    def test_lite_profile_low_ram(self):
        """RAM insuffisante -> LITE."""
        hw = HardwareInfo(cpu_cores_physical=4, ram_total_gb=6.0)
        bench = BenchmarkResult(benchmark_ran=False)
        assert _determine_profile(hw, bench) == PerformanceProfile.LITE

    def test_lite_profile_low_cores(self):
        """Peu de coeurs -> LITE."""
        hw = HardwareInfo(cpu_cores_physical=2, ram_total_gb=8.0)
        bench = BenchmarkResult(benchmark_ran=False)
        assert _determine_profile(hw, bench) == PerformanceProfile.LITE

    def test_lite_profile_slow_bench(self):
        """Benchmark lent -> LITE."""
        hw = HardwareInfo(cpu_cores_physical=4, ram_total_gb=8.0)
        bench = BenchmarkResult(cpu_inference_ms=200.0, gpu_inference_ms=999.0, benchmark_ran=True)
        assert _determine_profile(hw, bench) == PerformanceProfile.LITE

    def test_balanced_profile(self):
        """Machine moyenne -> BALANCED."""
        hw = HardwareInfo(cpu_cores_physical=6, ram_total_gb=12.0)
        bench = BenchmarkResult(cpu_inference_ms=80.0, gpu_inference_ms=999.0, benchmark_ran=True)
        assert _determine_profile(hw, bench) == PerformanceProfile.BALANCED

    def test_balanced_default_no_bench(self):
        """Sans benchmark, 4 coeurs 8GB -> BALANCED."""
        hw = HardwareInfo(cpu_cores_physical=4, ram_total_gb=8.0)
        bench = BenchmarkResult(benchmark_ran=False)
        # bench_ms fallback = _BENCH_SLOW_MS = 150, seuil = 150, pas > donc pas LITE
        # pas >= 8 cores, donc pas PERFORMANCE
        assert _determine_profile(hw, bench) == PerformanceProfile.BALANCED


class TestApplyProfileParams:
    """Tests des parametres derives du profil."""

    def test_performance_params(self):
        result = ProfileResult(profile="PERFORMANCE")
        _apply_profile_params(result)
        assert result.frame_skip == 3
        assert result.analysis_width == 480
        assert result.analysis_height == 360
        assert result.gaze_enabled is True

    def test_balanced_params(self):
        result = ProfileResult(profile="BALANCED")
        _apply_profile_params(result)
        assert result.frame_skip == 5
        assert result.analysis_width == 320
        assert result.analysis_height == 240
        assert result.gaze_enabled is True

    def test_lite_params(self):
        result = ProfileResult(profile="LITE")
        _apply_profile_params(result)
        assert result.frame_skip == 10
        assert result.analysis_width == 160
        assert result.analysis_height == 120
        assert result.gaze_enabled is False


class TestSaveLoadProfile:
    """Tests de la sauvegarde/chargement du profil."""

    def test_save_and_load(self, tmp_config_dir: Path):
        result = ProfileResult(
            profile="BALANCED",
            timestamp="2026-03-10T12:00:00",
            frame_skip=5,
            analysis_width=320,
            analysis_height=240,
            gaze_enabled=True,
        )
        save_profile(result, tmp_config_dir)
        loaded = load_profile(tmp_config_dir)
        assert loaded is not None
        assert loaded.profile == "BALANCED"
        assert loaded.frame_skip == 5
        assert loaded.gaze_enabled is True

    def test_load_nonexistent(self, tmp_config_dir: Path):
        loaded = load_profile(tmp_config_dir)
        assert loaded is None

    def test_load_corrupted(self, tmp_config_dir: Path):
        path = tmp_config_dir / "hardware_profile.json"
        path.write_text("invalid json {{{", encoding="utf-8")
        loaded = load_profile(tmp_config_dir)
        assert loaded is None

    def test_run_profiler_caches(self, tmp_config_dir: Path):
        """Le profiler sauvegarde et ne re-benchmark pas."""
        result1 = run_profiler(config_dir=tmp_config_dir, force=True)
        result2 = run_profiler(config_dir=tmp_config_dir, force=False)
        assert result2.profile == result1.profile
