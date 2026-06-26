"""Tests for bus-cc-routing tasks 2.1, 2.2, 2.4, 2.5, 2.6.

Task 2.3 (otaman_check MCP tool) lives in otaman-plugin and is not
implemented here — coordination question sent to plugin-agent.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from otaman_cli.main import _ensure_routing_rules
from otaman_cli.onboard.program_init.platform_gen import write_platform_yaml


# --------------------------------------------------------------------- task 2.1
class TestSendCcFlag:
    """`otaman send --cc <agent>` writes `cc: [...]` in message frontmatter."""

    def _run_send(self, root: Path, *extra: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "OTAMAN_AGENT": "cli-agent",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
        }
        return subprocess.run(
            [
                sys.executable, "-m", "otaman_cli.main",
                "send", "plugin-agent",
                "--subject", "test cc subject",
                "--body", "test body",
                *extra,
            ],
            cwd=root, env=env, capture_output=True, text=True, timeout=30,
        )

    def _setup_root(self, tmp_path: Path) -> Path:
        # Stage a minimal otaman project root: platform.yaml + .agents/bus/active
        (tmp_path / ".agents" / "bus" / "active").mkdir(parents=True)
        (tmp_path / "platform.yaml").write_text(
            "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
            "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_single_cc_recipient(self, tmp_path: Path):
        root = self._setup_root(tmp_path)
        r = self._run_send(root, "--cc", "spec-agent")
        assert r.returncode == 0, r.stderr
        # cli-send-cc-fanout-parity: cmd_send now writes primary + 1 CC copy
        msgs = list((root / ".agents" / "bus" / "active").glob("*.md"))
        assert len(msgs) == 2
        # Primary has cc: but no x-cc: marker; CC copy has both
        primary = next(m for m in msgs if "x-cc: true" not in m.read_text(encoding="utf-8"))
        cc_copy = next(m for m in msgs if "x-cc: true" in m.read_text(encoding="utf-8"))
        assert "cc: [spec-agent]" in primary.read_text(encoding="utf-8")
        assert "cc: [spec-agent]" in cc_copy.read_text(encoding="utf-8")
        assert "to-spec-agent" in cc_copy.name

    def test_multiple_cc_recipients_repeated(self, tmp_path: Path):
        root = self._setup_root(tmp_path)
        r = self._run_send(root, "--cc", "spec-agent", "--cc", "cpo-agent")
        assert r.returncode == 0, r.stderr
        msgs = list((root / ".agents" / "bus" / "active").glob("*.md"))
        body = msgs[0].read_text(encoding="utf-8")
        assert "cc: [spec-agent, cpo-agent]" in body

    def test_cc_deduplicates_and_drops_primary(self, tmp_path: Path):
        root = self._setup_root(tmp_path)
        # plugin-agent is the primary `to`; should be dropped from cc.
        # spec-agent appears twice; should appear once.
        r = self._run_send(
            root,
            "--cc", "spec-agent",
            "--cc", "plugin-agent",   # dropped: same as `to`
            "--cc", "spec-agent",     # dropped: duplicate
            "--cc", "  ",             # dropped: empty/whitespace
            "--cc", "cpo-agent",
        )
        assert r.returncode == 0, r.stderr
        msgs = list((root / ".agents" / "bus" / "active").glob("*.md"))
        body = msgs[0].read_text(encoding="utf-8")
        assert "cc: [spec-agent, cpo-agent]" in body
        assert "plugin-agent" not in body.split("cc:")[1].split("\n")[0]

    def test_no_cc_flag_no_cc_field(self, tmp_path: Path):
        """Absence of --cc → no `cc:` line in frontmatter."""
        root = self._setup_root(tmp_path)
        r = self._run_send(root)
        assert r.returncode == 0, r.stderr
        msgs = list((root / ".agents" / "bus" / "active").glob("*.md"))
        body = msgs[0].read_text(encoding="utf-8")
        # Frontmatter has no cc: line at the start of any line
        for line in body.splitlines():
            assert not line.startswith("cc:")


# --------------------------------------------------------------------- task 2.2 + 2.4
class TestCheckCcDisplay:
    """`otaman check` separates `x-cc: true` messages into a CC section."""

    def _setup_root(self, tmp_path: Path, *, agent: str = "spec-agent") -> Path:
        (tmp_path / ".agents" / "bus" / "active").mkdir(parents=True)
        (tmp_path / ".agents" / "current-agent").write_text(agent, encoding="utf-8")
        (tmp_path / "platform.yaml").write_text(
            "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
            f"repos:\n  - {{name: tst, path: ., owner: {agent}}}\n",
            encoding="utf-8",
        )
        return tmp_path

    def _plant_message(self, root: Path, *, name: str, to: str, frm: str = "runner-agent",
                       is_cc: bool = False, priority: str = "normal",
                       msg_type: str = "info", subject: str = "test subj",
                       cc_for: str = "spec-agent") -> Path:
        # Per the spec, CC copies carry both `cc:` (the recipient list) AND
        # `x-cc: true` (the marker that this file IS a CC copy).
        cc_line = f"cc: [{cc_for}]\n" if is_cc else ""
        x_cc_line = "x-cc: true\n" if is_cc else ""
        msg = (
            f"---\n"
            f"id: tst-{name}\n"
            f"from: {frm}\n"
            f"to: {to}\n"
            f"{cc_line}"
            f"{x_cc_line}"
            f"priority: {priority}\n"
            f"type: {msg_type}\n"
            f"timestamp: 2026-06-08T20:00:00+00:00\n"
            f"status: pending\n"
            f"---\n\n"
            f"## Subject: {subject}\n\n"
            f"body here\n"
        )
        path = root / ".agents" / "bus" / "active" / f"20260608T200000-{name}.md"
        path.write_text(msg, encoding="utf-8")
        return path

    def _run_check(self, root: Path) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "OTAMAN_AGENT": "spec-agent",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
            "NO_COLOR": "1",
        }
        return subprocess.run(
            [sys.executable, "-m", "otaman_cli.main", "check"],
            cwd=root, env=env, capture_output=True, text=True, timeout=30,
        )

    def test_no_cc_messages_no_cc_section(self, tmp_path: Path):
        root = self._setup_root(tmp_path)
        self._plant_message(root, name="primary1", to="spec-agent")
        r = self._run_check(root)
        assert r.returncode == 0
        assert "CC (copies):" not in r.stdout
        assert "primary1" in r.stdout

    def test_cc_section_appears_when_x_cc_present(self, tmp_path: Path):
        root = self._setup_root(tmp_path)
        self._plant_message(root, name="primary1", to="spec-agent")
        self._plant_message(root, name="cc1", to="human", is_cc=True, frm="runner-agent")
        r = self._run_check(root)
        assert r.returncode == 0
        assert "CC (copies):" in r.stdout
        assert "cc1" in r.stdout

    def test_cc_line_uses_dot_bullet_not_star(self, tmp_path: Path):
        root = self._setup_root(tmp_path)
        self._plant_message(root, name="cc1", to="human", is_cc=True)
        r = self._run_check(root)
        assert r.returncode == 0
        # Find the CC line
        cc_lines = [l for l in r.stdout.splitlines() if "cc1" in l]
        assert cc_lines, "expected a line containing cc1"
        # The CC bullet line should contain "·" and not begin with "*"
        bullet_line = next((l for l in cc_lines if "·" in l), None)
        assert bullet_line is not None, f"expected · bullet, got: {cc_lines}"

    def test_cc_line_includes_to_field(self, tmp_path: Path):
        root = self._setup_root(tmp_path)
        self._plant_message(root, name="cc1", to="human", is_cc=True, frm="runner-agent")
        r = self._run_check(root)
        assert r.returncode == 0
        # The CC bullet line should mention the primary recipient
        cc_lines = [l for l in r.stdout.splitlines() if "cc1" in l and "·" in l]
        assert any("to" in l and "human" in l for l in cc_lines), \
            f"CC bullet should include to-field; got: {cc_lines}"

    def test_cc_does_not_appear_in_primary_section(self, tmp_path: Path):
        root = self._setup_root(tmp_path)
        self._plant_message(root, name="primary1", to="spec-agent", subject="A primary")
        self._plant_message(root, name="cc1", to="human", is_cc=True, subject="A cc copy")
        r = self._run_check(root)
        assert r.returncode == 0
        # Split output by the CC heading
        before, _, after = r.stdout.partition("CC (copies):")
        assert "A primary" in before
        # cc1 must NOT appear in the primary section
        assert "cc1" not in before
        # cc1 IS in the CC section
        assert "cc1" in after


# --------------------------------------------------------------------- task 2.5 + 2.6
class TestInitRoutingRules:
    """`otaman init` / `init --update` writes default bus.routing_rules."""

    def test_fresh_init_produces_routing_rules(self, tmp_path: Path):
        """write_platform_yaml (used by program-init) includes routing_rules."""
        out = tmp_path / "platform.yaml"
        write_platform_yaml({
            "program_name": "fresh",
            "primary_repo": ".",
            "mode": 1,
            "active_edition": "ce",
        }, out)
        doc = yaml.safe_load(out.read_text())
        assert "bus" in doc
        assert "routing_rules" in doc["bus"]
        assert any(
            r.get("when", {}).get("to") == "human" and "spec-agent" in r.get("cc", [])
            for r in doc["bus"]["routing_rules"]
        )

    def test_fresh_init_includes_cpo_rule_when_cpo_repo_present(self, tmp_path: Path):
        out = tmp_path / "platform.yaml"
        write_platform_yaml({
            "program_name": "cpo-test",
            "primary_repo": ".",
            "scaffold_business": True,    # adds <name>-business owned by cpo-agent
            "mode": 2,
            "active_edition": "ce",
        }, out)
        doc = yaml.safe_load(out.read_text())
        rules = doc["bus"]["routing_rules"]
        cpo_rule = next(
            (r for r in rules if "cpo-agent" in (r.get("cc") or [])),
            None,
        )
        assert cpo_rule is not None
        assert cpo_rule["when"].get("to") == "human"
        assert "high" in cpo_rule["when"].get("priority", [])

    def test_fresh_init_no_cpo_rule_without_cpo_repo(self, tmp_path: Path):
        out = tmp_path / "platform.yaml"
        write_platform_yaml({
            "program_name": "no-cpo",
            "primary_repo": ".",
            "mode": 1,
            "active_edition": "ce",
        }, out)
        doc = yaml.safe_load(out.read_text())
        rules = doc["bus"]["routing_rules"]
        assert all("cpo-agent" not in (r.get("cc") or []) for r in rules)

    def test_update_adds_missing_block_appends_plaintext(self, tmp_path: Path):
        """When bus: is entirely absent, the helper appends a plain-text block."""
        platform_yaml = tmp_path / "platform.yaml"
        platform_yaml.write_text(
            "project: addrules\nversion: '1.0'\n"
            "repos:\n  - {name: x, path: ., owner: spec-agent}\n",
            encoding="utf-8",
        )
        rc = _ensure_routing_rules(platform_yaml)
        assert rc == 1
        doc = yaml.safe_load(platform_yaml.read_text())
        assert "bus" in doc
        assert any(
            r["when"]["to"] == "human" and r["cc"] == ["spec-agent"]
            for r in doc["bus"]["routing_rules"]
        )

    def test_update_idempotent_when_rules_already_present(self, tmp_path: Path):
        """Re-running the helper does NOT duplicate existing rules."""
        platform_yaml = tmp_path / "platform.yaml"
        platform_yaml.write_text(
            "project: idemp\nversion: '1.0'\n"
            "repos:\n  - {name: x, path: ., owner: spec-agent}\n"
            "bus:\n"
            "  routing_rules:\n"
            "    - when: {to: human}\n"
            "      cc: [spec-agent]\n",
            encoding="utf-8",
        )
        rc = _ensure_routing_rules(platform_yaml)
        assert rc == 0, "no rules should be added"
        # Second invocation also no-op
        rc2 = _ensure_routing_rules(platform_yaml)
        assert rc2 == 0
        doc = yaml.safe_load(platform_yaml.read_text())
        # Still exactly one rule
        assert len(doc["bus"]["routing_rules"]) == 1

    def test_update_adds_cpo_rule_when_cpo_repo_present_and_missing(self, tmp_path: Path):
        """cpo-agent rule injected only when cpo-agent owns a repo + rule absent."""
        platform_yaml = tmp_path / "platform.yaml"
        platform_yaml.write_text(
            "project: addcpo\nversion: '1.0'\n"
            "repos:\n"
            "  - {name: x, path: ., owner: spec-agent}\n"
            "  - {name: y, path: ../y, owner: cpo-agent}\n"
            "bus:\n"
            "  routing_rules:\n"
            "    - when: {to: human}\n"
            "      cc: [spec-agent]\n",
            encoding="utf-8",
        )
        rc = _ensure_routing_rules(platform_yaml)
        assert rc == 1, "cpo-agent rule should be added"
        doc = yaml.safe_load(platform_yaml.read_text())
        rules = doc["bus"]["routing_rules"]
        assert any("cpo-agent" in (r.get("cc") or []) for r in rules)

    def test_update_does_not_add_cpo_rule_without_cpo_repo(self, tmp_path: Path):
        platform_yaml = tmp_path / "platform.yaml"
        platform_yaml.write_text(
            "project: nocpo\nversion: '1.0'\n"
            "repos:\n  - {name: x, path: ., owner: spec-agent}\n",
            encoding="utf-8",
        )
        rc = _ensure_routing_rules(platform_yaml)
        assert rc == 1
        doc = yaml.safe_load(platform_yaml.read_text())
        rules = doc["bus"]["routing_rules"]
        assert all("cpo-agent" not in (r.get("cc") or []) for r in rules)

    def test_update_handles_missing_platform_yaml_silently(self, tmp_path: Path):
        rc = _ensure_routing_rules(tmp_path / "missing.yaml")
        assert rc == 0
