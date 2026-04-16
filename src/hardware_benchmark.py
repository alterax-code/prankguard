"""
Benchmark hardware au premier lancement de PrankGuard (Vague 3).
Mesure la vitesse face_encodings() pour auto-tuner analyze_every_n_frames.
Bloque ~3s max au premier lancement uniquement.
"""
import time
import numpy as np


def run_benchmark(config) -> None:
    """
    Mesure le temps moyen de face_encodings() sur ce CPU.
    Met à jour config.analyze_every_n_frames selon le résultat.
    No-op si config.hardware_benchmarked == True.
    """
    if config.hardware_benchmarked:
        return

    print("[PrankGuard] Premier lancement — calibration hardware...")

    # Tenter d'obtenir un frame réel depuis la webcam
    frame_rgb = None
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        cap.release()
    except Exception:
        pass

    # Fallback : frame aléatoire 640×480
    if frame_rgb is None:
        frame_rgb = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

    # Mesure : 5 appels face_encodings (liste vide = encode sans locs connues)
    import face_recognition
    N = 5
    try:
        start = time.perf_counter()
        for _ in range(N):
            face_recognition.face_encodings(frame_rgb, num_jitters=1, model="large")
        elapsed_ms = (time.perf_counter() - start) / N * 1000
    except Exception:
        elapsed_ms = 200.0  # défaut prudent

    # Calibration → analyze_every_n
    if elapsed_ms < 80:
        every_n = 2       # rapide (>12 fps analyse)
    elif elapsed_ms < 150:
        every_n = 3       # moyen  (~8 fps analyse)
    elif elapsed_ms < 300:
        every_n = 5       # lent   (~5 fps analyse)
    else:
        every_n = 8       # très lent

    print(
        f"[PrankGuard] Benchmark: {elapsed_ms:.0f}ms/frame "
        f"→ analyze_every_n={every_n}"
    )

    config.update(
        hardware_benchmarked=True,
        analyze_every_n_frames=every_n,
    )
