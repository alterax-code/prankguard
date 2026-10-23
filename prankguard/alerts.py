"""Alarme sonore : sirène synthétisée (aucun fichier externe), volume réglable, non bloquante."""
from __future__ import annotations

import io
import threading
import wave
import winsound

import numpy as np

from . import paths

_RATE = 22050


def _siren(volume: int, seconds: float = 1.0) -> bytes:
    t = np.arange(int(_RATE * seconds)) / _RATE
    freq = 900 + 500 * (0.5 + 0.5 * np.sin(2 * np.pi * 2.0 * t))      # balayage 900-1400 Hz
    phase = 2 * np.pi * np.cumsum(freq) / _RATE
    sig = np.sign(np.sin(phase)) * 0.35 + np.sin(phase) * 0.65       # timbre un peu "buzzer"
    amp = (max(0, min(100, volume)) / 100.0) ** 1.5 * 0.9
    pcm = (sig * amp * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class Alarm:
    def __init__(self):
        self._file = paths.HOME / "siren.wav"
        self._volume = -1
        self._timer: threading.Timer | None = None

    def play(self, volume: int, duration: float = 5.0) -> None:
        if volume != self._volume or not self._file.exists():
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_bytes(_siren(volume))
            self._volume = volume
        winsound.PlaySound(str(self._file), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(duration, self.stop)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        winsound.PlaySound(None, winsound.SND_PURGE)


def chime() -> None:
    """Petit bip discret (avertissement)."""
    threading.Thread(target=lambda: winsound.Beep(1320, 120), daemon=True).start()
