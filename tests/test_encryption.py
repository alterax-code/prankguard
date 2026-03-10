# -*- coding: utf-8 -*-
"""Tests unitaires — Chiffrement des encodings."""

import numpy as np
import pytest
from pathlib import Path

from src.security.encryption import (
    save_encrypted_owner_encodings,
    load_encrypted_owner_encodings,
    delete_encrypted_encodings,
    encrypt_encodings,
    decrypt_encodings,
    derive_key,
)


class TestEncryptDecrypt:
    """Tests du chiffrement/dechiffrement AES-256-GCM."""

    def test_roundtrip(self, tmp_config_dir: Path, fake_encodings):
        """Chiffrer puis dechiffrer donne les memes encodings."""
        save_encrypted_owner_encodings(fake_encodings, config_dir=tmp_config_dir)
        loaded = load_encrypted_owner_encodings(config_dir=tmp_config_dir)
        assert len(loaded) == len(fake_encodings)
        for orig, dec in zip(fake_encodings, loaded):
            assert np.allclose(orig, dec, atol=1e-6)

    def test_encrypted_file_not_readable(self, tmp_config_dir: Path, fake_encodings):
        """Le fichier chiffre ne contient pas de texte lisible."""
        path = save_encrypted_owner_encodings(fake_encodings, config_dir=tmp_config_dir)
        with open(path, "rb") as f:
            raw = f.read()
        # Le fichier ne doit PAS contenir "encodings" ou "float32" en clair
        assert b"encodings" not in raw
        assert b"float32" not in raw

    def test_different_shape(self, tmp_config_dir: Path):
        """Fonctionne avec differentes formes d'encodings."""
        encs = [np.random.randn(512).astype(np.float32) for _ in range(3)]
        save_encrypted_owner_encodings(encs, config_dir=tmp_config_dir)
        loaded = load_encrypted_owner_encodings(config_dir=tmp_config_dir)
        assert len(loaded) == 3

    def test_empty_raises(self, tmp_config_dir: Path):
        """Sauvegarder une liste vide leve une erreur."""
        with pytest.raises(ValueError):
            save_encrypted_owner_encodings([], config_dir=tmp_config_dir)

    def test_load_nonexistent(self, tmp_config_dir: Path):
        """Charger un fichier inexistant retourne une liste vide."""
        loaded = load_encrypted_owner_encodings(config_dir=tmp_config_dir)
        assert loaded == []


class TestDelete:
    """Tests de la suppression (RGPD)."""

    def test_delete_existing(self, tmp_config_dir: Path, fake_encodings):
        save_encrypted_owner_encodings(fake_encodings, config_dir=tmp_config_dir)
        assert delete_encrypted_encodings(config_dir=tmp_config_dir) is True
        # Re-charger doit retourner vide
        loaded = load_encrypted_owner_encodings(config_dir=tmp_config_dir)
        assert loaded == []

    def test_delete_nonexistent(self, tmp_config_dir: Path):
        assert delete_encrypted_encodings(config_dir=tmp_config_dir) is False


class TestKeyDerivation:
    """Tests de la derivation de cle."""

    def test_key_is_deterministic(self, tmp_config_dir: Path):
        """La meme machine produit la meme cle."""
        key1 = derive_key(config_dir=tmp_config_dir)
        key2 = derive_key(config_dir=tmp_config_dir)
        assert key1 == key2

    def test_key_length(self, tmp_config_dir: Path):
        """La cle fait 32 octets (AES-256)."""
        key = derive_key(config_dir=tmp_config_dir)
        assert len(key) == 32
