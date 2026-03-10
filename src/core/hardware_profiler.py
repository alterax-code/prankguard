# -*- coding: utf-8 -*-
"""
Hardware Profiler — PrankGuard v3.0

Analyse le matériel au premier lancement et détermine le profil de performance
optimal (PERFORMANCE, BALANCED ou LITE). Le résultat est sauvegardé dans un
fichier JSON local pour ne pas refaire le benchmark aux lancements suivants.

Dépendances : psutil, py-cpuinfo, onnxruntime (+ onnxruntime-directml optionnel)
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np
import psutil

logger = logging.getLogger("prankguard.hardware_profiler")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

class PerformanceProfile(str, Enum):
    """Profils de performance adaptatifs définis dans le plan v3."""
    PERFORMANCE = "PERFORMANCE"  # i7+ / 16GB+ / GPU dédié
    BALANCED = "BALANCED"        # i5 / 8-16GB
    LITE = "LITE"                # i3 / 8GB minimum


# Répertoire de configuration par défaut (AppData sur Windows)
_DEFAULT_CONFIG_DIR = Path(
    os.environ.get("APPDATA", Path.home() / ".config")
) / "PrankGuard"

_PROFILE_FILENAME = "hardware_profile.json"

# Seuils pour l'attribution du profil
_RAM_HIGH = 16  # Go — seuil PERFORMANCE
_RAM_LOW = 8    # Go — seuil minimum (LITE)
_CORES_HIGH = 8
_CORES_LOW = 4

# Seuil du benchmark ONNX (en millisecondes pour une inférence)
_BENCH_FAST_MS = 50.0   # En dessous → la machine est rapide
_BENCH_SLOW_MS = 150.0  # Au dessus  → la machine est lente


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------

@dataclass
class HardwareInfo:
    """Informations matérielles collectées."""
    cpu_model: str = "unknown"
    cpu_cores_physical: int = 0
    cpu_cores_logical: int = 0
    cpu_freq_mhz: float = 0.0
    cpu_has_avx: bool = False
    cpu_has_sse42: bool = False
    ram_total_gb: float = 0.0
    ram_available_gb: float = 0.0
    gpu_name: Optional[str] = None
    gpu_backend: Optional[str] = None  # "DmlExecutionProvider" ou None
    os_version: str = ""
    python_version: str = ""


@dataclass
class BenchmarkResult:
    """Résultats du benchmark ONNX."""
    cpu_inference_ms: float = 0.0
    gpu_inference_ms: float = 0.0
    best_provider: str = "CPUExecutionProvider"
    benchmark_ran: bool = False


@dataclass
class ProfileResult:
    """Résultat final du profilage."""
    profile: str = PerformanceProfile.BALANCED.value
    hardware: Optional[dict] = None
    benchmark: Optional[dict] = None
    timestamp: str = ""

    # Paramètres dérivés du profil (utilisés par les autres modules)
    frame_skip: int = 5          # 1 frame sur N analysée
    analysis_width: int = 320    # Résolution d'analyse
    analysis_height: int = 240
    gaze_enabled: bool = True    # Gaze estimation actif ?


# ---------------------------------------------------------------------------
# Collecte d'informations matérielles
# ---------------------------------------------------------------------------

def _collect_hardware_info() -> HardwareInfo:
    """Collecte les informations CPU, RAM, GPU de la machine."""
    info = HardwareInfo()

    # --- CPU ---
    try:
        import cpuinfo
        cpu_data = cpuinfo.get_cpu_info()
        info.cpu_model = cpu_data.get("brand_raw", "unknown")
        flags = cpu_data.get("flags", [])
        info.cpu_has_avx = "avx" in flags or "avx2" in flags
        info.cpu_has_sse42 = "sse4_2" in flags
    except Exception as exc:
        logger.warning("py-cpuinfo indisponible, informations CPU limitées : %s", exc)
        info.cpu_model = platform.processor() or "unknown"

    info.cpu_cores_physical = psutil.cpu_count(logical=False) or 1
    info.cpu_cores_logical = psutil.cpu_count(logical=True) or 1

    freq = psutil.cpu_freq()
    if freq:
        info.cpu_freq_mhz = freq.max or freq.current or 0.0

    # --- RAM ---
    mem = psutil.virtual_memory()
    info.ram_total_gb = round(mem.total / (1024 ** 3), 1)
    info.ram_available_gb = round(mem.available / (1024 ** 3), 1)

    # --- GPU (DirectML / DmlExecutionProvider) ---
    try:
        import onnxruntime as ort
        available_providers = ort.get_available_providers()
        if "DmlExecutionProvider" in available_providers:
            info.gpu_backend = "DmlExecutionProvider"
            info.gpu_name = _detect_gpu_name()
        else:
            info.gpu_backend = None
            info.gpu_name = None
    except ImportError:
        logger.warning("onnxruntime non installé, détection GPU impossible")

    # --- OS ---
    info.os_version = f"{platform.system()} {platform.version()}"
    info.python_version = platform.python_version()

    return info


def _detect_gpu_name() -> Optional[str]:
    """Tente de détecter le nom du GPU via WMI (Windows uniquement)."""
    try:
        import wmi  # type: ignore[import-untyped]
        w = wmi.WMI()
        gpus = w.Win32_VideoController()
        if gpus:
            return gpus[0].Name
    except Exception:
        pass

    # Fallback : variable d'environnement ou inconnu
    return os.environ.get("GPU_NAME", None)


# ---------------------------------------------------------------------------
# Benchmark ONNX
# ---------------------------------------------------------------------------

def _run_onnx_benchmark(hardware: HardwareInfo) -> BenchmarkResult:
    """
    Exécute une inférence factice sur un petit modèle ONNX pour mesurer
    la vitesse CPU vs GPU (DirectML). Utilise une matrice aléatoire
    si aucun modèle InsightFace n'est disponible.
    """
    result = BenchmarkResult()

    try:
        import onnxruntime as ort
    except ImportError:
        logger.warning("onnxruntime non disponible, benchmark ignoré")
        return result

    # Créer un modèle ONNX minimal pour le benchmark (matmul 512x512)
    model_bytes = _create_minimal_onnx_model()
    if model_bytes is None:
        logger.warning("Impossible de créer le modèle ONNX de benchmark")
        return result

    dummy_input = np.random.randn(1, 512).astype(np.float32)

    # --- Benchmark CPU ---
    result.cpu_inference_ms = _benchmark_provider(
        model_bytes, dummy_input, ["CPUExecutionProvider"]
    )

    # --- Benchmark GPU (DirectML) ---
    if hardware.gpu_backend == "DmlExecutionProvider":
        result.gpu_inference_ms = _benchmark_provider(
            model_bytes, dummy_input, ["DmlExecutionProvider", "CPUExecutionProvider"]
        )
    else:
        result.gpu_inference_ms = float("inf")

    # Choisir le meilleur provider
    if result.gpu_inference_ms < result.cpu_inference_ms:
        result.best_provider = "DmlExecutionProvider"
    else:
        result.best_provider = "CPUExecutionProvider"

    result.benchmark_ran = True
    logger.info(
        "Benchmark ONNX — CPU: %.1f ms | GPU: %.1f ms → %s",
        result.cpu_inference_ms,
        result.gpu_inference_ms,
        result.best_provider,
    )
    return result


def _create_minimal_onnx_model() -> Optional[bytes]:
    """Crée un modèle ONNX minimal (MatMul 512×512) pour le benchmark."""
    try:
        import onnx
        from onnx import helper, TensorProto

        # Entrée : vecteur 1×512
        X = helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, 512])
        Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [1, 512])

        # Poids constants : matrice 512×512
        W_init = numpy_helper_from_array(
            np.random.randn(512, 512).astype(np.float32), "W"
        )

        node = helper.make_node("MatMul", ["X", "W"], ["Y"])
        graph = helper.make_graph([node], "benchmark", [X], [Y], [W_init])
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
        return model.SerializeToString()
    except ImportError:
        # onnx pas installé — on utilise un fallback sans modèle
        return _create_benchmark_model_no_onnx()


def numpy_helper_from_array(arr: np.ndarray, name: str):
    """Convertit un ndarray en TensorProto ONNX."""
    from onnx import numpy_helper
    return numpy_helper.from_array(arr, name=name)


def _create_benchmark_model_no_onnx() -> Optional[bytes]:
    """
    Fallback : si le package 'onnx' n'est pas installé, on ne peut pas
    créer de modèle. Le benchmark sera basé uniquement sur les specs matérielles.
    """
    logger.info("Package 'onnx' non disponible, benchmark ONNX ignoré")
    return None


def _benchmark_provider(
    model_bytes: bytes, dummy_input: np.ndarray, providers: list[str],
    warmup: int = 3, iterations: int = 10
) -> float:
    """
    Exécute un benchmark sur un provider ONNX Runtime donné.
    Retourne le temps moyen d'inférence en millisecondes.
    """
    import onnxruntime as ort

    try:
        opts = ort.SessionOptions()
        opts.log_severity_level = 3  # Supprimer les logs verbeux
        session = ort.InferenceSession(model_bytes, opts, providers=providers)
    except Exception as exc:
        logger.warning("Provider %s indisponible : %s", providers, exc)
        return float("inf")

    input_name = session.get_inputs()[0].name

    # Warmup
    for _ in range(warmup):
        session.run(None, {input_name: dummy_input})

    # Mesure
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        session.run(None, {input_name: dummy_input})
        elapsed = (time.perf_counter() - start) * 1000.0
        times.append(elapsed)

    return round(sum(times) / len(times), 2)


# ---------------------------------------------------------------------------
# Attribution du profil
# ---------------------------------------------------------------------------

def _determine_profile(hardware: HardwareInfo, benchmark: BenchmarkResult) -> PerformanceProfile:
    """
    Détermine le profil de performance en fonction du matériel et du benchmark.

    Logique :
      - PERFORMANCE : ≥8 cœurs physiques ET ≥16 Go RAM ET (GPU ou bench rapide)
      - LITE         : <4 cœurs OU <8 Go RAM OU bench lent
      - BALANCED     : tout le reste
    """
    cores = hardware.cpu_cores_physical
    ram = hardware.ram_total_gb
    bench_ms = min(benchmark.cpu_inference_ms, benchmark.gpu_inference_ms) \
        if benchmark.benchmark_ran else _BENCH_SLOW_MS

    has_gpu = hardware.gpu_backend is not None

    # PERFORMANCE
    if cores >= _CORES_HIGH and ram >= _RAM_HIGH and (has_gpu or bench_ms < _BENCH_FAST_MS):
        return PerformanceProfile.PERFORMANCE

    # LITE
    if cores < _CORES_LOW or ram < _RAM_LOW or bench_ms > _BENCH_SLOW_MS:
        return PerformanceProfile.LITE

    # BALANCED (défaut)
    return PerformanceProfile.BALANCED


def _apply_profile_params(result: ProfileResult) -> None:
    """
    Renseigne les paramètres dérivés du profil (frame_skip, résolution, gaze).
    Valeurs issues de la section 5 du plan v3.
    """
    profile = PerformanceProfile(result.profile)

    if profile == PerformanceProfile.PERFORMANCE:
        result.frame_skip = 3
        result.analysis_width = 480
        result.analysis_height = 360
        result.gaze_enabled = True

    elif profile == PerformanceProfile.BALANCED:
        result.frame_skip = 5
        result.analysis_width = 320
        result.analysis_height = 240
        result.gaze_enabled = True

    elif profile == PerformanceProfile.LITE:
        result.frame_skip = 10
        result.analysis_width = 160
        result.analysis_height = 120
        result.gaze_enabled = False


# ---------------------------------------------------------------------------
# Sauvegarde / Chargement
# ---------------------------------------------------------------------------

def _get_profile_path(config_dir: Optional[Path] = None) -> Path:
    """Retourne le chemin du fichier de profil."""
    directory = config_dir or _DEFAULT_CONFIG_DIR
    return directory / _PROFILE_FILENAME


def save_profile(result: ProfileResult, config_dir: Optional[Path] = None) -> Path:
    """Sauvegarde le profil dans un fichier JSON."""
    path = _get_profile_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "profile": result.profile,
        "hardware": result.hardware,
        "benchmark": result.benchmark,
        "timestamp": result.timestamp,
        "frame_skip": result.frame_skip,
        "analysis_width": result.analysis_width,
        "analysis_height": result.analysis_height,
        "gaze_enabled": result.gaze_enabled,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Profil sauvegardé dans %s", path)
    return path


def load_profile(config_dir: Optional[Path] = None) -> Optional[ProfileResult]:
    """
    Charge le profil depuis le fichier JSON.
    Retourne None si le fichier n'existe pas.
    """
    path = _get_profile_path(config_dir)

    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        result = ProfileResult(
            profile=data["profile"],
            hardware=data.get("hardware"),
            benchmark=data.get("benchmark"),
            timestamp=data.get("timestamp", ""),
            frame_skip=data.get("frame_skip", 5),
            analysis_width=data.get("analysis_width", 320),
            analysis_height=data.get("analysis_height", 240),
            gaze_enabled=data.get("gaze_enabled", True),
        )
        logger.info("Profil chargé depuis %s → %s", path, result.profile)
        return result

    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("Fichier de profil corrompu, re-profilage nécessaire : %s", exc)
        return None


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def run_profiler(
    config_dir: Optional[Path] = None,
    force: bool = False
) -> ProfileResult:
    """
    Point d'entrée principal du Hardware Profiler.

    - Au premier lancement : collecte hardware, benchmark ONNX, attribue un profil.
    - Aux lancements suivants : charge le profil depuis le fichier JSON.
    - Si force=True : re-exécute le benchmark même si un profil existe déjà.

    Retourne un ProfileResult avec le profil et ses paramètres dérivés.
    """
    # Charger un profil existant (sauf si force)
    if not force:
        existing = load_profile(config_dir)
        if existing is not None:
            return existing

    logger.info("Démarrage du profilage matériel...")
    start = time.perf_counter()

    # 1. Collecte des informations matérielles
    hardware = _collect_hardware_info()
    logger.info(
        "CPU: %s (%d cœurs) | RAM: %.1f Go | GPU: %s",
        hardware.cpu_model,
        hardware.cpu_cores_physical,
        hardware.ram_total_gb,
        hardware.gpu_name or "aucun",
    )

    # 2. Benchmark ONNX
    benchmark = _run_onnx_benchmark(hardware)

    # 3. Détermination du profil
    profile = _determine_profile(hardware, benchmark)

    # 4. Construction du résultat
    result = ProfileResult(
        profile=profile.value,
        hardware=asdict(hardware),
        benchmark=asdict(benchmark),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    _apply_profile_params(result)

    elapsed = time.perf_counter() - start
    logger.info("Profil attribué : %s (en %.1f s)", profile.value, elapsed)

    # 5. Sauvegarde
    save_profile(result, config_dir)

    return result


# ---------------------------------------------------------------------------
# Exécution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )
    result = run_profiler(force=True)
    print(f"\n{'=' * 50}")
    print(f"  Profil attribué : {result.profile}")
    print(f"  Frame skip      : 1/{result.frame_skip}")
    print(f"  Résolution      : {result.analysis_width}×{result.analysis_height}")
    print(f"  Gaze estimation : {'actif' if result.gaze_enabled else 'désactivé'}")
    print(f"{'=' * 50}")
