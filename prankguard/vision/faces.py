"""Détection (YuNet) + reconnaissance (SFace) + orientation de tête + suivi multi-visages."""
from __future__ import annotations

import itertools
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import models
from ..log import get

log = get("faces")

cv2.setNumThreads(2)        # charge CPU de fond modérée ; YuNet+SFace restent < 15 ms

DETECT_WIDTH = 320          # YuNet tourne sur une image réduite : ~2-4 ms CPU
MIN_EMBED_PX = 48           # visage trop petit -> embedding peu fiable, on ne tente pas
MAX_EMBED_YAW = 0.75        # profil trop marqué -> embedding peu fiable


@dataclass
class Face:
    box: np.ndarray          # x, y, w, h (pixels image complète)
    landmarks: np.ndarray    # 5x2 : oeil droit, oeil gauche, nez, coin bouche droit, coin bouche gauche
    score: float
    raw: np.ndarray          # ligne YuNet (15 valeurs) à l'échelle de l'image complète, pour alignCrop
    yaw: float = 0.0         # -1 (gauche) .. 0 (face) .. 1 (droite), au-delà = profil
    pitch: float = 0.0       # ~0 = face, >0 tête baissée, <0 tête levée
    rel_size: float = 0.0    # hauteur visage / hauteur image


def head_pose(lm: np.ndarray) -> tuple[float, float]:
    """Orientation approximative à partir des 5 points YuNet (robuste, sans solvePnP)."""
    re, le, nose, rm, lmo = lm
    ex = le[0] - re[0]
    if abs(ex) < 1e-3:
        return 1.0, 0.0
    yaw = ((nose[0] - re[0]) / ex - 0.5) * 2.0
    eye_y = (re[1] + le[1]) / 2.0
    mouth_y = (rm[1] + lmo[1]) / 2.0
    span = mouth_y - eye_y
    pitch = ((nose[1] - eye_y) / span - 0.55) * 2.0 if span > 1e-3 else 0.0
    return float(yaw), float(pitch)


SFACE = "sface"
EDGEFACE = "edgeface_xs"


class OrtRecognizer:
    """Modèle de reconnaissance ArcFace-like via onnxruntime (entrée 112x112 RGB, (x-127,5)/127,5)."""

    def __init__(self, path):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self._input = self._sess.get_inputs()[0].name

    def feature(self, crop_bgr: np.ndarray) -> np.ndarray:
        x = (cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) - 127.5) / 127.5
        return self._sess.run(None, {self._input: x.transpose(2, 0, 1)[None]})[0].reshape(-1)


class FaceEngine:
    def __init__(self, score_threshold: float = 0.75):
        self._det = cv2.FaceDetectorYN.create(str(models.resolve(models.YUNET)), "", (DETECT_WIDTH, 240),
                                              score_threshold, 0.3, 50)
        self._rec = cv2.FaceRecognizerSF.create(str(models.resolve(models.SFACE)), "")
        self._det_size = (0, 0)
        self._fas: Liveness | None = None
        # SFace reste toujours disponible (secours, profils anciens) ; EdgeFace s'y ajoute s'il est là
        self._extra: dict[str, OrtRecognizer] = {}
        self.enable(EDGEFACE, download=False)

    def enable(self, name: str, download: bool = True) -> bool:
        """Charge un modèle supplémentaire (téléchargé et vérifié si besoin). Sans erreur si impossible."""
        if name in self._extra:
            return True
        if name != EDGEFACE:
            return False
        try:
            rec = OrtRecognizer(models.resolve(models.EDGEFACE_XS, allow_download=download))
        except Exception as e:
            if download:
                log.warning("EdgeFace indisponible (%s) — reconnaissance SFace conservée", e)
            return False
        self._extra[name] = rec
        log.info("Reconnaissance EdgeFace-XS prête")
        return True

    def available(self, model: str) -> bool:
        return model == SFACE or model in self._extra

    @property
    def preferred(self) -> str:
        """Modèle utilisé pour les NOUVEAUX enrôlements."""
        return EDGEFACE if EDGEFACE in self._extra else SFACE

    def detect(self, frame: np.ndarray) -> list[Face]:
        h, w = frame.shape[:2]
        s = DETECT_WIDTH / w
        dw, dh = DETECT_WIDTH, int(round(h * s))
        small = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
        if (dw, dh) != self._det_size:
            self._det.setInputSize((dw, dh))
            self._det_size = (dw, dh)
        _, dets = self._det.detect(small)
        out: list[Face] = []
        if dets is None:
            return out
        for d in dets:
            raw = d.copy()
            raw[:14] /= s
            box = raw[:4].copy()
            lm = raw[4:14].reshape(5, 2).copy()
            yaw, pitch = head_pose(lm)
            out.append(Face(box=box, landmarks=lm, score=float(d[14]), raw=raw,
                            yaw=yaw, pitch=pitch, rel_size=float(box[3] / h)))
        return out

    def embed(self, frame: np.ndarray, face: Face, model: str | None = None) -> np.ndarray:
        crop = self._rec.alignCrop(frame, face.raw)      # même gabarit 5 points ArcFace pour les deux modèles
        model = model or self.preferred
        if model == SFACE:
            feat = self._rec.feature(crop).flatten().astype(np.float32)
        else:
            feat = self._extra[model].feature(crop).astype(np.float32)
        n = np.linalg.norm(feat)
        return feat / n if n > 0 else feat

    def aligned_crop(self, frame: np.ndarray, face: Face) -> np.ndarray:
        return self._rec.alignCrop(frame, face.raw)

    def liveness(self, frame: np.ndarray, face: Face) -> float:
        """Probabilité qu'il s'agisse d'un vrai visage (vs photo / écran). Chargé à la demande."""
        if self._fas is None:
            self._fas = Liveness()
        return self._fas.score(frame, face.box)

    @staticmethod
    def screen_pattern(frame: np.ndarray, face: Face) -> float | None:
        return moire_ratio(frame, face.box)

    @staticmethod
    def embeddable(face: Face) -> bool:
        return face.box[3] >= MIN_EMBED_PX and abs(face.yaw) <= MAX_EMBED_YAW


class Liveness:
    """Anti-spoofing passif MiniFASNet (ensemble V2 @2.7 + V1SE @4.0), entrée 80x80 BGR brute."""

    def __init__(self):
        self._nets = [(cv2.dnn.readNetFromONNX(str(models.resolve(models.FAS_V2))), 2.7),
                      (cv2.dnn.readNetFromONNX(str(models.resolve(models.FAS_V1SE))), 4.0)]

    @staticmethod
    def _crop(frame: np.ndarray, box: np.ndarray, scale: float) -> np.ndarray:
        H, W = frame.shape[:2]
        x, y, w, h = (float(v) for v in box)
        scale = min((H - 1) / max(h, 1), (W - 1) / max(w, 1), scale)
        nw, nh = w * scale, h * scale
        cx, cy = x + w / 2, y + h / 2
        x1, y1 = cx - nw / 2, cy - nh / 2
        x2, y2 = cx + nw / 2, cy + nh / 2
        # on décale la boîte pour rester dans l'image (sans la rétrécir)
        if x1 < 0:
            x2 -= x1
            x1 = 0
        if y1 < 0:
            y2 -= y1
            y1 = 0
        if x2 > W - 1:
            x1 -= x2 - W + 1
            x2 = W - 1
        if y2 > H - 1:
            y1 -= y2 - H + 1
            y2 = H - 1
        crop = frame[int(max(0, y1)):int(y2) + 1, int(max(0, x1)):int(x2) + 1]
        return cv2.resize(crop, (80, 80))

    def score(self, frame: np.ndarray, box: np.ndarray) -> float:
        probs = []
        for net, sc in self._nets:
            blob = self._crop(frame, box, sc).astype(np.float32).transpose(2, 0, 1)[None]
            net.setInput(blob)
            logits = net.forward()[0].astype(np.float64)
            e = np.exp(logits - logits.max())
            probs.append(e / e.sum())
        return float(np.mean(probs, axis=0)[1])


# ── Indices anti-spoof complémentaires (sans modèle) ─────────────────────────
# Mesuré à la webcam : vrai visage ≤ 15 en usage (≤ 39 en poses extrêmes) ; vrai téléphone tenu devant
# la caméra : médiane 21, p90 45, max 77. Les deux se chevauchent : la trame n'est qu'un indice d'appoint.
# Pénalité plafonnée à 0,5 : seule, elle ne fait JAMAIS passer un visage que MiniFASNet juge réel pour
# une photo (sinon un intrus filmé par une webcam très compressée serait ignoré) ; elle fait basculer
# les cas douteux (téléphone à 0,4-0,5).
MOIRE_N = 64
MOIRE_FROM, MOIRE_TO = 40.0, 80.0
MOIRE_PENALTY = 0.5
MOIRE_CLEAN = 0.2            # en dessous, aucune trame d'écran
# Parallaxe : un objet plan bougé à la main reste ≤ 0,31 ; une vraie tête qui tourne dépasse 0,35.
PARALLAX_3D = 0.35
DEPTH_WINDOW = 3.0           # s
DEPTH_FLOOR = 0.6            # relief 3D prouvé et pas d'écran : jamais classé « photo »

_HANN = np.outer(np.hanning(MOIRE_N), np.hanning(MOIRE_N))
_yy, _xx = np.mgrid[:MOIRE_N, :MOIRE_N] - MOIRE_N // 2
_r = np.hypot(_yy, _xx) / (MOIRE_N / 2)
_BAND = (_r > 0.25) & (_r < 0.95)


def moire_ratio(frame: np.ndarray, box: np.ndarray) -> float | None:
    """Pic spectral isolé (trame d'écran refilmé) / niveau médian, sur le centre du visage (yeux-nez)."""
    H, W = frame.shape[:2]
    x, y, w, h = (float(v) for v in box)
    side = 0.6 * w
    cx, cy = x + w / 2, y + 0.45 * h
    x1, y1 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    x2, y2 = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
    if min(x2 - x1, y2 - y1) < 24:
        return None
    g = frame[y1:y2, x1:x2]
    g = (cv2.cvtColor(g, cv2.COLOR_BGR2GRAY) if g.ndim == 3 else g).astype(np.float32)
    interp = cv2.INTER_AREA if g.shape[0] > MOIRE_N else cv2.INTER_LINEAR
    g = cv2.resize(g, (MOIRE_N, MOIRE_N), interpolation=interp)
    g = g - cv2.GaussianBlur(g, (0, 0), 2.0)        # passe-haut : la peau lisse disparaît, la trame reste
    v = np.abs(np.fft.fft2(g * _HANN))
    v = np.fft.fftshift(v)[_BAND]
    return float(v.max() / (np.median(v) + 1e-6))


def moire_level(ratio: float | None) -> float:
    if ratio is None:
        return 0.0
    return float(np.clip((ratio - MOIRE_FROM) / (MOIRE_TO - MOIRE_FROM), 0.0, 1.0))


def nose_parallax(a: np.ndarray, b: np.ndarray) -> float:
    """Écart du nez à l'homographie des 4 points quasi coplanaires (yeux, coins de bouche), en distance
    inter-oculaire. Nul pour une photo ou un écran (tout est dans un plan), grand quand une vraie tête tourne."""
    src = a[[0, 1, 3, 4]].astype(np.float32)
    dst = b[[0, 1, 3, 4]].astype(np.float32)
    try:
        Hm = cv2.getPerspectiveTransform(src, dst)
    except cv2.error:
        return 0.0
    p = cv2.perspectiveTransform(a[2][None, None].astype(np.float32), Hm)[0, 0]
    if not np.all(np.isfinite(p)):
        return 0.0
    iod = float(np.linalg.norm(b[1] - b[0]))
    return float(np.linalg.norm(p - b[2]) / max(iod, 1.0))


def combine_liveness(p_fas: float, moire: float, depth: bool) -> float:
    """MiniFASNet reste la base ; la trame d'écran l'abaisse, le relief 3D (sans trame) la protège."""
    p = p_fas * (1.0 - MOIRE_PENALTY * moire)
    if depth and moire < MOIRE_CLEAN:
        p = max(p, DEPTH_FLOOR)
    return float(p)


def sharpness(img: np.ndarray) -> float:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


# ── Galerie en mémoire (matching vectorisé) ──────────────────────────────────


class Matcher:
    """Similarité cosinus contre tous les embeddings enrôlés en une multiplication matricielle."""

    def __init__(self, gallery: dict[str, np.ndarray] | None = None):
        self.set_gallery(gallery or {})

    def set_gallery(self, gallery: dict[str, np.ndarray]) -> None:
        names, mats = [], []
        for name, embs in gallery.items():
            if len(embs):
                mats.append(np.asarray(embs, dtype=np.float32))
                names += [name] * len(embs)
        labels = np.array(names)
        mat = np.vstack(mats) if mats else np.zeros((0, 128), np.float32)
        groups = [(str(n), np.flatnonzero(labels == n)) for n in dict.fromkeys(names)]
        self._data = (mat, groups)   # une seule affectation : best() ne voit jamais un état mélangé

    @property
    def empty(self) -> bool:
        return len(self._data[0]) == 0

    def best(self, emb: np.ndarray) -> tuple[str | None, float]:
        mat, groups = self._data
        if len(mat) == 0:
            return None, 0.0
        sims = mat @ emb
        # moyenne des 3 meilleurs par personne -> plus stable qu'un max isolé
        best_name, best_sim = None, -1.0
        for name, idx in groups:
            g = sims[idx]
            v = float(np.partition(g, -3)[-3:].mean()) if len(g) >= 3 else float(g.max())
            if v > best_sim:
                best_name, best_sim = name, v
        return best_name, best_sim


class ProfileMatcher:
    """Profils enrôlés avec des modèles différents : on ne compare jamais deux espaces d'embeddings."""

    def __init__(self, galleries: dict[str, dict[str, np.ndarray]] | None = None):
        self.set_galleries(galleries or {})

    def set_galleries(self, galleries: dict[str, dict[str, np.ndarray]]) -> None:
        self._by_model = {m: Matcher(g) for m, g in galleries.items()}   # une seule affectation

    @property
    def models(self) -> list[str]:
        return [m for m, mt in self._by_model.items() if not mt.empty]

    @property
    def empty(self) -> bool:
        return not self.models

    def best(self, embs: dict[str, np.ndarray]) -> tuple[str | None, float, str | None]:
        name, sim, model = None, 0.0, None
        for m, e in embs.items():
            mt = self._by_model.get(m)
            if mt is None or mt.empty:
                continue
            n, s = mt.best(e)
            if n is not None and (model is None or s > sim):
                name, sim, model = n, s, m
        return name, sim, model


# ── Suivi ────────────────────────────────────────────────────────────────────

_ids = itertools.count(1)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    face: Face
    id: int = field(default_factory=lambda: next(_ids))
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_embed: float = 0.0
    sims: deque = field(default_factory=lambda: deque(maxlen=6))
    ema: float | None = None
    name: str | None = None
    status: str = "pending"   # pending | known | unknown | spoof
    hits: int = 1
    live: float | None = None  # EMA de la probabilité "visage réel"
    live_n: int = 0
    ever_known: bool = False
    lm_hist: deque = field(default_factory=lambda: deque(maxlen=40))   # (t, 5 points) pour la parallaxe
    moires: deque = field(default_factory=lambda: deque(maxlen=3))
    moire: float = 0.0        # niveau de trame d'écran (médiane des 3 dernières mesures), 0..1
    moire_raw: float | None = None   # dernier rapport pic/médiane brut (diagnostic)
    depth: bool = False       # relief 3D observé récemment

    UNKNOWN_GAP = 0.12        # inconnu seulement si nettement sous le seuil (évite de verrouiller le propriétaire)
    UNKNOWN_MAX = 0.30        # plafond absolu : un seuil « strict » ne transforme jamais le propriétaire en inconnu
    PENDING_MAX = 4.0         # jamais reconnu après ce temps (et ≥ 4 mesures) : inconnu, quel que soit le score

    @classmethod
    def unknown_below(cls, threshold: float) -> float:
        return min(threshold - cls.UNKNOWN_GAP, cls.UNKNOWN_MAX)

    def observe_liveness(self, p_real: float) -> None:
        self.live = p_real if self.live is None else 0.5 * p_real + 0.5 * self.live
        self.live_n += 1

    def depth_evidence(self) -> bool:
        """Au moins deux images récentes où le nez sort du plan yeux-bouche par rapport à l'image actuelle."""
        if len(self.lm_hist) < 3:
            return False
        t_last, last = self.lm_hist[-1]
        hits = 0
        for t, lm in list(self.lm_hist)[:-1]:
            if t_last - t <= DEPTH_WINDOW and nose_parallax(lm, last) > PARALLAX_3D:
                hits += 1
                if hits >= 2:
                    return True
        return False

    def observe_cues(self, p_fas: float, ratio: float | None) -> None:
        self.moire_raw = ratio
        self.moires.append(moire_level(ratio))
        self.moire = float(np.median(self.moires))
        self.depth = self.depth_evidence()
        self.observe_liveness(combine_liveness(p_fas, self.moire, self.depth))

    @property
    def is_spoof(self) -> bool:
        return self.live is not None and self.live_n >= 3 and self.live < 0.3

    def observe_similarity(self, name: str | None, sim: float, threshold: float, now: float) -> None:
        self.last_embed = now
        self.sims.append(sim)
        self.ema = sim if self.ema is None else 0.55 * sim + 0.45 * self.ema
        n = len(self.sims)
        if self.is_spoof:
            self.status, self.name = "spoof", None
            return
        if self.ema >= threshold and name:
            # reconnu : immédiat si très net, sinon après 2 observations
            if sim >= threshold + 0.08 or n >= 2:
                self.status, self.name = "known", name
                self.ever_known = True
        elif self.ema < self.unknown_below(threshold):
            if self.status == "known" and (n < 4 or any(x >= self.unknown_below(threshold) for x in list(self.sims)[-3:])):
                return  # hystérésis : seuls 3 ratés consécutifs font basculer un propriétaire reconnu
            if n >= 2:
                self.status, self.name = "unknown", None
        elif (self.status == "pending" and not self.ever_known and n >= 4
              and now - self.first_seen >= self.PENDING_MAX):
            # entre « inconnu » et « reconnu » trop longtemps : on ne peut pas l'identifier -> inconnu
            self.status, self.name = "unknown", None
        elif self.status == "unknown" and self.ema >= threshold - self.UNKNOWN_GAP / 2:
            self.status = "pending"   # zone d'incertitude : ni propriétaire, ni menace


class Tracker:
    def __init__(self, max_age: float = 0.8):
        self.max_age = max_age
        self.tracks: list[Track] = []

    def update(self, faces: list[Face], now: float) -> list[Track]:
        unmatched = list(range(len(faces)))
        pairs = sorted(((iou(t.face.box, faces[j].box), i, j)
                        for i, t in enumerate(self.tracks) for j in unmatched), reverse=True)
        used_t, used_f = set(), set()
        for score, i, j in pairs:
            if score < 0.25 or i in used_t or j in used_f:
                continue
            t = self.tracks[i]
            t.face, t.last_seen = faces[j], now
            t.hits += 1
            t.lm_hist.append((now, faces[j].landmarks))
            used_t.add(i)
            used_f.add(j)
        for j in range(len(faces)):
            if j not in used_f:
                t = Track(face=faces[j], first_seen=now, last_seen=now)
                t.lm_hist.append((now, faces[j].landmarks))
                self.tracks.append(t)
        self.tracks = [t for t in self.tracks if now - t.last_seen <= self.max_age]
        return [t for t in self.tracks if t.last_seen == now]

    def reset(self) -> None:
        self.tracks.clear()
