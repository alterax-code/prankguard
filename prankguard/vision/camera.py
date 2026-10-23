"""Capture webcam threadée : on garde uniquement la dernière image (latence minimale),
reconnexion automatique, ouverture/fermeture à la demande (caméra libérée quand la session est verrouillée).
"""
from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")  # ouverture MSMF bien plus rapide
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from ..log import get  # noqa: E402

log = get("camera")


class Camera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 480, fps: int = 30):
        self.index, self.width, self.height, self.fps = index, width, height, fps
        self._cap: cv2.VideoCapture | None = None
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._ts = 0.0
        self._cond = threading.Condition()
        self._want_open = False
        self._reconfig = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.backend = ""
        self.measured_fps = 0.0
        self.last_error = ""
        self.opening = False       # ouverture en cours (peut durer > 20 s juste après un déverrouillage)

    # ── Contrôle ──
    def start(self) -> None:
        self._want_open = True
        if not self._thread or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="Camera", daemon=True)
            self._thread.start()

    def pause(self) -> None:
        """Libère la caméra (LED éteinte) sans arrêter le thread."""
        self._want_open = False

    def resume(self) -> None:
        self._want_open = True

    def stop(self) -> None:
        self._want_open = False
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(3)

    def reconfigure(self, index: int, width: int, height: int) -> None:
        if (index, width, height) != (self.index, self.width, self.height):
            self.index, self.width, self.height = index, width, height
            self._reconfig = True   # libération faite par le thread caméra, jamais pendant un read()

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def last_frame_age(self) -> float:
        return time.time() - self._ts if self._ts else float("inf")

    def latest(self) -> tuple[int, float, np.ndarray | None]:
        with self._cond:
            return self._seq, self._ts, self._frame

    def wait_newer(self, seq: int, timeout: float = 0.5) -> tuple[int, float, np.ndarray | None]:
        """Bloque jusqu'à ce qu'une image plus récente que `seq` arrive (ou timeout)."""
        with self._cond:
            if self._seq <= seq:
                self._cond.wait(timeout)
            return self._seq, self._ts, self._frame

    # ── Interne ──
    def _open(self) -> bool:
        self.opening = True
        try:
            return self._try_open()
        finally:
            self.opening = False

    def _try_open(self) -> bool:
        # MSMF (HW transforms off) : ~0,4 s d'ouverture contre ~1,3 s pour DSHOW, même cadence
        backends = [(cv2.CAP_MSMF, "MSMF"), (cv2.CAP_DSHOW, "DSHOW")]
        for api, name in backends:
            t0 = time.perf_counter()
            cap = cv2.VideoCapture(self.index, api)
            if not cap.isOpened():
                cap.release()
                continue
            if api == cv2.CAP_DSHOW:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                continue
            self._cap, self.backend, self.last_error = cap, name, ""
            log.info("Caméra %d ouverte via %s en %.0f ms (%dx%d)", self.index, name,
                     (time.perf_counter() - t0) * 1000, frame.shape[1], frame.shape[0])
            return True
        self.last_error = "Caméra introuvable ou utilisée par une autre application"
        return False

    def _release(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()
            log.info("Caméra libérée")

    def _loop(self) -> None:
        fails = 0
        count, t_win = 0, time.time()
        while not self._stop.is_set():
            if not self._want_open or self._reconfig:
                self._reconfig = False
                self._release()
                if not self._want_open:
                    time.sleep(0.1)
                continue
            if self._cap is None:
                if not self._open():
                    time.sleep(1.0)
                    continue
                fails = 0
            cap = self._cap
            if cap is None:
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                fails += 1
                if fails >= 15:
                    log.warning("Flux caméra interrompu — reconnexion")
                    self.last_error = "Flux caméra interrompu"
                    self._release()
                    time.sleep(0.5)
                else:
                    time.sleep(0.02)
                continue
            fails = 0
            now = time.time()
            with self._cond:
                self._frame, self._ts = frame, now
                self._seq += 1
                self._cond.notify_all()
            count += 1
            if now - t_win >= 1.0:
                self.measured_fps = count / (now - t_win)
                count, t_win = 0, now
        self._release()
