"""Enrôlement guidé : capture automatique d'échantillons variés (face, gauche, droite, haut, bas)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .vision.faces import Face, FaceEngine, sharpness

# Poses du point de vue de l'utilisateur. La caméra n'est pas en miroir : quand l'utilisateur
# tourne la tête vers SA gauche, le nez se déplace vers la droite de l'image -> yaw > 0.
TARGETS = {"center": 6, "left": 3, "right": 3, "up": 3, "down": 3}
MIN_SAMPLES = 8
MIN_REL_SIZE = 0.14
MIN_SHARPNESS = 25.0
CAPTURE_INTERVAL = 0.22
CALIB_FRAMES = 8


# Seuils de pose relatifs à la pose naturelle. Le tangage (haut/bas) se lit sur la position
# verticale du nez entre yeux et bouche : il varie peu (~0,9 par radian), d'où un seuil bas
# (~9°) — avec 0,25 il fallait ~16° et l'utilisateur « galérait » sur haut/bas.
YAW_SIDE = 0.28
PITCH_UPDOWN = 0.14


def pose_bucket(yaw: float, pitch: float, prefer: str | None = None) -> str | None:
    """Pose atteinte. `prefer` = pose demandée : une tête un peu tournée ET levée compte pour « haut »
    quand c'est « haut » qui est demandé (sinon elle tomberait dans « gauche »)."""
    if prefer == "up" and abs(yaw) < 0.35 and pitch <= -PITCH_UPDOWN:
        return "up"
    if prefer == "down" and abs(yaw) < 0.35 and pitch >= PITCH_UPDOWN:
        return "down"
    if abs(yaw) < 0.16 and abs(pitch) < 0.11:
        return "center"
    if YAW_SIDE <= yaw <= 0.85 and abs(pitch) < 0.45:
        return "left"
    if -0.85 <= yaw <= -YAW_SIDE and abs(pitch) < 0.45:
        return "right"
    if abs(yaw) < 0.35 and pitch <= -PITCH_UPDOWN:
        return "up"
    if abs(yaw) < 0.35 and pitch >= PITCH_UPDOWN:
        return "down"
    return None


def pose_gauge(target: str | None, yaw: float, pitch: float) -> float | None:
    """Avancement 0..1 vers la pose demandée (jauge affichée sous la consigne)."""
    reach = {"left": yaw / YAW_SIDE, "right": -yaw / YAW_SIDE,
             "up": -pitch / PITCH_UPDOWN, "down": pitch / PITCH_UPDOWN}.get(target or "")
    return None if reach is None else max(0.0, min(1.0, reach))


@dataclass
class EnrollSession:
    name: str
    model: str | None = None          # figé au début : tous les échantillons d'un profil viennent du même modèle
    started: float = field(default_factory=time.time)
    counts: dict = field(default_factory=lambda: {k: 0 for k in TARGETS})
    embs: list = field(default_factory=list)
    best_thumb: bytes = b""
    best_thumb_score: float = -1.0
    last_capture: float = 0.0
    hint: str = "Placez votre visage dans le cercle"
    current_pose: str | None = None
    rel: tuple | None = None          # pose relative courante (yaw, pitch)
    done: bool = False
    base_yaw: float | None = None
    base_pitch: float = 0.0
    _calib: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.embs)

    @property
    def target_total(self) -> int:
        return sum(TARGETS.values())

    @property
    def complete(self) -> bool:
        return all(self.counts[k] >= v for k, v in TARGETS.items())

    def next_target(self) -> str | None:
        for k, v in TARGETS.items():
            if self.counts[k] < v:
                return k
        return None

    def progress(self) -> dict:
        return {"name": self.name, "counts": self.counts, "targets": TARGETS, "total": self.total,
                "target_total": self.target_total, "hint": self.hint, "pose": self.current_pose,
                "next": self.next_target(), "can_finish": self.total >= MIN_SAMPLES, "complete": self.complete,
                "calibrated": self.base_yaw is not None,
                "gauge": pose_gauge(self.next_target(), *self.rel) if self.rel else None}

    def feed(self, engine: FaceEngine, frame: np.ndarray, faces: list[Face], now: float) -> bool:
        """Traite une image ; retourne True si un échantillon a été capturé."""
        self.current_pose = None
        self.rel = None
        if not faces:
            self.hint = "Aucun visage détecté — placez-vous face à la caméra"
            return False
        if len(faces) > 1:
            self.hint = "Une seule personne devant la caméra, s'il vous plaît"
            return False
        f = faces[0]
        if f.rel_size < MIN_REL_SIZE:
            self.hint = "Rapprochez-vous un peu"
            return False
        if f.rel_size > 0.75:
            self.hint = "Reculez légèrement"
            return False
        # Calibration : la webcam est rarement dans l'axe de l'écran. On mesure d'abord la pose
        # "naturelle" quand l'utilisateur regarde son écran ; les poses sont relatives à celle-ci.
        if self.base_yaw is None:
            self._calib.append((f.yaw, f.pitch))
            self.hint = "Regardez votre écran normalement…"
            if len(self._calib) >= CALIB_FRAMES:
                ys, ps = zip(*self._calib[-CALIB_FRAMES:])
                if max(ys) - min(ys) < 0.25 and max(ps) - min(ps) < 0.3:
                    self.base_yaw, self.base_pitch = float(np.median(ys)), float(np.median(ps))
                else:
                    self._calib = self._calib[-CALIB_FRAMES // 2:]
                    self.hint = "Restez immobile un instant…"
            return False
        self.rel = (f.yaw - self.base_yaw, f.pitch - self.base_pitch)
        bucket = pose_bucket(*self.rel, prefer=self.next_target())
        self.current_pose = bucket
        target = self.next_target()
        if target is None:
            self.hint = "C'est parfait ! Vous pouvez terminer."
            return False
        wanted = bucket if bucket and self.counts[bucket] < TARGETS[bucket] else None
        if wanted is None:
            self.hint = _HINTS[target]
            return False
        if now - self.last_capture < CAPTURE_INTERVAL:
            return False
        crop = engine.aligned_crop(frame, f)
        sh = sharpness(crop)
        if sh < MIN_SHARPNESS:
            self.hint = "Image floue — restez immobile un instant"
            return False
        emb = engine.embed(frame, f, self.model)
        self.embs.append(emb)
        self.counts[wanted] += 1
        self.last_capture = now
        if wanted == "center":
            score = sh * f.score
            if score > self.best_thumb_score:
                ok, jpg = cv2.imencode(".jpg", cv2.resize(crop, (160, 160)), [cv2.IMWRITE_JPEG_QUALITY, 88])
                if ok:
                    self.best_thumb, self.best_thumb_score = jpg.tobytes(), score
        nt = self.next_target()
        self.hint = _HINTS[nt] if nt else "C'est parfait ! Vous pouvez terminer."
        return True

    def result(self) -> np.ndarray:
        """Embeddings nettoyés : on écarte les échantillons aberrants (mauvais alignement, autre visage)."""
        E = np.asarray(self.embs, np.float32)
        if len(E) < 3:
            return E
        mean = E.mean(axis=0)
        mean /= np.linalg.norm(mean) or 1.0
        sims = E @ mean
        keep = sims >= max(0.35, float(np.median(sims)) - 0.2)
        return E[keep]


_HINTS = {
    "center": "Regardez droit vers la caméra",
    "left": "Tournez doucement la tête vers votre gauche",
    "right": "Tournez doucement la tête vers votre droite",
    "up": "Levez un peu le menton, comme pour regarder le haut de l'écran",
    "down": "Baissez un peu le menton, comme pour regarder votre clavier",
}
