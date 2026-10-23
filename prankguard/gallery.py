"""Profils enrôlés : {nom: embeddings (N,128) float32} + miniature, chiffrés au repos par DPAPI
(lié au compte Windows — aucun mot de passe à retenir, inutilisable sur une autre machine/compte)."""
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


class Gallery:
    def __init__(self, path: Path = paths.GALLERY_FILE, encrypt: bool = True):
        self.path = path
        self.encrypt = encrypt
        self._lock = threading.RLock()
        self.people: dict[str, dict] = {}   # nom -> {"emb": ndarray, "thumb": bytes(jpeg), "created": ts}
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
                        self.people[m["name"]] = {
                            "emb": z[f"e{i}"].astype(np.float32),
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
                meta.append({"name": name, "created": p["created"]})
                arrays[f"e{i}"] = p["emb"]
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
    def embeddings(self) -> dict[str, np.ndarray]:
        with self._lock:
            return {n: p["emb"] for n, p in self.people.items()}

    def upsert(self, name: str, emb: np.ndarray, thumb: bytes) -> None:
        with self._lock:
            self.people[name] = {"emb": np.asarray(emb, np.float32), "thumb": thumb, "created": time.time()}
            self.save()

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
            return [{"name": n, "samples": int(len(p["emb"])), "created": p["created"]}
                    for n, p in self.people.items()]

    def thumb(self, name: str) -> bytes | None:
        with self._lock:
            p = self.people.get(name)
            return p["thumb"] if p else None
