"""Smoke test avec les VRAIS modèles ONNX embarqués (YuNet, SFace, MiniFASNet) — CPU, pas de webcam."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from prankguard.vision.faces import FaceEngine

SIZE = (640, 480)
ASSETS = Path(__file__).resolve().parent / "assets"


@pytest.fixture(scope="module")
def engine():
    return FaceEngine()


def load(name):
    img = cv2.imread(str(ASSETS / name))
    assert img is not None, name
    return cv2.resize(img, SIZE)


@pytest.fixture(scope="module")
def lena():
    return load("lena.jpg")


@pytest.fixture(scope="module")
def messi():
    return load("messi5.jpg")


def only_face(engine, img):
    faces = engine.detect(img)
    assert len(faces) == 1
    return faces[0]


def test_detect_one_face_each(engine, lena, messi):
    for img in (lena, messi):
        f = only_face(engine, img)
        x, y, w, h = f.box
        assert 0 <= x < SIZE[0] and 0 <= y < SIZE[1] and w > 10 and h > 10
        assert f.landmarks.shape == (5, 2)
        assert 0.75 <= f.score <= 1.0
        assert 0 < f.rel_size < 1


def test_detect_blank_image_finds_nothing(engine):
    assert engine.detect(np.full((480, 640, 3), 127, np.uint8)) == []


def test_embed_is_unit_norm_128d_float32(engine, lena):
    e = engine.embed(lena, only_face(engine, lena))
    assert e.shape == (128,) and e.dtype == np.float32
    assert float(np.linalg.norm(e)) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize("variant", ["blur", "bright"])
def test_same_person_after_degradation_stays_similar(engine, lena, messi, variant):
    for img in (lena, messi):
        ref = engine.embed(img, only_face(engine, img))
        if variant == "blur":
            v = cv2.GaussianBlur(img, (0, 0), 1.5)
        else:
            v = cv2.convertScaleAbs(img, alpha=1.0, beta=40)
        got = engine.embed(v, only_face(engine, v))
        assert float(ref @ got) > 0.6


def test_different_people_are_dissimilar(engine, lena, messi):
    a = engine.embed(lena, only_face(engine, lena))
    b = engine.embed(messi, only_face(engine, messi))
    assert float(a @ b) < 0.35


def test_liveness_is_probability(engine, lena, messi):
    for img in (lena, messi):
        p = engine.liveness(img, only_face(engine, img))
        assert isinstance(p, float) and 0.0 <= p <= 1.0


def test_aligned_crop_and_embeddable(engine, lena):
    f = only_face(engine, lena)
    crop = engine.aligned_crop(lena, f)
    assert crop.shape == (112, 112, 3)
    assert engine.embeddable(f)
