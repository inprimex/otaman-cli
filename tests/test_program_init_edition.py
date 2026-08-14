"""Tests for edition.py — CE/EE detection."""

from __future__ import annotations

from otaman_cli.onboard.program_init.edition import (
    EDITION_CE,
    EDITION_EE,
    detect_edition,
    detect_mode,
)


class TestDetectEdition:
    def test_defaults_to_ce(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OTAMAN_EDITION", raising=False)
        monkeypatch.delenv("OTAMAN_LICENSE_FILE", raising=False)
        # Ensure no license files exist
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.edition._LICENSE_SEARCH_PATHS",
            [],
        )
        assert detect_edition() == EDITION_CE

    def test_env_var_override_ce(self, monkeypatch):
        monkeypatch.setenv("OTAMAN_EDITION", "ce")
        assert detect_edition() == EDITION_CE

    def test_env_var_override_ee(self, monkeypatch):
        monkeypatch.setenv("OTAMAN_EDITION", "ee")
        assert detect_edition() == EDITION_EE

    def test_license_file_env_var_valid(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OTAMAN_EDITION", raising=False)
        lic = tmp_path / "license.key"
        lic.write_text("OTAMAN-EE-abc123\n")
        monkeypatch.setenv("OTAMAN_LICENSE_FILE", str(lic))
        assert detect_edition() == EDITION_EE

    def test_license_file_env_var_invalid(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OTAMAN_EDITION", raising=False)
        lic = tmp_path / "license.key"
        lic.write_text("NOT-A-LICENSE\n")
        monkeypatch.setenv("OTAMAN_LICENSE_FILE", str(lic))
        assert detect_edition() == EDITION_CE

    def test_search_path_valid(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OTAMAN_EDITION", raising=False)
        monkeypatch.delenv("OTAMAN_LICENSE_FILE", raising=False)
        lic = tmp_path / "license.key"
        lic.write_text("OTAMAN-EE-xyz789\n")
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.edition._LICENSE_SEARCH_PATHS",
            [lic],
        )
        assert detect_edition() == EDITION_EE

    def test_search_path_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OTAMAN_EDITION", raising=False)
        monkeypatch.delenv("OTAMAN_LICENSE_FILE", raising=False)
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.edition._LICENSE_SEARCH_PATHS",
            [tmp_path / "nonexistent.key"],
        )
        assert detect_edition() == EDITION_CE


class TestDetectMode:
    def test_defaults_to_1(self, monkeypatch):
        monkeypatch.delenv("OTAMAN_ZITADEL_URL", raising=False)
        assert detect_mode() == 1

    def test_mode_2_from_env(self, monkeypatch):
        monkeypatch.setenv("OTAMAN_ZITADEL_URL", "https://auth.example.com")
        assert detect_mode() == 2

    def test_mode_from_platform_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OTAMAN_ZITADEL_URL", raising=False)
        yaml_path = tmp_path / "platform.yaml"
        yaml_path.write_text("project: test\nmode: 2\n")
        assert detect_mode(yaml_path) == 2

    def test_mode_1_from_platform_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OTAMAN_ZITADEL_URL", raising=False)
        yaml_path = tmp_path / "platform.yaml"
        yaml_path.write_text("project: test\nmode: 1\n")
        assert detect_mode(yaml_path) == 1
