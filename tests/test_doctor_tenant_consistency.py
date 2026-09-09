"""scan-init-edition-backfill 1.2 — doctor tenant-wide runner/terminal consistency.

The runner derives its bootstrap from the alphabetically-first registered
platform and applies it tenant-wide, so doctor WARNs when a program's
runner:/terminal: diverges from — or is missing versus — that primary, naming
both the divergent programs and the primary. WARN-only (never the exit code).
"""

from __future__ import annotations

import pytest
import yaml

from otaman_cli.commands.doctor import (
    _check_tenant_consistency,
    _print_tenant_consistency_report,
)

_RUNNER = {
    "harnesses": [{"id": "claude", "binary": "claude"}],
    "agent_bootstrap": {"mcp_config": ".mcp.json"},
}
_TERMINAL = {"local_auth": {"enabled": True, "session_ttl": 3600}, "users": []}


def _platform(path, name, *, runner=None, terminal=None):
    doc = {"project": name, "version": "1.0"}
    if runner is not None:
        doc["runner"] = runner
    if terminal is not None:
        doc["terminal"] = terminal
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


@pytest.fixture
def registry(tmp_path, monkeypatch):
    pdir = tmp_path / "registry"
    pdir.mkdir()
    monkeypatch.setenv("OTAMAN_PLATFORMS_DIR", str(pdir))

    def _register(name, platform_path):
        (pdir / f"{name}.yaml").symlink_to(platform_path)

    return tmp_path, _register


def test_not_applicable_with_fewer_than_two(registry):
    root, register = registry
    register("solo", _platform(root / "solo" / "platform.yaml", "solo", runner=_RUNNER))
    assert _check_tenant_consistency()["applicable"] is False


def test_consistent_tenant_has_no_divergence(registry):
    root, register = registry
    register(
        "alpha",
        _platform(root / "alpha" / "platform.yaml", "alpha", runner=_RUNNER, terminal=_TERMINAL),
    )
    register(
        "beta",
        _platform(root / "beta" / "platform.yaml", "beta", runner=_RUNNER, terminal=_TERMINAL),
    )
    result = _check_tenant_consistency()
    assert result["applicable"] is True
    assert result["primary"] == "alpha"  # alphabetical-first
    assert result["divergent"] == []


def test_divergent_runner_is_flagged_against_primary(registry):
    root, register = registry
    register(
        "alpha",
        _platform(root / "alpha" / "platform.yaml", "alpha", runner=_RUNNER, terminal=_TERMINAL),
    )
    other = {
        "harnesses": [{"id": "codex", "binary": "codex"}],
        "agent_bootstrap": {"mcp_config": "x"},
    }
    register(
        "zeta", _platform(root / "zeta" / "platform.yaml", "zeta", runner=other, terminal=_TERMINAL)
    )
    result = _check_tenant_consistency()
    assert result["primary"] == "alpha"
    assert [d["name"] for d in result["divergent"]] == ["zeta"]
    assert any("runner: differs" in i for i in result["divergent"][0]["issues"])


def test_missing_terminal_is_flagged_as_missing(registry):
    root, register = registry
    register(
        "alpha",
        _platform(root / "alpha" / "platform.yaml", "alpha", runner=_RUNNER, terminal=_TERMINAL),
    )
    # the pmeets case: a program with no runner:/terminal: at all
    register("pmeets", _platform(root / "pmeets" / "platform.yaml", "pmeets"))
    result = _check_tenant_consistency()
    issues = result["divergent"][0]["issues"]
    assert "runner: missing" in issues and "terminal: missing" in issues


def test_report_names_primary_and_divergent(registry, capsys):
    root, register = registry
    register(
        "alpha",
        _platform(root / "alpha" / "platform.yaml", "alpha", runner=_RUNNER, terminal=_TERMINAL),
    )
    register("pmeets", _platform(root / "pmeets" / "platform.yaml", "pmeets"))
    _print_tenant_consistency_report(_check_tenant_consistency())
    out = capsys.readouterr().out
    assert "WARN" in out and "alpha" in out and "pmeets" in out
    assert "tenant-wide" in out


def test_report_ok_when_consistent(registry, capsys):
    root, register = registry
    register(
        "alpha",
        _platform(root / "alpha" / "platform.yaml", "alpha", runner=_RUNNER, terminal=_TERMINAL),
    )
    register(
        "beta",
        _platform(root / "beta" / "platform.yaml", "beta", runner=_RUNNER, terminal=_TERMINAL),
    )
    _print_tenant_consistency_report(_check_tenant_consistency())
    out = capsys.readouterr().out
    assert "OK" in out and "consistent" in out and "WARN" not in out


def test_report_quiet_when_not_applicable(registry, capsys):
    root, register = registry
    register("solo", _platform(root / "solo" / "platform.yaml", "solo", runner=_RUNNER))
    _print_tenant_consistency_report(_check_tenant_consistency())
    assert capsys.readouterr().out == ""
