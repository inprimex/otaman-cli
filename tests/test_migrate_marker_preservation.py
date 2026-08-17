"""migrate marker rewrite preserves an existing agent: field.

2026-08-16 clobber incident (plugin-agent 20260816T231146): migrate's
marker writer unconditionally overwrote repo `.otaman` markers, dropping
`agent:` whenever the platform.yaml entry had no owner. Config owner wins
when present; otherwise the marker's own value survives — mirroring
init.py --update and otaman-plugin PR #89.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.commands.migrate import cmd_migrate


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner_line: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "platform.yaml").write_text(
        f"project: sampleproj\nrepos:\n  - name: svc\n    path: ../svc\n{owner_line}",
        encoding="utf-8",
    )
    (root / ".agents").mkdir()
    # Path is relative to the created <project>-otaman/ meta dir: ../svc
    # from root/sampleproj-otaman resolves to root/svc.
    svc = root / "svc"
    svc.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    return svc


def _marker_agent(svc: Path) -> str:
    for line in (svc / ".otaman").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("agent:"):
            return line.split(":", 1)[1].strip()
    return ""


def test_existing_agent_preserved_when_config_has_no_owner(tmp_path, monkeypatch):
    svc = _project(tmp_path, monkeypatch, owner_line="")
    (svc / ".otaman").write_text(
        "# Path to otaman folder\n../old-meta\nagent: pre-existing-agent\n", encoding="utf-8"
    )
    assert cmd_migrate(["--yes"]) == 0
    assert _marker_agent(svc) == "pre-existing-agent"


def test_config_owner_wins_over_existing_agent(tmp_path, monkeypatch):
    svc = _project(tmp_path, monkeypatch, owner_line="    owner: cli-agent\n")
    (svc / ".otaman").write_text(
        "# Path to otaman folder\n../old-meta\nagent: stale-agent\n", encoding="utf-8"
    )
    assert cmd_migrate(["--yes"]) == 0
    assert _marker_agent(svc) == "cli-agent"


def test_fresh_marker_written_with_owner(tmp_path, monkeypatch):
    svc = _project(tmp_path, monkeypatch, owner_line="    owner: cli-agent\n")
    assert not (svc / ".otaman").exists()
    assert cmd_migrate(["--yes"]) == 0
    assert _marker_agent(svc) == "cli-agent"
