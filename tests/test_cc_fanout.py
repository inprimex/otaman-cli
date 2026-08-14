"""Tests for cli-send-cc-fanout-parity (tasks 1.1-1.7).

Ported helpers match bus_server.py:157-283 behavior; cmd_send writes per-CC
copies after the primary file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from otaman_cli.cc_fanout import (
    cc_copy_filename,
    compute_effective_cc,
    evaluate_routing_rules,
    inject_x_cc,
    load_routing_rules,
)


def _stage_project(tmp_path: Path, platform_yaml_extra: str = "") -> Path:
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\n"
        "repos:\n  - {name: tst, path: ., owner: cli-agent}\n" + platform_yaml_extra,
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------- task 1.1
class TestLoadRoutingRules:
    def test_no_platform_yaml_returns_empty(self, tmp_path: Path):
        assert load_routing_rules(tmp_path) == []

    def test_no_bus_section_returns_empty(self, tmp_path: Path):
        _stage_project(tmp_path)
        assert load_routing_rules(tmp_path) == []

    def test_loads_rules_when_present(self, tmp_path: Path):
        _stage_project(
            tmp_path,
            platform_yaml_extra=(
                "bus:\n  routing_rules:\n    - {when: {to: human}, cc: [spec-agent]}\n"
            ),
        )
        rules = load_routing_rules(tmp_path)
        assert len(rules) == 1
        assert rules[0]["when"]["to"] == "human"
        assert rules[0]["cc"] == ["spec-agent"]

    def test_malformed_yaml_returns_empty(self, tmp_path: Path):
        _stage_project(tmp_path)
        (tmp_path / "platform.yaml").write_text("not: valid: yaml: shape:\n", encoding="utf-8")
        assert load_routing_rules(tmp_path) == []

    def test_non_dict_rules_filtered_out(self, tmp_path: Path):
        _stage_project(
            tmp_path,
            platform_yaml_extra=(
                "bus:\n"
                "  routing_rules:\n"
                "    - {when: {to: human}, cc: [spec-agent]}\n"
                "    - garbage-string\n"
            ),
        )
        rules = load_routing_rules(tmp_path)
        assert len(rules) == 1


# ---------------------------------------------------------------- task 1.2
class TestEvaluateRoutingRules:
    def test_simple_to_match(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        assert evaluate_routing_rules(rules, "human", "normal") == {"spec-agent"}

    def test_to_mismatch_no_match(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        assert evaluate_routing_rules(rules, "runner-agent", "normal") == set()

    def test_union_across_rules(self):
        rules = [
            {"when": {"to": "human"}, "cc": ["spec-agent"]},
            {"when": {"to": "human", "priority": "high"}, "cc": ["cpo-agent"]},
        ]
        assert evaluate_routing_rules(rules, "human", "high") == {"spec-agent", "cpo-agent"}

    def test_priority_list_OR_semantics(self):
        rules = [
            {
                "when": {"to": "human", "priority": ["high", "urgent"]},
                "cc": ["cpo-agent"],
            }
        ]
        assert evaluate_routing_rules(rules, "human", "high") == {"cpo-agent"}
        assert evaluate_routing_rules(rules, "human", "urgent") == {"cpo-agent"}
        assert evaluate_routing_rules(rules, "human", "normal") == set()

    def test_type_match_outcome_proposal_routing(self):
        """outcome-proposal-routing 1.1: when.type aware matching."""
        rules = [
            {
                "when": {"type": "outcome-proposal"},
                "cc": ["cpo-agent", "cofounder-agent"],
            }
        ]
        result = evaluate_routing_rules(
            rules,
            "human",
            "normal",
            msg_type="outcome-proposal",
        )
        assert result == {"cpo-agent", "cofounder-agent"}

    def test_type_rule_skipped_when_msg_type_none(self):
        rules = [{"when": {"type": "outcome-proposal"}, "cc": ["x"]}]
        assert evaluate_routing_rules(rules, "human", "normal", msg_type=None) == set()

    def test_unknown_when_key_silently_skipped(self):
        rules = [{"when": {"to": "human", "color": "blue"}, "cc": ["x"]}]
        assert evaluate_routing_rules(rules, "human", "normal") == set()


# ---------------------------------------------------------------- task 1.3
class TestComputeEffectiveCC:
    def test_no_cc_returns_empty(self):
        assert compute_effective_cc("human", "normal", None, [], None) == []

    def test_explicit_cc_only(self):
        assert compute_effective_cc(
            "human",
            "normal",
            ["a", "b"],
            [],
        ) == ["a", "b"]

    def test_routing_rule_fires(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        assert compute_effective_cc("human", "normal", None, rules) == ["spec-agent"]

    def test_union_explicit_and_rule(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        result = compute_effective_cc("human", "normal", ["other"], rules)
        assert "other" in result and "spec-agent" in result
        # explicit ordered first
        assert result.index("other") < result.index("spec-agent")

    def test_dedup_when_explicit_and_rule_agree(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        result = compute_effective_cc("human", "normal", ["spec-agent"], rules)
        assert result.count("spec-agent") == 1

    def test_primary_to_excluded_from_cc(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent", "human"]}]
        result = compute_effective_cc("human", "normal", ["human"], rules)
        assert "human" not in result

    def test_type_aware_rule_in_compute(self):
        rules = [{"when": {"type": "outcome-proposal"}, "cc": ["cpo-agent"]}]
        result = compute_effective_cc(
            "human",
            "normal",
            None,
            rules,
            msg_type="outcome-proposal",
        )
        assert result == ["cpo-agent"]


# ---------------------------------------------------------------- task 1.4
class TestInjectXCC:
    def test_simple_injection(self):
        content = "---\nid: x\nfrom: a\nto: b\n---\n\nbody\n"
        out = inject_x_cc(content)
        assert "x-cc: true" in out
        # Frontmatter still parseable
        assert out.count("---") == 2
        # Body preserved
        assert "body" in out

    def test_no_frontmatter_passthrough(self):
        content = "no frontmatter here"
        assert inject_x_cc(content) == content

    def test_injection_after_last_field(self):
        content = "---\nid: x\nfrom: a\n---\n"
        out = inject_x_cc(content)
        # x-cc: true sits immediately before the closing ---
        assert out.endswith("x-cc: true\n---\n")


# ---------------------------------------------------------------- task 1.5 cc_copy_filename
class TestCcCopyFilename:
    def test_canonical_shape(self):
        f = cc_copy_filename(
            timestamp="20260626T120000",
            from_agent="cli-agent",
            cc_recipient="plugin-agent",
            slug="some-subject",
        )
        assert f == "20260626T120000-cli-agent-to-plugin-agent-some-subject.md"

    def test_slashes_in_recipient_sanitized(self):
        f = cc_copy_filename(
            timestamp="t",
            from_agent="a",
            cc_recipient="ns/agent",
            slug="s",
        )
        assert "/" not in f


# ---------------------------------------------------------------- task 1.5 + 1.7 — cmd_send
class TestCmdSendFanout:
    """Verify the actual cmd_send writes per-CC copies after the primary."""

    def _run_send(self, root: Path, *extra) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "OTAMAN_AGENT": "cli-agent",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
            "NO_COLOR": "1",
        }
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "otaman_cli.main",
                "send",
                "plugin-agent",
                "--subject",
                "ping",
                "--body",
                "body",
                *extra,
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    # 1.6 (a) — no-cc: no extra files written
    def test_no_cc_writes_only_primary(self, tmp_path: Path):
        _stage_project(tmp_path)
        r = self._run_send(tmp_path)
        assert r.returncode == 0
        msgs = list((tmp_path / ".agents" / "bus" / "active").glob("*.md"))
        assert len(msgs) == 1
        body = msgs[0].read_text()
        assert "x-cc:" not in body

    # 1.6 (b) — explicit-cc: one extra file per recipient
    def test_explicit_cc_writes_per_recipient_copies(self, tmp_path: Path):
        _stage_project(tmp_path)
        r = self._run_send(tmp_path, "--cc", "spec-agent", "--cc", "cpo-agent")
        assert r.returncode == 0, (r.stdout, r.stderr)
        msgs = list((tmp_path / ".agents" / "bus" / "active").glob("*.md"))
        # 1 primary + 2 CC copies
        assert len(msgs) == 3
        # CC copies have x-cc: true; primary does NOT
        primary_count = sum(1 for m in msgs if "x-cc: true" not in m.read_text())
        cc_count = sum(1 for m in msgs if "x-cc: true" in m.read_text())
        assert primary_count == 1
        assert cc_count == 2

    # 1.6 (c) — routing-rule-fire: implicit CC from platform.yaml routing_rules
    def test_routing_rule_implicit_cc(self, tmp_path: Path):
        _stage_project(
            tmp_path,
            platform_yaml_extra=(
                "bus:\n  routing_rules:\n    - {when: {to: plugin-agent}, cc: [spec-agent]}\n"
            ),
        )
        r = self._run_send(tmp_path)  # no --cc; rule should add spec-agent
        assert r.returncode == 0
        msgs = list((tmp_path / ".agents" / "bus" / "active").glob("*.md"))
        # 1 primary + 1 CC copy (spec-agent from the rule)
        assert len(msgs) == 2
        # CC copy stem includes "spec-agent"
        cc_copies = [m for m in msgs if "x-cc: true" in m.read_text()]
        assert len(cc_copies) == 1
        assert "to-spec-agent" in cc_copies[0].name

    # 1.6 (d) — union+dedup: explicit + rule with overlap → unique recipients
    def test_union_with_dedup_single_copy_per_recipient(self, tmp_path: Path):
        _stage_project(
            tmp_path,
            platform_yaml_extra=(
                "bus:\n  routing_rules:\n    - {when: {to: plugin-agent}, cc: [spec-agent]}\n"
            ),
        )
        r = self._run_send(tmp_path, "--cc", "spec-agent")
        assert r.returncode == 0
        cc_copies = [
            m
            for m in (tmp_path / ".agents" / "bus" / "active").glob("*.md")
            if "x-cc: true" in m.read_text()
        ]
        # Even though spec-agent appears in both --cc AND the rule, only one copy
        assert len(cc_copies) == 1

    # 1.6 (e) — primary-excluded: --cc primary_to is dropped
    def test_primary_recipient_excluded_from_cc_copies(self, tmp_path: Path):
        _stage_project(tmp_path)
        self._run_send(tmp_path, "--cc", "plugin-agent", "--cc", "spec-agent")
        # plugin-agent is the primary; should NOT get a CC copy
        cc_copies = [
            m
            for m in (tmp_path / ".agents" / "bus" / "active").glob("*.md")
            if "x-cc: true" in m.read_text()
        ]
        # Only spec-agent CC copy
        assert len(cc_copies) == 1
        assert "to-spec-agent" in cc_copies[0].name
        assert not any(
            "to-plugin-agent" in m.name and "x-cc: true" in m.read_text()
            for m in (tmp_path / ".agents" / "bus" / "active").glob("*.md")
        )

    # 1.6 (f) — type-aware-rule: --type matches when.type
    def test_type_aware_rule_fires(self, tmp_path: Path):
        _stage_project(
            tmp_path,
            platform_yaml_extra=(
                "bus:\n  routing_rules:\n    - {when: {type: outcome-proposal}, cc: [cpo-agent]}\n"
            ),
        )
        r = self._run_send(tmp_path, "--type", "outcome-proposal")
        assert r.returncode == 0, (r.stdout, r.stderr)
        cc_copies = [
            m
            for m in (tmp_path / ".agents" / "bus" / "active").glob("*.md")
            if "x-cc: true" in m.read_text()
        ]
        assert len(cc_copies) == 1
        assert "to-cpo-agent" in cc_copies[0].name

    # 1.7 — integration: incident #1 replication
    def test_integration_replicates_incident_1(self, tmp_path: Path):
        """Incident #1 (2026-06-22): cli-agent sent drift report with
        --cc plugin-agent, plugin-agent's check never surfaced it.
        After this PR, the x-cc copy MUST exist and be addressed-to
        plugin-agent so their check picks it up via the to: filter.
        """
        _stage_project(tmp_path)
        r = self._run_send(tmp_path, "--cc", "plugin-agent")
        # Wait — primary to: is already plugin-agent in our _run_send.  Use a
        # different `to:` to make the CC distinct.
        # Re-run with a different primary
        for m in (tmp_path / ".agents" / "bus" / "active").glob("*.md"):
            m.unlink()
        env = {
            **os.environ,
            "OTAMAN_AGENT": "cli-agent",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
            "NO_COLOR": "1",
        }
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "otaman_cli.main",
                "send",
                "spec-agent",  # primary
                "--cc",
                "plugin-agent",  # CC
                "--subject",
                "drift report",
                "--body",
                "see analysis",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0

        # 1 primary (to: spec-agent) + 1 CC copy (for plugin-agent)
        msgs = list((tmp_path / ".agents" / "bus" / "active").glob("*.md"))
        cc_copies = [m for m in msgs if "x-cc: true" in m.read_text()]
        assert len(cc_copies) == 1
        # plugin-agent's check filter looks for "to: plugin-agent" OR
        # x-cc + plugin-agent in cc[]; the CC copy carries both signals
        body = cc_copies[0].read_text()
        assert "x-cc: true" in body
        assert "cc: [plugin-agent]" in body or "plugin-agent" in body
        # And the filename mentions plugin-agent so the glob hits
        assert "to-plugin-agent" in cc_copies[0].name
