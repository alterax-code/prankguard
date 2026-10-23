"""Localisation / téléchargement vérifié (SHA-256) des modèles ONNX.

YuNet + SFace : OpenCV Zoo (MIT / Apache-2.0). MiniFASNet : Silent-Face-Anti-Spoofing, conversion
ONNX de yakhyo/face-anti-spoofing (Apache-2.0). EdgeFace-XS (Idiap, CC BY-NC-SA 4.0 — usage non
commercial) : jamais embarqué dans l'installeur, téléchargé au premier lancement et vérifié.
"""
from __future__ import annotations

import hashlib
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..log import get

log = get("models")

_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
_FAS = "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/"


@dataclass(frozen=True)
class Model:
    filename: str
    url: str
    sha256: str


YUNET = Model("face_detection_yunet_2023mar.onnx", _ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
              "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4")
SFACE = Model("face_recognition_sface_2021dec.onnx",
              _ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
              "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79")
FAS_V2 = Model("MiniFASNetV2.onnx", _FAS + "MiniFASNetV2.onnx",
               "b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907")
FAS_V1SE = Model("MiniFASNetV1SE.onnx", _FAS + "MiniFASNetV1SE.onnx",
                 "ebab7f90c7833fbccd46d3a555410e78d969db5438e169b6524be444862b3676")
# Reconnaissance plus robuste (faible lumière, bruit) et plus rapide que SFace — mesuré sur LFW
# dégradé : TAR@FAR1% 0,95 contre 0,64 pour SFace dans le noir ; 4 ms contre 11 ms par visage.
EDGEFACE_XS = Model("edgeface_xs_gamma_06.onnx",
                    "https://huggingface.co/yakhyo/uniface-weights/resolve/main/edgeface_xs_gamma_06.onnx",
                    "9206e2eb13a2761d7b5b76e13016d4b9acd3fa3535a9a09939f3adacd139a5ff")
ALL = (YUNET, SFACE, FAS_V2, FAS_V1SE)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_verified: dict[Path, bool] = {}


def _valid(p: Path, m: Model) -> bool:
    if p in _verified:
        return _verified[p]
    ok = p.is_file() and sha256(p) == m.sha256
    _verified[p] = ok
    return ok


def resolve(m: Model, allow_download: bool = True) -> Path:
    for d in (paths.BUNDLED_MODELS_DIR, paths.MODELS_DIR):
        p = d / m.filename
        if _valid(p, m):
            return p
    if not allow_download:
        raise FileNotFoundError(m.filename)
    paths.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dst = paths.MODELS_DIR / m.filename
    tmp = dst.with_suffix(".part")
    log.info("Téléchargement du modèle %s…", m.filename)
    with urllib.request.urlopen(m.url, timeout=60) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if sha256(tmp) != m.sha256:
        tmp.unlink(missing_ok=True)
        raise IOError(f"Empreinte SHA-256 invalide pour {m.filename}")
    tmp.replace(dst)
    _verified.pop(dst, None)
    log.info("Modèle %s prêt", m.filename)
    return dst
