"""Tests for doctor.check_plugin_doctor — plugin-side M4/M13B checks wire-in.

Verifies that check_plugin_doctor() correctly maps DoctorWarning severity to
doctor issue severity, surfaces issues vs. returns ok, and degrades gracefully
when otaman_plugin is not importable.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from otaman_cli.doctor import check_plugin_doctor


class TestImportFailure:
    def test_returns_ok_when_plugin_not_importable(self, tmp_path):
        with patch.dict(
            "sys.modules", {"otaman_plugin": None, "otaman_plugin.doctor_checks": None}
        ):
            result = check_plugin_doctor(tmp_path)
        assert result["status"] == "ok"
        assert result["details"].get("skipped")


class TestNoWarnings:
    def test_ok_when_run_all_checks_returns_empty(self, tmp_path):
        mock_module = MagicMock()
        mock_module.run_all_checks.return_value = []
        with patch.dict(
            "sys.modules",
            {"otaman_plugin": MagicMock(), "otaman_plugin.doctor_checks": mock_module},
        ):
            result = check_plugin_doctor(tmp_path)
        assert result["status"] == "ok"
        assert not result.get("issues")


class TestWarnSeverity:
    def _make_warning(self, severity, code, message, repo=None, hint=None):
        from otaman_plugin.doctor_checks import DoctorWarning

        return DoctorWarning(severity=severity, code=code, message=message, repo=repo, hint=hint)

    def test_warn_severity_maps_to_medium(self, tmp_path):
        from otaman_plugin.doctor_checks import DoctorWarning

        w = DoctorWarning(
            severity="warn",
            code="M4_PLUGIN_DIR_DRIFT",
            message="plugin-dir mismatch",
            repo="api",
            hint="align them",
        )
        mock_module = MagicMock()
        mock_module.run_all_checks.return_value = [w]
        with patch.dict(
            "sys.modules",
            {"otaman_plugin": MagicMock(), "otaman_plugin.doctor_checks": mock_module},
        ):
            result = check_plugin_doctor(tmp_path)
        assert result["status"] == "warn"
        issue = result["issues"][0]
        assert issue["severity"] == "medium"
        assert "M4_PLUGIN_DIR_DRIFT" in issue["issue"]
        assert "api" in issue["issue"]
        assert issue["fix"] == "align them"

    def test_error_severity_maps_to_fail(self, tmp_path):
        from otaman_plugin.doctor_checks import DoctorWarning

        w = DoctorWarning(
            severity="error", code="TEST_ERR", message="bad config", repo=None, hint=None
        )
        mock_module = MagicMock()
        mock_module.run_all_checks.return_value = [w]
        with patch.dict(
            "sys.modules",
            {"otaman_plugin": MagicMock(), "otaman_plugin.doctor_checks": mock_module},
        ):
            result = check_plugin_doctor(tmp_path)
        assert result["status"] == "fail"

    def test_info_severity_maps_to_low(self, tmp_path):
        from otaman_plugin.doctor_checks import DoctorWarning

        w = DoctorWarning(severity="info", code="INFO_CODE", message="fyi", repo=None, hint=None)
        mock_module = MagicMock()
        mock_module.run_all_checks.return_value = [w]
        with patch.dict(
            "sys.modules",
            {"otaman_plugin": MagicMock(), "otaman_plugin.doctor_checks": mock_module},
        ):
            result = check_plugin_doctor(tmp_path)
        assert result["status"] == "warn"
        assert result["issues"][0]["severity"] == "low"

    def test_no_repo_omits_tag(self, tmp_path):
        from otaman_plugin.doctor_checks import DoctorWarning

        w = DoctorWarning(
            severity="warn", code="M4_WSL_PATH_UNDER_SSH", message="wsl path", repo=None, hint=None
        )
        mock_module = MagicMock()
        mock_module.run_all_checks.return_value = [w]
        with patch.dict(
            "sys.modules",
            {"otaman_plugin": MagicMock(), "otaman_plugin.doctor_checks": mock_module},
        ):
            result = check_plugin_doctor(tmp_path)
        issue = result["issues"][0]
        assert "wsl path" in issue["issue"]
        assert "None" not in issue["issue"]
