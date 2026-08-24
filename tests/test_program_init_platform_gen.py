"""Tests for platform_gen.py — platform.yaml generation + round-trip (tasks.md 5.2)."""

from __future__ import annotations

import pytest

# ruamel.yaml may not be installed; skip the whole module gracefully
pytest.importorskip("ruamel.yaml", reason="ruamel.yaml not installed — skipping platform_gen tests")

from otaman_cli.onboard.program_init.platform_gen import (
    _build_platform_yaml,
    update_platform_yaml,
    write_platform_yaml,
)

_BASE_ANSWERS = {
    "program_name": "acme-platform",
    "description": "ACME multi-repo platform",
    "active_edition": "ce",
    "mode": 1,
    "domains": ["software-development"],
    "roles": ["CTO", "CPO"],
    "role_assignments": {"CTO": "alice", "CPO": "bob"},
    "processes": ["outcomes", "risks"],
    "currency_code": "USD",
    "currency_symbol": "$",
    "currency_decimals": 2,
    "probability_scale": "t-shirt",
    "impact_scale": "t-shirt",
    "releases": ["MVP", "post-MVP"],
    "skill_profile": "software-development-default",
    "extra_skills": [],
    "primary_repo": "/tmp/acme/acme-specs",
    "git_platform": "local",
    "scaffold_business": True,
    "scaffold_strategy": False,
}


class TestBuildPlatformYaml:
    def test_basic_structure(self):
        doc = _build_platform_yaml(_BASE_ANSWERS)
        assert doc["project"] == "acme-platform"
        assert doc["description"] == "ACME multi-repo platform"
        assert doc["edition"] == "ce"
        assert doc["mode"] == 1

    def test_currency_section(self):
        doc = _build_platform_yaml(_BASE_ANSWERS)
        assert doc["currency"]["code"] == "USD"
        assert doc["currency"]["symbol"] == "$"
        assert doc["currency"]["decimal_places"] == 2

    def test_triage_section(self):
        doc = _build_platform_yaml(_BASE_ANSWERS)
        assert doc["triage"]["probability_scale"] == "t-shirt"

    def test_releases(self):
        doc = _build_platform_yaml(_BASE_ANSWERS)
        assert doc["releases"] == ["MVP", "post-MVP"]

    def test_skills(self):
        doc = _build_platform_yaml(_BASE_ANSWERS)
        assert doc["skills"]["profile"] == "software-development-default"

    def test_repos_includes_business(self):
        doc = _build_platform_yaml(_BASE_ANSWERS)
        names = [r["name"] for r in doc["repos"]]
        assert "acme-platform-specs" in names
        assert "acme-platform-business" in names
        assert "acme-platform-strategy" not in names

    def test_processes_map(self):
        """Processes are nested under `program.processes` (schema requires it).
        Each process gets the `{enabled: true}` shape per
        outcome-and-solution-registries/design.md Appendix D — not the
        old `{name: True}` flat form (which was at top level + rejected
        by platform-schema.yaml as additionalProperties)."""
        doc = _build_platform_yaml(_BASE_ANSWERS)
        # Top-level processes: key must NOT exist (schema rejects it)
        assert "processes" not in doc
        # Nested under program: with the {enabled: true} shape
        program = doc["program"]
        assert program["processes"]["outcomes"] == {"enabled": True}
        assert program["processes"]["risks"] == {"enabled": True}
        assert "strategy" not in program["processes"]

    def test_ee_section_absent_for_ce(self):
        doc = _build_platform_yaml(_BASE_ANSWERS)
        assert "ee" not in doc

    def test_ee_section_present_for_ee(self):
        answers = {**_BASE_ANSWERS, "active_edition": "ee", "organisation_name": "acme-org"}
        doc = _build_platform_yaml(answers)
        assert "ee" in doc
        assert doc["ee"]["organisation"] == "acme-org"

    def test_standards_git_defaults_to_trunk_based(self):
        """git-flow-branch-config task 2.2 — a freshly scaffolded project
        gets standards.git.branching: trunk-based rather than an absent
        standards.git section entirely."""
        doc = _build_platform_yaml(_BASE_ANSWERS)
        assert doc["standards"]["git"]["branching"] == "trunk-based"
        # v1 scaffold: no environments block (project doesn't know its
        # branch/environment mapping yet)
        assert "environments" not in doc["standards"]["git"]


class TestWritePlatformYaml:
    def test_creates_file(self, tmp_path):
        out = tmp_path / "platform.yaml"
        result = write_platform_yaml(_BASE_ANSWERS, out)
        assert result == out
        assert out.is_file()
        content = out.read_text()
        assert "acme-platform" in content

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "deep" / "nested" / "platform.yaml"
        write_platform_yaml(_BASE_ANSWERS, out)
        assert out.is_file()

    def test_has_header_comment(self, tmp_path):
        out = tmp_path / "platform.yaml"
        write_platform_yaml(_BASE_ANSWERS, out)
        content = out.read_text()
        assert "program-init" in content  # comment mentions program-init


class TestUpdatePlatformYaml:
    def test_updates_field_preserves_others(self, tmp_path):
        # Write initial version with a hand-crafted comment
        platform = tmp_path / "platform.yaml"
        platform.write_text(
            "# My handcrafted comment — must survive the round-trip\n"
            "project: old-name\n"
            "version: '1.0'\n"
            "extra_key: should-survive\n",
            encoding="utf-8",
        )
        answers = {**_BASE_ANSWERS, "program_name": "new-name"}
        update_platform_yaml(answers, platform)
        content = platform.read_text(encoding="utf-8")
        # Updated field
        assert "new-name" in content
        # Preserved extra key
        assert "extra_key" in content
        # Comment should survive (ruamel.yaml preserves comments)
        assert "handcrafted comment" in content

    def test_idempotent(self, tmp_path):
        platform = tmp_path / "platform.yaml"
        write_platform_yaml(_BASE_ANSWERS, platform)
        platform.read_text()
        update_platform_yaml(_BASE_ANSWERS, platform)
        content_after_second = platform.read_text()
        # Core fields should be unchanged
        assert "acme-platform" in content_after_second


def test_update_does_not_reflow_long_command_scalar(tmp_path):
    """`otaman init --update` must not fold long 'cmd || cmd' launch scalars.

    Regression for deploy-agent bug 20260824T125215: width=120 re-wrapped
    long command scalars on every --update re-dump, re-dirtying the
    owner-managed otaman-meta checkout with no semantic change. A key
    present only in the existing file survives the merge untouched, so its
    long scalar must round-trip byte-identical (single line, no fold).
    """
    long_cmd = (
        "cd ../otaman-cli && claude --dangerously-skip-permissions "
        "|| echo otaman-cli-pane-failed-to-launch-open-it-manually "
        "|| tmux kill-pane || sleep 1"
    )
    assert len(long_cmd) > 120, "test scalar must exceed the old fold width"

    existing = f"project: testproj\nversion: '1.0'\nlaunch_hint: {long_cmd}\nrepos: []\n"
    p = tmp_path / "platform.yaml"
    p.write_text(existing, encoding="utf-8")

    # Minimal answers that do not touch launch_hint (a key _build_platform_yaml
    # never emits, so the merge leaves it intact).
    update_platform_yaml({"program_name": "testproj"}, p)

    text = p.read_text(encoding="utf-8")
    # If the scalar were folded, its single-line form would not be a substring.
    assert long_cmd in text, "long command scalar was reflowed/folded on --update"
    # And the value stays on exactly one physical line.
    hint_lines = [ln for ln in text.splitlines() if ln.startswith("launch_hint:")]
    assert len(hint_lines) == 1
    assert hint_lines[0].endswith("sleep 1")


def test_update_is_byte_stable_on_noop_rerun(tmp_path):
    """A second --update with the same answers must not change the file."""
    long_cmd = "run --a || run --b || run --c || " + "x" * 130
    existing = f"project: testproj\nversion: '1.0'\nlaunch_hint: {long_cmd}\nrepos: []\n"
    p = tmp_path / "platform.yaml"
    p.write_text(existing, encoding="utf-8")

    update_platform_yaml({"program_name": "testproj"}, p)
    first = p.read_text(encoding="utf-8")
    update_platform_yaml({"program_name": "testproj"}, p)
    second = p.read_text(encoding="utf-8")

    assert first == second, "repeated --update drifted the file (non-idempotent write)"
