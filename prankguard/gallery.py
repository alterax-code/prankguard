"""Profils enrôlés : {nom: embeddings (N,128) float32} + miniature, chiffrés au repos par DPAPI
(lié au compte Windows — aucun mot de passe à retenir, inutilisable sur une autre machine/compte).

Chaque profil a deux jeux d'embeddings : « emb » (enrôlement, jamais modifié automatiquement) et
« adapt » (appris en continu, borné, remplacé en FIFO) qui suit les variations du quotidien.
"""
from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

import numpy as np

from . import paths, winsys
from .log import get

log = get("gallery")
_MAGIC = b"PGv3"
MAX_ADAPTIVE = 40          # échantillons appris par profil (au-delà, le plus redondant est remplacé)


class Gallery:
    def __init__(self, path: Path = paths.GALLERY_FILE, encrypt: bool = True):
        self.path = path
        self.encrypt = encrypt
        self._lock = threading.RLock()
        self.people: dict[str, dict] = {}   # nom -> {"emb", "adapt": ndarray, "thumb": bytes(jpeg), "created"}
        self.load()

    # ── Persistance ──
    def load(self) -> None:
        with self._lock:
            self.people = {}
            if not self.path.exists():
                return
            try:
                raw = self.path.read_bytes()
                if raw.startswith(_MAGIC):
                    raw = raw[len(_MAGIC):]
                    raw = winsys.dpapi_unprotect(raw) if self.encrypt else raw
                with np.load(io.BytesIO(raw), allow_pickle=False) as z:
                    meta = json.loads(bytes(z["__meta__"]).decode("utf-8"))
                    for i, m in enumerate(meta):
                        emb = z[f"e{i}"].astype(np.float32)
                        adapt = z[f"a{i}"].astype(np.float32) if f"a{i}" in z.files else emb[:0]
                        self.people[m["name"]] = {
                            "emb": emb,
                            "adapt": adapt,
                            "model": m.get("model", "sface"),
                            "thumb": bytes(z[f"t{i}"]),
                            "created": m.get("created", 0),
                        }
            except Exception:
                log.exception("Lecture de la galerie impossible — profils ignorés")
                self.people = {}

    def save(self) -> None:
        with self._lock:
            meta, arrays = [], {}
            for i, (name, p) in enumerate(self.people.items()):
                meta.append({"name": name, "created": p["created"], "model": p["model"]})
                arrays[f"e{i}"] = p["emb"]
                arrays[f"a{i}"] = p["adapt"]
                arrays[f"t{i}"] = np.frombuffer(p["thumb"], dtype=np.uint8)
            arrays["__meta__"] = np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8)
            buf = io.BytesIO()
            np.savez_compressed(buf, **arrays)
            raw = buf.getvalue()
            raw = winsys.dpapi_protect(raw) if self.encrypt else raw
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(_MAGIC + raw)
            tmp.replace(self.path)

    # ── API ──
    def embeddings(self, model: str | None = None) -> dict[str, np.ndarray]:
        """Enrôlement + appris : ce que la reconnaissance compare (filtré par modèle si demandé)."""
        with self._lock:
            return {n: np.vstack([p["emb"], p["adapt"]]) for n, p in self.people.items()
                    if model is None or p["model"] == model}

    def enrolled(self, model: str | None = None) -> dict[str, np.ndarray]:
        """Enrôlement seul : ancre qui empêche l'apprentissage continu de dériver."""
        with self._lock:
            return {n: p["emb"] for n, p in self.people.items() if model is None or p["model"] == model}

    def by_model(self, enrolled_only: bool = False) -> dict[str, dict[str, np.ndarray]]:
        with self._lock:
            models = {p["model"] for p in self.people.values()}
            get = self.enrolled if enrolled_only else self.embeddings
            return {m: get(m) for m in models}

    def model_of(self, name: str) -> str | None:
        with self._lock:
            p = self.people.get(name)
            return p["model"] if p else None

    def upsert(self, name: str, emb: np.ndarray, thumb: bytes, model: str = "sface") -> None:
        emb = np.asarray(emb, np.float32)
        with self._lock:
            self.people[name] = {"emb": emb, "adapt": emb[:0], "thumb": thumb, "created": time.time(),
                                 "model": model}
            self.save()

    def add_adaptive(self, name: str, emb: np.ndarray) -> bool:
        with self._lock:
            p = self.people.get(name)
            if p is None:
                return False
            adapt = np.vstack([p["adapt"], np.asarray(emb, np.float32)[None]])
            if len(adapt) > MAX_ADAPTIVE:
                # garder la diversité (jour / soir, lunettes…) : on retire la vue la plus redondante,
                # jamais la nouvelle
                sims = adapt[:-1] @ adapt.T
                np.fill_diagonal(sims[:, :-1], -1.0)
                adapt = np.delete(adapt, int(sims.max(axis=1).argmax()), axis=0)
            p["adapt"] = adapt
            self.save()
            return True

    def forget_adaptive(self, name: str) -> bool:
        with self._lock:
            p = self.people.get(name)
            if p is None:
                return False
            p["adapt"] = p["emb"][:0]
            self.save()
            return True

    def remove(self, name: str) -> bool:
        with self._lock:
            if self.people.pop(name, None) is None:
                return False
            self.save()
            return True

    def rename(self, old: str, new: str) -> bool:
        with self._lock:
            if old not in self.people or new in self.people:
                return False
            self.people = {(new if k == old else k): v for k, v in self.people.items()}
            self.save()
            return True

    def summary(self) -> list[dict]:
        with self._lock:
            return [{"name": n, "samples": int(len(p["emb"])), "learned": int(len(p["adapt"])), "created": p["created"],
                     "model": p["model"]}
                    for n, p in self.people.items()]

    def thumb(self, name: str) -> bytes | None:
        with self._lock:
            p = self.people.get(name)
            return p["thumb"] if p else None
