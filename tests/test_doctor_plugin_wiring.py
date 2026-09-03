"""ce-bootstrap-plugin-wiring 1.2 — the otaman-cli doctor wrapper.

`_check_plugin_wiring` surfaces core's `resolve_plugin_wiring` WARNs through
`otaman doctor` (the WARN only reaches users once cli calls the primitive —
core PR #41 / gate 2.1). WARN-only. Core primitive monkeypatched — no disk.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.commands import doctor  # noqa: E402


def _program(tmp_path):
    (tmp_path / "platform.yaml").write_text("project: x\nversion: '1.0'\n", encoding="utf-8")
    return tmp_path


def test_surfaces_warn_finding(tmp_path, monkeypatch):
    from otaman_core import plugin_wiring

    root = _program(tmp_path)
    monkeypatch.setattr(
        plugin_wiring,
        "resolve_plugin_wiring",
        lambda config, *, home, platform_dir: [
            types.SimpleNamespace(level="warn", message="plugin tree vendored but not wired")
        ],
    )
    findings = doctor._check_plugin_wiring(root)
    assert findings == [{"level": "warn", "message": "plugin tree vendored but not wired"}]


def test_healthy_is_empty(tmp_path, monkeypatch):
    from otaman_core import plugin_wiring

    root = _program(tmp_path)
    monkeypatch.setattr(
        plugin_wiring, "resolve_plugin_wiring", lambda config, *, home, platform_dir: []
    )
    assert doctor._check_plugin_wiring(root) == []


def test_missing_platform_yaml_is_empty(tmp_path):
    # no platform.yaml → nothing to check, never raises
    assert doctor._check_plugin_wiring(tmp_path) == []
