"""Tests for `otaman init --update` platform.yaml structure preservation.

Regression coverage for the bug fixed alongside this file: the original
implementation read platform.yaml with yaml.safe_load, mutated the parsed
dict, then wrote back with yaml.dump — which alphabetized top-level keys
and repo-entry fields, dropped comments, and normalized quoting style.
The downstream launcher (PowerShell-based) keyed on `- name:` as the
repo-entry marker and failed to find any repos after the re-emit, since
entries now started with `- description:`.

The fix applies the OTAMAN_AGENT=<owner> injection via in-place text
substitution instead. These tests verify the on-disk file's structure
is preserved after `_cmd_init_update()`.
"""
from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import pytest


HANDCRAFTED_PLATFORM_YAML = dedent("""\
    # Sample Platform Configuration
    # Handcrafted YAML with specific structure that init --update must preserve.

    project: sample
    version: "1.0"

    repos:
      - name: sample-core
        path: ../sample-core
        owner: core-agent
        tech: [python]
        description: "Inline-list tech; original key order"
        launch:
          title: "Core"
          color: "#1E90FF"
          shell: ssh
          commands:
            - "source ~/.nvm/nvm.sh && claude --plugin-dir ~/sample/sample-plugin '/sample:check'"

      - name: sample-cli
        path: ../sample-cli
        owner: cli-agent
        tech: [python]
        description: "Second repo"
        launch:
          title: "CLI"
          color: "#00CED1"
          shell: ssh
          commands:
            - "source ~/.nvm/nvm.sh && claude --plugin-dir ~/sample/sample-plugin '/sample:check'"

    profiles:
      core:
        description: "Daily-driver code"
        repos: [sample-core, sample-cli]
    """)


def _setup_project(tmp_path: Path) -> Path:
    """Create a minimal otaman-meta-like project at tmp_path.

    Layout:
        tmp_path/
            meta/
                platform.yaml
                .otaman   <- marker file (legacy convention; not a dir here)
            sample-core/  (stub dir so cmd_init doesn't skip it)
            sample-cli/   (stub dir)
    """
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "platform.yaml").write_text(HANDCRAFTED_PLATFORM_YAML, encoding="utf-8")
    # Legacy `.otaman` marker as a file — find_project_root reads it
    (meta / ".otaman").write_text("# pointer\n.\n", encoding="utf-8")
    # Sibling repo dirs (the --update path skips if these don't exist)
    (tmp_path / "sample-core").mkdir()
    (tmp_path / "sample-cli").mkdir()
    return meta


@pytest.fixture
def project(tmp_path, monkeypatch):
    meta = _setup_project(tmp_path)
    monkeypatch.chdir(meta)
    return meta


class TestInitUpdateStructurePreservation:
    """init --update must preserve key order, comments, and quoting style.

    The launcher (PowerShell parser) is brittle: it identifies repo entries by
    looking for `- name:` and breaks if entries are alphabetized to start with
    `- description:` etc.
    """

    def test_inserts_otaman_agent_prefix(self, project):
        from otaman_cli.commands.init import _cmd_init_update

        rc = _cmd_init_update()
        assert rc == 0

        text = (project / "platform.yaml").read_text(encoding="utf-8")
        assert "OTAMAN_AGENT=core-agent claude" in text
        assert "OTAMAN_AGENT=cli-agent claude" in text

    def test_preserves_top_level_key_order(self, project):
        from otaman_cli.commands.init import _cmd_init_update

        _cmd_init_update()

        text = (project / "platform.yaml").read_text(encoding="utf-8")
        # Top-level keys must appear in handcrafted order, not alphabetical
        idx_project = text.index("\nproject:")
        idx_version = text.index("\nversion:")
        idx_repos = text.index("\nrepos:")
        idx_profiles = text.index("\nprofiles:")
        assert idx_project < idx_version < idx_repos < idx_profiles, (
            "top-level keys were reordered (likely alphabetized via yaml.dump)"
        )

    def test_preserves_comments(self, project):
        from otaman_cli.commands.init import _cmd_init_update

        _cmd_init_update()

        text = (project / "platform.yaml").read_text(encoding="utf-8")
        assert "# Sample Platform Configuration" in text
        assert "# Handcrafted YAML" in text

    def test_repo_entries_still_start_with_name(self, project):
        """The launcher's parser keys on `- name:` to identify repo entries.

        If yaml.dump reorders entry fields alphabetically, entries start with
        `- description:` and the launcher sees zero repos. This test catches
        that specific regression.
        """
        from otaman_cli.commands.init import _cmd_init_update

        _cmd_init_update()

        text = (project / "platform.yaml").read_text(encoding="utf-8")
        # At least two `  - name:` lines (one per repo)
        name_marker_count = sum(
            1 for line in text.splitlines() if line.startswith("  - name:")
        )
        assert name_marker_count == 2, (
            f"expected 2 repo entries beginning with `  - name:`, "
            f"found {name_marker_count} — repo-entry fields were likely reordered"
        )

    def test_preserves_inline_list_style(self, project):
        from otaman_cli.commands.init import _cmd_init_update

        _cmd_init_update()

        text = (project / "platform.yaml").read_text(encoding="utf-8")
        # Inline-style `tech: [python]` must survive (yaml.dump would expand
        # to block style `tech:\n- python`)
        assert "tech: [python]" in text

    def test_idempotent(self, project):
        """Running --update twice must not change the file after the first run."""
        from otaman_cli.commands.init import _cmd_init_update

        _cmd_init_update()
        after_first = (project / "platform.yaml").read_text(encoding="utf-8")

        _cmd_init_update()
        after_second = (project / "platform.yaml").read_text(encoding="utf-8")

        assert after_first == after_second
