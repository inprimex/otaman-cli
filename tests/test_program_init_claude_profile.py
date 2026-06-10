"""Tests for program-init-claude-profile (task 1.5).

Covers:
- Wizard question present in builtin questions
- Skipping (empty answer) → no program.claude.config_dir in platform.yaml
- Providing a value → field written with tilde preserved
- Existing platform.yaml without the field loads cleanly
- launch_resolve exports CLAUDE_CONFIG_DIR when present, omits when absent
- Precedence: account override > program.claude.config_dir > empty
- Non-TTY path doesn't crash and produces a field-absent platform.yaml
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# 1.1 — wizard question is in _builtin_questions


def test_builtin_questions_includes_claude_config_dir():
    from otaman_cli.onboard.program_init.runner import _builtin_questions

    qs = _builtin_questions()
    ids = [q["id"] for q in qs]
    assert "claude_config_dir" in ids
    q = next(q for q in qs if q["id"] == "claude_config_dir")
    assert q["type"] == "text"
    assert q["step"] == "identity"
    assert q["default"] == ""
    assert q["output_mapping"] == "program.claude.config_dir"


# ---------------------------------------------------------------------------
# platform_gen behavior — empty omits, value preserved with tilde


def _base_answers(**overrides):
    base = {
        "program_name": "myproj",
        "primary_repo": ".",
        "mode": 1,
        "active_edition": "ce",
        "roles": [],
        "processes": [],
    }
    base.update(overrides)
    return base


def test_empty_claude_config_dir_omits_field():
    from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml

    doc = _build_platform_yaml(_base_answers(claude_config_dir=""))
    program = doc.get("program") or {}
    assert "claude" not in program


def test_missing_claude_config_dir_key_omits_field():
    from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml

    doc = _build_platform_yaml(_base_answers())  # no claude_config_dir in answers
    program = doc.get("program") or {}
    assert "claude" not in program


def test_whitespace_only_claude_config_dir_omits_field():
    from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml

    doc = _build_platform_yaml(_base_answers(claude_config_dir="   \t  "))
    program = doc.get("program") or {}
    assert "claude" not in program


def test_populated_claude_config_dir_writes_field_with_tilde_preserved():
    """Live emission now that otaman-core PR #13 added top-level `program:`
    to platform-schema.yaml (commit 598a947, 2026-06-03)."""
    from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml

    doc = _build_platform_yaml(_base_answers(claude_config_dir="~/.claude-myprog"))
    assert doc["program"]["claude"]["config_dir"] == "~/.claude-myprog"


# ---------------------------------------------------------------------------
# 1.2 — Pydantic schema: ClaudeConfig + program.claude


def test_program_extensions_without_claude_field_loads():
    """Existing platform.yaml without the new field stays valid."""
    from otaman_cli.registries.platform_ext import ProgramExtensions

    ext = ProgramExtensions.model_validate({})
    assert ext.claude is None


def test_program_extensions_with_claude_field_loads():
    from otaman_cli.registries.platform_ext import ProgramExtensions

    ext = ProgramExtensions.model_validate({"claude": {"config_dir": "~/.claude-x"}})
    assert ext.claude is not None
    assert ext.claude.config_dir == "~/.claude-x"


def test_program_extensions_claude_alias_with_hyphen():
    """Schema accepts both `config_dir` and `config-dir` (alias)."""
    from otaman_cli.registries.platform_ext import ProgramExtensions

    ext = ProgramExtensions.model_validate({"claude": {"config-dir": "~/.claude-y"}})
    assert ext.claude.config_dir == "~/.claude-y"


def test_program_extensions_claude_rejects_unknown_subkey():
    """ClaudeConfig uses extra='forbid' — typos surface as validation errors."""
    from pydantic import ValidationError
    from otaman_cli.registries.platform_ext import ProgramExtensions

    with pytest.raises(ValidationError):
        ProgramExtensions.model_validate({"claude": {"unknown_field": "x"}})


# ---------------------------------------------------------------------------
# 1.3 — launch_resolve precedence chain


def _make_launch_project(tmp_path: Path, *, account=None, program_claude_dir=None) -> Path:
    """Build a minimal otaman-meta layout for launch_resolve.resolve()."""
    meta = tmp_path / "meta"
    meta.mkdir()
    # platform.yaml
    yaml_text = "project: x\nrepos: []\n"
    if program_claude_dir is not None:
        yaml_text += f"program:\n  claude:\n    config_dir: \"{program_claude_dir}\"\n"
    (meta / "platform.yaml").write_text(yaml_text, encoding="utf-8")
    # launch-settings.yaml — minimal with optional account
    settings_lines = ["connections:", "  default:", "    type: local"]
    if account:
        settings_lines += [f"    routing: {account}"]
        settings_lines += ["accounts:", f"  {account}:", f"    config_dir: ~/.claude-{account}"]
    (meta / "launch-settings.yaml").write_text("\n".join(settings_lines) + "\n", encoding="utf-8")
    return meta


def test_launch_resolve_uses_program_claude_when_no_account_override(tmp_path: Path):
    """Precedence #2: program.claude.config_dir used when no account override."""
    from otaman_cli.launch_resolve import resolve

    meta = _make_launch_project(tmp_path, program_claude_dir="~/.claude-prog")
    state = resolve(maestro_root=meta, connection_name="default", shell="bash")
    assert state["config_dir_raw"] == "~/.claude-prog"
    assert state["config_dir_expanded"].endswith("/.claude-prog")


def test_launch_resolve_account_override_wins_over_program_claude(tmp_path: Path):
    """Precedence #1: account override beats program.claude.config_dir."""
    from otaman_cli.launch_resolve import resolve

    meta = _make_launch_project(
        tmp_path, account="acct1", program_claude_dir="~/.claude-prog",
    )
    state = resolve(maestro_root=meta, connection_name="default", shell="bash")
    # Account override (~/.claude-acct1) wins
    assert "acct1" in state["config_dir_raw"]
    assert "prog" not in state["config_dir_raw"]


def test_launch_resolve_empty_when_neither_set(tmp_path: Path):
    """No account, no program.claude — config_dir_raw stays empty (shell/Claude fallback)."""
    from otaman_cli.launch_resolve import resolve

    meta = _make_launch_project(tmp_path)
    state = resolve(maestro_root=meta, connection_name="default", shell="bash")
    assert state["config_dir_raw"] == ""
    assert state["config_dir_expanded"] == ""


# ---------------------------------------------------------------------------
# 1.3 — emit_exports honors / omits CLAUDE_CONFIG_DIR


def _full_state(**overrides):
    base = {
        "connection_name": "default",
        "connection_type": "local",
        "account_name": "",
        "config_dir_raw": "",
        "config_dir_expanded": "",
        "secrets": {},
        "repos": [],
        "model": "",
        "effort": "",
    }
    base.update(overrides)
    return base


def test_emit_exports_includes_claude_config_dir_when_present():
    from otaman_cli.launch_resolve import emit_exports

    state = _full_state(
        config_dir_raw="~/.claude-x",
        config_dir_expanded="/home/u/.claude-x",
    )
    out = emit_exports(state)
    assert "export CLAUDE_CONFIG_DIR='/home/u/.claude-x'" in out


def test_emit_exports_omits_claude_config_dir_when_absent():
    from otaman_cli.launch_resolve import emit_exports

    state = _full_state()
    out = emit_exports(state)
    assert "CLAUDE_CONFIG_DIR" not in out


# ---------------------------------------------------------------------------
# 1.4 — non-TTY behavior


def test_non_tty_path_produces_no_claude_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Non-TTY otaman init with no claude_config_dir answer → field absent in platform.yaml."""
    from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml

    # Simulate non-TTY wizard run: no claude_config_dir key in answers at all
    doc = _build_platform_yaml(_base_answers())
    program = doc.get("program") or {}
    assert "claude" not in program


# ---------------------------------------------------------------------------
# Schema-validation regression — post-condition that the wizard's output
# actually passes `otaman validate`. (Self-review 2026-06-07 found that
# `processes:` was being written at the top level, which the schema rejects.
# This test guards against any future field-name / nesting drift.)


# Previously xfail (2026-06-07 → 2026-06-10): the wizard emitted 8 top-level
# keys (currency, description, edition, mode, releases, roles, skills, triage)
# that platform-schema.yaml rejected via `additionalProperties: false`.
# Closed by otaman-core commit 27f2c7c (2026-06-10) which allowlisted all 13
# missing root keys, including the wizard set. Test now passes natively.
def test_wizard_output_passes_platform_schema_validation(tmp_path, monkeypatch):
    """End-to-end post-condition: the platform.yaml the wizard writes must
    pass otaman-core's platform-schema.yaml validator. Catches the bug class
    where the wizard emits valid-looking YAML that the validator rejects.
    """
    from otaman_cli.onboard.program_init.platform_gen import write_platform_yaml

    # Realistic answer set — one of each interesting kind (processes,
    # claude config, currency, releases, repos[]).
    answers = _base_answers(
        program_name="schematest",
        primary_repo="./schematest-specs",
        processes=["outcomes", "solutions"],
        claude_config_dir="~/.claude-schematest",
        roles=["CTO", "CPO"],
        currency_code="USD",
        currency_symbol="$",
        currency_decimals=2,
        probability_scale="t-shirt",
        impact_scale="t-shirt",
        releases=["MVP"],
        skill_profile="software-development-default",
        git_platform="local",
        secret_backend="env-file",
    )
    output = tmp_path / "platform.yaml"
    write_platform_yaml(answers, output)

    # Validate against the real schema (otaman-core).  No mocking — this is
    # the integration boundary that the previous bug slipped past.
    from otaman_core.validate_platform import (
        SCHEMA_PATH,
        load_yaml,
        validate_with_jsonschema,
    )
    config = load_yaml(output)
    schema = load_yaml(SCHEMA_PATH)
    errors = validate_with_jsonschema(config, schema)
    assert errors == [], (
        "Wizard output failed schema validation:\n"
        + "\n".join(f"  - {e}" for e in errors)
        + f"\n\nGenerated platform.yaml:\n{output.read_text(encoding='utf-8')}"
    )


def test_wizard_processes_nested_under_program_key(tmp_path):
    """Specific to the 2026-06-07 fix: processes must be at
    `program.processes.<name>.enabled: true`, NOT a top-level `processes:` key.
    """
    from otaman_cli.onboard.program_init.platform_gen import write_platform_yaml
    import yaml as _y

    answers = _base_answers(
        program_name="ptest",
        processes=["outcomes", "vocabulary"],
    )
    output = tmp_path / "platform.yaml"
    write_platform_yaml(answers, output)
    doc = _y.safe_load(output.read_text(encoding="utf-8"))

    # Must NOT be at top level
    assert "processes" not in doc, (
        "Top-level `processes:` rejected by platform-schema.yaml. "
        "Must nest under `program.processes`."
    )
    # MUST be nested under program with the {enabled: true} shape per design.md
    assert "program" in doc
    assert "processes" in doc["program"]
    assert doc["program"]["processes"]["outcomes"] == {"enabled": True}
    assert doc["program"]["processes"]["vocabulary"] == {"enabled": True}


def test_wizard_omits_processes_block_when_none_opted_in(tmp_path):
    """No processes selected → no `program.processes` block written."""
    from otaman_cli.onboard.program_init.platform_gen import write_platform_yaml
    import yaml as _y

    answers = _base_answers(program_name="noproc", processes=[])
    output = tmp_path / "platform.yaml"
    write_platform_yaml(answers, output)
    doc = _y.safe_load(output.read_text(encoding="utf-8"))

    # processes is either absent, or program has no processes key
    program = doc.get("program") or {}
    assert "processes" not in program
