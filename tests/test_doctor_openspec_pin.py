"""openspec-cli-adoption 2.2 — doctor pins the OpenSpec CLI version.

`@latest` in a fix hint would silently drift the fleet onto an unvetted
release. The adopted, drift-report-validated version is 1.10.0, held in a
single constant. These tests assert both fix hints carry that pin and no
`@latest` survives anywhere in the openspec check output.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from otaman_cli import doctor
from otaman_cli.doctor import OPENSPEC_PINNED_VERSION, check_openspec

_CONFIG = {"specs": {"format": "openspec", "path": "../otaman-specs"}}


def _flatten(result: dict) -> str:
    """All fix/issue text from a check result, as one JSON string."""
    return json.dumps(result)


def test_not_installed_hint_is_pinned_no_latest(tmp_path):
    # openspec absent AND npx absent → the high-severity "not installed" fix.
    with patch.object(doctor, "_which", return_value=None):
        result = check_openspec(_CONFIG, tmp_path)

    assert result["status"] == "fail"
    blob = _flatten(result)
    assert "@latest" not in blob
    assert f"@fission-ai/openspec@{OPENSPEC_PINNED_VERSION}" in blob


def test_via_npx_hint_is_pinned_no_latest(tmp_path):
    # openspec absent but npx present and resolves → low-severity warn path.
    def fake_which(name):
        return "/usr/bin/npx" if name == "npx" else None

    with (
        patch.object(doctor, "_which", side_effect=fake_which),
        patch.object(doctor, "_run", return_value=(0, "1.10.0", "")),
    ):
        result = check_openspec(_CONFIG, tmp_path)

    assert result["status"] == "warn"
    blob = _flatten(result)
    assert "@latest" not in blob
    assert f"@fission-ai/openspec@{OPENSPEC_PINNED_VERSION}" in blob


def test_pinned_constant_value():
    # Guards the drift-report-adopted version; bump deliberately on cutover.
    assert OPENSPEC_PINNED_VERSION == "1.10.0"
