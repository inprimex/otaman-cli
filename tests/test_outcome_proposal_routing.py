"""Tests for outcome-proposal-routing tasks 3.1–3.5.

Covers:
- 3.1 — `outcome-proposal` in MESSAGE_TYPES; send accepts it; unknown types rejected
- 3.2 — subject-pattern nudge when --type info + outcome-y subject
- 3.3 — strategic agent detection (explicit role wins; repo suffix fallback)
- 3.4 — upsert routing rule by when.type (append if missing, replace cc if present)
- 3.5 — 7 enumerated cases from tasks.md
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from otaman_cli.commands.bus_messaging import MESSAGE_TYPES
from otaman_cli.commands.init import _detect_strategic_agents, _ensure_routing_rules


# ---------------------------------------------------------------- task 3.1
class TestMessageTypeRegistry:
    def test_outcome_proposal_is_registered(self):
        assert "outcome-proposal" in MESSAGE_TYPES

    def test_canonical_types_still_registered(self):
        for t in (
            "info",
            "question",
            "task-assignment",
            "task-complete",
            "spec-change",
            "spec-change-request",
            "contract-change",
            "review-request",
            "proposal",
        ):
            assert t in MESSAGE_TYPES, f"missing canonical type {t!r}"

    def test_privileged_types_deliberately_excluded(self):
        """F012 (security GAP finding, 2026-07-04): spec-change-approved/
        -rejected assert a human decision was made and must only be
        producible via `otaman approve`'s TTY-gated confirmation, never
        the general send path — so they're deliberately absent from
        MESSAGE_TYPES even though otaman-core's VALID_TYPES includes them
        as legitimate bus message types overall."""
        assert "spec-change-approved" not in MESSAGE_TYPES
        assert "spec-change-rejected" not in MESSAGE_TYPES


def _project_root(tmp_path: Path) -> Path:
    (tmp_path / ".agents" / "bus" / "active").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
        "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_send(root: Path, *extra: str) -> subprocess.CompletedProcess:
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
            "test subject",
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


class TestSendTypeValidation:
    def test_outcome_proposal_type_accepted(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_send(root, "--type", "outcome-proposal")
        assert r.returncode == 0, (r.stdout, r.stderr)

    def test_unknown_type_rejected(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_send(root, "--type", "not-a-real-type")
        assert r.returncode == 2
        assert "Unknown message type" in r.stdout or "Unknown message type" in r.stderr

    def test_default_info_type_still_accepted(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_send(root)
        assert r.returncode == 0


# ---------------------------------------------------------------- task 3.2
class TestSubjectPatternNudge:
    def test_warns_on_outcome_keyword_with_info_type(self, tmp_path: Path):
        root = _project_root(tmp_path)
        # Override --subject via custom invocation
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
                "human",
                "--subject",
                "Outcome: ship X by end of quarter",
                "--body",
                "body",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0
        assert "outcome-proposal" in r.stdout, "expected nudge in stdout"

    def test_warns_on_proposal_keyword(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "otaman_cli.main",
                "send",
                "human",
                "--subject",
                "Proposal: refactor the bus",
                "--body",
                "body",
            ],
            cwd=root,
            env={
                **os.environ,
                "OTAMAN_AGENT": "cli-agent",
                "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
                "NO_COLOR": "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0
        assert "outcome-proposal" in r.stdout

    def test_warns_on_business_impact_phrase(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "otaman_cli.main",
                "send",
                "human",
                "--subject",
                "Considering business impact of the migration",
                "--body",
                "body",
            ],
            cwd=root,
            env={
                **os.environ,
                "OTAMAN_AGENT": "cli-agent",
                "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
                "NO_COLOR": "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0
        assert "outcome-proposal" in r.stdout

    def test_no_warning_on_neutral_subject(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_send(root)  # subject="test subject"
        assert r.returncode == 0
        assert "outcome-proposal" not in r.stdout

    def test_no_warning_when_type_is_outcome_proposal(self, tmp_path: Path):
        """User already declared the right type → no nudge needed."""
        root = _project_root(tmp_path)
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "otaman_cli.main",
                "send",
                "human",
                "--subject",
                "Outcome: critical refactor",
                "--body",
                "body",
                "--type",
                "outcome-proposal",
            ],
            cwd=root,
            env={
                **os.environ,
                "OTAMAN_AGENT": "cli-agent",
                "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
                "NO_COLOR": "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0
        # No "consider" nudge in output
        assert (
            "consider" not in r.stdout.lower()
            or "outcome-proposal" not in r.stdout.lower()
            or "Subject looks like" not in r.stdout
        )

    def test_send_NOT_blocked_when_warning_fires(self, tmp_path: Path):
        """The nudge must be advisory only — message still goes out."""
        root = _project_root(tmp_path)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "otaman_cli.main",
                "send",
                "human",
                "--subject",
                "Outcome candidate",
                "--body",
                "body",
            ],
            cwd=root,
            env={
                **os.environ,
                "OTAMAN_AGENT": "cli-agent",
                "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
                "NO_COLOR": "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Message file should exist on disk
        msgs = list((root / ".agents" / "bus" / "active").glob("*.md"))
        assert len(msgs) == 1
        assert "Outcome candidate" in msgs[0].read_text(encoding="utf-8")


# ---------------------------------------------------------------- task 3.3
class TestStrategicAgentDetection:
    def test_business_repo_suffix_detects_cpo(self):
        doc = {"repos": [{"name": "myprog-business", "owner": "cpo-agent"}]}
        assert _detect_strategic_agents(doc) == ["cpo-agent"]

    def test_strategy_repo_suffix_detects_cofounder(self):
        doc = {"repos": [{"name": "myprog-strategy", "owner": "cofounder-agent"}]}
        assert _detect_strategic_agents(doc) == ["cofounder-agent"]

    def test_both_suffixes_detect_both_agents(self):
        doc = {
            "repos": [
                {"name": "myprog-business", "owner": "cpo-agent"},
                {"name": "myprog-strategy", "owner": "cofounder-agent"},
            ]
        }
        result = _detect_strategic_agents(doc)
        assert result == ["cpo-agent", "cofounder-agent"]

    def test_no_strategic_repos_returns_empty(self):
        doc = {"repos": [{"name": "myprog-frontend", "owner": "frontend-agent"}]}
        assert _detect_strategic_agents(doc) == []

    def test_explicit_role_cpo_takes_precedence_over_suffix(self):
        """Spec: explicit role wins over name-suffix inference."""
        doc = {
            "agents": [{"name": "custom-cpo", "role": "cpo"}],
            "repos": [{"name": "myprog-business", "owner": "should-be-ignored"}],
        }
        assert _detect_strategic_agents(doc) == ["custom-cpo"]

    def test_explicit_role_cofounder_takes_precedence(self):
        doc = {
            "agents": [{"name": "custom-cofounder", "role": "cofounder"}],
            "repos": [{"name": "myprog-strategy", "owner": "should-be-ignored"}],
        }
        assert _detect_strategic_agents(doc) == ["custom-cofounder"]

    def test_one_role_explicit_other_via_suffix(self):
        """Explicit CPO + fallback cofounder via strategy repo."""
        doc = {
            "agents": [{"name": "custom-cpo", "role": "cpo"}],
            "repos": [{"name": "myprog-strategy", "owner": "cofounder-agent"}],
        }
        assert _detect_strategic_agents(doc) == ["custom-cpo", "cofounder-agent"]

    def test_no_signals_returns_empty(self):
        assert _detect_strategic_agents({}) == []
        assert _detect_strategic_agents({"repos": [], "agents": []}) == []


# ---------------------------------------------------------------- tasks 3.4 + 3.5
class TestRoutingRuleUpsert:
    """The 7 cases from tasks.md 3.5 plus task 3.4's upsert semantics."""

    def _write(self, p: Path, body: str) -> Path:
        p.write_text(body, encoding="utf-8")
        return p

    # 3.5 (a)
    def test_init_with_business_repo_seeds_rule(self, tmp_path: Path):
        p = self._write(
            tmp_path / "platform.yaml",
            "project: x\nversion: '1.0'\n"
            "repos:\n"
            "  - {name: x-business, path: ./b, owner: cpo-agent}\n"
            "  - {name: x, path: ., owner: spec-agent}\n",
        )
        rc = _ensure_routing_rules(p)
        assert rc == 1
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        rules = doc["bus"]["routing_rules"]
        op_rule = next((r for r in rules if r["when"].get("type") == "outcome-proposal"), None)
        assert op_rule is not None
        assert op_rule["cc"] == ["cpo-agent"]

    # 3.5 (b)
    def test_init_with_strategy_repo_seeds_rule(self, tmp_path: Path):
        p = self._write(
            tmp_path / "platform.yaml",
            "project: x\nversion: '1.0'\n"
            "repos:\n"
            "  - {name: x-strategy, path: ./s, owner: cofounder-agent}\n"
            "  - {name: x, path: ., owner: spec-agent}\n",
        )
        rc = _ensure_routing_rules(p)
        assert rc == 1
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        rules = doc["bus"]["routing_rules"]
        op_rule = next((r for r in rules if r["when"].get("type") == "outcome-proposal"), None)
        assert op_rule is not None
        assert op_rule["cc"] == ["cofounder-agent"]

    # 3.5 (c)
    def test_init_with_both_seeds_one_rule_with_both_agents(self, tmp_path: Path):
        p = self._write(
            tmp_path / "platform.yaml",
            "project: x\nversion: '1.0'\n"
            "repos:\n"
            "  - {name: x-business, path: ./b, owner: cpo-agent}\n"
            "  - {name: x-strategy, path: ./s, owner: cofounder-agent}\n"
            "  - {name: x, path: ., owner: spec-agent}\n",
        )
        rc = _ensure_routing_rules(p)
        assert rc == 1
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        rules = doc["bus"]["routing_rules"]
        op_rules = [r for r in rules if r["when"].get("type") == "outcome-proposal"]
        assert len(op_rules) == 1
        assert op_rules[0]["cc"] == ["cpo-agent", "cofounder-agent"]

    # 3.5 (d)
    def test_init_without_strategic_repos_seeds_no_outcome_rule(self, tmp_path: Path):
        p = self._write(
            tmp_path / "platform.yaml",
            "project: x\nversion: '1.0'\nrepos:\n  - {name: x, path: ., owner: spec-agent}\n",
        )
        _ensure_routing_rules(p)
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        rules = (doc.get("bus") or {}).get("routing_rules") or []
        op_rules = [r for r in rules if r.get("when", {}).get("type") == "outcome-proposal"]
        assert op_rules == []

    # 3.5 (e)
    def test_update_upserts_cc_when_agent_added(self, tmp_path: Path):
        """An existing `outcome-proposal` rule's `cc:` is replaced to match current detection."""
        p = self._write(
            tmp_path / "platform.yaml",
            "project: x\nversion: '1.0'\n"
            "repos:\n"
            "  - {name: x-business, path: ./b, owner: cpo-agent}\n"
            "  - {name: x-strategy, path: ./s, owner: cofounder-agent}\n"
            "  - {name: x, path: ., owner: spec-agent}\n"
            "bus:\n"
            "  routing_rules:\n"
            "    - when: {type: outcome-proposal}\n"
            "      cc: [cpo-agent]\n",
        )
        rc = _ensure_routing_rules(p)
        assert rc == 1
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        op_rules = [
            r for r in doc["bus"]["routing_rules"] if r["when"].get("type") == "outcome-proposal"
        ]
        assert len(op_rules) == 1
        assert op_rules[0]["cc"] == ["cpo-agent", "cofounder-agent"]

    # 3.5 (f)
    def test_update_does_not_duplicate_rule(self, tmp_path: Path):
        """If the rule already matches detected agents, no duplication."""
        p = self._write(
            tmp_path / "platform.yaml",
            "project: x\nversion: '1.0'\n"
            "repos:\n"
            "  - {name: x-business, path: ./b, owner: cpo-agent}\n"
            "  - {name: x, path: ., owner: spec-agent}\n"
            "bus:\n"
            "  routing_rules:\n"
            "    - when: {to: human}\n"
            "      cc: [spec-agent]\n"
            "    - when: {type: outcome-proposal}\n"
            "      cc: [cpo-agent]\n",
        )
        _ensure_routing_rules(p)
        # First call may add the cpo high/urgent rule (bus-cc-routing default).
        # But the outcome-proposal rule should NOT be duplicated.
        rc2 = _ensure_routing_rules(p)
        assert rc2 == 0, "second call should be a pure no-op"
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        op_rules = [
            r
            for r in doc["bus"]["routing_rules"]
            if r.get("when", {}).get("type") == "outcome-proposal"
        ]
        assert len(op_rules) == 1

    # 3.5 (g)
    def test_explicit_role_cpo_precedence_in_seed(self, tmp_path: Path):
        """Explicit `role: cpo` on an agent overrides the repo suffix detection."""
        p = self._write(
            tmp_path / "platform.yaml",
            "project: x\nversion: '1.0'\n"
            "agents:\n"
            "  - {name: custom-cpo, role: cpo}\n"
            "repos:\n"
            "  - {name: x-business, path: ./b, owner: should-be-ignored}\n"
            "  - {name: x, path: ., owner: spec-agent}\n",
        )
        _ensure_routing_rules(p)
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        op_rules = [
            r for r in doc["bus"]["routing_rules"] if r["when"].get("type") == "outcome-proposal"
        ]
        assert len(op_rules) == 1
        assert op_rules[0]["cc"] == ["custom-cpo"]

    # Additional: idempotency on no-bus-block path
    def test_idempotent_when_no_bus_block_and_no_strategic_agents(self, tmp_path: Path):
        p = self._write(
            tmp_path / "platform.yaml",
            "project: x\nversion: '1.0'\nrepos:\n  - {name: x, path: ., owner: spec-agent}\n",
        )
        rc1 = _ensure_routing_rules(p)
        assert rc1 == 1  # adds the bus-cc-routing default (to: human → spec-agent)
        rc2 = _ensure_routing_rules(p)
        assert rc2 == 0  # no further changes
