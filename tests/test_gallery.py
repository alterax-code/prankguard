"""Gallery : persistance des profils, avec et sans chiffrement DPAPI (local, inoffensif)."""
import io

import numpy as np
import pytest

from prankguard.gallery import Gallery

MAGIC = b"PGv3"
THUMB = b"\xff\xd8\xff\xe0fake-jpeg-thumbnail\xff\xd9"


@pytest.fixture(params=[True, False], ids=["dpapi", "plain"])
def encrypt(request):
    return request.param


@pytest.fixture
def path(tmp_path):
    return tmp_path / "data" / "faces.bin"


def embs(seed=0, n=5):
    rng = np.random.default_rng(seed)
    e = rng.normal(size=(n, 128)).astype(np.float32)
    return e / np.linalg.norm(e, axis=1, keepdims=True)


def test_empty_gallery(path, encrypt):
    g = Gallery(path, encrypt=encrypt)
    assert g.people == {} and g.summary() == [] and g.embeddings() == {}
    assert g.thumb("nobody") is None


def test_upsert_and_reload_roundtrip(path, encrypt):
    g = Gallery(path, encrypt=encrypt)
    e1, e2 = embs(1), embs(2, n=8)
    g.upsert("alice", e1, THUMB)
    g.upsert("bob", e2, b"bobthumb")
    g2 = Gallery(path, encrypt=encrypt)
    assert list(g2.people) == ["alice", "bob"]
    got = g2.embeddings()
    assert got["alice"].dtype == np.float32
    np.testing.assert_array_equal(got["alice"], e1)
    np.testing.assert_array_equal(got["bob"], e2)
    assert g2.thumb("alice") == THUMB and g2.thumb("bob") == b"bobthumb"
    assert g2.people["alice"]["created"] == pytest.approx(g.people["alice"]["created"])


def test_upsert_replaces_existing_profile(path, encrypt):
    g = Gallery(path, encrypt=encrypt)
    g.upsert("alice", embs(1, 5), THUMB)
    g.upsert("alice", embs(2, 3), b"new")
    g2 = Gallery(path, encrypt=encrypt)
    assert len(g2.embeddings()["alice"]) == 3 and g2.thumb("alice") == b"new"


def test_summary(path, encrypt):
    g = Gallery(path, encrypt=encrypt)
    g.upsert("alice", embs(1, 5), THUMB)
    s = g.summary()
    assert s == [{"name": "alice", "samples": 5, "learned": 0, "created": g.people["alice"]["created"]}]


def test_rename(path, encrypt):
    g = Gallery(path, encrypt=encrypt)
    g.upsert("alice", embs(1), THUMB)
    g.upsert("bob", embs(2), b"b")
    assert g.rename("alice", "carol") is True
    assert g.rename("ghost", "x") is False                 # source inconnue
    assert g.rename("bob", "carol") is False               # collision
    g2 = Gallery(path, encrypt=encrypt)
    assert list(g2.people) == ["carol", "bob"]             # ordre conservé
    assert g2.thumb("carol") == THUMB


def test_remove(path, encrypt):
    g = Gallery(path, encrypt=encrypt)
    g.upsert("alice", embs(1), THUMB)
    g.upsert("bob", embs(2), b"b")
    assert g.remove("alice") is True
    assert g.remove("alice") is False
    assert list(Gallery(path, encrypt=encrypt).people) == ["bob"]
    assert g.remove("bob") is True
    assert Gallery(path, encrypt=encrypt).people == {}


def test_file_format_header(path, encrypt):
    g = Gallery(path, encrypt=encrypt)
    g.upsert("alice", embs(1), THUMB)
    raw = path.read_bytes()
    assert raw.startswith(MAGIC)
    assert not path.with_suffix(".tmp").exists()


def test_plain_file_is_a_readable_npz_and_encrypted_is_not(path):
    Gallery(path, encrypt=False).upsert("alice", embs(1), THUMB)
    plain = path.read_bytes()
    with np.load(io.BytesIO(plain[len(MAGIC):]), allow_pickle=False) as z:
        assert "__meta__" in z.files and z["e0"].shape == (5, 128)

    enc_path = path.with_name("enc.bin")
    Gallery(enc_path, encrypt=True).upsert("alice", embs(1), THUMB)
    enc = enc_path.read_bytes()
    body = enc[len(MAGIC):]
    assert not body.startswith(b"PK")                          # pas un zip lisible
    assert b"__meta__" not in enc and b"alice" not in enc and b"e0.npy" not in enc
    with pytest.raises(Exception):
        np.load(io.BytesIO(body), allow_pickle=False)


def test_encrypted_file_unreadable_without_decryption(path):
    Gallery(path, encrypt=True).upsert("alice", embs(1), THUMB)
    assert Gallery(path, encrypt=False).people == {}           # mauvais mode -> profils ignorés, pas de crash


def test_corrupted_file_gives_empty_gallery(path, encrypt):
    path.parent.mkdir(parents=True)
    path.write_bytes(MAGIC + b"this is not valid data")
    assert Gallery(path, encrypt=encrypt).people == {}
    path.write_bytes(b"garbage without magic")
    assert Gallery(path, encrypt=encrypt).people == {}


def test_embeddings_returns_a_shallow_copy_of_the_mapping(path):
    g = Gallery(path, encrypt=False)
    g.upsert("alice", embs(1), THUMB)
    g.embeddings().pop("alice")
    assert "alice" in g.people


def test_adaptive_samples_roundtrip_and_fifo(path, encrypt, monkeypatch):
    import prankguard.gallery as gal
    monkeypatch.setattr(gal, "MAX_ADAPTIVE", 3)
    g = Gallery(path, encrypt=encrypt)
    g.upsert("alice", embs(1, 5), THUMB)
    extra = embs(9, 4)
    for e in extra:
        g.add_adaptive("alice", e)
    g2 = Gallery(path, encrypt=encrypt)
    assert np.allclose(g2.people["alice"]["adapt"], extra[-3:])        # FIFO borné
    assert len(g2.embeddings()["alice"]) == 8 and len(g2.enrolled()["alice"]) == 5
    assert g2.summary()[0]["learned"] == 3


def test_reenroll_and_forget_reset_learning(path, encrypt):
    g = Gallery(path, encrypt=encrypt)
    g.upsert("alice", embs(1, 5), THUMB)
    g.add_adaptive("alice", embs(2, 1)[0])
    assert g.forget_adaptive("alice") and g.summary()[0]["learned"] == 0
    g.add_adaptive("alice", embs(2, 1)[0])
    g.upsert("alice", embs(3, 5), THUMB)
    assert g.summary()[0]["learned"] == 0
    assert not g.add_adaptive("bob", embs(2, 1)[0])


def test_legacy_file_without_adaptive_loads(path, encrypt):
    """Galerie écrite avant l'apprentissage continu : aucun champ a{i}."""
    g = Gallery(path, encrypt=encrypt)
    g.upsert("alice", embs(1, 5), THUMB)
    g.people["alice"].pop("adapt")
    import io, json
    arrays = {"e0": g.people["alice"]["emb"], "t0": np.frombuffer(THUMB, np.uint8),
              "__meta__": np.frombuffer(json.dumps([{"name": "alice", "created": 1}]).encode(), np.uint8)}
    buf = io.BytesIO(); np.savez_compressed(buf, **arrays)
    raw = buf.getvalue()
    from prankguard import gallery as gal, winsys
    path.write_bytes(gal._MAGIC + (winsys.dpapi_protect(raw) if encrypt else raw))
    g2 = Gallery(path, encrypt=encrypt)
    assert g2.summary()[0]["learned"] == 0 and len(g2.embeddings()["alice"]) == 5
