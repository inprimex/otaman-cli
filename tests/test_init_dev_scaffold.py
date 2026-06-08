"""Tests for `otaman-init-dev-scaffold` (LaunchSettings + wizard + generator).

Covers tasks.md 3.1 (schema validation), 3.2 (wizard defaults + --yes),
3.3 (end-to-end integration), and 3.4 (load_settings merge).
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from otaman_cli.init.generator import generate
from otaman_cli.init.schema import (
    AgentEntry,
    Connection,
    LaunchSettings,
    SSHParams,
    TmuxLayoutConfig,
    load_settings,
)
from otaman_cli.init.wizard import default_settings, run_wizard


# ---------------------------------------------------------------- task 3.1
class TestSchema:
    def test_minimum_valid_config_accepted(self):
        s = LaunchSettings(
            tmux=TmuxLayoutConfig(session_prefix="proj"),
            agents=[AgentEntry(name="spec-agent", enabled=True)],
        )
        assert s.version == 1
        assert s.connection.mode == "local"
        assert s.agents[0].name == "spec-agent"

    def test_missing_spec_agent_rejected(self):
        with pytest.raises(ValueError, match="spec-agent"):
            LaunchSettings(
                tmux=TmuxLayoutConfig(session_prefix="proj"),
                agents=[AgentEntry(name="backend-agent", enabled=True)],
            )

    def test_disabled_spec_agent_rejected(self):
        with pytest.raises(ValueError, match="enabled"):
            LaunchSettings(
                tmux=TmuxLayoutConfig(session_prefix="proj"),
                agents=[AgentEntry(name="spec-agent", enabled=False)],
            )

    def test_unknown_top_level_keys_rejected(self):
        with pytest.raises(ValueError):
            LaunchSettings(
                tmux=TmuxLayoutConfig(session_prefix="proj"),
                agents=[AgentEntry(name="spec-agent", enabled=True)],
                bogus_field="x",  # type: ignore[call-arg]
            )

    def test_ssh_mode_requires_host(self):
        with pytest.raises(ValueError):
            LaunchSettings(
                tmux=TmuxLayoutConfig(session_prefix="proj"),
                agents=[AgentEntry(name="spec-agent", enabled=True)],
                connection=Connection(mode="ssh", ssh=SSHParams(host="", user="u")),
            )


# ---------------------------------------------------------------- task 3.2
class TestWizardDefaults:
    def test_default_settings_locks_spec_agent_on(self):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent", "backend-agent"])
        names = [a.name for a in s.agents]
        assert "spec-agent" in names
        spec = next(a for a in s.agents if a.name == "spec-agent")
        assert spec.enabled is True

    def test_default_settings_drops_spec_dupe(self):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent", "spec-agent", "backend-agent"])
        assert [a.name for a in s.agents].count("spec-agent") == 1

    def test_default_settings_local_connection_no_ssh(self):
        s = default_settings(project_name="proj", extra_agent_names=[])
        assert s.connection.mode == "local"
        assert s.connection.ssh is None

    def test_yes_flag_skips_prompts(self, monkeypatch):
        def _no_input(*_a, **_kw):
            raise AssertionError("--yes path must not call input()")

        monkeypatch.setattr("builtins.input", _no_input)
        s = run_wizard(project_name="proj", platform_agent_names=["spec-agent", "backend-agent"], yes=True)
        assert s.agents[0].name == "spec-agent"
        assert s.connection.mode == "local"


# ---------------------------------------------------------------- task 3.3
class TestEndToEnd:
    def test_generate_writes_all_files(self, tmp_path: Path):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent", "backend-agent"])
        out = tmp_path / "launcher"
        r = generate(s, out)

        assert r.settings_yaml.is_file()
        assert r.local_example.is_file()
        assert r.launch_sh.is_file()
        assert r.launch_ps1.is_file()
        assert r.gitignore.is_file()

        # gitignore content
        assert r.gitignore.read_text() == "launch-settings.local.yaml\n"

        # launch.sh has +x bit on POSIX
        if os.name == "posix":
            mode = r.launch_sh.stat().st_mode
            assert mode & 0o100, "launch.sh missing user-execute bit"

    def test_generated_yaml_round_trips(self, tmp_path: Path):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent", "backend-agent"])
        out = tmp_path / "launcher"
        r = generate(s, out)
        loaded = yaml.safe_load(r.settings_yaml.read_text())
        assert loaded["version"] == 1
        assert any(a["name"] == "spec-agent" for a in loaded["agents"])

    def test_launch_sh_contains_per_agent_block(self, tmp_path: Path):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent", "backend-agent"])
        # Default leaves extras disabled — enable backend-agent so the loop emits it
        for a in s.agents:
            if a.name == "backend-agent":
                a.enabled = True
        r = generate(s, tmp_path / "launcher")
        body = r.launch_sh.read_text()
        assert "spec-agent" in body
        assert "backend-agent" in body
        assert "tmux" in body

    def test_ssh_branch_renders_when_ssh_mode(self, tmp_path: Path):
        s = LaunchSettings(
            tmux=TmuxLayoutConfig(session_prefix="proj"),
            agents=[AgentEntry(name="spec-agent", enabled=True)],
            connection=Connection(
                mode="ssh", ssh=SSHParams(host="dev.local", user="roman")
            ),
        )
        r = generate(s, tmp_path / "launcher")
        assert "ssh " in r.launch_sh.read_text()
        assert "dev.local" in r.launch_sh.read_text()

    def test_cmd_init_yes_creates_launcher_folder(self, tmp_path: Path, monkeypatch):
        """Smoke: invoke cmd_init programmatically against a tmp platform.yaml
        and assert launcher/ files materialised."""
        # Minimal valid platform.yaml at tmp_path
        platform_yaml = tmp_path / "platform.yaml"
        platform_yaml.write_text(
            "project: tmp-proj\n"
            "version: \"1.0\"\n"
            "mode: 1\n"
            "edition: ce\n"
            "roles: [main]\n"
            "currency: {code: USD, symbol: '$', decimal_places: 2}\n"
            "triage: {probability_scale: t-shirt, impact_scale: t-shirt}\n"
            "releases: [MVP]\n"
            "skills: {profile: software-development-default, extra: []}\n"
            "repos:\n"
            "  - {name: tmp-specs, path: ., owner: spec-agent}\n",
            encoding="utf-8",
        )

        # Run helper directly (cmd_init is heavier; we test the scaffold step)
        from otaman_cli.main import _scaffold_launcher_after_init

        _scaffold_launcher_after_init(platform_yaml, yes=True)

        launcher = tmp_path / "launcher"
        assert (launcher / "launch-settings.yaml").is_file()
        assert (launcher / "launch-settings.local.yaml").is_file()
        assert (launcher / "launch.sh").is_file()
        assert (launcher / "launch.ps1").is_file()
        assert (launcher / ".gitignore").is_file()
        # otaman-init-dev-scaffold amendment #1: platform.yaml copied into launcher/
        assert (launcher / "platform.yaml").is_file()
        assert (launcher / "platform.yaml").read_text() == platform_yaml.read_text()


# ---------------------------------------------------------------- amendments
class TestAmendmentPlatformYamlCopy:
    """otaman-init-dev-scaffold amendment #1 — platform.yaml in launcher/."""

    def test_generator_copies_platform_yaml(self, tmp_path: Path):
        src = tmp_path / "platform.yaml"
        src.write_text("project: copy-test\n", encoding="utf-8")
        s = default_settings(project_name="copy-test", extra_agent_names=["spec-agent"])
        out = tmp_path / "launcher"
        r = generate(s, out, platform_yaml_source=src)
        assert r.platform_yaml_copy is not None
        assert r.platform_yaml_copy.is_file()
        assert r.platform_yaml_copy.read_text() == src.read_text()

    def test_generator_omits_copy_when_source_absent(self, tmp_path: Path):
        s = default_settings(project_name="no-copy", extra_agent_names=["spec-agent"])
        r = generate(s, tmp_path / "launcher", platform_yaml_source=None)
        assert r.platform_yaml_copy is None
        assert not (tmp_path / "launcher" / "platform.yaml").exists()

    def test_generator_handles_missing_source_file(self, tmp_path: Path):
        """Source path given but file doesn't exist — silent no-op, no crash."""
        s = default_settings(project_name="ghost", extra_agent_names=["spec-agent"])
        r = generate(s, tmp_path / "launcher", platform_yaml_source=tmp_path / "missing.yaml")
        assert r.platform_yaml_copy is None


class TestAmendmentMetaAgent:
    """otaman-init-dev-scaffold amendment #2 — orchestration meta-agent locked-on."""

    def test_default_settings_includes_meta_agent_locked(self):
        s = default_settings(
            project_name="proj",
            meta_agent_name="ehrbridge-meta-agent",
            extra_agent_names=["cpo-agent"],
        )
        names_enabled = {a.name: a.enabled for a in s.agents}
        assert names_enabled["spec-agent"] is True
        assert names_enabled["ehrbridge-meta-agent"] is True
        assert names_enabled["cpo-agent"] is False

    def test_default_settings_dedupes_meta_against_extras(self):
        # Meta-agent name also appears in extras → should not double-add
        s = default_settings(
            project_name="proj",
            meta_agent_name="meta",
            extra_agent_names=["meta", "other"],
        )
        meta_entries = [a for a in s.agents if a.name == "meta"]
        assert len(meta_entries) == 1
        assert meta_entries[0].enabled is True

    def test_yes_flag_with_meta_agent(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("no prompts in --yes")))
        s = run_wizard(
            project_name="proj",
            platform_agent_names=["spec-agent", "cpo-agent"],
            meta_agent_name="proj-meta-agent",
            yes=True,
        )
        enabled_names = [a.name for a in s.agents if a.enabled]
        assert "spec-agent" in enabled_names
        assert "proj-meta-agent" in enabled_names

    def test_scaffold_helper_detects_meta_agent_from_platform_yaml(self, tmp_path: Path):
        """Integration: platform.yaml with `agents: [{role: orchestration}]` →
        meta-agent shows up enabled in generated launch-settings.yaml."""
        platform_yaml = tmp_path / "platform.yaml"
        platform_yaml.write_text(
            "project: meta-test\n"
            "version: \"1.0\"\n"
            "mode: 1\n"
            "edition: ce\n"
            "roles: [main]\n"
            "currency: {code: USD, symbol: '$', decimal_places: 2}\n"
            "triage: {probability_scale: t-shirt, impact_scale: t-shirt}\n"
            "releases: [MVP]\n"
            "skills: {profile: software-development-default, extra: []}\n"
            "repos:\n"
            "  - {name: meta-test-specs, path: ., owner: spec-agent}\n"
            "agents:\n"
            "  - {name: meta-test-meta-agent, role: orchestration}\n",
            encoding="utf-8",
        )
        from otaman_cli.main import _scaffold_launcher_after_init
        _scaffold_launcher_after_init(platform_yaml, yes=True)

        live = yaml.safe_load((tmp_path / "launcher" / "launch-settings.yaml").read_text())
        names = {a["name"]: a["enabled"] for a in live["agents"]}
        assert names.get("spec-agent") is True
        assert names.get("meta-test-meta-agent") is True

    def test_scaffold_helper_graceful_when_no_agents_field(self, tmp_path: Path):
        """platform.yaml without `agents:` list → only spec-agent locked."""
        platform_yaml = tmp_path / "platform.yaml"
        platform_yaml.write_text(
            "project: no-meta\n"
            "version: \"1.0\"\n"
            "mode: 1\n"
            "edition: ce\n"
            "roles: [main]\n"
            "currency: {code: USD, symbol: '$', decimal_places: 2}\n"
            "triage: {probability_scale: t-shirt, impact_scale: t-shirt}\n"
            "releases: [MVP]\n"
            "skills: {profile: software-development-default, extra: []}\n"
            "repos:\n"
            "  - {name: no-meta-specs, path: ., owner: spec-agent}\n",
            encoding="utf-8",
        )
        from otaman_cli.main import _scaffold_launcher_after_init
        _scaffold_launcher_after_init(platform_yaml, yes=True)

        live = yaml.safe_load((tmp_path / "launcher" / "launch-settings.yaml").read_text())
        names = [a["name"] for a in live["agents"] if a["enabled"]]
        assert names == ["spec-agent"]


# ---------------------------------------------------------------- task 3.4
class TestLoadSettingsMerge:
    def test_load_settings_returns_committed_when_no_local(self, tmp_path: Path):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent"])
        generate(s, tmp_path / "launcher")
        # local file as written by generator is comment-only example
        loaded = load_settings(tmp_path / "launcher")
        assert loaded.connection.mode == "local"

    def test_load_settings_local_scalar_override(self, tmp_path: Path):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent"])
        out = tmp_path / "launcher"
        generate(s, out)

        # Local override: switch to ssh mode
        (out / "launch-settings.local.yaml").write_text(
            "connection:\n"
            "  mode: ssh\n"
            "  ssh:\n"
            "    host: override.example.com\n"
            "    user: dev\n",
            encoding="utf-8",
        )
        loaded = load_settings(out)
        assert loaded.connection.mode == "ssh"
        assert loaded.connection.ssh is not None
        assert loaded.connection.ssh.host == "override.example.com"

    def test_load_settings_local_list_replaces_wholesale(self, tmp_path: Path):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent", "backend-agent"])
        out = tmp_path / "launcher"
        generate(s, out)

        # Local override: replace agents list entirely
        (out / "launch-settings.local.yaml").write_text(
            "agents:\n"
            "  - {name: spec-agent, enabled: true}\n"
            "  - {name: ops-agent, enabled: true}\n",
            encoding="utf-8",
        )
        loaded = load_settings(out)
        names = [a.name for a in loaded.agents]
        assert names == ["spec-agent", "ops-agent"]
        assert "backend-agent" not in names

    def test_load_settings_preserves_spec_agent_invariant(self, tmp_path: Path):
        s = default_settings(project_name="proj", extra_agent_names=["spec-agent"])
        out = tmp_path / "launcher"
        generate(s, out)
        # Local override attempts to disable spec-agent
        (out / "launch-settings.local.yaml").write_text(
            "agents:\n  - {name: spec-agent, enabled: false}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="spec-agent"):
            load_settings(out)
