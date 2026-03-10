# -*- coding: utf-8 -*-
"""Tests unitaires — RGPD & Consentement."""

import json
import pytest
from pathlib import Path

from src.security.rgpd import (
    has_consent,
    save_consent,
    revoke_consent,
    delete_all_user_data,
    get_stored_data_summary,
)


class TestConsent:
    """Tests de la gestion du consentement."""

    def test_no_consent_initially(self, tmp_config_dir: Path):
        assert has_consent(config_dir=tmp_config_dir) is False

    def test_save_and_check(self, tmp_config_dir: Path):
        save_consent(True, config_dir=tmp_config_dir)
        assert has_consent(config_dir=tmp_config_dir) is True

    def test_save_refused(self, tmp_config_dir: Path):
        save_consent(False, config_dir=tmp_config_dir)
        assert has_consent(config_dir=tmp_config_dir) is False

    def test_revoke(self, tmp_config_dir: Path):
        save_consent(True, config_dir=tmp_config_dir)
        assert has_consent(config_dir=tmp_config_dir) is True
        revoke_consent(config_dir=tmp_config_dir)
        assert has_consent(config_dir=tmp_config_dir) is False

    def test_corrupted_consent_file(self, tmp_config_dir: Path):
        path = tmp_config_dir / "consent.json"
        path.write_text("{{{invalid", encoding="utf-8")
        assert has_consent(config_dir=tmp_config_dir) is False


class TestDeleteAllData:
    """Tests du droit a l'effacement."""

    def test_delete_with_data(self, tmp_config_dir: Path):
        # Creer des fichiers de donnees
        (tmp_config_dir / "hardware_profile.json").write_text("{}", encoding="utf-8")
        (tmp_config_dir / "device_whitelist.json").write_text("{}", encoding="utf-8")
        save_consent(True, config_dir=tmp_config_dir)
        enc_dir = tmp_config_dir / "encodings"
        enc_dir.mkdir()
        (enc_dir / "owner_encodings.npz").write_bytes(b"fake")

        deleted = delete_all_user_data(config_dir=tmp_config_dir)
        assert deleted["encodings"] is True
        assert deleted["config"] is True
        assert deleted["whitelist"] is True
        assert deleted["consent"] is True
        assert deleted["total_files"] == 4

    def test_delete_empty(self, tmp_path: Path):
        nodir = tmp_path / "nonexistent"
        deleted = delete_all_user_data(config_dir=nodir)
        assert deleted["total_files"] == 0


class TestDataSummary:
    """Tests du resume des donnees stockees."""

    def test_empty_summary(self, tmp_config_dir: Path):
        summary = get_stored_data_summary(config_dir=tmp_config_dir)
        assert summary["consent_given"] is False
        assert summary["encodings_exist"] is False
        assert summary["profile_exists"] is False

    def test_summary_with_data(self, tmp_config_dir: Path):
        save_consent(True, config_dir=tmp_config_dir)
        (tmp_config_dir / "hardware_profile.json").write_text("{}", encoding="utf-8")
        summary = get_stored_data_summary(config_dir=tmp_config_dir)
        assert summary["consent_given"] is True
        assert summary["profile_exists"] is True
