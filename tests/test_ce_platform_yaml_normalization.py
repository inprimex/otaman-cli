"""Tests for ce-org-agent-bootstrap task 4.1 — CE platform.yaml acceptance.

`otaman init` and `otaman validate` accept CE-shaped platform.yaml by
normalizing in-memory before schema validation:

  - `agent:` aliased to `owner:` per repo
  - `project:` inferred from parent directory name when absent
  - `version:` defaulted to "1.0" when absent
  - Hints emitted; on-disk file never rewritten
  - CE-shaped files NOT rejected outright
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from otaman_cli.main import _normalize_ce_platform_yaml_for_validation


class TestNormalizationHelper:
    def _write_yaml(self, p: Path, doc: dict) -> Path:
        p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return p

    def test_canonical_yaml_returns_unchanged(self, tmp_path: Path):
        """A canonical platform.yaml (all required fields) → no normalization."""
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "canon",
            "version": "1.0",
            "repos": [{"name": "r1", "path": "./r1", "owner": "ops-agent"}],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        assert norm == src, "no normalization → return source path"
        assert hints == []

    def test_agent_aliased_to_owner(self, tmp_path: Path):
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "version": "1.0",
            "repos": [{"name": "r1", "path": "./r1", "agent": "ops-agent"}],
        })
        original_text = src.read_text()
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        assert norm != src
        normalized = yaml.safe_load(norm.read_text())
        assert normalized["repos"][0]["owner"] == "ops-agent"
        # `agent:` is stripped from the validation copy (schema has
        # additionalProperties: false on repo items).  The source file is
        # untouched — `agent:` remains there for the user.
        assert "agent" not in normalized["repos"][0]
        assert src.read_text() == original_text
        assert any("aliased to `owner:`" in h for h in hints)
        norm.unlink()

    def test_project_inferred_from_parent_dir(self, tmp_path: Path):
        org_dir = tmp_path / "myorg"
        org_dir.mkdir()
        src = self._write_yaml(org_dir / "platform.yaml", {
            "version": "1.0",
            "repos": [{"name": "r1", "path": "./r1", "owner": "ops-agent"}],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        assert normalized["project"] == "myorg"
        assert any("inferred" in h.lower() and "myorg" in h for h in hints)
        norm.unlink()

    def test_project_sanitized_when_parent_has_invalid_chars(self, tmp_path: Path):
        org_dir = tmp_path / "My Org_Name"
        org_dir.mkdir()
        src = self._write_yaml(org_dir / "platform.yaml", {
            "version": "1.0",
            "repos": [{"name": "r1", "path": "./r1", "owner": "ops-agent"}],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        # 'My Org_Name' → 'my-org-name' (lowercase, non-[a-z0-9-] → '-')
        assert normalized["project"] == "my-org-name"
        norm.unlink()

    def test_version_defaulted_to_1_0(self, tmp_path: Path):
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "repos": [{"name": "r1", "path": "./r1", "owner": "ops-agent"}],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        assert normalized["version"] == "1.0"
        assert any("version" in h.lower() and "1.0" in h for h in hints)
        norm.unlink()

    def test_all_three_normalizations_combined(self, tmp_path: Path):
        org_dir = tmp_path / "myorg"
        org_dir.mkdir()
        src = self._write_yaml(org_dir / "platform.yaml", {
            "repos": [
                {"name": "r1", "path": "./r1", "agent": "ops-agent"},
                {"name": "r2", "path": "./r2", "agent": "dev-agent"},
            ],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        assert normalized["project"] == "myorg"
        assert normalized["version"] == "1.0"
        assert normalized["repos"][0]["owner"] == "ops-agent"
        assert normalized["repos"][1]["owner"] == "dev-agent"
        assert len(hints) == 3
        norm.unlink()

    def test_source_file_never_modified(self, tmp_path: Path):
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "repos": [{"name": "r1", "path": "./r1", "agent": "ops-agent"}],
        })
        original = src.read_text()
        _norm, _hints = _normalize_ce_platform_yaml_for_validation(src)
        assert src.read_text() == original, "source file must not be rewritten"

    def test_missing_file_returns_source_path_no_hints(self, tmp_path: Path):
        ghost = tmp_path / "missing.yaml"
        norm, hints = _normalize_ce_platform_yaml_for_validation(ghost)
        assert norm == ghost
        assert hints == []

    def test_runner_key_stripped_with_hint(self, tmp_path: Path):
        """ce-org-agent-bootstrap follow-up: `runner:` accepted as pass-through."""
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "version": "1.0",
            "runner": {"harnesses": [{"id": "claude-code", "binary": "claude"}]},
            "repos": [{"name": "r1", "path": "./r1", "owner": "ops-agent"}],
        })
        original = src.read_text()
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        assert "runner" not in normalized
        assert any("CE-runtime" in h and "runner" in h for h in hints)
        # Source untouched
        assert src.read_text() == original
        norm.unlink()

    def test_terminal_key_stripped_with_hint(self, tmp_path: Path):
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "version": "1.0",
            "terminal": {"local_auth": True},
            "repos": [{"name": "r1", "path": "./r1", "owner": "ops-agent"}],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        assert "terminal" not in normalized
        assert any("terminal" in h for h in hints)
        norm.unlink()

    def test_both_runner_and_terminal_stripped(self, tmp_path: Path):
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "version": "1.0",
            "runner": {"harnesses": []},
            "terminal": {"local_auth": True},
            "repos": [{"name": "r1", "path": "./r1", "owner": "ops-agent"}],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        assert "runner" not in normalized
        assert "terminal" not in normalized
        # Single combined hint mentions both
        hint_text = next((h for h in hints if "CE-runtime" in h), "")
        assert "runner" in hint_text and "terminal" in hint_text
        norm.unlink()

    def test_empty_repos_accepted_for_ce_org_scaffold(self, tmp_path: Path):
        """Empty repos[] accepted when CE-runtime markers are present (fresh org-dir)."""
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "version": "1.0",
            "runner": {"harnesses": []},
            "repos": [],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        # Placeholder repo injected into validation copy
        assert len(normalized["repos"]) == 1
        repo = normalized["repos"][0]
        assert repo["name"] == "ce-org-placeholder"
        assert repo["owner"] == "ops-agent"
        assert any("CE org-dir scaffold" in h for h in hints)
        norm.unlink()

    def test_empty_repos_without_ce_markers_not_padded(self, tmp_path: Path):
        """Empty repos[] WITHOUT CE-runtime keys should still fail validation
        (no synthetic injection — that's a CE-specific accommodation)."""
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "version": "1.0",
            "repos": [],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        # No changes were made (no CE markers); no placeholder injection
        assert norm == src, "non-CE empty repos should not get placeholder"
        assert not any("CE org-dir scaffold" in h for h in hints)

    def test_missing_repos_with_ce_markers_padded(self, tmp_path: Path):
        """`repos:` entirely absent + CE markers → also gets placeholder."""
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "version": "1.0",
            "terminal": {"local_auth": True},
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        normalized = yaml.safe_load(norm.read_text())
        assert len(normalized["repos"]) == 1
        norm.unlink()

    def test_existing_owner_takes_precedence_over_agent(self, tmp_path: Path):
        """If both agent: and owner: are set, owner is canonical; agent stripped from validation copy."""
        src = self._write_yaml(tmp_path / "platform.yaml", {
            "project": "x",
            "version": "1.0",
            "repos": [{
                "name": "r1", "path": "./r1",
                "agent": "ignored-agent",
                "owner": "real-owner",
            }],
        })
        norm, hints = _normalize_ce_platform_yaml_for_validation(src)
        # A tmp file is written because we strip `agent:` (schema rejects unknown),
        # but `owner:` is preserved unchanged and no aliasing hint fires.
        assert norm != src
        normalized = yaml.safe_load(norm.read_text())
        assert normalized["repos"][0]["owner"] == "real-owner"
        assert "agent" not in normalized["repos"][0]
        assert not any("aliased" in h for h in hints), \
            "no aliasing hint when owner was already canonical"
        norm.unlink()


class TestValidateCommand:
    """Integration: `otaman validate` accepts CE-shaped platform.yaml."""

    def _run_cli(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
            "NO_COLOR": "1",
        }
        return subprocess.run(
            [sys.executable, "-m", "otaman_cli.main", *args],
            cwd=root, env=env, capture_output=True, text=True, timeout=30,
        )

    def test_ce_shaped_yaml_passes_validation(self, tmp_path: Path):
        org_dir = tmp_path / "myorg"
        org_dir.mkdir()
        # CE shape: agent (not owner), no project, no version
        (org_dir / "platform.yaml").write_text(
            "repos:\n  - {name: r1, path: ./r1, agent: ops-agent}\n",
            encoding="utf-8",
        )
        r = self._run_cli(org_dir, "validate")
        assert r.returncode == 0, (
            f"CE-shaped yaml should pass.  stdout={r.stdout!r}  stderr={r.stderr!r}"
        )
        # Hints appear in stdout
        assert "hint" in r.stdout.lower()

    def test_canonical_yaml_passes_with_no_hints(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text(
            "project: canon\nversion: '1.0'\nrepos:\n  - {name: r1, path: ./r1, owner: ops-agent}\n",
            encoding="utf-8",
        )
        r = self._run_cli(tmp_path, "validate")
        assert r.returncode == 0
        assert "hint:" not in r.stdout

    def test_truly_invalid_yaml_still_fails(self, tmp_path: Path):
        """Garbage (e.g. missing repos entirely) → still rejected."""
        (tmp_path / "platform.yaml").write_text(
            "project: x\nversion: '1.0'\n",  # no repos
            encoding="utf-8",
        )
        r = self._run_cli(tmp_path, "validate")
        assert r.returncode != 0, "missing required field must still fail"

    def test_tmp_norm_file_cleaned_up(self, tmp_path: Path):
        """After `otaman validate` returns, no .otaman-ce-norm-*.yaml stragglers."""
        (tmp_path / "platform.yaml").write_text(
            "repos:\n  - {name: r1, path: ./r1, agent: ops-agent}\n",
            encoding="utf-8",
        )
        self._run_cli(tmp_path, "validate")
        stragglers = list(tmp_path.glob(".otaman-ce-norm-*.yaml"))
        assert stragglers == [], f"normalized tmp files leaked: {stragglers}"

    def test_source_file_byte_for_byte_unchanged_after_validate(self, tmp_path: Path):
        body = "repos:\n  - {name: r1, path: ./r1, agent: ops-agent}\n"
        src = tmp_path / "platform.yaml"
        src.write_text(body, encoding="utf-8")
        self._run_cli(tmp_path, "validate")
        assert src.read_text(encoding="utf-8") == body

    def test_fresh_ce_org_scaffold_passes_validation(self, tmp_path: Path):
        """End-to-end mirror of deploy-agent's failing case (2026-06-09):

        fresh CE-bootstrapped org dir has runner: + terminal: at root and
        empty repos:[]. After this follow-up, `otaman validate` must accept
        it cleanly so the bootstrap can delegate to `otaman init`.
        """
        org_dir = tmp_path / "myorg"
        org_dir.mkdir()
        (org_dir / "platform.yaml").write_text(
            "project: myorg\n"
            "version: '1.0'\n"
            "runner:\n"
            "  harnesses:\n"
            "    - {id: claude-code, binary: claude}\n"
            "terminal:\n"
            "  local_auth: true\n"
            "repos: []\n",
            encoding="utf-8",
        )
        r = self._run_cli(org_dir, "validate")
        assert r.returncode == 0, (
            f"fresh CE org scaffold must pass validation.  "
            f"stdout={r.stdout!r}  stderr={r.stderr!r}"
        )
        # Hints surface the pass-through behavior
        assert "CE-runtime" in r.stdout or "CE org-dir" in r.stdout
